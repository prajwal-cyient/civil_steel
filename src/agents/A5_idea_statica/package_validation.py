"""Bind reference IDEA plate grades to the actual standard Tekla details."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re


def reference_plate_material_policy():
    from agents.A3_generate_structure.connection_scope import STANDARD_DETAILS
    folder = Path(__file__).resolve().parents[3] / "tekla_connection_attributes"
    attributes, hashes = {}, {}
    for detail in STANDARD_DETAILS.values():
        number, name = detail["component"], detail["attribute_file"]
        path = folder / f"{name}.j{number}"
        content = path.read_bytes()
        materials = {key: value for key, value in re.findall(
            r'^joint_attributes\.(mat\d*)\s+"([^"]+)"', content.decode("utf-8-sig"), re.MULTILINE)}
        if not materials or set(materials.values()) != {"S355J0"}:
            raise ValueError(f"Reference plate grade is not verified S355J0 in {path.name}.")
        attributes[str(number)] = materials
        hashes[path.name] = hashlib.sha256(content).hexdigest()
    return {"idea_material_name": "S 355", "tekla_material_name": "S355J0",
            "component_attributes": attributes, "attribute_file_sha256": hashes,
            "basis": "Plate materials explicitly specified by the senior standard Tekla component files."}


def material_only_plate_evidence(before, after, policy):
    def canonical(value):
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("Nonfinite connection geometry during material verification.")
            return round(value, 9)
        if isinstance(value, dict):
            return {k: canonical(v) for k, v in value.items()}
        if isinstance(value, list):
            return [canonical(v) for v in value]
        return value
    def geometry(value):
        result = deepcopy(value)
        for plate in result.get("plates", []):
            plate.pop("material", None)
        return canonical(result)
    if geometry(before) != geometry(after):
        raise ValueError("Plate material correction changed unrelated geometry, members, welds or topology.")
    original = {p["id"]: p for p in before.get("plates", [])}
    plates = after.get("plates", [])
    if not plates or any(p.get("material") != policy["idea_material_name"] for p in plates):
        raise ValueError("IDEA plates did not retain the declared reference material.")
    changes = [{"id": p["id"], "name": p["name"], "old": original[p["id"]]["material"],
                "new": p["material"]} for p in plates if p["material"] != original[p["id"]]["material"]]
    return {"policy": policy, "geometry_unchanged": True,
            "plates": [{"id": p["id"], "name": p["name"], "material": p["material"]} for p in plates],
            "material_corrections": changes}


def verify_plate_material_evidence(evidence, policy, iom_file):
    if not policy:
        return
    if not evidence or evidence.get("policy") != policy or evidence.get("geometry_unchanged") is not True:
        raise ValueError("Reference plate material evidence is missing or differs from the declared Tekla details.")
    plates = evidence.get("plates", [])
    if not plates or any(p.get("material") != policy["idea_material_name"] for p in plates):
        raise ValueError("Plate material evidence does not cover the reference grade.")
    if not iom_file or not Path(iom_file).is_file():
        raise ValueError("Plate material source IOM file is missing.")
    content = Path(iom_file).read_bytes()
    if hashlib.sha256(content).hexdigest() != evidence.get("iom_sha256"):
        raise ValueError("Plate material source IOM file changed after calculation.")
    actual = [{"id": p["id"], "name": p["name"], "material": p["material"]}
              for p in json.loads(content).get("plates", [])]
    if actual != plates:
        raise ValueError("Plate material evidence differs from the native IOM plates.")


"""
a6_package_validator_agent.py

