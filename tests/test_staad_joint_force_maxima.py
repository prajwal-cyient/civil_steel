import ast
from pathlib import Path
import unittest


source_path = Path(__file__).resolve().parents[1] / "staad_integration.py"
source_tree = ast.parse(source_path.read_text(encoding="utf-8"))
helper = next(
    node for node in source_tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "_joint_force_maxima"
)
namespace = {"start_end": 0, "final_end": 1}
exec(compile(ast.Module(body=[helper], type_ignores=[]), str(source_path), "exec"), namespace)
joint_force_maxima = namespace["_joint_force_maxima"]


class JointForceMaximaTests(unittest.TestCase):
    def test_grouping_uses_correct_member_end_and_absolute_components(self):
        connectivity = {
            10: [(1, 0)],
            20: [(1, 1), (2, 0)],
            30: [(2, 1)],
        }
        start_forces = [
            {"beam": 1, "forces": (-10, 2, -3, 4, -5, 6)},
            {"beam": 2, "forces": (11, -12, 13, -14, 15, -16)},
        ]
        end_forces = [
            {"beam": 1, "forces": (-20, 3, -4, 5, -6, 7)},
            {"beam": 2, "forces": (1, -22, 2, -3, 4, -5)},
        ]
        groups = joint_force_maxima(connectivity, start_forces, end_forces)
        self.assertEqual(groups["1"]["member_count"], 1)
        self.assertEqual(groups["1"]["joints"], [
            {"node": 10, "members": [{
                "beam": 1, "end": "start",
                "forces": {"Fx": -10, "Fy": 2, "Fz": -3, "Mx": 4, "My": -5, "Mz": 6},
            }]},
            {"node": 30, "members": [{
                "beam": 2, "end": "end",
                "forces": {"Fx": 1, "Fy": -22, "Fz": 2, "Mx": -3, "My": 4, "Mz": -5},
            }]},
        ])
        self.assertEqual(groups["1"]["max_abs_forces"], {
            "Fx": 10, "Fy": 22, "Fz": 3, "Mx": 4, "My": 5, "Mz": 6,
        })
        self.assertEqual(groups["2"]["joints"], [
            {"node": 20, "members": [
                {
                    "beam": 1, "end": "end",
                    "forces": {"Fx": -20, "Fy": 3, "Fz": -4, "Mx": 5, "My": -6, "Mz": 7},
                },
                {
                    "beam": 2, "end": "start",
                    "forces": {"Fx": 11, "Fy": -12, "Fz": 13, "Mx": -14, "My": 15, "Mz": -16},
                },
            ]},
        ])
        self.assertEqual(groups["2"]["max_abs_forces"], {
            "Fx": 20, "Fy": 12, "Fz": 13, "Mx": 14, "My": 15, "Mz": 16,
        })
        self.assertEqual(groups["2"]["max_force_sources"]["Fx"], {
            "node": 20, "beam": 1, "end": "end", "signed_value": -20,
            "forces": {"Fx": -20, "Fy": 3, "Fz": -4, "Mx": 5, "My": -6, "Mz": 7},
        })
        self.assertEqual(groups["1"]["max_force_sources"]["Fy"]["beam"], 2)
        self.assertEqual(groups["1"]["max_force_sources"]["Fy"]["end"], "end")

    def test_load_cases_do_not_share_maxima(self):
        connectivity = {10: [(1, 0)]}
        first = joint_force_maxima(
            connectivity, [{"beam": 1, "forces": (-100, 0, 0, 0, 0, 0)}], []
        )
        second = joint_force_maxima(
            connectivity, [{"beam": 1, "forces": (2, 0, 0, 0, 0, 0)}], []
        )
        self.assertEqual(first["1"]["max_abs_forces"]["Fx"], 100)
        self.assertEqual(second["1"]["max_abs_forces"]["Fx"], 2)
        self.assertEqual(second["1"]["max_force_sources"]["Fy"]["signed_value"], 0)

    def test_empty_connectivity(self):
        self.assertEqual(joint_force_maxima({}, [], []), {})


if __name__ == "__main__":
    unittest.main()