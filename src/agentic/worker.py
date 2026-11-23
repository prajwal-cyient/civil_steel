"""A disposable process owns one engineering run and its COM apartment."""

import argparse
import asyncio
import json
import sys

from agentic.config import load_environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-publication", action="store_true")
    parser.add_argument("--chat-action", choices=("preview_structure", "generate_structure", "analyze_frame",
                                                 "design_connections", "validate_package", "publish_to_tekla",
                                                 "update_selected_connections", "publish_selected_connections"))
    args = parser.parse_args()
    load_environment()
    # OpenSTAAD explicitly requires STA. Import it before pythonnet can select
    # a different COM apartment on this thread.
    sys.coinit_flags = 2
    from agentic.state import WorkflowState, fail_workflow
    from agentic.store import WORKFLOW_STORE
    state = WORKFLOW_STORE.get_state(args.run_id)
    if state is None:
        raise RuntimeError("Worker input was not persisted.")
    try:
        if sys.platform == "win32" and args.chat_action != "preview_structure":
            import common.staad_common  # noqa: F401
        from agentic.orchestrator import run_agentic_workflow
        if args.chat_action:
            from agentic.chat_actions import run_chat_action
            result = asyncio.run(run_chat_action(state, args.chat_action))
        elif args.resume_publication:
            from agentic.publication_recovery import run_publication_retry
            result = asyncio.run(run_publication_retry(state))
        else:
            result = asyncio.run(run_agentic_workflow(state.raw_input, run_id=args.run_id))
    except Exception as exc:
        result = fail_workflow(state, state.current_agent, f"Worker initialization failed: {exc}")
    WORKFLOW_STORE.save_state(result)


if __name__ == "__main__":
    main()
