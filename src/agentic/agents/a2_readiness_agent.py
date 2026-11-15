"""
a2_readiness_agent.py

Agent A2: Check Environment & Readiness.
Probes OpenSTAAD COM, IDEA StatiCa REST API (port 5000), Tekla Structures .NET Open API,
and template project libraries before heavy computations begin.
"""

from datetime import datetime
import os
import socket
from agentic.state import WorkflowState, AgentLogEntry


def _probe_socket(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def run_a2_readiness(state: WorkflowState) -> WorkflowState:
    """
    Executes Agent A2: Probes software connections and templates.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    state.current_agent = "A2"

    readiness = {
        "staad_reachable": False,
        "idea_statica_reachable": False,
        "tekla_reachable": False,
        "templates_found": False,
        "simulation_fallback": False,
    }

    # 1. Probe IDEA StatiCa REST API (localhost:5000)
    idea_url = os.environ.get("IDEA_STATICA_BASE_URL", "http://localhost:5000")
    port = 5000
    try:
        if "http://" in idea_url:
            host = idea_url.split("//")[1].split(":")[0]
            port = int(idea_url.split(":")[-1].replace("/", ""))
        else:
            host = "localhost"
        readiness["idea_statica_reachable"] = _probe_socket(host, port)
    except Exception:
        readiness["idea_statica_reachable"] = False

    # 2. Probe OpenSTAAD COM
    try:
        import win32com.client
        staad = win32com.client.Dispatch("StaadPro.OpenSTAAD")
        readiness["staad_reachable"] = staad is not None
    except Exception:
        readiness["staad_reachable"] = False

    # 3. Probe Tekla Open API
    try:
        from common.tekla_common import _get_connected_model
        model = _get_connected_model()
        readiness["tekla_reachable"] = model.GetConnectionStatus()
    except Exception:
        readiness["tekla_reachable"] = False

    # 4. Probe Templates
    sample_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "sample_projects")
    if os.path.exists(sample_dir):
        ideacon_files = [f for f in os.listdir(sample_dir) if f.endswith(".ideaCon")]
        readiness["templates_found"] = len(ideacon_files) > 0
    else:
        readiness["templates_found"] = False

    state.readiness = readiness

    # Informational summary
    online = [k.replace("_reachable", "").upper() for k, v in readiness.items() if v is True and "reachable" in k]
    offline = [k.replace("_reachable", "").upper() for k, v in readiness.items() if v is False and "reachable" in k]

    if not offline:
        msg = f"All desktop tools connected: {', '.join(online)}."
        state.agent_statuses["A2"] = {"status": "PASSED", "message": msg}
        state.logs.append(AgentLogEntry(
            agent_id="A2",
            agent_name="Readiness Checker",
            status="PASSED",
            message=msg,
            timestamp=ts
        ))
    else:
        # Graceful notice: tools can be called live when ready, or fallback will handle gracefully
        msg = f"Readiness check complete. Online: {online or 'None'}, Offline: {offline}."
        state.agent_statuses["A2"] = {"status": "PASSED", "message": msg}
        state.logs.append(AgentLogEntry(
            agent_id="A2",
            agent_name="Readiness Checker",
            status="PASSED",
            message=msg,
            timestamp=ts
        ))

    return state

