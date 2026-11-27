"""Generate continuous and bay-split structural members."""

from typing import Any, Callable, Dict, List, Optional

from .A3A_evaluate_plan import FrameParameters, evaluate_frame_parameters


def generate_frame(cfg: Dict[str, Any], parameters: Optional[FrameParameters], evaluate_parameters: Callable[[Dict[str, Any]], FrameParameters]) -> Dict[str, Any]:
    n_long = cfg["n_long_bays"]
    n_trans = cfg["n_trans_bays"]
    bay_long = cfg["bay_long_mm"]
    bay_trans = cfg["bay_trans_mm"]
    col_height = cfg["col_height_mm"]
    total_structure_height = cfg["total_structure_height"]
    longitudinal_beam_extra_mm = cfg.get("longitudinal_beam_extra_mm", 140.0)
    x0, y0, z0 = cfg.get("starting_point", [0.0, 0.0, 0.0])

    long_brace_bays = cfg.get("longitudinal_brace_bays", [])
    trans_brace_bays = cfg.get("transverse_brace_bays", [])
    vertical_base_eccentricity = cfg.get("vertical_base_eccentricity_mm", 0.0)
    gusset_t = cfg.get("gusset_plate_thickness_mm", 0.0)
    embedded_t = cfg.get("embedded_plate_thickness_mm", 0.0)
    bracing_distance_from_column_mm = cfg.get("bracing_distance_from_column_mm", 0.0)

    beam_material = cfg.get("beam_material", "S355JR")
    column_material = cfg.get("column_material", "S355JR")
    long_brace_material = cfg.get("longitudinal_brace_material", "S355JR")
    trans_brace_material = cfg.get("transverse_brace_material", "S355JR")

    beam_profile = cfg.get("beam_profile", "HEA140")
    column_profile = cfg.get("column_profile", "CFRHS120X120X5")
    long_brace_profile = cfg.get("longitudinal_brace_profile", "CFRHS100X100X4")
    trans_brace_profile = cfg.get("transverse_brace_profile", "CFRHS100X100X4")

    beam_class = cfg.get("beamClass", "3")
    column_class = cfg.get("columnClass", "11")
    long_brace_class = cfg.get("longitudinalClass", "2")
    trans_brace_class = cfg.get("transverseClass", "7")

    if parameters is None:
        parameters = evaluate_parameters(cfg)
    elif (
        parameters.beam_profile != beam_profile
        or parameters.column_profile != column_profile
        or parameters.col_height_mm != col_height
        or parameters.total_structure_height != total_structure_height
    ):
        raise ValueError("Frame parameters do not match the current profile and height configuration.")
    beam_height = parameters.beam_height_mm
    col_height_eff = parameters.effective_col_height_mm

    col_bottom_z = z0
    col_top_z = col_bottom_z + col_height_eff

    long_beam_z = col_top_z + beam_height
    trans_beam_z = col_top_z + 2 * beam_height

    brace_bottom_z = col_bottom_z + vertical_base_eccentricity
    brace_top_z = col_top_z
    brace_bottom_z_staad = col_bottom_z

    def point(i: int, j: int, z: float) -> List[float]:
        return [x0 + i * bay_long, y0 + j * bay_trans, z]

    def transform_to_staad_co_ordinates(p_in: List[float]) -> List[float]:
        p = list(p_in)
        # Swap Y and Z
        p[1], p[2] = p[2], p[1]
        # Swap X and Z
        p[0], p[2] = p[2], p[0]
        return p

    columns = []
    staad_columns = []
    beams_tekla = []
    beams_staad = []
    braces = []
    staad_braces = []

    # 1. Columns
    for j in range(n_trans + 1):
        for i in range(n_long + 1):
            columns.append({
                "p1": point(i, j, col_bottom_z),
                "p2": point(i, j, col_top_z),
                "profile": column_profile,
                "material": column_material,
                "class": column_class,
                "type": "column",
                "name": f"column-{j}-{i}",
            })
            p1 = transform_to_staad_co_ordinates(point(i, j, col_bottom_z))
            p2 = transform_to_staad_co_ordinates(point(i, j, trans_beam_z))
            staad_columns.append({
                "p1": p1,
                "p2": p2,
                "profile": column_profile,
                "material": column_material,
                "class": column_class,
                "type": "column",
                "name": f"column-{j}-{i}",
                "tekla_name": f"column-{j}-{i}",
            })

    # 2. Longitudinal Beams (X-direction)
    for j in range(n_trans + 1):
        p1 = point(0, j, col_top_z)
        p2 = point(n_long, j, col_top_z)
        p1[0] -= longitudinal_beam_extra_mm
        p2[0] += longitudinal_beam_extra_mm
        beams_tekla.append({
            "p1": p1,
            "p2": p2,
            "profile": beam_profile,
            "material": beam_material,
            "class": beam_class,
            "type": "beam",
            "name": f"beam-x-{j}",
        })
    for j in range(n_trans + 1):
        for i in range(n_long):
            p1 = transform_to_staad_co_ordinates(point(i, j, trans_beam_z))
            p2 = transform_to_staad_co_ordinates(point(i + 1, j, trans_beam_z))
            beams_staad.append({
                "p1": p1,
                "p2": p2,
                "profile": beam_profile,
                "material": beam_material,
                "class": beam_class,
                "type": "beam",
                "name": f"beam-x-{j}-{i}",
                "tekla_name": f"beam-x-{j}",
            })

    # 3. Transverse Beams (Y-direction)
    for i in range(n_long + 1):
        p1 = point(i, 0, col_top_z)
        p2 = point(i, n_trans, col_top_z)
        beams_tekla.append({
            "p1": p1,
            "p2": p2,
            "profile": beam_profile,
            "material": beam_material,
            "class": beam_class,
            "type": "beam",
            "name": f"beam-y-{i}",
        })
    for j in range(n_trans):
        for i in range(n_long + 1):
            p1 = transform_to_staad_co_ordinates(point(i, j, trans_beam_z))
            p2 = transform_to_staad_co_ordinates(point(i, j + 1, trans_beam_z))
            beams_staad.append({
                "p1": p1,
                "p2": p2,
                "profile": beam_profile,
                "material": beam_material,
                "class": beam_class,
                "type": "beam",
                "name": f"beam-y-{j}-{i}",
                "tekla_name": f"beam-y-{i}",
            })

    # 4. Longitudinal Bracing
    for entry in long_brace_bays:
        bay_1based = entry["bay"]
        mirror = entry.get("mirror", False)
        if bay_1based < 1 or bay_1based > n_long:
            continue
        b = bay_1based - 1
        bottom_i, top_i = (b + 1, b) if mirror else (b, b + 1)
        for j in range(n_trans + 1):
            p1 = point(bottom_i, j, brace_bottom_z)
            p2 = point(top_i, j, brace_top_z)
            braces.append({
                "p1": p1,
                "p2": p2,
                "profile": long_brace_profile,
                "material": long_brace_material,
                "class": long_brace_class,
                "type": "longitudinal_brace",
                "name": f"bracing-long-{j}-{bay_1based}",
                "is_mirrored": mirror,
            })
            p1_staad = transform_to_staad_co_ordinates(point(bottom_i, j, brace_bottom_z_staad))
            p2_staad = transform_to_staad_co_ordinates(point(top_i, j, trans_beam_z))
            staad_braces.append({
                "p1": p1_staad,
                "p2": p2_staad,
                "profile": long_brace_profile,
                "material": long_brace_material,
                "class": long_brace_class,
                "type": "longitudinal_brace",
                "name": f"bracing-long-{j}-{bay_1based}",
                "is_mirrored": mirror,
                "tekla_name": f"bracing-long-{j}-{bay_1based}",
            })

    # 5. Transverse Bracing
    for entry in trans_brace_bays:
        bay_1based = entry["bay"]
        mirror = entry.get("mirror", False)
        if bay_1based < 1 or bay_1based > n_trans:
            continue
        b = bay_1based - 1
        bottom_j, top_j = (b + 1, b) if mirror else (b, b + 1)
        for i in range(n_long + 1):
            p1 = point(i, bottom_j, brace_bottom_z)
            p2 = point(i, top_j, brace_top_z)
            braces.append({
                "p1": p1,
                "p2": p2,
                "profile": trans_brace_profile,
                "material": trans_brace_material,
                "class": trans_brace_class,
                "type": "transverse_brace",
                "name": f"bracing-trans-{i}-{bay_1based}",
                "is_mirrored": mirror,
            })
            p1_staad = transform_to_staad_co_ordinates(point(i, bottom_j, brace_bottom_z_staad))
            p2_staad = transform_to_staad_co_ordinates(point(i, top_j, trans_beam_z))
            staad_braces.append({
                "p1": p1_staad,
                "p2": p2_staad,
                "profile": trans_brace_profile,
                "material": trans_brace_material,
                "class": trans_brace_class,
                "type": "transverse_brace",
                "name": f"bracing-trans-{i}-{bay_1based}",
                "is_mirrored": mirror,
                "tekla_name": f"bracing-trans-{i}-{bay_1based}",
            })

    members_tekla = columns + beams_tekla + braces
    members_staad = staad_columns + beams_staad + staad_braces

    members_by_type = {}
    for m in members_tekla:
        members_by_type[m["type"]] = members_by_type.get(m["type"], 0) + 1

    summary = {
        "total_members": len(members_tekla),
        "members_by_type": members_by_type,
        "effective_col_height_mm": col_height_eff,
        "beam_height_mm": beam_height,
    }

    return {
        "grid": cfg,
        "members": members_tekla,
        "members_split_beams": members_staad,
        "summary": summary,
    }

def generate_members(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parametric 3D Steel Frame Generator.
    Produces:
      - 'members': Continuous physical members spanning bay runs (for Tekla Structures).
      - 'members_split_beams': Discretized bay-split member segments (for STAAD.Pro FEA).
      - 'summary': Component breakdowns and effective height adjustments.
    """
    return generate_frame(cfg, None, evaluate_frame_parameters)


def generate_members_from_parameters(
    cfg: Dict[str, Any], parameters: FrameParameters
) -> Dict[str, Any]:
    """Generate the frame using profile and height evaluation from the same configuration."""
    return generate_frame(cfg, parameters, evaluate_frame_parameters)




if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Generate 3D steel grid")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="output.json")
    args = parser.parse_args()
    with open(args.input) as f:
        cfg = json.load(f)
    grid_cfg = cfg.get("grid", cfg)
    result = generate_members(grid_cfg)
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Generated {result['summary']['total_members']} members to {args.output}")
