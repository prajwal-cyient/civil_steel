const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function setup({ states = [], active = null, search = '', history = [] } = {}) {
  const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'static', 'radiator_support', 'index.html'), 'utf8');
  const start = html.indexOf('const agenticRunBtn =');
  const end = html.indexOf('\nif (agenticRunBtn) {', start);
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { style: {}, hidden: false, disabled: false, value: '', textContent: '', className: '',
      children: [], handlers: {}, addEventListener(event, callback) { this.handlers[event] = callback; },
      classList: { add() {} }, replaceChildren(...nodes) { this.children = nodes; this.value = ''; }, append(node) { this.children.push(node); },
    });
    return elements.get(id);
  };
  const calls = [], geometry = [], staad = [], idea = [], summary = [], urls = [];
  const location = { href: 'http://localhost:8060/radiator_support' + search, search };
  const context = vm.createContext({
    URL, URLSearchParams, window: { location, history: { replaceState(_, __, url) { urls.push(String(url)); location.href = String(url); location.search = url.search; } } },
    document: { getElementById: element, createElement: tag => ({ tag, value: '', textContent: '' }) },
    genBtn: element('generate-btn'), sendStaadBtn: element('send-staad-btn'), updateTeklaBtn: element('update-tekla-btn'), sendIdeaBtn: element('send-idea-btn'),
    statusMsg: element('status-msg'), staadStatus: element('staad-status'), ideaStatus: element('idea-status'), teklaStatus: element('tekla-status'),
    lastGeneratedResult: null, staadViewResult: null, revCounter: 0,
    clearStructureView() { context.staadViewResult = null; },
    renderStructure(members, result) { geometry.push([members, result]); context.staadViewResult = result; },
    renderSummary(value) { summary.push(value); }, memberName2type: () => 'column',
    renderStaadResults: result => staad.push(result), renderIdeaStaticaGroups: results => idea.push(results), renderTeklaResults() {},
    setTimeout: callback => callback(),
    fetch: async (url, options) => {
      calls.push([url, options]);
      const body = url.includes('?limit=') ? history : url.endsWith('/active') ? (Array.isArray(active) ? active.shift() : active) : states.shift();
      return { ok: true, json: async () => body };
    },
  });
  vm.runInContext(html.slice(start, end), context);
  return { context, calls, geometry, staad, idea, summary, urls, element };
}

function state(agent = 'A3', overrides = {}) {
  return { run_id: 'run_saved', status: 'RUNNING', current_agent: agent, design_generation: 1,
    grid_config: { col_height_mm: 5000 }, frame_candidate: { members: [{ name: 'column', p1: [0, 0, 0], p2: [0, 0, 5000] }], summary: { total_members: 1 } },
    agent_statuses: { A3: { status: 'PASSED' }, ...(agent !== 'A3' ? { A4: { status: 'PASSED' } } : {}) }, ...overrides };
}
const staadResult = () => ({ created: 1, candidate_signature: 'verified-frame', member_data_by_id: { 900001: { name: 'column', start: 1, end: 2 } } });

function chatBridge(ui) {
  const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'static', 'radiator_support', 'index.html'), 'utf8');
  const start = html.indexOf('// Small integration bridge:');
  ui.context.buildPayload = () => ({ grid: {} });
  ui.context.buildLoadConfiguration = () => ({});
  vm.runInContext(html.slice(start, html.indexOf('</script>', start)), ui.context);
  return ui.context.window.structuralChatBridge;
}

test('manual sign-off displays success only after backend confirmation', async () => {
  const ui = setup();
  const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'static', 'radiator_support', 'index.html'), 'utf8');
  const start = html.indexOf('\nif (gate3AcceptBtn) {');
  vm.runInContext(html.slice(start, html.indexOf('// Small integration bridge:', start)), ui.context);
  ui.context.renderAgenticRunState(state('A8', { status: 'PAUSED_AT_GATE3' }));
  const errors = [];
  ui.context.alert = text => errors.push(text);
  const button = ui.element('gate3-accept-btn');
  button.textContent = 'Accept & Sign Off';
  ui.context.fetch = async () => ({ ok: false, json: async () => ({ detail: 'Package cannot be accepted.' }) });
  await button.handlers.click();
  assert.match(errors[0], /cannot be accepted/);
  assert.ok(!ui.element('gate3-banner').innerHTML?.includes('Design Accepted'));
  assert.equal(button.textContent, 'Accept & Sign Off');
  assert.equal(button.disabled, false);
  ui.context.fetch = async () => ({ ok: true, json: async () => ({ status: 'COMPLETED', gate3_approved: true, accepted_by: 'Test Engineer' }) });
  await button.handlers.click();
  assert.match(ui.element('agentic-status-msg').textContent, /Test Engineer/);
  assert.equal(ui.element('step-A8').className, 'agent-step passed');
});

