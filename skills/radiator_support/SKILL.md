# Skill Card: Parametric Steel Frame — JSON Geometry Generator

## Purpose
Generate a parametric steel frame structure as a flat JSON member list,
using only primitive Python types as inputs and outputs.
No VIKTOR SDK types in the data model — pure dicts, lists, ints, floats,
strings, bools.  The JSON can be consumed by Tekla Structures, IFC writers,
Grasshopper, or any other downstream tool.

---

## Input Schema (all primitives)

params = {
    # Grid & Layout
    "n_long_bays":   int,    # number of longitudinal bays,   e.g. 21
    "bay_long_mm":   float,  # longitudinal bay spacing (mm), e.g. 2600.0
    "n_trans_bays":  int,    # number of transverse bays,     e.g. 3
    "bay_trans_mm":  float,  # transverse bay spacing (mm),   e.g. 3300.0
    "col_height_mm": float,  # column height (mm),            e.g. 5000.0

    # Section sizes (string keys from lookup table)
    "col_section":          str,  # e.g. "CFRHS120×120×5"
    "beam_section":         str,  # e.g. "HEA140"  (used for both long & trans beams)
    "long_brace_section":   str,  # e.g. "CFRHS100×100×4"
    "trans_brace_section":  str,  # e.g. "CFRHS100×100×4"

    # Longitudinal bracing — up to 4 independent single-diagonal slots
    "long_bracings": [
        {
            "bay":    int,   # 0-based bay index along X (bay 1 = index 0)
            "mirror": bool,  # False = ↗ bottom-left→top-right
                             # True  = ↘ top-left→bottom-right
        }
    ],

    # Transverse bracing
    "trans_bracing_spans":  [int],  # 0-based span indices (span 1 = A→B = index 0)
    "trans_bracing_mirror": bool,   # False = top-left→ground-right
                                    # True  = ground-left→top-right
}

---

## Output Schema (JSON member list)

