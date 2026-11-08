// Hardcoded IDEA StatiCa request defaults, copied from
// inner_node_1_longitudinal_1_transverse_bracing.json.
// member_updates and load_effects here are only a reference sample: the UI
// replaces them with the STAAD response's
// envelope_load_cases_by_joint_group[envelopeId][jointGroup].
const IDEA_STATICA_DEFAULTS = {
  "template_project_path": "sample_projects/inner_node_1_longitudinal_1_transverse_bracing.ideaCon",
  "connection_selector": {
    "name": "CON1"
  },
  "member_updates": [
    {
      "member_name": "column",
      "cross_section_mprl_name": "SHS100/100/4.0",
      "material_mprl_name": "S355"
    },
    {
      "member_name": "beam_longitudinal",
      "cross_section_mprl_name": "HEA140",
      "material_mprl_name": "S355"
    },
    {
      "member_name": "beam_transverse_placeholder",
      "cross_section_mprl_name": "HEA140",
      "material_mprl_name": "S355",
      "aligned_plate": {
        "plate_side": "lowerSide",
        "member": "beam_transverse",
        "part_type": "bottomFlange"
      },
      "related_plate": {
        "plate_side": "upperSide",
        "member": "beam_longitudinal",
        "part_type": "topFlange"
      }
    },
    {
      "member_name": "bracing_longitudinal",
      "cross_section_mprl_name": "SHS100/100/5.0",
      "material_mprl_name": "S355",
      "gama_pitch": -65
    },
    {
      "member_name": "bracing_transverse",
      "cross_section_mprl_name": "SHS100/100/5.0",
      "material_mprl_name": "S355",
      "gama_pitch": -40
    }
  ],
  "load_effects": [
    {
      "name": "LC1 - generated",
      "member_loadings": [
        {
          "member_name": "column",
          "n": -19.365,
          "vy": 0.1,
          "vz": -0.005,
          "mx": 0.006,
          "my": 0.0,
          "mz": 0.0,
          "position": "End"
        },
        {
          "member_name": "beam_longitudinal",
          "n": 6.53,
          "vy": 1.76,
          "vz": -0.16,
          "mx": 0.0,
          "my": 0.0,
          "mz": 0.6,
          "position": "Begin"
        },
        {
          "member_name": "beam_longitudinal",
          "n": 2.488,
          "vy": -1.842,
          "vz": 0.016,
          "mx": 0.0,
          "my": 0.017,
          "mz": 0.81,
          "position": "End"
        },
        {
          "member_name": "beam_transverse",
          "n": 2.167,
          "vy": 0.25,
          "vz": -0.01,
          "mx": -0.0001,
          "my": 0.0147,
          "mz": 0.1235,
          "position": "Begin"
        },
        {
          "member_name": "beam_transverse",
          "n": 4.551,
          "vy": 0.202,
          "vz": -0.012,
          "mx": 0.0,
          "my": 0.0001,
          "mz": 0.106,
          "position": "End"
        },
        {
          "member_name": "bracing_longitudinal",
          "n": 23.6,
          "vy": 0.152,
          "vz": 0.0,
          "mx": 0.0,
          "my": 0.0,
          "mz": 0.0,
          "position": "End"
        },
        {
          "member_name": "bracing_transverse",
          "n": -5.92,
          "vy": 0.193,
          "vz": 0.0,
          "mx": 0.0,
          "my": 0.0,
          "mz": 0.0,
          "position": "End"
        }
      ]
    },
    {
      "name": "LC2 - generated",
      "member_loadings": [
        {
          "member_name": "column",
          "n": -29.365,
          "vy": 0.1,
          "vz": -0.005,
          "mx": 0.006,
          "my": 0.0,
          "mz": 0.0,
          "position": "End"
        },
        {
          "member_name": "beam_longitudinal",
          "n": 16.53,
          "vy": 1.76,
          "vz": -0.16,
          "mx": 0.0,
          "my": 0.0,
          "mz": 0.6,
          "position": "Begin"
        },
        {
          "member_name": "beam_longitudinal",
          "n": 12.488,
          "vy": -1.842,
          "vz": 0.016,
          "mx": 0.0,
          "my": 0.017,
          "mz": 0.81,
          "position": "End"
        },
        {
          "member_name": "beam_transverse",
          "n": 12.167,
          "vy": 0.25,
          "vz": -0.01,
          "mx": -0.0001,
          "my": 0.0147,
          "mz": 0.1235,
          "position": "Begin"
        },
        {
          "member_name": "beam_transverse",
          "n": 14.551,
          "vy": 0.202,
          "vz": -0.012,
          "mx": 0.0,
          "my": 0.0001,
          "mz": 0.106,
          "position": "End"
        },
        {
          "member_name": "bracing_longitudinal",
          "n": 23.6,
          "vy": 0.152,
          "vz": 0.0,
          "mx": 0.0,
          "my": 0.0,
          "mz": 0.0,
          "position": "End"
        },
        {
          "member_name": "bracing_transverse",
          "n": -15.92,
          "vy": 0.193,
          "vz": 0.0,
          "mx": 0.0,
          "my": 0.0,
          "mz": 0.0,
          "position": "End"
        }
      ]
    }
  ],
  "output_dir": "output_CON1",
  "save_project_path": "output_CON1/CON1_designed.ideaCon"
};

