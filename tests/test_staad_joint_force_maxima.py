"""Regression checks for the current per-joint member-end force extractor."""
import ast
from pathlib import Path
import unittest
from typing import Any, Dict, List, Tuple

source_path = Path(__file__).resolve().parents[1] / "src/radiator_support/staad_integration.py"
tree = ast.parse(source_path.read_text(encoding="utf-8"))
helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "find_connected_members_forces")
namespace = dict(Any=Any, Dict=Dict, List=List, Tuple=Tuple)
exec(compile(ast.Module(body=[helper], type_ignores=[]), str(source_path), "exec"), namespace)
extract = namespace["find_connected_members_forces"]


class JointEndForceTests(unittest.TestCase):
    def test_uses_the_connected_member_end_and_preserves_sign(self):
        forces = {1: [[-10, 2, -3, 4, -5, 6], [-20, 3, -4, 5, -6, 7]],
                  2: [[11, -12, 13, -14, 15, -16], [1, -22, 2, -3, 4, -5]]}
        result = extract(20, {20: [(1, 1), (2, 0)]}, forces)
        self.assertEqual(result, [{"beam": 1, "end": 1, "forces": forces[1][1]},
                                  {"beam": 2, "end": 0, "forces": forces[2][0]}])

    def test_separate_cases_do_not_share_results(self):
        connectivity = {10: [(1, 0)]}
        first = extract(10, connectivity, {1: [[-100, 0, 0, 0, 0, 0], [0] * 6]})
        second = extract(10, connectivity, {1: [[2, 0, 0, 0, 0, 0], [0] * 6]})
        self.assertEqual(first[0]["forces"][0], -100)
        self.assertEqual(second[0]["forces"][0], 2)

    def test_missing_forces_raise_instead_of_creating_zero_loads(self):
        with self.assertRaises(KeyError):
            extract(10, {10: [(1, 0)]}, {})
