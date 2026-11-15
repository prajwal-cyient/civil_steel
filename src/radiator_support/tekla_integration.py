"""
tekla_integration.py

Backward-compatibility interface.
The full Tekla Structures 3D Detailing Modeler Engine has been absorbed into Agent A7 (a7_tekla_modeler_agent.py).
"""

import sys
import os

_src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from agentic.agents.a7_tekla_modeler_agent import (
    send_to_tekla,
    connection_attributes,
    joint_group_columns,
    joint_group_beam_pairs,
    set_component_attribute,
    add_base_connection,
    add_column2beam_connection,
    dump_attributes,
    print_attributes,
    pick_and_print_attributes,
    TEKLA_CONNECTION_PARAMETER_MAP,
)

__all__ = [
    "send_to_tekla",
    "connection_attributes",
    "joint_group_columns",
    "joint_group_beam_pairs",
    "set_component_attribute",
    "add_base_connection",
    "add_column2beam_connection",
    "dump_attributes",
    "print_attributes",
    "pick_and_print_attributes",
    "TEKLA_CONNECTION_PARAMETER_MAP",
]
