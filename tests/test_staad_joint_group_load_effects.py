import ast
from pathlib import Path
import unittest


source_path = Path(__file__).resolve().parents[1] / "src" / "agents" / "A4_staad" / "staad_openstaad.py"
source_tree = ast.parse(source_path.read_text(encoding="utf-8"))
helpers = [
    node for node in source_tree.body
    if isinstance(node, ast.FunctionDef) and node.name in {
        "find_joint_group_governing_forces", "joint_load_effect", "build_joint_group_load_effects",
    }
]
constants = [
    node for node in source_tree.body
    if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
        isinstance(target, ast.Name) and target.id in {"JOINT_DESIGN_FORCE_COMPONENTS", "IDEA_STATICA_FORCE_NAMES"}
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
    )
]
namespace = {}
exec("from typing import Any, Dict, List, Tuple", namespace)
helper_module = ast.fix_missing_locations(ast.Module(body=[*constants, *helpers], type_ignores=[]))
exec(compile(helper_module, str(source_path), "exec"), namespace)
build_joint_group_load_effects = namespace["build_joint_group_load_effects"]


def f(fx, fy=0.0, fz=0.0):
    return [fx, fy, fz, 0.0, 0.0, 0.0]


# Two joints (nodes 10 and 20) of the same group. Each joint has a column ending at it, a
# longitudinal beam ending at it, one starting at it, and a longitudinal bracing ending at it.
JOINT_GROUP = {
    "member_count": 4,
    "joints": [
        {"node": 10, "members": [
            {"beam": 1, "member_type": "column", "end": 1},
            {"beam": 2, "member_type": "beam_longitudinal", "end": 1},
            {"beam": 3, "member_type": "beam_longitudinal", "end": 0},
            {"beam": 4, "member_type": "bracing_longitudinal", "end": 1},
        ]},
        {"node": 20, "members": [
            {"beam": 5, "member_type": "column", "end": 1},
            {"beam": 3, "member_type": "beam_longitudinal", "end": 1},
            {"beam": 6, "member_type": "beam_longitudinal", "end": 0},
            {"beam": 7, "member_type": "bracing_longitudinal", "end": 1},
        ]},
    ],
}


def forces_for(overrides):
    forces = {beam: [f(0.0), f(0.0)] for beam in range(1, 8)}
    for (beam, end), values in overrides.items():
        forces[beam][end] = values
    return forces


