"""Agent A3: Frame plan evaluation, geometry generation, and connection scope."""

from .A3A_evaluate_plan import (
    STANDARD_PROFILE_DIMENSIONS,
    FrameParameters,
    _lookup_profile_param,
    _fallback_dimension,
    _lookup_profile_height,
    _lookup_profile_width,
    evaluate_frame_parameters,
    validate_profile_catalogue,
)
from .A3B_generate_structure import generate_frame, generate_members, generate_members_from_parameters
from .geometry_checks import (
    check_geometry,
    run_a3_frame_generator,
    evaluate_a3_frame,
    generate_a3_frame,
    run_a3_staged,
)
from .connection_scope import (
    REFERENCE_IDEA_TEMPLATES,
    STANDARD_DETAILS,
    build_connection_scope,
    scope_for_state,
    template_for_group,
    preview_joint_groups,
    template_coverage,
)
from .graph import FrameState, build_frame_graph, run_frame_graph