Agent A6: Validate the Package (Data Integrity Auditor).
Cross-checks evidence from A3, A4, and A5.
Verifies that 100% of joints passed and data is intact before Tekla publication.
"""

from datetime import datetime
import hashlib
import json
from orchestration.state import WorkflowState, AgentLogEntry
from agents.A4_staad import grade_staad_results
from .A5C_evaluate_and_audit import grade_joint_check_summary, validate_calculation_check_coverage
from . import idea_runtime as idea_agent
from utils.evidence import frame_signature
from agents.A5_idea_statica.package_validation import reference_plate_material_policy, verify_plate_material_evidence


def run_a6_package_validator(state: WorkflowState, *, agent_id: str = "A6") -> WorkflowState:
    """
    Executes Agent A6: Audits all calculation evidence and prepares the verified package.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    state.current_agent = agent_id

    from agents.A6_tekla.A6B_trigger_api import unmapped_connection_corrections
    unsupported = unmapped_connection_corrections(state)
    if unsupported:
        from orchestration.state import fail_workflow
        return fail_workflow(state, agent_id, "Connection corrections lack a validated Tekla attribute mapping; "
                             f"publication stopped before changing the physical model: {unsupported}")

    failures = []
    frame_failures = []
    if state.staad_results.get("candidate_signature") != frame_signature(state):
        frame_failures.append("STAAD evidence does not belong to the current frame and loads.")

    # Recheck source evidence instead of trusting previously cached PASS flags.
    raw_frame_grade = grade_staad_results(state.staad_results, state.grid_config.get("col_height_mm", 5000), state.run_policy)
    if not raw_frame_grade.passed:
        frame_failures.append(f"STAAD source evidence is invalid or failed: {raw_frame_grade.summary}")

    # 1. Check frame candidate
    if not state.frame_candidate.get("members"):
        frame_failures.append("No members found in Frame Candidate.")

    # 2. Check STAAD compliance
    if not state.frame_grader_result or not state.frame_grader_result.passed:
        frame_failures.append("STAAD structural analysis did not receive a PASS from Frame Grader.")
    # 3. Check Connection design coverage
    joint_groups = state.staad_results.get("joint_groups", {})
    if not joint_groups:
        frame_failures.append("No connection groups returned by STAAD.")
    failures.extend(frame_failures)
    from agents.A3_generate_structure.connection_scope import scope_for_state
    try:
        scope = scope_for_state(state)
    except ValueError as exc:
        from orchestration.state import fail_workflow
        return fail_workflow(state, agent_id, str(exc))
    plate_policy = reference_plate_material_policy() if scope["mode"] == "reference_procedure" else None
    for jg_id in scope["idea_required_groups"]:
        grader = state.joint_grader_results.get(jg_id)
        if not grader or not grader.passed:
            failures.append(f"Connection for joint group '{jg_id}' did not receive a PASS.")
        result = state.ideastatica_results.get(jg_id, {})
        try:
            validate_calculation_check_coverage(result.get("check_summary", {}).get("summary", []))
        except idea_agent.IdeaStaticaJointError as exc:
            failures.append(f"Connection check coverage for '{jg_id}' is incomplete: {exc}")
        if result.get("passed") is not True or result.get("simulation") or not grade_joint_check_summary(result.get("check_summary", {}), state.run_policy).passed:
            failures.append(f"Connection source evidence for '{jg_id}' is missing, simulated, or failed.")
        try:
            verify_plate_material_evidence(result.get("plate_material_evidence"), plate_policy,
                result.get("iom_connection_data_file"))
            signature = __import__('agents.A5_idea_statica.A5A_build_json', fromlist=['_connection_signature'])._connection_signature(__import__('agents.A5_idea_statica.A5A_build_json', fromlist=['_connection_payload'])._connection_payload(state, jg_id), state.design_generation)
            if result.get("input_signature") != signature:
                failures.append(f"Connection evidence for '{jg_id}' belongs to different inputs or template.")
        except Exception as exc:
            failures.append(f"Connection inputs for '{jg_id}' cannot be verified: {exc}")

    if failures:
        if frame_failures:
            from orchestration.state import fail_workflow
            state.validation_report = {"passed": False, "failures": failures}
            return fail_workflow(
                state, agent_id,
                "Package Validation Failed: " + "; ".join(failures)
                + " Run the complete workflow again for current frame and STAAD evidence.",
            )
        # Loop C: Validation Recovery
        if state.loop_c_count < state.max_loop_c:
            state.loop_c_count += 1
            target = "a5_ideastatica_analyzer"
            state.validation_report = {
                "passed": False,
                "recovery_target": target,
                "failures": failures,
                "iteration": state.loop_c_count,
            }
            state.loop_c_history.append({
                "iteration": state.loop_c_count,
                "target": target,
                "failures": failures,
            })
            msg = f"Loop C ({state.loop_c_count}/{state.max_loop_c}): Missing/failed package evidence -> Routing recovery to {target}"
            state.agent_statuses[agent_id] = {"status": "REVISING", "message": msg, "target": target}
            state.logs.append(AgentLogEntry(
                agent_id=agent_id,
                agent_name="Package Validator",
                status="REVISING",
                message=msg,
                timestamp=datetime.now().astimezone().isoformat(),
                loop_info=f"Loop C #{state.loop_c_count}",
            ))
            return state

        err_msg = "Package Validation Failed: " + "; ".join(failures)
        state.status = "FAILED"
        state.error = err_msg
        state.agent_statuses[agent_id] = {"status": "FAILED", "message": err_msg}
        state.logs.append(AgentLogEntry(
            agent_id=agent_id,
            agent_name="Package Validator",
            status="FAILED",
            message=err_msg,
            timestamp=datetime.now().astimezone().isoformat()
        ))
        return state

    # Build validated joint_groups payload for Tekla
    validated_joint_groups = {}
    for jg_id in scope["idea_required_groups"]:
        jg_data = joint_groups[jg_id]
        op_params = state.ideastatica_results[jg_id]["check_summary"].get("operation_parameters", [])
        # Tekla needs evaluated values, not IDEA formulas such as GetBoltDiameter(...).
        op_params = [{**p, "expression": str(p.get("value") if p.get("value") is not None else p.get("expression", "")).strip("\"'")} for p in op_params]
        validated_joint_groups[jg_id] = {
            "joints": jg_data.get("joints", []),
            "operation_parameters": op_params
        }

    # Compute package integrity hash
    pkg_str = json.dumps({
        "run_policy": state.run_policy,
        "grid": state.grid_config,
        "col_profile": state.active_column_profile,
        "beam_profile": state.active_beam_profile,
        "frame_signature": frame_signature(state),
        "connection_scope": scope,
        "plate_material_policy": plate_policy,
        "joint_groups": validated_joint_groups
    }, sort_keys=True)
    pkg_hash = hashlib.sha256(pkg_str.encode("utf-8")).hexdigest()[:12]
    state.package_hash = pkg_hash

    state.validation_report = {
        "passed": True,
        "run_policy": state.run_policy,
        "package_hash": pkg_hash,
        "joint_groups_verified": len(validated_joint_groups),
        "connection_scope": scope,
        "plate_material_policy": plate_policy,
        "total_members": len(state.frame_candidate.get("members", [])),
        "validated_joint_groups_payload": validated_joint_groups
    }

    msg = (f"Package validated (Hash: {pkg_hash}): frame checks and {len(validated_joint_groups)} IDEA families verified; "
           f"{len(scope['standard_detail_groups'])} families use declared standard Tekla details.")
    state.agent_statuses[agent_id] = {"status": "PASSED", "message": msg, "hash": pkg_hash}
    state.logs.append(AgentLogEntry(
        agent_id=agent_id,
        agent_name="Package Validator",
        status="PASSED",
        message=msg,
        timestamp=datetime.now().astimezone().isoformat()
    ))

    return state