```json
{
  "grid": {
    "n_long_bays":   21,
    "bay_long_mm":   2600.0,
    "n_trans_bays":  3,
    "bay_trans_mm":  3300.0,
    "col_height_mm": 5000.0
  },
  "sections": {
    "column":      "CFRHS120×120×5",
    "long_beam":   "HEA140",
    "trans_beam":  "HEA140",
    "long_brace":  "CFRHS100×100×4",
    "trans_brace": "CFRHS100×100×4"
  },
  "members": [
    {
      "type":     "column",
      "p1":       [0.0, 0.0, 0.0],
      "p2":       [0.0, 0.0, 5000.0],
      "section":  "CFRHS120×120×5",
      "material": "S355"
    },
    {
      "type":     "long_beam",
      "p1":       [0.0, 0.0, 5000.0],
      "p2":       [2600.0, 0.0, 5000.0],
      "section":  "HEA140",
      "material": "S355"
    },
    {
      "type":     "trans_beam",
      "p1":       [0.0, 0.0, 5000.0],
      "p2":       [0.0, 3300.0, 5000.0],
      "section":  "HEA140",
      "material": "S355"
    },
    {
      "type":     "long_brace",
      "p1":       [0.0, 0.0, 0.0],
      "p2":       [2600.0, 0.0, 5000.0],
      "section":  "CFRHS100×100×4",
      "material": "S355"
    },
    {
      "type":     "trans_brace",
      "p1":       [0.0, 0.0, 5000.0],
      "p2":       [0.0, 3300.0, 0.0],
      "section":  "CFRHS100×100×4",
      "material": "S355"
    }
  ]
}

### Point format
[x, y, z]  — all values in millimetres (mm), float
x = longitudinal axis  (along bay_long_mm)
y = transverse axis    (along bay_trans_mm)
z = vertical axis      (up, T.O.F. = 0, T.O.S. = col_height_mm)

---

## Member Generation Logic (pure Python, no SDK)

def build_frame_json(params: dict) -> dict:
    """Generate a flat member list for a parametric steel frame.
    All coordinates in mm.  No VIKTOR types used.
    """
    nl   = params["n_long_bays"]
    bl   = params["bay_long_mm"]
    nt   = params["n_trans_bays"]
    bt   = params["bay_trans_mm"]
    h    = params["col_height_mm"]

    col_sec    = params["col_section"]
    beam_sec   = params["beam_section"]
    lbr_sec    = params["long_brace_section"]
    tbr_sec    = params["trans_brace_section"]

    xs = [i * bl for i in range(nl + 1)]
    ys = [j * bt for j in range(nt + 1)]
    members = []

    # Columns
    for xi in xs:
        for yi in ys:
            members.append({"type":"column",
                "p1":[xi,yi,0.0], "p2":[xi,yi,h],
                "section":col_sec, "material":"S355"})

    # Longitudinal beams (top chord, along X)
    for yi in ys:
        for i in range(nl):
            members.append({"type":"long_beam",
                "p1":[xs[i],yi,h], "p2":[xs[i+1],yi,h],
                "section":beam_sec, "material":"S355"})

    # Transverse beams (top chord, along Y)
    for xi in xs:
        for j in range(nt):
            members.append({"type":"trans_beam",
                "p1":[xi,ys[j],h], "p2":[xi,ys[j+1],h],
                "section":beam_sec, "material":"S355"})

    # Longitudinal bracing (XZ plane)
    for entry in params["long_bracings"]:
        bay_i  = entry["bay"]    # 0-based
        mirror = entry["mirror"]
        x1, x2 = xs[bay_i], xs[bay_i + 1]
        for yi in ys:
            if mirror:
                p1, p2 = [x1,yi,h],   [x2,yi,0.0]   # ↘
            else:
                p1, p2 = [x1,yi,0.0], [x2,yi,h]     # ↗
            members.append({"type":"long_brace",
                "p1":p1, "p2":p2,
                "section":lbr_sec, "material":"S355"})

    # Transverse bracing (YZ plane)
    for span_j in params["trans_bracing_spans"]:
        mirror = params["trans_bracing_mirror"]
        y1, y2 = ys[span_j], ys[span_j + 1]
        for xi in xs:
            if mirror:
                p1, p2 = [xi,y1,0.0], [xi,y2,h]     # ground-left → top-right
            else:
                p1, p2 = [xi,y1,h],   [xi,y2,0.0]   # top-left → ground-right
            members.append({"type":"trans_brace",
                "p1":p1, "p2":p2,
                "section":tbr_sec, "material":"S355"})

    return {
        "grid":    {"n_long_bays":nl,"bay_long_mm":bl,
                    "n_trans_bays":nt,"bay_trans_mm":bt,"col_height_mm":h},
        "sections":{"column":col_sec,"long_beam":beam_sec,
                    "trans_beam":beam_sec,"long_brace":lbr_sec,"trans_brace":tbr_sec},
        "members": members,
    }

---

## Section Lookup Tables (string → (width_m, height_m) for 3D rendering)

BEAM_SECTIONS = {
    "HEA140": (0.140, 0.133),   # flange_width=140 mm, total_depth=133 mm
    "HEA160": (0.160, 0.152),
}

COLUMN_SECTIONS = {
    "CFRHS100×100×4": (0.100, 0.100),
    "CFRHS100×100×5": (0.100, 0.100),
    "CFRHS120×120×4": (0.120, 0.120),
    "CFRHS120×120×5": (0.120, 0.120),
}

BRACE_SECTIONS = {
    "CFRHS100×100×4": (0.100, 0.100),
    "CFRHS100×100×5": (0.100, 0.100),
    "CFRHS120×120×4": (0.120, 0.120),
    "CFRHS120×120×5": (0.120, 0.120),
    "CFRHS140×140×4": (0.140, 0.140),
    "CFRHS140×140×6": (0.140, 0.140),
}

### Profile name mapping (VIKTOR → Tekla)
def to_tekla_profile(viktor_name: str) -> str:
    """Replace Unicode × (U+00D7) with ASCII * for Tekla profile catalogue."""
    return viktor_name.replace("×", "*")

# Examples
# CFRHS120×120×5  →  CFRHS120*120*5
# HEA140          →  HEA140  (unchanged)

---

## Coordinate System

        Z (up)
        │
        │
        └──── X (longitudinal, along bays)
       /
      Y (transverse)

Origin:  column grid point (1, A) at (0, 0, 0)
T.O.F.:  z = 0.0              (top of foundation / base plate)
T.O.S.:  z = col_height_mm    (top of steel)
Units:   millimetres (mm) throughout

---

## Tekla Open API Mapping

| JSON field      | Tekla API property                          |
|-----------------|---------------------------------------------|
| `p1`            | `Point(p1[0], p1[1], p1[2])`               |
| `p2`            | `Point(p2[0], p2[1], p2[2])`               |
| `section`       | `beam.Profile.ProfileString`  (use `*`)    |
| `material`      | `beam.Material.MaterialString`             |
| `type=column`   | `beam.Type = Beam.BeamTypeEnum.COLUMN`     |
| `type=*_beam`   | `beam.Type = Beam.BeamTypeEnum.BEAM`       |
| `type=*_brace`  | `beam.Type = Beam.BeamTypeEnum.BEAM`       |

---

## VIKTOR Field → Primitive Type Mapping

| VIKTOR field type       | Primitive type  | Example value                        |
|-------------------------|-----------------|--------------------------------------|
| `vkt.NumberField`       | `float`         | `2600.0`                             |
| `vkt.IntegerField`      | `int`           | `21`                                 |
| `vkt.OptionField`       | `str`           | `"HEA140"`                           |
| `vkt.BooleanField`      | `bool`          | `True`                               |
| `vkt.MultiSelectField`  | `list[str]`     | `["2", "3"]`                         |
| `vkt.DynamicArray`      | `list[dict]`    | `[{"bay": 6, "mirror": False}]`      |
| `vkt.Line`              | `{"p1": [x,y,z], "p2": [x,y,z]}` | —              |
| `vkt.Point`             | `[x, y, z]`    | `[0.0, 0.0, 5000.0]`                 |
| `vkt.Color`             | `[r, g, b]`    | `[255, 165, 0]`                      |
| `vkt.Material`          | `str`           | `"S355"`                             |

---

## VIKTOR App Architecture

┌─────────────────────────────────────────────────────────┐
│  VIKTOR App  (app.py)                                   │
│                                                         │
│  Parametrization                                        │
│  ├── Grid & Layout      (int, float fields)             │
│  ├── Longitudinal Bracing (bool + option fields × 4)   │
│  ├── Transverse Bracing  (multiselect + bool)           │
│  ├── Sections            (option fields × 4)            │
│  └── Export                                             │
│       ├── ⬇ Download frame JSON   → frame_data.json    │
│       └── ⬇ Download worker script → run_tekla_model.py│
│                                                         │
│  Controller                                             │
│  ├── view_geometry()     → GeometryView (3D)            │
│  ├── view_data()         → WebView (SVG drawings)       │
│  ├── _build_frame_json() → dict (shared helper)         │
│  ├── download_frame_json()   → DownloadResult (.json)   │
│  └── download_worker_script() → DownloadResult (.py)   │
└─────────────────────────────────────────────────────────┘
         │ frame_data.json
         ▼
┌─────────────────────────────────────────────────────────┐
│  Worker machine  (where Tekla Structures is installed)  │
│                                                         │
│  run_tekla_model.py                                     │
│  ├── reads frame_data.json                              │
│  ├── connects to open Tekla model via Open API          │
│  ├── inserts Beam / Column objects (native, not dumb)   │
│  ├── calls model.CommitChanges()                        │
│  └── writes output.json  { status, members_inserted }  │
└─────────────────────────────────────────────────────────┘

---

## Worker config.yaml (on worker machine)

```yaml
executables:
  run_tekla:
    path: "C:\Python311\python.exe"
    arguments:
      - "run_tekla_model.py"
    workingDirectoryPath: "C:\TeklaWorker"