test('chat preview clears prior calculation and sign-off evidence without enabling native handoff', () => {
  const ui = setup();
  const bridge = chatBridge(ui);
  ui.context.renderAgenticRunState(state('A8', { status: 'PAUSED_AT_GATE3', staad_results: staadResult() }));
  assert.equal(ui.element('gate3-banner').style.display, 'flex');
  bridge.showPreview(state().frame_candidate, 'run_preview');
  assert.equal(ui.element('active-run-id').textContent, 'Run: run_preview');
  assert.equal(ui.element('gate3-banner').style.display, 'none');
  assert.equal(ui.element('staad-results-panel').hidden, true);
  assert.equal(ui.context.staadViewResult, undefined);
  assert.equal(ui.context.lastGeneratedResult, null);
  assert.equal(ui.element('send-staad-btn').disabled, true);
  assert.equal(ui.element('update-tekla-btn').disabled, true);
  assert.match(ui.element('status-msg').textContent, /unverified/);
  const count = ui.geometry.length;
  bridge.showPreview(state().frame_candidate, 'run_preview');
  assert.equal(ui.geometry.length, count, 'unchanged preview should not rebuild the scene');
});

test('chat turn locks survive conversation changes and retain ownership until every pending turn ends', () => {
  const ui = setup();
  const bridge = chatBridge(ui);
  bridge.setTurnBusy('turn_first', true);
  bridge.setTurnBusy('turn_second', true);
  assert.equal(ui.context.beginManualAction(), false);
  assert.equal(ui.element('agentic-run-btn').disabled, true);
  bridge.setTurnBusy('turn_first', false);
  assert.equal(ui.element('generate-btn').disabled, true);
  bridge.setTurnBusy('turn_second', false);
  assert.equal(ui.element('generate-btn').disabled, false);
});

test('chat rendering keeps a saved final state locked while its worker is still active', () => {
  const ui = setup();
  const bridge = chatBridge(ui);
  const final = state('A8', { status: 'PAUSED_AT_GATE3', staad_results: staadResult() });
  bridge.showRun(final, { active: true });
  assert.equal(ui.element('generate-btn').disabled, true);
  assert.equal(ui.element('gate3-banner').style.display, 'none');
  bridge.showRun(final, { active: false });
  assert.equal(ui.element('generate-btn').disabled, false);
  assert.equal(ui.element('gate3-banner').style.display, 'flex');
});

test('restoring a saved preview never promotes it to a live validated candidate', async () => {
  const preview = state('A3', { run_id: 'run_preview', status: 'READY', readiness: { preview_only: true } });
  const ui = setup({ states: [preview], search: '?run_id=run_preview' });
  await ui.context.restoreSavedAgenticRun();
  assert.equal(ui.context.lastGeneratedResult, null);
  assert.equal(ui.element('send-staad-btn').disabled, true);
  assert.equal(ui.element('generate-btn').disabled, false);
  assert.match(ui.element('status-msg').textContent, /unverified/);
});

test('refresh restores an explicitly linked final package using GET requests only', async () => {
  const ui = setup({ states: [state('A8', { status: 'COMPLETED' })], search: '?run_id=run_saved' });
  await ui.context.restoreSavedAgenticRun();
  assert.equal(ui.geometry.length, 1);
  assert.ok(ui.calls.every(([, options]) => options === undefined));
  assert.equal(ui.element('agentic-run-btn').disabled, false);
  assert.equal(ui.element('active-run-id').textContent, 'Run: run_saved');
});

