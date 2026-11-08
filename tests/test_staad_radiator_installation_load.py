import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


# The helpers under test are split between the radiator-support module and
# the shared STAAD module it star-imports (common.staad_common), so pull the
# named functions/constants out of both.
src_dir = Path(__file__).resolve().parents[1] / "src"
source_path = src_dir / "radiator_support" / "staad_integration.py"
common_source_path = src_dir / "common" / "staad_common.py"
source_tree = ast.Module(
    body=[
        *ast.parse(common_source_path.read_text(encoding="utf-8")).body,
        *ast.parse(source_path.read_text(encoding="utf-8")).body,
    ],
    type_ignores=[],
)
helpers = [
    node for node in source_tree.body
    if isinstance(node, ast.FunctionDef) and node.name in {
        "_get_top_node_coordinates", "_get_boundary_nodes", "_get_corner_nodes",
        "_get_edge_nodes", "_get_inner_nodes", "_get_edge_members", "_node_key",
        "_apply_distributed_nodal_load", "_apply_radiator_installation_load",
        "_apply_distributed_seismic_joint_weight",
        "_apply_radiator_wind_nodal_loads",
        "_apply_load_configuration", "searchDictByKeyStart", "_create_directional_wind_cases",
    }
]
constants = [
    node for node in source_tree.body
    if isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id in {
            "LOAD_TYPE_MAP", "DIRECTION_TYPE_MAP", "SEISMIC_DIRECTION_TYPE_MAP",
            "WIND_DIRECTION_MAP", "SEISMIC_LOAD_CODE_TYPE_MAP", "SEISMIC_LOAD_TYPE",
            "SEISMIC_LOAD_CODE_TYPE_MAP_AddSeismicDefMemberWeight", "self_weight_direction_global_y",
        }
        for target in node.targets
    )
]
namespace = {}
future_annotations = ast.ImportFrom(
    module="__future__", names=[ast.alias(name="annotations")], level=0
)
helper_module = ast.fix_missing_locations(
    ast.Module(body=[future_annotations, *constants, *helpers], type_ignores=[])
)
exec(compile(helper_module, str(source_path), "exec"), namespace)
# staad_integration.py also calls some shared helpers qualified as
# staad_common.<name>; resolve those against the same extracted functions.
namespace["staad_common"] = SimpleNamespace(**namespace)
apply_radiator_installation_load = namespace["_apply_radiator_installation_load"]


def classify_nodes(node_cache, top_nodes):
    return tuple(namespace[helper_name](node_cache, top_nodes) for helper_name in (
        "_get_corner_nodes", "_get_edge_nodes", "_get_inner_nodes",
    ))


