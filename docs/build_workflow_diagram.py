"""Build an offline SVG/HTML companion to workflow_diagram.md (stdlib only)."""
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class Drawing:
    def __init__(self, width, height, title):
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
                      '<defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4" orient="auto"><path d="M0 0L8 4L0 8" fill="#526780"/></marker></defs>',
                      '<style>text{font-family:Segoe UI,Arial,sans-serif;fill:#18304b} .edge{fill:none;stroke:#526780;stroke-width:2;marker-end:url(#arrow)} .note{font-size:15px} .label{font-size:19px;font-weight:600}</style>',
                      f'<rect width="{width}" height="{height}" fill="#f8fafc"/>']

    def box(self, x, y, w, h, title, lines=(), color='#e8f2ff'):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{color}" stroke="#8ba3bc"/>')
        self.text(x + w / 2, y + 29, title, 'label')
        for i, line in enumerate(lines):
            self.text(x + w / 2, y + 57 + i * 24, line)

    def text(self, x, y, text, cls='note'):
        self.parts.append(f'<text x="{x}" y="{y}" text-anchor="middle" class="{cls}">{escape(text)}</text>')

    def edge(self, points, label=None, at=None, dashed=False):
        coords = ' '.join(f'{x},{y}' for x, y in points)
        dash = ' stroke-dasharray="6 5"' if dashed else ''
        self.parts.append(f'<polyline points="{coords}" class="edge"{dash}/>')
        if label:
            x, y = at
            width = len(label) * 7.6 + 16
            self.parts.append(f'<rect x="{x-width/2}" y="{y-17}" width="{width}" height="23" fill="#f8fafc"/>')
            self.text(x, y, label)

    def finish(self):
        return '\n'.join(self.parts + ['</svg>'])