class JointGroupLoadEffectTests(unittest.TestCase):
    def test_distinct_braces_receive_distinct_template_member_names(self):
        joint = {"members": [{"beam": 1, "member_type": "bracing_longitudinal", "end": 0},
                             {"beam": 2, "member_type": "bracing_longitudinal", "end": 1}]}
        forces = {1: [[1, 0, 0, 0, 0, 0], [0] * 6], 2: [[0] * 6, [2, 0, 0, 0, 0, 0]]}
        effect = namespace["joint_load_effect"](joint, forces, "ULS")
        self.assertEqual([m["member_name"] for m in effect["member_loadings"]], ["bracing_longitudinal", "bracing_longitudinal_1"])

    def test_governing_forces_use_joint_end_only_and_dedupe_load_cases(self):
        forces_by_load_case = {
            # Far end of beam 2 (end 0, not in the group) is huge and must be ignored.
            100: forces_for({(2, 0): f(999.0), (2, 1): f(5.0, 1.0, -1.0), (4, 1): f(-3.0)}),
            101: forces_for({(6, 0): f(-7.0, 2.0, 4.0), (7, 1): f(8.0)}),
            102: forces_for({(3, 1): f(1.0, -6.0, 0.5)}),
        }
        titles = {100: "A", 101: "B", 102: "C"}
        governing, load_effects = build_joint_group_load_effects(JOINT_GROUP, forces_by_load_case, [100, 101, 102], titles)

        beams = governing["beam_longitudinal"]
        self.assertEqual((beams["max_Fx"]["value"], beams["max_Fx"]["load_case"], beams["max_Fx"]["node"]), (5.0, 100, 10))
        self.assertEqual((beams["min_Fx"]["value"], beams["min_Fx"]["load_case"], beams["min_Fx"]["node"]), (-7.0, 101, 20))
        self.assertEqual((beams["min_Fy"]["value"], beams["min_Fy"]["load_case"], beams["min_Fy"]["node"]), (-6.0, 102, 20))
        self.assertEqual((beams["max_Fz"]["value"], beams["max_Fz"]["beam"], beams["max_Fz"]["end"]), (4.0, 6, 0))
        bracings = governing["bracing_longitudinal"]
        self.assertEqual((bracings["max_Fx"]["value"], bracings["max_Fx"]["load_case"]), (8.0, 101))
        self.assertEqual((bracings["min_Fx"]["value"], bracings["min_Fx"]["load_case"]), (-3.0, 100))
        self.assertIn("column", governing)
        self.assertNotIn("beam_transverse", governing)

        keys = [(le["load_case"], le["node"]) for le in load_effects]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn((100, 10), keys)
        self.assertIn((101, 20), keys)
        self.assertIn((102, 20), keys)

    def test_load_effect_contains_all_joint_members_at_joint_end(self):
        forces_by_load_case = {100: forces_for({(1, 1): [-19.0, 0.1, -0.005, 0.006, 0.0, 0.0],
                                                 (2, 1): f(5.0), (3, 0): f(2.0, -1.8), (4, 1): f(-3.0)})}
        _, load_effects = build_joint_group_load_effects(JOINT_GROUP, forces_by_load_case, [100], {100: "LC"})
        effect = next(le for le in load_effects if le["node"] == 10)
        self.assertEqual(effect["name"], "LC100 node 10 - LC")
        self.assertEqual(effect["member_loadings"], [
            {"member_name": "column", "n": -19.0, "vy": 0.1, "vz": -0.005, "mx": 0.006, "my": 0.0, "mz": 0.0, "position": "End"},
            {"member_name": "beam_longitudinal", "n": 5.0, "vy": 0.0, "vz": 0.0, "mx": 0.0, "my": 0.0, "mz": 0.0, "position": "End"},
            {"member_name": "beam_longitudinal", "n": 2.0, "vy": -1.8, "vz": 0.0, "mx": 0.0, "my": 0.0, "mz": 0.0, "position": "Begin"},
            {"member_name": "bracing_longitudinal", "n": -3.0, "vy": 0.0, "vz": 0.0, "mx": 0.0, "my": 0.0, "mz": 0.0, "position": "End"},
        ])

    def test_column_only_support_receives_its_real_governing_loads(self):
        group = {"member_count": 1, "joints": [{"node": 10, "members": [{"beam": 1, "member_type": "column", "end": 0}]}]}
        forces = {100: forces_for({(1, 0): [-25, 2, 3, 4, 5, 6]}),
                  101: forces_for({(1, 0): [5, 1, 2, 3, 4, 50]})}
        governing, load_effects = build_joint_group_load_effects(group, forces, [100, 101], {})
        self.assertEqual(governing["column"]["min_Fx"]["value"], -25)
        self.assertEqual(governing["column"]["max_Mz"]["load_case"], 101)
        self.assertEqual({effect["load_case"] for effect in load_effects}, {100, 101})
        compression = next(effect for effect in load_effects if effect["load_case"] == 100)
        self.assertEqual(compression["member_loadings"][0]["n"], -25)

    def test_moment_governing_case_is_not_lost_to_force_only_selection(self):
        group = {"joints": [{"node": 10, "members": [{"beam": 2, "member_type": "beam_longitudinal", "end": 1}]}]}
        forces = {100: forces_for({(2, 1): [20, 10, 10, 0, 0, 0]}),
                  101: forces_for({(2, 1): [10, 5, 5, 0, 0, 90]}),
                  102: forces_for({(2, 1): [0, 0, 0, 0, 0, 0]})}
        governing, effects = build_joint_group_load_effects(group, forces, [100, 101, 102], {})
        self.assertEqual(governing["beam_longitudinal"]["max_Mz"]["load_case"], 101)
        self.assertIn(101, {effect["load_case"] for effect in effects})


if __name__ == "__main__":
    unittest.main()
