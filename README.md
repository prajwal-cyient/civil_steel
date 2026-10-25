# civil-steel-standard-web
Web App to design Steel Standard structures in Tekla, Staad.Pro and Idea Statica

## STAAD results viewer

After **Send to STAAD**, the 3D preview is replaced with the returned
`member_data_by_id` geometry in STAAD's Y-up coordinate system. Member colours
represent their `class`; pyramids mark `support_nodes`. The load-case selector
combines the cases from all load envelopes and displays their `load_case_title`.
The STAAD view starts rotated 180 degrees horizontally, with column indices
shown along all four edges of the ground grid, offset beyond the support icons.
Longitudinal indices are numeric; transverse (Y) indices use spreadsheet-style
letters: A through Z, then AA, AB, and so on, in both preview and STAAD views.

Hover over a member for its metadata, name-derived type, start/end forces and
any support reactions for the selected case. Hover over a support joint for its
reactions. Force and moment units are taken from the response. Click a joint to
highlight the member ends reported by `joint_member_ends`; click empty space to
clear the selection. Generating a new structure resets the STAAD view.

Run the results UI unit tests with `node --test tests\test_staad_results.cjs`.