def main_flow():
    d = Drawing(1460, 2650, 'Civil Steel complete workflow, approval and bounded recovery')
    d.box(470, 25, 460, 78, 'INPUTS', ['Grid / sections / loads / per-run policy'])
    d.box(470, 135, 460, 76, 'Dashboard / form', ['Run complete workflow'])
    d.box(40, 135, 300, 100, 'Chat assistant', ['Questions / status / evidence', 'or request engineering actions'], '#fce8f2')
    d.box(40, 285, 300, 100, 'Input-change preview', ['User: Approve and Apply', 'Reject leaves inputs unchanged'], '#fce8f2')
    d.box(40, 435, 300, 124, 'Save approved inputs', ['Complete run → dispatch', 'Configuration only → saved', 'Preview / partial → guarded action'], '#fce8f2')
    d.edge([(470, 64), (190, 64), (190, 135)])
    d.edge([(190, 235), (190, 285)], 'Changes', (190, 267))
    d.edge([(190, 385), (190, 435)], 'Approved', (190, 418))
    d.box(470, 280, 460, 78, 'FastAPI dispatch', ['One desktop job at a time'])
    d.edge([(340, 490), (395, 490), (395, 319), (470, 319)])
    d.box(470, 405, 460, 78, 'Windows STA worker', ['Disposable process / COM and .NET / lock'])
    for a, b in [(103, 135), (211, 280), (358, 405), (483, 540)]:
        d.edge([(700, a), (700, b)])

    d.box(470, 540, 460, 78, 'A1 Requirements', ['Validate inputs / load basis / run policy'])
    d.box(470, 670, 460, 78, 'A2 Readiness', ['Saved STAAD + Tekla / IDEA / templates'])
    d.edge([(700, 618), (700, 670)], 'Valid', (700, 652))
    d.box(470, 800, 460, 140, 'A3 Frame', ['A3a Evaluate plan / profile catalogue / height', '↓', 'A3b Generate members / split beams / scope'], '#dcf4ff')
    d.edge([(700, 748), (700, 800)], 'Ready', (700, 782))
    d.box(470, 1010, 460, 190, 'A4 STAAD', ['A4a Build STAAD request', '↓ A4b Run OpenSTAAD / solver', '↓ A4c Frame grader', '↓ A4d Decision / correction', 'Optional LLM advice for permitted choices'], '#dcfce8')
    d.edge([(700, 940), (700, 1010)])
    d.edge([(470, 1165), (390, 1165), (390, 835), (470, 835)])
    d.box(40, 905, 290, 125, 'Loop A: frame correction', ['Permitted correction only', 'Remaining revision budget', 'Regenerate + recalculate'], '#eaf6ed')
    d.edge([(330, 965), (390, 965)], dashed=True)

    d.box(470, 1280, 460, 345, 'A5 IDEA + package audit', ['A5a Prepare next connection group', '↓ A5b Run IDEA REST calculation', '↓ A5c Grade joint result', '↓ A5d Decision / correction', 'Passed group → next group at A5a', 'Reuse eligible evidence / skip completed groups', 'All groups complete ↓', 'A5c Audit signatures / grades / materials', '↓ A5d Package recovery decision', 'Optional LLM advice for permitted choices'], '#fff0d9')
    d.edge([(700, 1200), (700, 1280)], 'Frame passed', (700, 1248))
    d.edge([(470, 1450), (390, 1450), (390, 1315), (470, 1315)])
    d.box(40, 1325, 290, 148, 'Loop B: connection correction', ['Failed group: apply permitted change', 'Re-enter A5 with bounded revisions', 'Passing groups advance normally', 'Selected changes retain their scope'], '#fff0d9')
    d.edge([(330, 1395), (390, 1395)], dashed=True)
    d.edge([(930, 1570), (1000, 1570), (1000, 1315), (930, 1315)])
    d.box(1060, 1330, 350, 124, 'Loop C: package recovery', ['Eligible recovery target: IDEA', 'Re-enter A5 / verify evidence again', 'Separate package recovery budget'], '#fff0d9')
    d.edge([(1060, 1390), (1000, 1390)], dashed=True)

    d.box(470, 1700, 460, 190, 'A6 Tekla', ['A6a Build validated Tekla payload', '↓ A6b Publish through Open API', '↓ A6c Member + connection read-back', '↓ A6d Decision / retry', 'Optional LLM advice: retry or stop'], '#eee7ff')
    d.edge([(700, 1625), (700, 1700)], 'Joints + package passed', (700, 1668))
    d.edge([(470, 1855), (390, 1855), (390, 1735), (470, 1735)])
    d.box(40, 1710, 290, 148, 'Permitted Tekla retry', ['Same validated payload', 'No structural section redesign', 'Retry only within allowed budget', 'Other failures stop for inspection'], '#eee7ff')
    d.edge([(330, 1780), (390, 1780)], dashed=True)

    d.box(470, 1970, 460, 78, 'A7 Report package', ['Assemble verified report and evidence'])
    d.edge([(700, 1890), (700, 1970)], 'Publication verified', (700, 1938))
    d.box(470, 2120, 460, 100, 'Human Gate 3', ['PAUSED_AT_GATE3', 'Wait for engineer acceptance'], '#fff3cc')
    d.edge([(700, 2048), (700, 2120)])
    d.box(470, 2295, 460, 100, 'Accepted', ['Acceptance API / UI validates package', 'Record engineer identity and timestamp'], '#e3f5e7')
    d.edge([(700, 2220), (700, 2295)], 'Engineer accepts', (700, 2265))
    d.box(470, 2470, 460, 78, 'COMPLETED', ['Accepted report / saved run evidence'], '#e3f5e7')
    d.edge([(700, 2395), (700, 2470)])

    d.box(1060, 80, 350, 196, 'Deep Agents: default ON', ['Wrapper around each A1–A7 stage', 'Plan → execute stage once → review', 'Coded tools and graders remain authoritative', 'No unrestricted subagent delegation', 'Wrapper error → FAILED; no direct fallback', 'Disabled → run coded stage directly'], '#e4efff')
    d.box(1060, 330, 350, 172, 'Shared evaluation rules', ['Per-run RunPolicy thresholds', 'Geometry and frame / joint graders', 'Package evidence + identity checks', 'Tekla publication checker', 'Native solver checks remain required'])
    d.box(1060, 565, 350, 148, 'Saved state and evidence', ['Workflow state / SQLite', 'Audit events / stage timings', 'Per-run archive / native evidence', 'Archive warnings remain visible'])
    d.box(1060, 790, 350, 172, 'Two different AI failure behaviors', ['Deep Agent failure: stop workflow', 'Correction advisor unavailable / invalid:', 'use deterministic permitted default', 'LLM calls and retries have limits', 'Chat has its own enable switch'])
    d.box(1060, 1730, 350, 196, 'Failure exit applies to every stage', ['Invalid inputs / readiness / tool error', 'Invalid or stale evidence', 'Unsupported recovery / advisor stop', 'Retry or model-call budget exhausted', '→ FAILED / stopped', 'Inspect saved evidence before a new run'], '#ffe6e6')
    # A shared failure rail keeps explicit exits visible without separate crossing routes.
    d.parts.append('<path d="M1030 579 V2009" fill="none" stroke="#a85b67" stroke-width="2" stroke-dasharray="6 5"/>')
    for y in (579, 709, 900, 1105, 1610, 1795, 2009):
        d.edge([(930, y), (1030, y)], dashed=True)
    d.edge([(1030, 1830), (1060, 1830)], dashed=True)
    d.box(40, 2130, 300, 148, 'Other chat paths', ['Selected-connection changes:', 'see the scoped diagram below', 'Questions do not dispatch engineering', 'Partial actions require current evidence'], '#fce8f2')
    d.text(730, 2600, 'Main complete-workflow path • current A1–A7 layout • supporting lines/notes are not extra execution stages')
    return d.finish()


