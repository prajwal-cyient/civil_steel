"""
idea_statica_integration.py

Backward-compatibility shim.
The complete IDEA StatiCa Connection REST API integration and design_joint() logic
have been absorbed into Agent A5 (src/agentic/agents/a5_ideastatica_analyzer_agent.py).
"""

import sys
import os

# Ensure src/ is on sys.path
_src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from agentic.agents.a5_ideastatica_analyzer_agent import (
    BASE_URL,
    SETUP_DIR,
    IdeaStaticaConnectionError,
    IdeaStaticaJointError,
    _to_jsonable,
    _get_client,
    _resolve_connection,
    _resolve_member,
    _free_end_position,
    _resolve_cross_section_id,
    _list_operations,
    _list_operations_properties,
    _get_parameters,
    _find_parameters,
    _quote_expression_if_needed,
    _set_parameters,
    to_json_convention_string,
    design_joint,
)

__all__ = [
    "BASE_URL",
    "SETUP_DIR",
    "IdeaStaticaConnectionError",
    "IdeaStaticaJointError",
    "design_joint",
]