maxParallelProcesses: 1

> The `executable_key` in `GenericAnalysis(executable_key="run_tekla")` must
> match the key in `config.yaml` exactly.

---

## Example: Full Default Input

default_params = {
    "n_long_bays":   21,
    "bay_long_mm":   2600.0,
    "n_trans_bays":  3,
    "bay_trans_mm":  3300.0,
    "col_height_mm": 5000.0,
    "col_section":          "CFRHS120×120×5",
    "beam_section":         "HEA140",
    "long_brace_section":   "CFRHS100×100×4",
    "trans_brace_section":  "CFRHS100×100×4",
    "long_bracings": [
        {"bay": 5,  "mirror": False},   # bay 6 (1-based) = index 5
        {"bay": 6,  "mirror": True},    # bay 7 (1-based) = index 6
        {"bay": 15, "mirror": False},   # bay 16 (1-based) = index 15
        {"bay": 16, "mirror": True},    # bay 17 (1-based) = index 16
    ],
    "trans_bracing_spans":  [1],        # span 2 (1-based) = B→C = index 1
    "trans_bracing_mirror": False,
}

frame_json = build_frame_json(default_params)
# → 276 members:
#   88 columns  (22 × 4)
#   84 long beams  (21 × 4)
#   88 trans beams (22 × 3 + ... wait: 22 × 3 = 66, but 4 rows × 21 = 84)
#   Actually: columns=(nl+1)*(nt+1)=22*4=88, long_beams=nl*(nt+1)=21*4=84,
#             trans_beams=(nl+1)*nt=22*3=66, long_braces=4*(nt+1)=4*4=16,
#             trans_braces=1*(nl+1)=1*22=22  → total = 276

---

## Drawing Types Reproduced in WebView

| Drawing       | Description                                                        |
|---------------|--------------------------------------------------------------------|
| 3D Axonometric | Cabinet-projection SVG matching Tekla/CAD isometric style         |
| LINE 1        | Transverse cross-section elevation with bracing, bubble labels,    |
|               | elevation tags (T.O.F. ±0.000 / T.O.F.+X.XXX), dimension chains  |
| LINE A–D      | Longitudinal elevation per transverse grid line, all bays + bracing|
| Section sheets | Grid of per-section elevation drawings (20 panels per sheet)      |

---

## Key Engineering Concepts

| Concept                  | Implementation                                              |
|--------------------------|-------------------------------------------------------------|
| Column grid              | (n_long+1) × (n_trans+1) nodes, fully parametric spacing   |
| Longitudinal bracing     | Single diagonal per bay, applied on ALL transverse rows     |
| Transverse bracing       | Single diagonal per column line in selected spans           |
| Bracing orientation      | Normal ↗ or Mirror ↘ controlled by bool flag               |
| Section hierarchy        | Separate profiles for columns, beams, long/trans braces     |
| Elevation reference      | T.O.F. at z=0; T.O.S. at z=col_height_mm                  |
| Grid naming              | Numbers 1…N+1 longitudinal, letters A…D transverse         |
| Profile name convention  | VIKTOR uses × (U+00D7); Tekla requires * (U+002A)          |