def selected_flow():
    d = Drawing(1460, 850, 'Selected-connection changes with scope, approval, validation and optional publication')
    d.box(40, 40, 350, 125, 'Propose selected joint changes', ['Read existing values / exact group scope', 'Preview and template fingerprints', 'Reject → no edits applied'], '#fce8f2')
    d.box(540, 40, 370, 125, 'User approves / validate proposal', ['Current passing STAAD evidence', 'Unchanged templates / same desktop lock', 'Invalid / stale evidence → FAILED'])
    d.edge([(390, 100), (540, 100)], 'Approve', (465, 90))
    d.box(540, 235, 370, 125, 'A5 selected calculations', ['Reuse only eligible approved calculations', 'Otherwise calculate / grade selected groups', 'Verify exact selected values'])
    d.edge([(725, 165), (725, 235)])
    d.box(540, 430, 370, 125, 'Verify package and approved scope', ['Failure / broader recovery → stop', 'Full-model rebuild is not substituted', 'Publish only when explicitly requested'])
    d.edge([(725, 360), (725, 430)])
    d.box(40, 650, 350, 100, 'READY', ['Calculations + package validated', 'Tekla publication not requested'], '#e3f5e7')
    d.edge([(540, 495), (215, 495), (215, 650)], 'No publication', (295, 484))
    d.box(1010, 430, 390, 125, 'Scoped Tekla connection updates', ['Read back selected connection changes', 'Failure → stop / inspect', 'No full-model rebuild'], '#eee7ff')
    d.edge([(910, 490), (1010, 490)], 'Publish', (960, 480))
    d.box(1010, 650, 390, 100, 'A7 report / Human Gate 3', ['Wait for engineer acceptance', 'Accepted → COMPLETED'], '#fff3cc')
    d.edge([(1205, 555), (1205, 650)])
    return d.finish()


if __name__ == '__main__':
    main = main_flow()
    selected = selected_flow()
    (ROOT / 'workflow_diagram.svg').write_text(main, encoding='utf-8')
    (ROOT / 'selected_connection_workflow.svg').write_text(selected, encoding='utf-8')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Civil Steel — implemented workflow</title><style>
body{margin:0;background:#f1f5f9;color:#18304b;font:16px/1.6 "Segoe UI",Arial,sans-serif}main{max-width:1460px;margin:auto;padding:28px}h1,h2{line-height:1.2}p{max-width:1000px}.sheet{background:white;border:1px solid #cbd5e1;border-radius:14px;overflow:auto;margin:24px 0}.sheet svg{display:block;width:100%;height:auto;min-width:820px}button,a{color:#174c8c}button{padding:8px 16px;cursor:pointer}body.zoom .sheet svg{width:1460px}nav{display:flex;gap:20px;align-items:center;flex-wrap:wrap}@media print{nav{display:none}main{padding:0}.sheet{overflow:visible;break-before:page}.sheet svg{min-width:0;width:100%}body{background:white}}
</style><main><h1>Civil Steel: implemented engineering workflow</h1>
<p>Current A1–A7 control flow, including AI enabled by default, approval, correction loops and failure exits. Approved input changes requesting a complete run still pass A1 validation before the graph enters A2. Configuration-only and individual-stage chat actions follow their requested scope.</p>
<nav><button onclick="document.body.classList.toggle('zoom')">Toggle full-size view</button><button onclick="window.print()">Print / save PDF</button><a href="workflow_diagram.svg" download>Download main SVG</a><a href="selected_connection_workflow.svg" download>Download selected-change SVG</a><a href="workflow_diagram.md">Editable Mermaid and source map</a></nav>
<h2>Complete workflow</h2>MAIN
<h2>Selected-connection changes</h2>SELECTED
<p>These diagrams describe implemented routing, not live solver verification. Partial chat actions can finish at READY; an unverified geometry preview does not establish engineering approval. See the companion Markdown for detailed routing, configuration switches and source files.</p></main></html>'''
    page = page.replace('MAIN', '<div class="sheet">' + main + '</div>').replace('SELECTED', '<div class="sheet">' + selected + '</div>')
    (ROOT / 'workflow_diagram.html').write_text(page, encoding='utf-8')
    print('Built workflow_diagram.html and two SVG diagrams.')
