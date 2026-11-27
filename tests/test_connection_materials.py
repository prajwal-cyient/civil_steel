"""Reference plate correction must retain geometry and reach verified Tekla materials."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agents.A5_idea_statica.package_validation import (
    material_only_plate_evidence, reference_plate_material_policy, verify_plate_material_evidence,
)
from agents.A6_tekla import A6B_trigger_api as tekla
from agents import A5_idea_statica as idea


class ConnectionMaterialTests(unittest.TestCase):
    def setUp(self):
        self.policy = reference_plate_material_policy()
        self.before = {"plates": [{"id": 56, "name": "STIFF1a", "material": "S 235", "thickness": .01}],
                       "beams": [{"id": 1, "name": "column", "section": "SHS120/120/5.0"}],
                       "welds": [{"id": 21, "thickness": .005}]}
        self.after = deepcopy(self.before)
        self.after["plates"][0]["material"] = "S 355"

    def test_policy_is_bound_to_standard_component_materials(self):
        self.assertEqual(self.policy["component_attributes"]["30"]["mat3"], "S355J0")
        self.assertEqual(self.policy["component_attributes"]["144"]["mat"], "S355J0")
        self.assertEqual(set(self.policy["component_attributes"]), {"1004", "144", "20", "30"})
        self.assertEqual(len(self.policy["attribute_file_sha256"]), 4)

    def test_material_only_change_records_exact_affected_plate(self):
        evidence = material_only_plate_evidence(self.before, self.after, self.policy)
        self.assertEqual(evidence["material_corrections"], [
            {"id": 56, "name": "STIFF1a", "old": "S 235", "new": "S 355"}])
        self.assertTrue(evidence["geometry_unchanged"])

    def test_actual_plate_export_verifies_update_when_common_selection_is_null(self):
        client = Mock()
        client.material.get_steel_materials.return_value = [
            {"id": 1, "name": "S 355", "fy": 355000000.0, "fu": 490000000.0}]
        client.operation.get_common_operation_properties.side_effect = lambda *_: SimpleNamespace(plate_material_id=None)
        client.export.export_iom_connection_data.side_effect = [self.before, self.after]
        evidence = idea._apply_reference_plate_material(client, "project", 1, self.policy)
        self.assertEqual(evidence["plates"][0]["material"], "S 355")
        self.assertEqual(client.operation.update_common_operation_properties.call_args.args[2].plate_material_id, 1)
        client.operation.get_common_operation_properties.assert_called_once()

    def test_material_repair_cannot_change_weld_size_or_geometry(self):
        for field, key, value in [("welds", "thickness", .006), ("plates", "thickness", .012),
                                  ("beams", "section", "HEA200")]:
            with self.subTest(field=field):
                changed = deepcopy(self.after)
                changed[field][0][key] = value
                with self.assertRaisesRegex(ValueError, "unrelated geometry"):
                    material_only_plate_evidence(self.before, changed, self.policy)

    def test_remaining_unmatched_grade_cannot_pass(self):
        with self.assertRaisesRegex(ValueError, "declared reference material"):
            material_only_plate_evidence(self.before, self.before, self.policy)

    def test_evidence_is_bound_to_the_actual_native_export(self):
        evidence = material_only_plate_evidence(self.before, self.after, self.policy)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "iom.json"
            path.write_text(json.dumps(self.after))
            evidence["iom_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            verify_plate_material_evidence(evidence, self.policy, path)
            path.write_text(json.dumps(self.before))
            with self.assertRaisesRegex(ValueError, "changed after calculation"):
                verify_plate_material_evidence(evidence, self.policy, path)

    def test_missing_or_changed_policy_evidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing or differs"):
            verify_plate_material_evidence(None, self.policy, None)
        evidence = material_only_plate_evidence(self.before, self.after, self.policy)
        changed = deepcopy(self.policy)
        changed["tekla_material_name"] = "S235JR"
        with self.assertRaisesRegex(ValueError, "missing or differs"):
            verify_plate_material_evidence(evidence, changed, None)

    def test_tekla_material_attributes_use_string_readback(self):
        model, component, persisted = Mock(), Mock(), Mock()
        model.SelectModelObject.return_value = persisted
        persisted.GetAttribute.side_effect = lambda key, default: (isinstance(default, str), "S355J0")
        tekla.set_component_attribute(component, "mat3", "S355J0")
        component.SetAttribute.assert_called_once_with("mat3", "S355J0")
        tekla._verify_component(model, component, {"mat3": "S355J0"})
        persisted.GetAttribute.return_value = (True, "S235JR")
        persisted.GetAttribute.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "differs"):
            tekla._verify_component(model, component, {"mat3": "S355J0"})

    def test_changed_standard_policy_stops_before_tekla_cleanup(self):
        changed = deepcopy(self.policy)
        changed["attribute_file_sha256"]["baseplate_1004.j1004"] = "changed"
        with patch.object(tekla, "clean_tekla") as clean:
            with self.assertRaisesRegex(RuntimeError, "changed after package"):
                tekla.send_to_tekla({"plate_material_policy": changed})
        clean.assert_not_called()


if __name__ == "__main__":
    unittest.main()
