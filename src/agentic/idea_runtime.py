"""Read-only guard against overlapping legacy native IDEA calculations."""
import sys
import time


def _active_solver_ids(service):
    active = []
    for solver in service.ExecQuery("SELECT ProcessId,ParentProcessId FROM Win32_Process WHERE Name='k2fem64.exe'"):
        parent_id = int(solver.ParentProcessId)
        parents = service.ExecQuery(f"SELECT Name FROM Win32_Process WHERE ProcessId={parent_id}")
        if any(str(parent.Name).lower() in ("ideastatica.connectionrestapi.exe", "ideaconnection.exe") for parent in parents):
            active.append(int(solver.ProcessId))
    return active


def ensure_idea_solver_idle(timeout_seconds=10.0):
    if sys.platform != "win32":
        return
    import pythoncom
    import win32com.client
    initialized = False
    service = None
    try:
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialized = True
        except pythoncom.com_error as exc:
            if exc.hresult != -2147417850:  # Existing MTA can also query WMI.
                raise
        service = win32com.client.GetObject("winmgmts:")
        deadline = time.monotonic() + max(0, timeout_seconds)
        while True:
            active = _active_solver_ids(service)
            if not active or time.monotonic() >= deadline:
                break
            # Allow an already completed request's native process to exit.
            time.sleep(.2)
    finally:
        service = None  # Release WMI proxies before balancing COM initialization.
        if initialized:
            pythoncom.CoUninitialize()
    if active:
        raise RuntimeError(f"IDEA native solver is still active (processes {active}); no new project or calculation was opened. "
                           "A legacy HTTP timeout does not cancel its native analysis.")
