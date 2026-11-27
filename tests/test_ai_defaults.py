"""AI defaults and explicit opt-out; no model or native tools are called."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from config.settings import deep_agents_enabled, load_environment, settings


class AIDefaultTests(unittest.TestCase):
    def test_unset_flags_enable_deep_agents_and_llm(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(deep_agents_enabled())
            self.assertTrue(settings().llm_enabled)

    def test_example_environment_enables_both(self):
        with patch.dict(os.environ, {}, clear=True):
            load_environment(ROOT / '.env.example')
            self.assertTrue(deep_agents_enabled())
            self.assertTrue(settings().llm_enabled)

    def test_explicit_false_disables_both(self):
        with patch.dict(os.environ, {'PLANT_DEEP_AGENTS_ENABLED': 'false', 'PLANT_LLM_ENABLED': 'false'}):
            self.assertFalse(deep_agents_enabled())
            self.assertFalse(settings().llm_enabled)

    def test_example_does_not_override_explicit_process_opt_out(self):
        with patch.dict(os.environ, {'PLANT_DEEP_AGENTS_ENABLED': 'false', 'PLANT_LLM_ENABLED': 'false'}, clear=True):
            load_environment(ROOT / '.env.example')
            self.assertFalse(deep_agents_enabled())
            self.assertFalse(settings().llm_enabled)
