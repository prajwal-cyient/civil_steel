"""Read-only checks that stop the workflow before unavailable desktop tools are used."""
from datetime import datetime
from importlib.metadata import version
import json
from pathlib import Path
from urllib.request import urlopen
import os

from agentic.config import settings
from agentic.state import WorkflowState, AgentLogEntry, fail_workflow


def run_a2_readiness(state: WorkflowState) -> WorkflowState:
    state.current_agent = "A2"
    ready = {**state.readiness, "staad_reachable": False, "idea_statica_reachable": False,
             "tekla_reachable": False, "templates_found": False}
    errors = []
    if not settings().live_enabled:
        return fail_workflow(state, "A2", "Live engineering is disabled in runtime configuration.")
    initialized = False
    try:
        import pythoncom
        pythoncom.CoInitialize()
        initialized = True
        from openstaadpy import os_analytical
        staad = os_analytical.connect()
        from common.staad_common import saved_model_path
        filename = str(saved_model_path(staad))
        if not filename or not Path(filename).is_file() or staad.IsPhysicalModel() or staad.IsAnalyzing():
            raise RuntimeError("Open a saved analytical STAAD model with no analysis running.")
        ready["staad_reachable"] = True
        ready["staad_model_path"] = filename
        ready["openstaad_sdk_version"] = version("openstaadpy")
    except Exception as exc:
        errors.append(f"STAAD: {exc}")
    finally:
        if initialized:
            pythoncom.CoUninitialize()
    try:
        from agentic.idea_runtime import ensure_idea_solver_idle
        ensure_idea_solver_idle()
        base = os.getenv("IDEA_STATICA_BASE_URL", "http://localhost:5000").rstrip("/")
        with urlopen(base + "/api/4/clients/idea-service-version", timeout=10) as response:
            service_version = json.load(response)
        sdk_version = version("ideastatica-connection-api")
        if service_version.split(".")[:2] != sdk_version.split(".")[:2]:
            raise RuntimeError(f"IDEA service {service_version} and SDK {sdk_version} have different product releases.")
        ready["idea_statica_reachable"] = True
        ready["idea_service_version"] = service_version
        ready["idea_sdk_version"] = sdk_version
    except Exception as exc:
        errors.append(f"IDEA: {exc}")
    try:
        from common.tekla_common import _get_connected_model
        model = _get_connected_model()
        if not Path(model.GetInfo().ModelPath).is_dir():
            raise RuntimeError("Open a saved Tekla model.")
        ready["tekla_reachable"] = True
        ready["tekla_model_path"] = str(Path(model.GetInfo().ModelPath).resolve())
    except Exception as exc:
        errors.append(f"Tekla: {exc}")
    ready["templates_found"] = any(settings().templates.glob("*.ideaCon"))
    if not ready["templates_found"]:
        errors.append("No IDEA connection templates are available.")
    state.readiness = ready
    if errors:
        return fail_workflow(state, "A2", "Readiness checks failed: " + "; ".join(errors))
    message = "Saved STAAD and Tekla models and IDEA API are ready."
    state.agent_statuses["A2"] = {"status": "PASSED", "message": message}
    state.logs.append(AgentLogEntry(agent_id="A2", agent_name="Readiness Checker",
        status="PASSED", message=message, timestamp=datetime.now().astimezone().isoformat()))
    return state