// Operation parameters exposed by each joint group's template
// (sample_projects/<joint group>.ideaCon), keyed by joint group name.
// They only seed the editable inputs in the "Idea Statica Connection Design"
// section; the request uses the UI values. Joint groups not listed here
// show no parameters.
const IDEA_STATICA_OPERATION_PARAMETERS_BY_JOINT_GROUP = {
  "inner_node_1_longitudinal_1_transverse_bracing": [
    {
      "key": "GRD_d",
      "description": "Grid bolt dia",
      "expression": "0.016"
    },
    {
      "key": "EP_d",
      "description": "Endplate bolt dia",
      "expression": "0.016"
    },
    {
      "key": "EP_pl_th",
      "description": "Endplate thickness",
      "expression": "0.015"
    },
    {
      "key": "Stiff_th",
      "description": "Stiffener thickness",
      "expression": "0.01"
    },
    {
      "key": "Stiff_sel",
      "description": "Stiffener requirement",
      "expression": "True"
    },
    {
      "key": "EP_weld",
      "description": "Endplate to column Weld sizes",
      "expression": "0.005"
    },
    {
      "key": "Stiff_bot_weld(web)",
      "description": "Bottom stiffener Web Weld sizes",
      "expression": "0.005"
    },
    {
      "key": "Stiff_bot_weld(flange)",
      "description": "Bottom stiffener flange Weld sizes",
      "expression": "0.005"
    },
    {
      "key": "Stiff_top_weld(web)",
      "description": "Top Stiffener web Weld sizes",
      "expression": "0.005"
    },
    {
      "key": "Stiff_top_weld(flange)",
      "description": "Top Stiffener flange Weld sizes",
      "expression": "0.005"
    },
    {
      "key": "theta",
      "description": "theta of bracing with column",
      "expression": "0.5235987755982991"
    },
    {
      "key": "CPL_d",
      "description": "Gusset bolt dia (Longitudinal)",
      "expression": "0.016"
    },
    {
      "key": "CPL_th",
      "description": "Gusset and tongue plate thickness (Longitudinal)",
      "expression": "0.012"
    },
    {
      "key": "CPL_weld",
      "description": "Weld sizes (Longitudinal)",
      "expression": "0.005"
    },
    {
      "key": "CPL_overlap_length",
      "description": "CPL Overlap (Longitudinal)",
      "expression": "0.1"
    },
    {
      "key": "CPL_tongue_weld",
      "description": "Weld sizes (Longitudinal)",
      "expression": "0.005"
    },
    {
      "key": "theta2",
      "description": "theta of bracing with column",
      "expression": "0.7853981633974484"
    },
    {
      "key": "CPL2_d",
      "description": "Gusset bolt dia (Transverse)",
      "expression": "0.016"
    },
    {
      "key": "CPL2_th",
      "description": "Gusset and tongue plate thickness (Transverse)",
      "expression": "0.012"
    },
    {
      "key": "CPL2_weld",
      "description": "Weld sizes (Transverse)",
      "expression": "0.005"
    },
    {
      "key": "CPL2_tongue_weld",
      "description": "Weld sizes (Transverse)",
      "expression": "0.005"
    },
    {
      "key": "CPL2_overlap_length",
      "description": "CPL Overlap (Transverse)",
      "expression": "0.1"
    },
    {
      "key": "Bolt_Type",
      "description": "Bolt Type",
      "expression": "M16 8.8"
    }
  ],
  "inner_node_1_longitudinal_bracing": [
    {
      "key": "GRD_d",
      "description": "Grid bolt dia",
      "expression": "0.016"
    },
    {
      "key": "EP_d",
      "description": "Endplate bolt dia",
      "expression": "0.016"
    },
    {
      "key": "EP_pl_th",
      "description": "Endplate thickness",
      "expression": "0.012"
    },
    {
      "key": "Stiff_th",
      "description": "Stiffener thickness",
      "expression": "0.008"
    },
    {
      "key": "Stiff_sel",
      "description": "Stiffener requirement",
      "expression": "True"
    },
    {
      "key": "EP_weld",
      "description": "Endplate to column Weld sizes",
      "expression": "0.005"
    },
    {
      "key": "Stiff_bot_weld(web)",
      "description": "Bottom stiffener Web Weld sizes",
      "expression": "0.005"
    },
    {
      "key": "Stiff_bot_weld(flange)",
      "description": "Bottom stiffener flange Weld sizes",
      "expression": "0.008"
    },
    {
      "key": "Stiff_top_weld(web)",
      "description": "Top Stiffener web Weld sizes",
      "expression": "0.005"
    },
    {
      "key": "Stiff_top_weld(flange)",
      "description": "Top Stiffener flange Weld sizes",
      "expression": "0.005"
    },
    {
      "key": "theta",
      "description": "theta of bracing with column",
      "expression": "0.5235987755982988"
    },
    {
      "key": "CPL_d",
      "description": "Gusset bolt dia",
      "expression": "0.016"
    },
    {
      "key": "CPL_th",
      "description": "Gusset and tongue plate thickness",
      "expression": "0.012"
    },
    {
      "key": "CPL_weld",
      "description": "CPL Weld sizes",
      "expression": "0.008"
    },
    {
      "key": "CPL_tongue_weld",
      "description": "CPL Weld tongue sizes",
      "expression": "0.008"
    },
    {
      "key": "CPL_overlap_length",
      "description": "CPL Overlap",
      "expression": "0.1"
    }
  ]
};