class RadiatorInstallationLoadTests(unittest.TestCase):
    def test_corners_edges_and_interior_preserve_total_weight(self):
        node_cache = {
            (100 + column * 2750, 5000, -200 + row * 3300): row * 3 + column + 1
            for row in range(3) for column in range(3)
        }
        node_cache[(100, 0, -200)] = 10
        node_cache[(1475, 2500, -200)] = 11
        node_cache[(100, 5000, -3500)] = 12
        load = Mock()

        node_groups = classify_nodes(node_cache, list(range(1, 10)))
        self.assertEqual(node_groups, ([1, 3, 7, 9], [2, 4, 6, 8], [5]))
        apply_radiator_installation_load(load, *node_groups, 4380)

        calls = load.AddNodalLoad.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual([call.args[0] for call in calls], [
            [1, 3, 7, 9], [2, 4, 6, 8], [5],
        ])
        base_value = 4380 * 9.81 / 1000 / 16
        for call, multiplier in zip(calls, [1, 2, 4]):
            self.assertEqual(call.args[1], 0.0)
            self.assertAlmostEqual(call.args[2], -multiplier * base_value)
            self.assertEqual(call.args[3:], (0.0, 0.0, 0.0, 0.0))
        total_force = sum(len(call.args[0]) * call.args[2] for call in calls)
        self.assertAlmostEqual(total_force, -4380 * 9.81 / 1000)

    def test_single_bay_has_only_corners(self):
        node_cache = {
            (0, 5000, 0): 4, (2750, 5000, 0): 3,
            (0, 5000, 3300): 2, (2750, 5000, 3300): 1,
        }
        load = Mock()

        apply_radiator_installation_load(load, *classify_nodes(node_cache, [1, 2, 3, 4]), 1000)

        load.AddNodalLoad.assert_called_once_with(
            [1, 2, 3, 4], 0.0, -9.81 / 4, 0.0, 0.0, 0.0, 0.0
        )

    def test_supplied_top_nodes_are_used_regardless_of_elevation(self):
        node_cache = {
            (0, 5000, 0): 1, (2750, 4900, 0): 2,
            (0, 4800, 3300): 3, (2750, 4700, 3300): 4,
            (5500, 6000, 6600): 5,
        }
        load = Mock()

        apply_radiator_installation_load(load, *classify_nodes(node_cache, [1, 2, 3, 4]), 1000)

        load.AddNodalLoad.assert_called_once_with(
            [1, 2, 3, 4], 0.0, -9.81 / 4, 0.0, 0.0, 0.0, 0.0
        )

    def test_no_top_nodes_rejects_load(self):
        load = Mock()
        with self.assertRaisesRegex(ValueError, "requires top nodes"):
            apply_radiator_installation_load(load, [], [], [], 1000)
        load.AddNodalLoad.assert_not_called()

    def test_edge_members_require_endpoints_on_same_boundary(self):
        node_cache = {
            (column * 2750, 5000, row * 3300): row * 3 + column + 1
            for row in range(3) for column in range(3)
        }
        members = [
            {"id": 1, "type": "beam", "p1": [0, 5000, 0], "p2": [2750, 5000, 0]},
            {"id": 2, "type": "beam", "p1": [0, 5000, 0], "p2": [0, 5000, 3300]},
            {"id": 3, "type": "beam", "p1": [0, 5000, 3300], "p2": [2750, 5000, 3300]},
            {"id": 4, "type": "beam", "p1": [0, 5000, 0], "p2": [5500, 5000, 6600]},
            {"id": 5, "type": "column", "p1": [0, 5000, 0], "p2": [0, 5000, 3300]},
        ]
        self.assertEqual(namespace["_get_edge_members"](members, node_cache, list(range(1, 10))), [1, 2])

    def test_configuration_separates_installation_and_edge_dead_loads(self):
        node_cache = {
            (column * 2000, 5000, row * 3000): row * 3 + column + 1
            for row in range(3) for column in range(3)
        }
        members = []
        for row in range(3):
            for column in range(2):
                members.append({
                    "id": len(members) + 1, "type": "beam", "name": f"beam-x-{row}-{column}",
                    "p1": [column * 2000, 5000, row * 3000],
                    "p2": [(column + 1) * 2000, 5000, row * 3000],
                })
        for column in range(3):
            for row in range(2):
                members.append({
                    "id": len(members) + 1, "type": "beam", "name": f"beam-y-{column}-{row}",
                    "p1": [column * 2000, 5000, row * 3000],
                    "p2": [column * 2000, 5000, (row + 1) * 3000],
                })
        support_nodes = []
        for column in (0, 2):
            for row in (0, 2):
                node_id = 100 + len(support_nodes)
                support_nodes.append(node_id)
                node_cache[(column * 2000, 0, row * 3000)] = node_id
                members.append({
                    "id": len(members) + 1, "type": "column", "name": f"column-{column}-{row}",
                    "p1": [column * 2000, 0, row * 3000],
                    "p2": [column * 2000, 5000, row * 3000],
                })
        load = Mock()
        load.CreateNewPrimaryLoadEx2.side_effect = lambda title, load_type, case_id: case_id
        load.CreateNewLoadCombination.side_effect = lambda title, case_id: case_id
        namespace["_apply_load_configuration"](
            load, node_cache,
            {"total_radiator_weight_including_liquid_kg": 4380,
             "total_live_weight_including_liquid_kg": 160,
             "live_load_intensity_kN_per_m2": 2.87},
            members, support_nodes, {"bay_long_mm": 3000, "bay_trans_mm": 2000,
                                     "n_long_bays": 2, "n_trans_bays": 2},
        )
        case_ids = {
            call.args[0]: call.args[2] for call in load.CreateNewPrimaryLoadEx2.call_args_list
        }
        load._load.AddSeismicDefSelfWeight.assert_called_once_with(1.1)
        load.AddSeismicDefSelfWeight.assert_not_called()
        load._load.AddSeismicDefMemberWeight.assert_not_called()
        seismic_uniform_calls = load.AddSeismicDefMemberWeight.call_args_list
        self.assertEqual([call.args for call in seismic_uniform_calls], [
            (26, namespace["SEISMIC_LOAD_TYPE"]["UNIFORM"], 1.5, 0.0, 0.0, [1, 2, 5, 6]),
            (26, namespace["SEISMIC_LOAD_TYPE"]["UNIFORM"], 1.0, 0.0, 0.0, [7, 8, 11, 12]),
        ])
        seismic_joint_calls = load.AddSeismicDefJointWeight.call_args_list
        self.assertEqual(len(seismic_joint_calls), 3)
        self.assertEqual([call.args[1] for call in seismic_joint_calls[:3]], [
            [1, 3, 7, 9], [2, 4, 6, 8], [5],
        ])
        for call, multiplier in zip(seismic_joint_calls[:3], [1, 2, 4]):
            self.assertAlmostEqual(call.args[0], multiplier * 4380 * 9.81 / 1000 / 16)
        self.assertAlmostEqual(
            sum(call.args[0] * len(call.args[1]) for call in seismic_joint_calls[:3]),
            4380 * 9.81 / 1000,
        )
        installation_creation = next(
            call for call in load.CreateNewPrimaryLoadEx2.call_args_list if call.args[0] == "INST"
        )
        self.assertEqual(installation_creation.args[1], namespace["LOAD_TYPE_MAP"]["Dead"])
        active_case = None
        calls_by_case = {}
        for method, arguments, keywords in load.mock_calls:
            if method == "SetLoadActive":
                active_case = arguments[0]
            calls_by_case.setdefault(active_case, []).append((method, arguments))
        dead_calls = calls_by_case[case_ids["DL DEAD LOAD"]]
        self.assertFalse(any(method == "AddNodalLoad" for method, arguments in dead_calls))
        self.assertIn(("AddSelfWeightInXYZ", (2, -1.0)), dead_calls)
        uniform_calls = [arguments for method, arguments in dead_calls if method == "AddMemberUniformForce"]
        self.assertEqual(uniform_calls, [
            ([1, 2, 5, 6], 5, -1.5, 0.0, 0.0, 0.0),
            ([7, 8, 11, 12], 5, -1.0, 0.0, 0.0, 0.0),
        ])
        for seismic_call, dead_call in zip(seismic_uniform_calls, uniform_calls):
            self.assertEqual(seismic_call.args[5], dead_call[0])
            self.assertEqual(seismic_call.args[2], -dead_call[2])
            self.assertEqual(seismic_call.args[3:5], (0.0, 0.0))
        installation_calls = calls_by_case[case_ids["INST"]]
        nodal_calls = [arguments for method, arguments in installation_calls if method == "AddNodalLoad"]
        self.assertEqual([arguments[0] for arguments in nodal_calls], [
            [1, 3, 7, 9], [2, 4, 6, 8], [5],
        ])
        self.assertFalse(any(method == "AddSelfWeightInXYZ" for method, arguments in installation_calls))
        live_calls = calls_by_case[case_ids["LL LIVE LOAD"]]
        live_nodal_calls = [arguments for method, arguments in live_calls if method == "AddNodalLoad"]
        self.assertEqual([arguments[0] for arguments in live_nodal_calls], [
            [1, 3, 7, 9], [2, 4, 6, 8], [5],
        ])
        for arguments, multiplier in zip(live_nodal_calls, [1, 2, 4]):
            self.assertAlmostEqual(arguments[2], -multiplier * 160 * 9.81 / 1000 / 16)
            self.assertEqual(arguments[1], 0.0)
            self.assertEqual(arguments[3:], (0.0, 0.0, 0.0, 0.0))
        self.assertAlmostEqual(
            sum(len(arguments[0]) * arguments[2] for arguments in live_nodal_calls),
            -160 * 9.81 / 1000,
        )
        live_uniform_calls = [arguments for method, arguments in live_calls if method == "AddMemberUniformForce"]
        self.assertEqual(live_uniform_calls, [])
        self.assertNotIn("WIND 1", case_ids)
        self.assertNotIn("WIND 2", case_ids)
        for combination in load.CreateNewLoadCombination.call_args_list:
            load.AddLoadAndFactorToCombination.assert_any_call(combination.args[1], case_ids["INST"], 1)
            wind_factor = 0.75 if combination.args[0] == "Comb DL LL WL" else 1.2
            for title in ("W+X", "W-X", "W+Z", "W-Z"):
                load.AddLoadAndFactorToCombination.assert_any_call(combination.args[1], case_ids[title], wind_factor)

    def test_radiator_wind_forces_use_two_rows_and_endpoint_multipliers(self):
        node_cache = {
            (column * 3300 + 100, 5000, row * 2600 - 200): column * 4 + row + 1
            for column in range(3) for row in range(4)
        }
        node_cache[(100, 0, -200)] = 99
        expected_groups = {
            "W+X": [[1, 4], [2, 3], [5, 8], [6, 7]],
            "W-X": [[9, 12], [10, 11], [5, 8], [6, 7]],
            "W+Z": [[1, 9], [5], [2, 10], [6]],
            "W-Z": [[4, 12], [8], [3, 11], [7]],
        }
        for title, groups in expected_groups.items():
            with self.subTest(title=title):
                load = Mock()
                namespace["_apply_radiator_wind_nodal_loads"](
                    load, node_cache, list(range(1, 13)), title, {},
                )
                calls = load.AddNodalLoad.call_args_list
                self.assertEqual([call.args[0] for call in calls], groups)
                along_x = title in ("W+X", "W-X")
                sign = -1 if title in ("W-X", "W-Z") else 1
                horizontal = 1.67 if along_x else 2.24
                vertical = 0.74 if along_x else 0.78
                for call, multiplier, vertical_sign in zip(calls, [1, 2, 1, 2], [1, 1, -1, -1]):
                    self.assertAlmostEqual(call.args[1], sign * multiplier * horizontal if along_x else 0.0)
                    self.assertAlmostEqual(call.args[2], vertical_sign * multiplier * vertical)
                    self.assertAlmostEqual(call.args[3], 0.0 if along_x else sign * multiplier * horizontal)
                    self.assertEqual(call.args[4:], (0.0, 0.0, 0.0))

    def test_radiator_wind_force_configuration_overrides(self):
        node_cache = {(column, 5, row): column * 2 + row + 1 for column in range(2) for row in range(2)}
        configuration = {
            "radiator_wind_force_x_kN": 10, "radiator_wind_force_y1_kN": 20,
            "radiator_wind_force_z_kN": 30, "radiator_wind_force_y2_kN": 40,
        }
        for title, expected_horizontal, expected_vertical in (("W+X", 10, 20), ("W+Z", 30, 40)):
            load = Mock()
            namespace["_apply_radiator_wind_nodal_loads"](load, node_cache, [1, 2, 3, 4], title, configuration)
            calls = load.AddNodalLoad.call_args_list
            self.assertEqual(len(calls), 2)
            horizontal_index = 1 if title == "W+X" else 3
            self.assertEqual(calls[0].args[horizontal_index], expected_horizontal)
            self.assertEqual(calls[0].args[2], expected_vertical)
            self.assertEqual(calls[1].args[2], -expected_vertical)

    def test_radiator_wind_requires_two_rows(self):
        load = Mock()
        with self.assertRaisesRegex(ValueError, "at least two"):
            namespace["_apply_radiator_wind_nodal_loads"](load, {(0, 5, 0): 1}, [1], "W+X", {})
        load.AddNodalLoad.assert_not_called()

    def test_directional_wind_cases_use_unique_rows_and_requested_ranges(self):
        members = []
        for row in range(3):
            for bay in range(3):
                members.append({
                    "type": "beam", "name": f"beam-x-{row}-{bay}",
                    "p1": [row * 3300, 5000, bay * 2600],
                    "p2": [row * 3300, 5000, (bay + 1) * 2600],
                })
        for row in range(4):
            for bay in range(2):
                members.append({
                    "type": "beam", "name": f"beam-y-{bay}-{row}",
                    "p1": [bay * 3300, 5000, row * 2600],
                    "p2": [(bay + 1) * 3300, 5000, row * 2600],
                })
        load = Mock()
        load.CreateNewPrimaryLoadEx2.side_effect = lambda title, load_type, case_id: case_id
        grid_spec = {
            "n_trans_bays": 2, "bay_trans_mm": 3300,
            "n_long_bays": 3, "bay_long_mm": 2600, "total_structure_height": 5300,
        }
        cases = namespace["_create_directional_wind_cases"](load, list(reversed(members)), grid_spec, 1, 11, True)
        self.assertEqual(cases, [11, 12, 13, 14])
        self.assertEqual([call.args for call in load.CreateNewPrimaryLoadEx2.call_args_list], [
            ("W+X", 3, 11), ("W-X", 3, 12), ("W+Z", 3, 13), ("W-Z", 3, 14),
        ])
        calls_by_case = {}
        active_case = None
        for method, arguments, keywords in load.mock_calls:
            if method == "SetLoadActive":
                active_case = arguments[0]
            elif method == "AddWindLoad":
                calls_by_case.setdefault(active_case, []).append(arguments)
        for case_id, direction, factor in ((11, 1, 1.0), (12, 1, -1.0)):
            self.assertEqual(len(calls_by_case[case_id]), 3)
            for arguments, coordinate in zip(calls_by_case[case_id], [0.0, 3.3, 6.6]):
                expected = (1, direction, factor, 1, 0.0, 5.3, -1.0, 8.0, coordinate - 0.1, coordinate + 0.1)
                for actual_value, expected_value in zip(arguments, expected):
                    self.assertAlmostEqual(actual_value, expected_value)
        for case_id, direction, factor in ((13, 3, 1.0), (14, 3, -1.0)):
            self.assertEqual(len(calls_by_case[case_id]), 4)
            for arguments, coordinate in zip(calls_by_case[case_id], [0.0, 2.6, 5.2, 7.8]):
                expected = (1, direction, factor, 1, 0.0, 5.3, coordinate - 0.1, coordinate + 0.1, -1.0, 6.8)
                for actual_value, expected_value in zip(arguments, expected):
                    self.assertAlmostEqual(actual_value, expected_value)

        load.reset_mock()
        grid_spec["total_structure_height"] = 4900
        namespace["_create_directional_wind_cases"](load, members, grid_spec, 1, 11, False)
        for call in load.AddWindLoad.call_args_list:
            self.assertEqual(call.args[3], 0)
            self.assertEqual(call.args[5], 5.0)


if __name__ == "__main__":
    unittest.main()