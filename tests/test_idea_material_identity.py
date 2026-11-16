"""A matching section shape must not silently retain a different steel grade."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentic.agents.a5_ideastatica_analyzer_agent import _resolve_cross_section_id, IdeaStaticaJointError


def section(identifier, material):
    return {"id": identifier, "name": "HEA140", "material": {"element": {"name": material}}}


class MaterialIdentityTests(unittest.TestCase):
    def test_wrong_grade_is_not_reused(self):
        client = Mock()
        client.material.get_cross_sections.return_value = [section(1, "S 235"), section(2, "S 355 JR")]
        self.assertEqual(_resolve_cross_section_id(client, "project", "HEA140", "S355JR"), 2)
        client.material.add_cross_section.assert_not_called()

    def test_requested_grade_is_created_if_missing(self):
        client = Mock()
        client.material.get_cross_sections.return_value = [section(1, "S 235")]
        client.material.add_cross_section.return_value = section(3, "S355JR")
        self.assertEqual(_resolve_cross_section_id(client, "project", "HEA140", "S355JR"), 3)
        self.assertEqual(client.material.add_cross_section.call_args.args[1].material_name, "S355JR")

    def test_incorrect_added_material_is_rejected(self):
        client = Mock()
        client.material.get_cross_sections.return_value = [section(1, "S 235")]
        client.material.add_cross_section.return_value = section(3, "S 235")
        with self.assertRaisesRegex(IdeaStaticaJointError, "does not verify its steel grade"):
            _resolve_cross_section_id(client, "project", "HEA140", "S355JR")


if __name__ == "__main__":
    unittest.main()
