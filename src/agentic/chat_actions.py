"""Selected chat actions on the existing STA worker; engineering rules stay in A1-A8."""
from agentic.state import fail_workflow


async def run_chat_action(state, action):
    from agentic import orchestrator as flow
    from agentic.agents.a3_frame_generator_agent import generate_members, check_geometry
    from agentic.evidence import frame_signature

    async def checked(stage):
        nonlocal state
        state = await getattr(flow, "node_" + stage)(state)
        return state.status != "FAILED"

    async def frame():
        if not await checked("a4"):
            return False
        while flow.route_after_a4(state) == "a3_frame_generator":
            if not await checked("a3") or not await checked("a4"):
                return False
        return state.agent_statuses.get("A4", {}).get("status") == "PASSED"

    async def joints():
        if not await checked("a5"):
            return False
        while flow.route_after_a5(state) == "a5_ideastatica_analyzer":
            if not await checked("a5"):
                return False
        return state.agent_statuses.get("A5", {}).get("status") == "PASSED"

    async def audit():
        if not await checked("a6"):
            return False
        while state.agent_statuses.get("A6", {}).get("status") == "REVISING":
            target = flow.route_after_a6(state)
            if target == "a3_frame_generator":
                if not await checked("a3") or not await frame() or not await joints():
                    return False
            elif target == "a5_ideastatica_analyzer":
                if not await joints():
                    return False
            else:
                return False
            if not await checked("a6"):
                return False
        return state.validation_report.get("passed") is True

    if action in ("preview_structure", "generate_structure"):
        if not await checked("a1"):
            return state
        if action == "preview_structure":
            # Existing catalogue fallbacks are allowed only for an explicitly unverified preview.
            candidate = generate_members(state.grid_config)
            check_geometry(candidate)
            state.frame_candidate = candidate
            state.readiness["preview_only"] = True
            state.agent_statuses["A3"] = {"status": "PREVIEW", "message": "Geometry preview only; no live catalogue or structural verification."}
        elif not await checked("a2") or not await checked("a3"):
            return state
    else:
        if state.readiness.get("preview_only") or state.agent_statuses.get("A3", {}).get("status") != "PASSED":
            return fail_workflow(state, "CHAT", "Generate a live validated structure before engineering analysis; a preview is not verified evidence.")
        if action == "analyze_frame":
            for agent in ("A4", "A5", "A6", "A7", "A8"):
                state.agent_statuses.pop(agent, None)
            if not await frame():
                return state
        else:
            if (not state.frame_grader_result or not state.frame_grader_result.passed or
                    state.staad_results.get("candidate_signature") != frame_signature(state)):
                return fail_workflow(state, "CHAT", "Current passing STAAD evidence is required.")
            if action == "design_connections":
                for agent in ("A5", "A6", "A7", "A8"):
                    state.agent_statuses.pop(agent, None)
                state.validation_report.clear()
                state.tekla_results.clear()
                state.final_report.clear()
                if not await joints():
                    return state
            elif action in ("validate_package", "publish_to_tekla"):
                for agent in ("A6", "A7", "A8"):
                    state.agent_statuses.pop(agent, None)
                state.tekla_results.clear()
                state.final_report.clear()
                if not await audit():
                    return state
                if action == "publish_to_tekla":
                    if not await checked("a7") or not await checked("a8"):
                        return state
                    return state  # Retain the existing human Gate 3 pause.
            else:
                raise ValueError("Unsupported chat action.")
    if state.status != "FAILED":
        state.status = "READY"
    return state
