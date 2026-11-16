"""Serialize native IDEA trials after the complete baseline releases the tools."""
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
ART=ROOT / "output_validation/backend_loop_test"
deadline=time.monotonic()+3700
while time.monotonic()<deadline:
    try:
        baseline=json.loads((ART / "baseline_report.json").read_text())
        if baseline.get("models_restored"):
            break
    except (OSError,ValueError):pass
    time.sleep(5)
else:raise RuntimeError("Baseline did not finish and restore its original models; subsequent tests not started.")
with (ROOT / "output_validation/live-connection-loop-console.log").open("w") as stream:
    result=subprocess.run([sys.executable,"-u",str(ROOT / "scripts/live_connection_loop_probe.py")],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
print("Connection probe process exit:",result.returncode,flush=True)
