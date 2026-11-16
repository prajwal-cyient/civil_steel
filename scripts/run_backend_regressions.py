"""Capture per-case real unittest execution; mocked cases are labeled as such."""
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output_validation" / "backend_loop_test"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "tests"))


class DetailedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def record(self, test, status, error=None):
        self.records.append({"test": test.id(), "status": status, "seconds": time.monotonic()-self.started,
                             "kind": "CONTROLLED REGRESSION - adapters mocked; not live FEA", "error": error})

    def addSuccess(self, test):
        self.record(test, "PASS")
        super().addSuccess(test)

    def addFailure(self, test, error):
        self.record(test, "FAIL", self._exc_info_to_string(error, test))
        super().addFailure(test, error)

    def addError(self, test, error):
        self.record(test, "ERROR", self._exc_info_to_string(error, test))
        super().addError(test, error)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    with (OUT / "regression-console.txt").open("w", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=DetailedResult).run(suite)
    (OUT / "regression-cases.json").write_text(json.dumps(result.records, indent=2))
    print(json.dumps({"executed":result.testsRun,"failures":len(result.failures),"errors":len(result.errors)}))
    raise SystemExit(not result.wasSuccessful())
