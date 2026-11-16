const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function setup(states, active = null) {
  const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'static', 'radiator_support', 'index.html'), 'utf8');
  const start = html.indexOf('async function restoreSavedAgenticRun(');
  const source = html.slice(start, html.indexOf('\n}', start) + 2);
  const calls = [], rendered = [];
  const context = vm.createContext({
    URLSearchParams, window: { location: { search: '' } }, currentAgenticRunId: null,
    agenticRunBtn: { disabled: false }, agenticCancelBtn: { hidden: true },
    agenticStatusMsg: { style: {} }, updateAgentSteps() {},
    renderAgenticRunState: state => rendered.push(state),
    setTimeout: callback => callback(),
    fetch: async (url, options) => {
      calls.push([url, options]);
      let body = url.includes('?limit=') ? [{ run_id: 'run_saved' }] :
        url.endsWith('/active') ? active : states.shift();
      return { ok: true, json: async () => body };
    },
  });
  vm.runInContext(source, context);
  return { context, calls, rendered };
}

test('refresh restores the final package without starting another calculation', async () => {
  const state = { run_id: 'run_saved', status: 'PAUSED_AT_GATE3' };
  const { context, calls, rendered } = setup([state]);
  await context.restoreSavedAgenticRun();
  assert.equal(rendered[0], state);
  assert.equal(calls.length, 2);
  assert.ok(calls.every(([, options]) => options === undefined));
  assert.equal(context.agenticRunBtn.disabled, false);
});

test('refresh follows an active run through its saved final result', async () => {
  const final = { run_id: 'run_saved', status: 'PAUSED_AT_GATE3' };
  const { context, rendered } = setup([
    { run_id: 'run_saved', status: 'RUNNING', current_agent: 'A5', agent_statuses: {} }, final,
  ], { run_id: 'run_saved' });
  await context.restoreSavedAgenticRun();
  assert.equal(rendered.at(-1), final);
  assert.equal(context.agenticCancelBtn.hidden, true);
});

test('an orphaned saved run does not leave the run button disabled indefinitely', async () => {
  const { context } = setup([{ run_id: 'run_saved', status: 'RUNNING', agent_statuses: {} }]);
  await context.restoreSavedAgenticRun();
  assert.equal(context.agenticRunBtn.disabled, false);
  assert.match(context.agenticStatusMsg.textContent, /no active worker/);
});