test('refresh immediately renders active intermediate geometry before waiting for final results', async () => {
  const ui = setup({ states: [state(), state('A8', { status: 'COMPLETED' })], active: [{ run_id: 'run_saved' }, null], search: '?run_id=run_saved' });
  const observed = [];
  ui.context.setTimeout = callback => { observed.push({ draws: ui.geometry.length, manualDisabled: ui.element('send-staad-btn').disabled }); callback(); };
  await ui.context.restoreSavedAgenticRun();
  assert.deepEqual(observed, [{ draws: 1, manualDisabled: true }]);
  assert.equal(ui.element('agentic-cancel-btn').hidden, true);
});

test('orphaned saved run remains inspectable without keeping controls locked', async () => {
  const ui = setup({ states: [state()], search: '?run_id=run_saved' });
  await ui.context.restoreSavedAgenticRun();
  assert.equal(ui.geometry.length, 1);
  assert.equal(ui.element('agentic-run-btn').disabled, false);
  assert.match(ui.element('agentic-status-msg').textContent, /no active worker/);
});

test('refresh keeps controls locked until a worker releases its completed run', async () => {
  const final = state('A8', { status: 'COMPLETED' });
  const ui = setup({ states: [final, final], active: [{ run_id: 'run_saved' }, null], search: '?run_id=run_saved' });
  let waiting = false;
  ui.context.setTimeout = callback => { waiting = ui.element('generate-btn').disabled; callback(); };
  await ui.context.restoreSavedAgenticRun();
  assert.equal(waiting, true);
  assert.equal(ui.element('generate-btn').disabled, false);
});

test('clean root does not force restoration of a historical failure; saved runs remain available', async () => {
  const ui = setup({ history: [{ run_id: 'old_failure', status: 'FAILED' }] });
  await ui.context.restoreSavedAgenticRun();
  assert.equal(ui.geometry.length, 0);
  await ui.context.loadSavedRunChoices();
  assert.ok(ui.element('saved-run-select').children.some(option => option.value === 'old_failure'));
  assert.equal(ui.calls.filter(([url]) => url.includes('/runs/')).length, 0);
});

test('clean root reconnects to an actual active run', async () => {
  const ui = setup({ active: [{ run_id: 'run_saved' }, null], states: [state('A8', { status: 'COMPLETED' })] });
  await ui.context.restoreSavedAgenticRun();
  assert.equal(ui.geometry.length, 1);
  assert.ok(ui.urls.at(-1).includes('run_id=run_saved'));
});

test('live rendering advances geometry, analysis and IDEA without rebuilding unchanged scenes', () => {
  const ui = setup();
  ui.context.renderAgenticRunState(state());
  const running = state('A5', { staad_results: staadResult(), ideastatica_results: { first: { passed: true, connection_id: 1 } } });
  ui.context.renderAgenticRunState(running);
  ui.context.renderAgenticRunState(JSON.parse(JSON.stringify(running)));
  assert.equal(ui.geometry.length, 2);
  assert.equal(ui.staad.length, 1);
  assert.equal(ui.idea.length, 1);
  assert.equal(ui.geometry[1][0][0].id, '900001');
  assert.equal(ui.context.staadViewResult.candidate_signature, 'verified-frame');
  running.ideastatica_results.second = { passed: false, connection_id: 1 };
  ui.context.renderAgenticRunState(running);
  assert.equal(ui.idea.length, 2);
  assert.equal(Object.keys(ui.idea.at(-1)).length, 2);
  assert.match(ui.element('idea-status').textContent, /1 passed, 1 failed/);
  assert.equal(ui.context.revCounter, 1);
});

test('revised geometry clears old STAAD bindings and connection panels before new analysis', () => {
  const ui = setup();
  ui.context.renderAgenticRunState(state('A5', { staad_results: staadResult(), ideastatica_results: { first: { passed: true } } }));
  ui.context.renderAgenticRunState(state('A3', { design_generation: 2, staad_results: staadResult(), agent_statuses: { A4: { status: 'PASSED' } } }));
  assert.equal(ui.context.staadViewResult, null);
  assert.equal(ui.element('staad-results-panel').hidden, true);
  assert.equal(ui.element('idea-results-panel').hidden, true);
  assert.equal(ui.context.beginManualAction(), false);
});

