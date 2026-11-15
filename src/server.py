#!/usr/bin/env python3
"""
server.py

server for generating and designing different civil structures.

Usage:
    python server.py --port 8000
    # then POST a JSON body (same shape as the input file below) to:
    #   http://<host>:8000/api/v1/generate_radiator_support
    # or open the web UI in a browser:
    #   http://<host>:8000/radiator_support
    # --input/--output/--show are ignored when --port is given.
    #
    # The web UI is served from static/radiator_support/index.html, which
    # must live in a "static" folder next to this script.
    #
    # POST /api/v1/to_tekla accepts the same JSON that
    # /api/v1/generate_radiator_support returned (grid + members +
    # members_split_beams + summary) and sends every member in "members"
    # into a running Tekla Structures model via
    # tekla_integration.send_to_tekla(). That module must live next to
    # this script and requires pythonnet + Tekla Structures installed
    # (Windows only); see tekla_integration.py for details.
    #
    # POST /api/v1/to_staad accepts {"members_split_beams": [...],
    # "load_configuration": {...}} (both present in the same generated
    # JSON) and sends the piecewise members plus load configuration into
    # a running STAAD.Pro session via staad_integration.send_to_staad().
    # That module must live next to this script and requires pywin32 +
    # STAAD.Pro installed (Windows only); see staad_integration.py for
    # details, including its load_configuration schema and — importantly
    # — which parts of that module are unverified best-effort.
    #
    # NOTE: generate_members() looks up beam/column profile dimensions
    # via tekla_integration.get_profile_height()/get_profile_width(). If
    # Tekla isn't reachable (or tekla_integration.py can't be imported),
    # those lookups silently return 0 rather than raising, so the rest of
    # this script can still be exercised on a machine without Tekla — the
    # beam-stacking heights and continuous-beam length extensions just
    # won't be physically correct until Tekla is actually reachable.

Input JSON format:
{
  "grid": {
    "n_long_bays": 21,
    "n_trans_bays": 5,
    "bay_long_mm": 2600,
    "bay_trans_mm": 3300,
    "col_height_mm": 5000,
    "total_structure_height": 5300,
    "starting_point": [0, 0, 0],
    "longitudinal_brace_bays": [{"bay": 6, "mirror": false}, {"bay": 7, "mirror": true}],
    "transverse_brace_bays": [{"bay": 2, "mirror": false}, {"bay": 4, "mirror": true}],
    "vertical_base_eccentricity_mm": 38,
    "gusset_plate_thickness_mm": 15,
    "embedded_plate_thickness_mm": 10,
    "bracing_distance_from_column_mm": 20,
    "beam_material": "S355JR",
    "column_material": "S355JR",
    "longitudinal_brace_material": "S355JR",
    "transverse_brace_material": "S355JR",
    "beam_profile": "HEA140",
    "column_profile": "CFRHS120X120X5",
    "longitudinal_brace_profile": "CFRHS100X100X4",
    "transverse_brace_profile": "CFRHS100X100X4",
    "beamClass": "3",
    "columnClass": "11",
    "longitudinalClass": "2",
    "transverseClass": "7"
  }
  // additional top-level keys (e.g. future "loading" config) are allowed
  // and passed through unchanged; only "grid" is strictly validated.
}

Output JSON format:
{
  "grid": { ... same config as input ... },
  "members": [
    {
      "p1": [x, y, z],
      "p2": [x, y, z],
      "profile": "...",
      "material": "...",
      "class": "...",
      "type": "column" | "beam" | "longitudinal_brace" | "transverse_brace",
      "name": "..."
    },
    ...
  ],
  "members_split_beams": [ ... same shape, but each beam run is split into one member per bay ... ],
  "summary": {
    "total_members": <int>,               // counts "members"
    "members_by_type": {"column": <int>, "beam": <int>, "longitudinal_brace": <int>, "transverse_brace": <int>},
    "effective_col_height_mm": <float>,    // may be less than col_height_mm — see TOTAL STRUCTURE HEIGHT below
    "beam_height_mm": <float>              // beam_profile's catalog HEIGHT, looked up via Tekla
  }
  // ...plus any other top-level keys that were present in the input.
}

"members" and "members_split_beams" contain the same columns and bracing;
they only differ in how the beams are split up:
  - "members": one continuous beam per grid line, spanning the full
    building length/width (n_long_bays * bay_long_mm or
    n_trans_bays * bay_trans_mm), each extended past its end points by
    half the width of whatever it lands on/against (see LENGTH
    EXTENSIONS below). This is what gets sent to Tekla via
    POST /api/v1/to_tekla.
  - "members_split_beams": the same beam runs (not length-extended),
    split into one member per bay (the original per-bay behavior), for
    import into STAAD.Pro or similar tools that expect a node at every
    bay.

GEOMETRY / INDEXING CONVENTIONS
--------------------------------
  - "i" indexes grid lines along the longitudinal (x) axis: 0 .. n_long_bays
  - "j" indexes grid lines along the transverse (y) axis:   0 .. n_trans_bays
  - A "bay" number b (used for bracing) refers to the bay BETWEEN grid
    line i=b and grid line i=b+1 (or j=b and j=b+1 for transverse bracing).

ELEVATIONS (z) AND BEAM-ON-BEAM STACKING
------------------------------------------
  - beam_height is the beam_profile's cross-section HEIGHT, looked up
    live from the Tekla catalog (see _lookup_profile_height() /
    tekla_integration.get_profile_height()). If Tekla isn't reachable,
    this silently resolves to 0 rather than raising (see the NOTE in the
    API usage section above).
  - The column base is raised off the starting point by both the
    embedded plate thickness and the vertical base eccentricity (see
    VERTICAL BASE ECCENTRICITY below):
        col_bottom_z = z0 + embedded_plate_thickness_mm + vertical_base_eccentricity_mm
        col_top_z    = col_bottom_z + effective_col_height_mm
  - The longitudinal (x-direction) beam sits on top of the column; the
    transverse (y-direction) beam sits on top of the longitudinal beam:
        long_beam_z  = col_top_z + beam_height
        trans_beam_z = col_top_z + 2 * beam_height
  - Gusset plates sit inboard of the column ends at every brace
    connection (unaffected by the beam stacking above, since bracing
    connects to the columns, not to the beams):
        brace_bottom_z = col_bottom_z + gusset_plate_thickness_mm
        brace_top_z    = col_top_z    - gusset_plate_thickness_mm

LENGTH EXTENSIONS (continuous beams only)
--------------------------------------------
  - column_width and beam_width are the column_profile's and
    beam_profile's cross-section WIDTH, looked up live from the Tekla
    catalog (tekla_integration.get_profile_width()); 0 if unavailable.
  - Each continuous longitudinal beam ("members", not
    "members_split_beams") has its p2 (the high-i end) extended by the
    full column_width; p1 is unchanged.
  - Each continuous transverse beam's x-coordinate (both endpoints) is
    shifted by +column_section_height/2 (the column_profile's own
    catalog HEIGHT, not the beam's width) so the beam's centerline
    aligns with the column line below it, rather than its edge.
    Longitudinal beams need no equivalent y-shift — their reference line
    already lands on the column's centerline in y.
  - Each continuous transverse beam's y-coordinate is shifted by half
    the beam's own width at each end (p1 -= beam_width/2,
    p2 += beam_width/2), which both centers it and extends its overall
    length by one beam_width.

TOTAL STRUCTURE HEIGHT
------------------------
  - total_structure_height is the fixed overall height, grade to top of
    the longitudinal beam: col_height + (transverse beam depth) +
    (longitudinal beam depth). Since both beam directions currently
    share "beam_profile", both depths equal beam_height, so the budget
    is col_height + 2 * beam_height.
  - If col_height_mm would make that total exceed total_structure_height,
    the column height is reduced (never increased) so the total fits
    exactly: effective_col_height_mm = total_structure_height - 2 * beam_height.
    The value actually used is reported back in summary.effective_col_height_mm.
  - Raises ValueError if total_structure_height leaves no room for a
    positive column height once both beam depths are subtracted.

VERTICAL BASE ECCENTRICITY
-----------------------------
  - vertical_base_eccentricity_mm raises the column base (and therefore
    the column top, both beam levels, and the bracing base connections)
    by a fixed vertical amount, instead of the previous horizontal
    (sideways) offset applied only to bracing base connections.

BRACE ORIENTATION (mirror flag)
---------------------------------
  - Bay numbers in longitudinal_brace_bays / transverse_brace_bays are
    1-BASED (bay 1 is the first bay, between grid lines 0 and 1). They are
    converted to 0-based grid-line indices internally: b = bay - 1.
  - Valid 1-based bay range is 1 .. n_long_bays for longitudinal bracing,
    and 1 .. n_trans_bays for transverse bracing. Any entry outside this
    range is skipped with a warning (rather than raising an IndexError or
    silently producing an out-of-grid member).
  - longitudinal_brace_bays entry {"bay": b, "mirror": false} (1-based b,
    0-based index bi = b - 1):
        bottom end at column i=bi   (bottom of column)
        top end    at column i=bi+1 (top of column)
  - {"bay": b, "mirror": true}:
        bottom end at column i=bi+1 (bottom of column)
        top end    at column i=bi   (top of column)
    Each longitudinal brace bay is repeated across every transverse grid
    line j = 0 .. n_trans_bays.
  - transverse_brace_bays follows the same bottom/top convention using
    j and j+1, and is repeated across every longitudinal grid line
    i = 0 .. n_long_bays.
"""

