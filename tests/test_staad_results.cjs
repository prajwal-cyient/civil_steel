const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.style = {};
    this.classList = { add() {} };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  addEventListener() {}
  get options() { return this.children; }
}

function setup() {
  const elements = new Map();
  const context = vm.createContext({
    document: {
      createElement: tag => new Element(tag),
      getElementById: id => {
        if (!elements.has(id)) elements.set(id, new Element('div'));
        return elements.get(id);
      },
    },
  });
  const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'radiator_support', 'results.js'), 'utf8');
  vm.runInContext(source.replace('initializePaneDividers();', ''), context);
  return { context, elements };
}

function text(element) {
  return [element.textContent || '', ...element.children.map(text)].join(' ');
}

function loadViewerFunction(context, name) {
  const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'radiator_support', 'index.html'), 'utf8');
  const start = html.indexOf(`function ${name}(`);
  assert.notEqual(start, -1);
  vm.runInContext(html.slice(start, html.indexOf('\n}', start) + 2), context);
}

test('member types are inferred from names, including split members', () => {
  const { context } = setup();
  for (const [name, expected] of [
    ['bracing-long-0-1', 'bracing_longitudinal'],
    ['bracing-trans-0-1', 'bracing_transverse'],
    ['beam-x-0-1-part-2', 'beam_longitudinal'],
    ['beam-y-0-1', 'beam_transverse'],
    ['column-0-0', 'column'],
    ['other', 'column'],
  ]) assert.equal(context.memberName2type(name), expected);
});

test('load case dropdown combines envelopes, deduplicates IDs and uses titles safely', () => {
  const { context, elements } = setup();
  context.renderStaadLoadCases({
    load_envelopes: [{ loadCases: [100, 101] }, { loadCases: ['101', 102] }],
    100: { load_case_title: '<b>Dead load</b>' },
    101: { load_case_title: 'Wind' },
  });
  const select = elements.get('staad-load-case');
  assert.deepEqual(select.options.map(option => [option.value, option.textContent]), [
    ['100', '<b>Dead load</b>'], ['101', 'Wind'], ['102', 'Load case 102'],
  ]);
  assert.equal(select.disabled, false);
  context.renderStaadLoadCases({});
  assert.equal(select.options.length, 0);
  assert.equal(select.disabled, true);
});

test('beam tooltip shows metadata, both member ends and selected support reactions', () => {
  const { context } = setup();
  const tooltip = new Element('div');
  const member = {
    id: '900001', name: 'beam-y-0-1', type: 'column',
    start: 900097, end: 900002, material: 'S355JR', profile: 'CFRHS120X120X5', class: '11',
  };
  const result = {
    units: { force: 'kN', moment: 'kN-m' },
    results_local_or_global: 0,
    support_nodes: [900097],
    100: { load_case_title: 'Dead', forces: { 900001: [[1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12]] }, support_reactions: { 900097: [[13, 14, 15, 16, 17, 18]] } },
    101: { load_case_title: 'Wind', forces: { 900001: [[101, 102, 103, 104, 105, 106], [107, 108, 109, 110, 111, 112]] } },
  };
  context.renderStaadTooltip(tooltip, result, '100', member);
  let output = text(tooltip);
  for (const expected of ['900001', 'beam_transverse', '900097', '900002', 'S355JR', 'CFRHS120X120X5', 'Class: 11', 'Start node 900097 forces (local)', 'End node 900002 forces (local)', 'Support 900097 reactions (global)', 'Fx (kN)', 'Mz (kN-m)', '18']) {
    assert.ok(output.includes(expected), expected);
  }
  context.renderStaadTooltip(tooltip, result, '101', member);
  output = text(tooltip);
  assert.ok(output.includes('Wind'));
  assert.ok(output.includes('112'));
  assert.ok(!output.includes('Dead'));
  assert.ok(output.includes('Not available for this load case.'));
});

test('joint tooltips show reactions and missing analysis data explicitly', () => {
  const { context } = setup();
  const tooltip = new Element('div');
  context.renderStaadTooltip(tooltip, { support_nodes: [42] }, '', undefined, '42');
  const output = text(tooltip);
  assert.ok(output.includes('Joint 42'));
  assert.ok(output.includes('No load case available.'));
  assert.ok(output.includes('Support 42 reactions (global)'));
  assert.ok(output.includes('Not available for this load case.'));
});

test('joint selection highlights exactly the reported members and restores class colours on clear', () => {
  const { context, elements } = setup();
  function material(baseColor) {
    return { color: { value: baseColor, setHex(value) { this.value = value; } }, opacity: 1 };
  }
  context.staadViewResult = { joint_member_ends: { 900004: [[900002, 1], [900055, 1], [900056, 0], [900104, 0]] } };
  context.staadMemberLines = new Map(['900002', '900055', '900056', '900104', '900999'].map(id => [
    id, { material: material(123), userData: { baseColor: 123 } },
  ]));
  context.staadJointMeshes = [{ material: material(456), userData: { nodeId: '900004', baseColor: 456 } }];
  loadViewerFunction(context, 'selectStaadJoint');
  context.selectStaadJoint('900004');
  for (const [id, line] of context.staadMemberLines) {
    assert.equal(line.material.color.value, id === '900999' ? 123 : 0xffff00);
    assert.equal(line.material.opacity, id === '900999' ? 0.25 : 1);
  }
  assert.equal(context.staadJointMeshes[0].material.color.value, 0xffff00);
  assert.ok(elements.get('staad-joint-selection').textContent.includes('900002 (end)'));
  assert.ok(elements.get('staad-joint-selection').textContent.includes('900056 (start)'));
  context.selectStaadJoint(null);
  for (const line of context.staadMemberLines.values()) {
    assert.equal(line.material.color.value, 123);
    assert.equal(line.material.opacity, 1);
  }
  assert.equal(context.staadJointMeshes[0].material.color.value, 456);
});

test('clearing the viewer removes and disposes geometry, labels and STAAD state', () => {
  const { context, elements } = setup();
  const disposed = [];
  const removed = [];
  context.staadViewResult = {};
  context.staadMemberLines = new Map([['1', {}]]);
  context.staadJointMeshes = [{}];
  context.staadTooltip = { hidden: false };
  context.scene = { remove(group) { removed.push(group); } };
  context.structureGroup = { traverse(callback) {
    callback({ geometry: { dispose() { disposed.push('geometry'); } }, material: { dispose() { disposed.push('material'); } } });
  } };
  context.labelsGroup = { traverse(callback) {
    callback({ isCSS2DObject: true, element: { remove() { disposed.push('label'); } } });
  } };
  loadViewerFunction(context, 'clearStructureView');
  context.clearStructureView();
  assert.deepEqual(disposed, ['geometry', 'material', 'label']);
  assert.equal(removed.length, 2);
  assert.equal(context.staadViewResult, null);
  assert.equal(context.structureGroup, null);
  assert.equal(context.labelsGroup, null);
  assert.equal(context.staadMemberLines.size, 0);
  assert.equal(context.staadJointMeshes.length, 0);
  assert.equal(context.staadTooltip.hidden, true);
  assert.equal(elements.get('staad-view-controls').hidden, true);
});
