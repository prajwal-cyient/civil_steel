"""Opt-in REAL model routing checks with mocked engineering dispatch; never runs FEA."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentic.config import load_environment
from agentic.chat import ChatRuntime
from agentic.chat_store import ChatStore


async def validate(output):
    records = []
    cases = [
        ("Change only my longitudinal bay count to 10. Do not generate or analyze anything.", "update_project_inputs"),
        ("Show an unverified geometry preview of my current frame without invoking any desktop engineering applications.", "preview_structure"),
        ("Run STAAD frame analysis for my already validated selected structure. Do not design connections or publish.", "analyze_frame"),
        ("Calculate the connection designs in IDEA for the selected frame that has current passing STAAD evidence. Do not publish.", "design_connections"),
    ]
    with tempfile.TemporaryDirectory() as directory:
        store = ChatStore(Path(directory) / "chat.db")
        for message, expected in cases:
            session = store.session()
            session.update(project_input={"grid": {"n_long_bays": 8, "n_trans_bays": 5}}, run_id="run_mock_fixture")
            store.save_session(session)
            turn = store.new_turn(session["id"], message)
            async def fake_engineering(selected, action, allow_native):
                return {"status": "MOCK_ONLY", "action": action, "notice": "Routing fixture only: no engineering software ran."}
            async def fixture_status(run_id: str = ""):
                return {"status": "READY", "run_id": "run_mock_fixture", "agents": {"A3": {"status": "PASSED"}}, "frame_grade": {"passed": True}, "notice": "MOCK_ONLY input evidence"}
            runtime = ChatRuntime(store)
            original = runtime.tools
            def tools(*args):
                from langchain_core.tools import StructuredTool
                registered = original(*args)
                for i, tool in enumerate(registered):
                    if tool.name == "get_run_status":
                        registered[i] = StructuredTool.from_function(coroutine=fixture_status, name=tool.name, description=tool.description)
                return registered
            with patch.object(runtime, "engineering", side_effect=fake_engineering), patch.object(runtime, "tools", side_effect=tools):
                await runtime.respond(turn, allow_native=True)
            names = [e["tool"] for e in turn["events"] if e["type"] == "tool_started"]
            records.append({"kind": "REAL LLM; MOCKED engineering, not live FEA", "message": message,
                            "expected_tool": expected, "selected_tools": names,
                            "passed": expected in names and turn["status"] == "COMPLETED",
                            "status": turn["status"], "error": turn["error"], "events": turn["events"], "assistant": turn["assistant"]})
            print(json.dumps({k: v for k, v in records[-1].items() if k not in ("events", "assistant")}), flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return all(r["passed"] for r in records)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-model", action="store_true", help="Authorize requests to the configured model; engineering remains mocked.")
    parser.add_argument("--output", type=Path, default=Path("output_chat_validation/model-routing.json"))
    args = parser.parse_args()
    if not args.live_model:
        parser.error("Specify --live-model to make actual model requests.")
    load_environment()
    raise SystemExit(0 if asyncio.run(validate(args.output)) else 1)