import argparse
import json
from math import sqrt
import sys

def create_app():
    """
    Build the FastAPI app. Imports are done lazily here so that plain CLI
    usage (--input/--output/--show) doesn't require fastapi/pydantic to be
    installed at all.
    """
    from fastapi import FastAPI, HTTPException
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, ConfigDict, Field
    from typing import Any, Dict, List, Optional
    import pathlib

    # Static assets (the radiator-support web UI) live in a "static"
    # folder next to this script, regardless of the current working
    # directory the server is launched from.
    base_dir = pathlib.Path(__file__).resolve().parent
    static_dir = base_dir / "static"

    class BraceBay(BaseModel):
        # Strict: only "bay" and "mirror" are accepted per brace entry.
        model_config = ConfigDict(extra="forbid")
        bay: int
        mirror: bool = False

    class RadiatorSupportGridConfig(BaseModel):
        # Strict: only known grid fields are accepted; anything else in
        # "grid" is a validation error. Required fields must be present.
        model_config = ConfigDict(extra="forbid")

        n_long_bays: int
        n_trans_bays: int
        bay_long_mm: float
        bay_trans_mm: float
        col_height_mm: float
        starting_point: List[float] = Field(..., min_length=3, max_length=3)

        longitudinal_brace_bays: List[BraceBay] = Field(default_factory=list)
        transverse_brace_bays: List[BraceBay] = Field(default_factory=list)
        vertical_base_eccentricity_mm: float = 0
        base_plate_offset_above_column_base_mm: float = 0
        gusset_plate_thickness_mm: float = 0
        embedded_plate_thickness_mm: float = 0
        bracing_distance_from_column_mm: float = 0
        total_structure_height: float = Field(..., gt=0)
        longitudinal_beam_extra_mm: float = Field(..., gt=0)

        beam_material: str
        column_material: str
        longitudinal_brace_material: str
        transverse_brace_material: str

        beam_profile: str
        column_profile: str
        longitudinal_brace_profile: str
        transverse_brace_profile: str

        beamClass: str
        columnClass: str
        longitudinalClass: str
        transverseClass: str

    class RadiatorSupportGenerateRequest(BaseModel):
        # Permissive at the top level: any additional keys (e.g. future
        # "loading" config) are accepted and passed through untouched.
        model_config = ConfigDict(extra="allow")
        grid: RadiatorSupportGridConfig

    class Member(BaseModel):
        # Permissive: this mirrors whatever generate_members() produced,
        # so unknown/extra fields (added later) are simply passed through
        # to send_to_tekla() rather than rejected.
        model_config = ConfigDict(extra="allow")
        p1: List[float] = Field(..., min_length=3, max_length=3)
        p2: List[float] = Field(..., min_length=3, max_length=3)
        profile: str
        material: str
        type: str
        name: str
        class_: str = Field(alias="class")

    class ToTeklaRequest(BaseModel):
        # This is the same JSON the UI already has in hand after calling
        # /api/v1/generate_radiator_support (grid + members +
        # members_split_beams + summary), so only "members" (the
        # continuous-beam list) is required here; everything else is
        # passed through untouched.
        model_config = ConfigDict(extra="allow")
        members: List[Member]
        grid: Optional[Dict[str, Any]] = None
        # Joint groups designed in IDEA StatiCa:
        # {<joint group>: {"joints": [...], "operation_parameters": [...]}}
        joint_groups: Dict[str, Any] = Field(default_factory=dict)

    class ToStaadRequest(BaseModel):
        # Same idea as ToTeklaRequest, but STAAD gets the piecewise
        # ("members_split_beams") representation, plus an optional,
        # loosely-typed "load_configuration" (see staad_integration.py's
        # module docstring for its schema).
        model_config = ConfigDict(extra="allow")
        grid: Optional[Dict[str, Any]] = None
        members_split_beams: List[Member]
        load_configuration: Optional[Dict[str, Any]] = None

    class ConnectionSelector(BaseModel):
        # Exactly one of name/id/index is expected (see
        # idea_statica_integration._resolve_connection()).
        model_config = ConfigDict(extra="forbid")
        name: Optional[str] = None
        id: Optional[int] = None
        index: Optional[int] = None

    class PlateReference(BaseModel):
        model_config = ConfigDict(extra="forbid")
        plate_side: str
        member: str
        part_type: str

    class MemberUpdate(BaseModel):
        model_config = ConfigDict(extra="allow")
        member_name: str
        cross_section_mprl_name: str
        material_mprl_name: str
        # Degrees; converted to radians by design_joint().
        gama_pitch: Optional[float] = None
        aligned_plate: Optional[PlateReference] = None
        related_plate: Optional[PlateReference] = None

    class MemberLoading(BaseModel):
        # Forces in kN / moments in kNm (design_joint() scales to N / Nm).
        model_config = ConfigDict(extra="forbid")
        member_name: str
        n: float = 0
        vy: float = 0
        vz: float = 0
        mx: float = 0
        my: float = 0
        mz: float = 0
        position: Optional[str] = None

    class LoadEffect(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str = "Generated load"
        member_loadings: List[MemberLoading] = Field(default_factory=list)
        # Informational fields from send_to_staad(); not used by design_joint().
        load_case: Optional[int] = None
        node: Optional[int] = None
        governs: List[str] = Field(default_factory=list)

    class OperationParameter(BaseModel):
        # Only "key" and "expression" are sent to IDEA StatiCa; the other
        # fields in the template (value, orig_expression, description) are
        # informational and passed through untouched.
        model_config = ConfigDict(extra="allow")
        key: str
        expression: str

    class ToIdeaStaticaRequest(BaseModel):
        # Mirrors braced_bothside_template.json (see design_joint()'s
        # docstring in idea_statica_integration.py for the full schema).
        model_config = ConfigDict(extra="allow")
        template_project_path: str
        connection_selector: ConnectionSelector
        member_updates: List[MemberUpdate] = Field(default_factory=list)
        load_effects: List[LoadEffect] = Field(default_factory=list)
        output_dir: str
        save_project_path: Optional[str] = None
        operation_parameters: List[OperationParameter] = Field(default_factory=list)
        extract_raw_results: bool = False
        extract_pdf_report: bool = False

    app = FastAPI(title="Structural Grid Generator API")

    # Serves any assets referenced from the UI (e.g. /static/... paths)
    # if the page is later split into multiple files.
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/radiator_support")
    def radiator_support_ui():
        return FileResponse(str(static_dir / "radiator_support" / "index.html"))

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(str(static_dir / "favicon.ico"))

    @app.post("/api/v1/radiator_support/generate")
    def generate_radiator_support(request: RadiatorSupportGenerateRequest):
        from radiator_support.generate_grid import generate_members
        cfg = request.grid.model_dump()

        try:
            result = generate_members(cfg)
        except RuntimeError as exc:
            # Tekla unreachable, or the beam profile's height couldn't be
            # resolved from the catalog.
            raise HTTPException(status_code=502, detail=str(exc))
        except ValueError as exc:
            # total_structure_height too small for the given profiles.
            raise HTTPException(status_code=400, detail=str(exc))

        # Preserve any extra top-level fields the caller sent alongside
        # "grid" (e.g. future loading config), merged with the generated
        # grid/members/summary.
        extra_fields = request.model_dump(exclude={"grid"})
        return {**extra_fields, **result}

    @app.post("/api/v1/radiator_support/to_tekla")
    def to_tekla(request: ToTeklaRequest):
        # "members" is already the continuous-beam representation (see
        # generate_members()'s return shape).
        from radiator_support.tekla_integration import send_to_tekla
        payload = {"members": [m.model_dump(by_alias=True) for m in request.members],
                   'grid': request.grid,
                   'joint_groups': request.joint_groups}

        try:
            result = send_to_tekla(payload)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        return result

    @app.post("/api/v1/radiator_support/to_staad")
    async def to_staad(request: ToStaadRequest):
        from radiator_support.staad_integration import send_to_staad
        payload = {
            "grid": request.grid,
            "members_split_beams": [m.model_dump(by_alias=True) for m in request.members_split_beams],
            "load_configuration": request.load_configuration,
        }

        try:
            result = await send_to_staad(payload)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        return result

    # Idea statica integration endpoint is common across all civil structures.
    # Hence it is not prefixed with "radiator_support" or any other structure type in the URL.
    # curl -X POST http://localhost:8080/api/v1/to_ideastatica -H "Content-Type: application/json" -d @inner_node_1_longitudinal_1_transverse_bracing.json
    @app.post("/api/v1/to_ideastatica")
    def to_ideastatica(request: ToIdeaStaticaRequest):
        from common.idea_statica_integration import design_joint
        # exclude_none: design_joint() checks optional member_update keys
        # (gama_pitch, aligned_plate, related_plate) by presence, not value.
        payload = request.model_dump(exclude_none=True)

        try:
            result = design_joint(payload)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        return result

    # Mount the 8-Agent Autonomous Design Workflow Router
    try:
        from agentic.router import router as agentic_router
        app.include_router(agentic_router)
    except Exception as exc:
        print(f"Notice: Agentic router import: {exc}")

    return app


def start_api_server(port):
    try:
        import uvicorn
    except ImportError:
        print(
            "fastapi and uvicorn are required for --port "
            "(pip install fastapi uvicorn)",
            file=sys.stderr,
        )
        sys.exit(1)

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=port)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a beam/column/bracing grid from a JSON spec."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help=(
            "API server on 0.0.0.0:PORT exposing "
            "POST /api/v1/generate_radiator_support"
        ),
    )
    args = parser.parse_args()

    start_api_server(args.port)

if __name__ == "__main__":
    main()
