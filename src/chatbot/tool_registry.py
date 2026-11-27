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
    from chatbot.chatbot import requests_complete_workflow
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


def execution_plan(action):
    """Known operation dependencies. These names refer to existing worker stages."""
    plans = {
        "configuration_only": [],
        "validate_selected_connections": ["A5:selected", "A5c:package"],
        "publish_selected_connections": ["A5:selected", "A5c:package", "A6:selected", "A7:report"],
        "run_complete_workflow": ["A1", "A2", "A3", "A4", "A5", "A5c:package", "A6", "A7:manual_approval"],
        "preview_structure": ["A1", "A3:unverified_preview"],
        "generate_structure": ["A1", "A2", "A3"],
        "analyze_frame": ["A1", "A2", "A3", "A4"],
        "design_connections": ["A1", "A2", "A3", "A4", "A5"],
        "validate_package": ["A1", "A2", "A3", "A4", "A5", "A5c:package"],
        "publish_to_tekla": ["A1", "A2", "A3", "A4", "A5", "A5c:package", "A6", "A7:manual_approval"],
    }
    if action not in plans:
        raise ValueError("Unsupported execution plan.")
    return plans[action][:]


def configuration_action(message):
    """Choose only an explicitly requested follow-on operation for input edits."""
    import re
    from chatbot.chatbot import requests_complete_workflow
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
    from config.settings import ROOT
    paths = {"workflow": "src/orchestration/workflow.py", "inputs": "src/agents/A1_requirements/validate_input.py",
             "staad": "src/agents/A4_staad/A4D_llm_reasoning.py", "idea": "src/config/run_policy.py",
             "tekla": "src/agents/A3_generate_structure/connection_scope.py", "run_policy": "src/config/run_policy.py"}
    if topic not in paths:
        raise ValueError("Choose a project reference topic: " + ", ".join(paths))
    return {"source": paths[topic], "text": (ROOT / paths[topic]).read_text(encoding="utf-8")[:12000]}
