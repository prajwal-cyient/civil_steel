import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agents.A6_tekla.publication_recovery import prepare_publication_retry, run_publication_retry
from orchestration.state import WorkflowState


class PublicationRecoveryTests(unittest.TestCase):
    def test_retry_isolated_from_source_and_clears_publication_acceptance(self):
        original = WorkflowState(run_id="source", status="FAILED", current_agent="A7", error="Typed attribute rejected",
                                 validation_report={"passed": True}, tekla_results={"created": 1}, gate3_approved=True)
        retry = prepare_publication_retry(original, "retry")
        self.assertEqual(original.status, "FAILED")
        self.assertEqual(original.tekla_results["created"], 1)
        self.assertEqual(retry.readiness["publication_resume_source"], "source")
        self.assertEqual(retry.tekla_results, {})
        self.assertFalse(retry.gate3_approved)

    def test_analysis_or_unvalidated_failures_cannot_skip_to_publication(self):
        for agent, validation in (("A4", True), ("A5", True), ("A7", False)):
            source = WorkflowState(run_id="source", status="FAILED", current_agent=agent,
                                   validation_report={"passed": validation})
            with self.assertRaises(ValueError):
                prepare_publication_retry(source, "retry")

    def test_changed_evidence_stops_before_any_tekla_mutation(self):
        from orchestration import workflow as orchestrator
        state = WorkflowState(run_id="retry", status="RUNNING", readiness={"publication_resume_source": "source"})
        def invalid(candidate):
            candidate.validation_report = {"passed": False}
            return candidate
        with patch.object(orchestrator, "node_a6", side_effect=invalid), \
             patch.object(orchestrator, "node_a7") as publish:
            result = asyncio.run(run_publication_retry(state))
        self.assertEqual(result.status, "FAILED")
        self.assertIn("revalidation", result.error)
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
