"""Approved capabilities and deterministic dependency plans, never model API names."""
READ_TOOLS = {"get_project_context", "get_run_status", "list_runs", "read_attachment",
              "get_joint_parameters", "get_project_reference"}
EDIT_TOOLS = {"update_project_inputs", "propose_joint_changes", "set_next_run_policy"}
ENGINEERING_TOOLS = {"preview_structure", "generate_structure", "analyze_frame", "design_connections",
                     "validate_package", "publish_to_tekla", "run_complete_workflow"}


def allowed_tools(intent):
    if intent == "READ":
        return READ_TOOLS
    if intent == "EDIT":
        return READ_TOOLS | EDIT_TOOLS
    return READ_TOOLS | EDIT_TOOLS | ENGINEERING_TOOLS


def request_capabilities(message, intent):
    from agentic.chat import requests_complete_workflow
    tools = allowed_tools(intent)
    if intent == 'READ':
        return tools
    if requests_complete_workflow(message):
        if intent == 'EDIT' and not policy_only_request(message):
            return READ_TOOLS | EDIT_TOOLS
        return READ_TOOLS | EDIT_TOOLS | {'run_complete_workflow'}
    if intent == 'EDIT':
        return tools
    literal = message.strip().lower()
    action = literal if literal in ENGINEERING_TOOLS else configuration_action(message.replace('_', ' '))
    return READ_TOOLS | EDIT_TOOLS | ({action} if action in ENGINEERING_TOOLS else set())


def policy_only_request(message):
    import re
    text = message.lower()
    policy = re.search(r'\b(rules?|thresholds?|policy|drift|utilization|member ratio)\b', text)
    geometry = re.search(r'\b(longitudinal|transverse|mirror|spacing|height|profile|material)\b|\b(?:grd|ep|cpl)[_ ]', text)
    return bool(policy and not geometry)


def input_preview_required(message):
    import re
    text = message.lower()
    return bool(re.search(r'\b(longitudinal|transverse|mirror|spacing|height|profile|material)\b|\b(?:grd|ep|cpl)[_ ]', text))


def requested_dimensions(message):
    import re
    values = {(int(a),int(b)) for a,b in re.findall(r'\b(\d+)\s*[x×]\s*(\d+)\b',message.lower())}
    if len(values)>1:
        raise ValueError('Several frame dimensions were supplied. Clarify the requested target before execution.')
    return next(iter(values),None)


def execution_plan(action, layout_version=2):
    """Known operation dependencies. These names refer to existing worker stages."""
    plans = {
        "configuration_only": [],
        "validate_selected_connections": ["A5:selected", "A6:package"],
        "publish_selected_connections": ["A5:selected", "A6:package", "A7:selected", "A8:report"],
        "run_complete_workflow": ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8:manual_approval"],
        "preview_structure": ["A1", "A3:unverified_preview"],
        "generate_structure": ["A1", "A2", "A3"],
        "analyze_frame": ["A1", "A2", "A3", "A4"],
        "design_connections": ["A1", "A2", "A3", "A4", "A5"],
        "validate_package": ["A1", "A2", "A3", "A4", "A5", "A6"],
        "publish_to_tekla": ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8:manual_approval"],
    }
    if action not in plans:
        raise ValueError("Unsupported execution plan.")
    if layout_version == 1:
        return plans[action][:]
    if layout_version != 2:
        raise ValueError("Unsupported workflow layout version.")
    mapping = {"A6": "A5c", "A7": "A6", "A8": "A7"}
    return [mapping.get(step.partition(":")[0], step.partition(":")[0])
            + (":" + step.partition(":")[2] if ":" in step else "")
            for step in plans[action]]


def configuration_action(message):
    """Choose only an explicitly requested follow-on operation for input edits."""
    import re
    from agentic.chat import requests_complete_workflow
    text = message.lower()
    # Negative clauses describe exclusions, not the requested operation.
    text = re.sub(r"\b(?:do not|don't|dont|never|without|skip|except)\b[^,;.\n]*", "", text)
    if requests_complete_workflow(message):
        return "run_complete_workflow"
    if not re.search(r"\b(run|analy[sz]e|calculate|preview|generate|publish|validate|design|send)\b", text):
        return "configuration_only"
    for pattern, action in [(r"tekla|publish", "publish_to_tekla"), (r"validate.*package", "validate_package"),
                            (r"idea|design.*connection", "design_connections"), (r"staad|analy[sz]e", "analyze_frame"),
                            (r"preview", "preview_structure"), (r"generate", "generate_structure")]:
        if re.search(pattern, text):
            return action
    return "configuration_only"


def publication_requested(message):
    import re
    text = re.sub(r"\b(?:do not|don't|dont|never|without|skip|except)\b[^,;.\n]*", "", message.lower())
    return bool(re.search(r"\b(?:publish|send|update|modify)\b.{0,50}\btekla\b|\btekla\b.{0,30}\b(?:update|publish)\b", text))


def configuration_tools(action):
    if action in ("run_complete_workflow", "preview_structure", "generate_structure"):
        return [action]
    paths = {"configuration_only": [], "analyze_frame": ["generate_structure", "analyze_frame"],
             "design_connections": ["generate_structure", "analyze_frame", "design_connections"],
             "validate_package": ["generate_structure", "analyze_frame", "design_connections", "validate_package"],
             "publish_to_tekla": ["generate_structure", "analyze_frame", "design_connections", "publish_to_tekla"]}
    if action not in paths:
        raise ValueError("Unsupported project execution action.")
    return paths[action][:]


def project_reference(topic):
    """Read curated implementation references; no caller-controlled filesystem path."""
    from agentic.config import ROOT
    paths = {"workflow": "src/agentic/orchestrator.py", "inputs": "src/agentic/agents/a1_requirements_agent.py",
             "staad": "src/agentic/agents/a4_staad_analyzer_agent.py", "idea": "src/agentic/run_policy.py",
             "tekla": "src/agentic/reference_procedure.py", "run_policy": "src/agentic/run_policy.py"}
    if topic not in paths:
        raise ValueError("Choose a project reference topic: " + ", ".join(paths))
    return {"source": paths[topic], "text": (ROOT / paths[topic]).read_text(encoding="utf-8")[:12000]}