test('reset clears the view without any POST or deleting saved runs', async () => {
  const ui = setup();
  ui.context.renderAgenticRunState(state('A8', { status: 'COMPLETED', staad_results: staadResult() }));
  ui.context.resetRunView();
  assert.equal(ui.context.lastGeneratedResult, null);
  assert.equal(ui.context.staadViewResult, null);
  assert.equal(ui.element('active-run-id').hidden, true);
  assert.equal(ui.element('hud').style.display, 'none');
  assert.ok(ui.urls.at(-1).includes('new=1'));
  assert.ok(!ui.urls.at(-1).includes('run_id='));
  const before = ui.calls.length;
  await ui.context.restoreSavedAgenticRun();
  assert.equal(ui.calls.length, before);
});

test('active run locks manual/reset/history actions, and completion releases valid controls', () => {
  const ui = setup();
  ui.context.renderAgenticRunState(state('A5', { staad_results: staadResult() }));
  for (const id of ['generate-btn', 'send-staad-btn', 'send-idea-btn', 'update-tekla-btn', 'new-run-btn', 'load-saved-run-btn']) assert.equal(ui.element(id).disabled, true, id);
  ui.context.resetRunView();
  assert.equal(ui.element('active-run-id').textContent, 'Run: run_saved');
  ui.context.renderAgenticRunState(state('A8', { status: 'COMPLETED', staad_results: staadResult() }));
  for (const id of ['generate-btn', 'send-staad-btn', 'send-idea-btn', 'update-tekla-btn', 'new-run-btn']) assert.equal(ui.element(id).disabled, false, id);
  assert.equal(ui.context.beginManualAction(), true);
  assert.equal(ui.element('agentic-run-btn').disabled, true);
  assert.equal(ui.context.beginManualAction(), false);
  ui.context.finishManualAction();
  assert.equal(ui.element('agentic-run-btn').disabled, false);
});

test('restoration failure releases controls and reports an error', async () => {
  const ui = setup({ search: '?run_id=missing' });
  ui.context.fetch = async () => ({ ok: false, status: 404 });
  await ui.context.restoreSavedAgenticRun();
  assert.equal(ui.element('agentic-run-btn').disabled, false);
  assert.match(ui.element('agentic-status-msg').textContent, /404/);
});

test('new run keeps the existing request payload, renders polling updates and ignores a late poll after completion', async () => {
  const ui = setup();
  const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'static', 'radiator_support', 'index.html'), 'utf8');
  const start = html.indexOf('\nif (agenticRunBtn) {');
  let poll, resolvePost, resolveLatePoll;
  const grid = { n_long_bays: 8, n_trans_bays: 5 }, loads = { wind_speed_mph: 90 };
  ui.context.buildPayload = () => ({ grid });
  ui.context.buildLoadConfiguration = () => loads;
  ui.context.setInterval = callback => { poll = callback; return 1; };
  ui.context.clearInterval = () => {};
  ui.context.fetch = async (url, options) => {
    ui.calls.push([url, options]);
    if (url.endsWith('/run')) return new Promise(resolve => { resolvePost = resolve; });
    if (url.endsWith('/active')) return new Promise(resolve => { resolveLatePoll = resolve; });
    return { ok: true, json: async () => [] };
  };
  vm.runInContext(html.slice(start, html.indexOf('\nif (agenticCancelBtn)', start)), ui.context);
  const job = ui.element('agentic-run-btn').handlers.click();
  assert.equal(ui.element('generate-btn').disabled, true);
  assert.deepEqual(JSON.parse(ui.calls[0][1].body), { grid, load_configuration: loads });
  const firstPoll = poll();
  resolveLatePoll({ ok: true, json: async () => state() });
  await firstPoll;
  assert.equal(ui.geometry.length, 1);
  const latePoll = poll();
  const final = state('A8', { status: 'COMPLETED', staad_results: staadResult() });
  resolvePost({ ok: true, json: async () => final });
  await job;
  resolveLatePoll({ ok: true, json: async () => state('A5', { staad_results: staadResult() }) });
  await latePoll;
  assert.equal(ui.element('generate-btn').disabled, false);
  assert.match(ui.element('agentic-status-msg').textContent, /Design accepted/);
  assert.equal(ui.geometry.length, 2);
  assert.equal(ui.calls.filter(([, options]) => options?.method === 'POST').length, 1);
});
