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

test('joint group dropdown uses response keys and replaces stale groups', () => {
  const { context, elements } = setup();
  context.renderStaadJointGroups({ joint_groups: {
    corner_node: { joints: [] },
    inner_node_with_1_longitudinal_and_1_transverse_bracings: { joints: [] },
  } });
  const select = elements.get('idea-joint-group');
  assert.deepEqual(select.options.map(option => [option.value, option.textContent]), [
    ['', 'Select a joint group'],
    ['corner_node', 'corner_node'],
    ['inner_node_with_1_longitudinal_and_1_transverse_bracings', 'inner_node_with_1_longitudinal_and_1_transverse_bracings'],
  ]);
  assert.equal(select.disabled, false);
  assert.equal(select.value, '');
  context.renderStaadJointGroups({});
  assert.equal(select.options.length, 1);
  assert.equal(select.options[0].textContent, 'No joint groups available');
  assert.equal(select.disabled, true);
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
  context.staadTooltip = { hidden: false };
  const paramRenders = [];
  context.renderIdeaOperationParameters = jointGroup => paramRenders.push(jointGroup);
  loadViewerFunction(context, 'highlightStaadJoints');
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

  context.staadViewResult.joint_groups = {
    corner_node: { joints: [
      { node: 900004, members: [{ beam: 900002, end: 1 }, { beam: 900055, end: 0 }] },
      { node: 900005, members: [{ beam: 900055, end: 1 }, { beam: 900056, end: 0 }] },
    ] },
    empty_group: { joints: [] },
  };
  context.staadJointMeshes.push({ material: material(789), userData: { nodeId: '900005', baseColor: 789 } });
  loadViewerFunction(context, 'selectStaadJointGroup');
  context.selectStaadJointGroup('corner_node');
  assert.equal(elements.get('staad-joint-selection').textContent, 'corner_node: 2 joints selected.');
  for (const [id, line] of context.staadMemberLines) {
    const selected = ['900002', '900055', '900056'].includes(id);
    assert.equal(line.material.color.value, selected ? 0xffff00 : 123);
    assert.equal(line.material.opacity, selected ? 1 : 0.25);
  }
  assert.ok(context.staadJointMeshes.every(joint => joint.material.color.value === 0xffff00));
  elements.get('idea-joint-group').value = 'corner_node';
  context.selectStaadJoint('900004');
  assert.equal(elements.get('idea-joint-group').value, '');
  assert.equal(context.staadJointMeshes[1].material.color.value, 789);
  context.selectStaadJointGroup('empty_group');
  assert.ok([...context.staadMemberLines.values()].every(line => line.material.opacity === 1 && line.material.color.value === 123));
  context.selectStaadJointGroup('');
  assert.ok(context.staadJointMeshes.every(joint => joint.material.color.value === joint.userData.baseColor));
  // Operation parameters follow the selected joint group; clicking a single joint clears them.
  assert.deepEqual(paramRenders, ['', '', 'corner_node', '', 'empty_group', '']);
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
  const paramRenders = [];
  context.renderIdeaOperationParameters = jointGroup => paramRenders.push(jointGroup);
  loadViewerFunction(context, 'clearStructureView');
  context.clearStructureView();
  assert.deepEqual(paramRenders, ['']);
  assert.deepEqual(disposed, ['geometry', 'material', 'label']);
  assert.equal(removed.length, 2);
  assert.equal(context.staadViewResult, null);
  assert.equal(context.structureGroup, null);
  assert.equal(context.labelsGroup, null);
  assert.equal(context.staadMemberLines.size, 0);
  assert.equal(context.staadJointMeshes.length, 0);
  assert.equal(context.staadTooltip.hidden, true);
  assert.equal(elements.get('staad-view-controls').hidden, true);
  assert.equal(elements.get('idea-joint-group').disabled, true);
});

test('transverse indices use spreadsheet-style letters at every rollover', () => {
  const { context } = setup();
  loadViewerFunction(context, 'indexToLetters');
  for (const [index, expected] of [
    [1, 'A'], [2, 'B'], [26, 'Z'], [27, 'AA'], [28, 'AB'],
    [52, 'AZ'], [53, 'BA'], [702, 'ZZ'], [703, 'AAA'],
  ]) assert.equal(context.indexToLetters(index), expected);
  const labels = Array.from({ length: 28 }, (_, index) => context.indexToLetters(index + 1));
  assert.equal(labels.length, 28);
  assert.equal(new Set(labels).size, 28);
  assert.deepEqual(labels.slice(25), ['Z', 'AA', 'AB']);
});

test('STAAD rendering restores both grid indices and rotates only the camera by 180 degrees', () => {
  const { context } = setup();
  class Vector3 {
    constructor(x = 0, y = 0, z = 0) { this.set(x, y, z); }
    set(x, y, z) { Object.assign(this, { x, y, z }); return this; }
    copy(v) { return this.set(v.x, v.y, v.z); }
    clone() { return new Vector3(this.x, this.y, this.z); }
    add(v) { return this.set(this.x + v.x, this.y + v.y, this.z + v.z); }
    multiplyScalar(s) { return this.set(this.x * s, this.y * s, this.z * s); }
    length() { return Math.hypot(this.x, this.y, this.z); }
    normalize() { return this.multiplyScalar(1 / this.length()); }
    applyAxisAngle(axis, angle) {
      assert.deepEqual([axis.x, axis.y, axis.z], [0, 1, 0]);
      return this.set(this.x * Math.cos(angle) + this.z * Math.sin(angle), this.y,
        this.z * Math.cos(angle) - this.x * Math.sin(angle));
    }
  }
  class Group {
    constructor() { this.children = []; }
    add(child) { this.children.push(child); }
  }
  class Box3 {
    constructor() {
      this.min = new Vector3(Infinity, Infinity, Infinity);
      this.max = new Vector3(-Infinity, -Infinity, -Infinity);
    }
    expandByPoint(p) {
      for (const axis of ['x', 'y', 'z']) {
        this.min[axis] = Math.min(this.min[axis], p[axis]);
        this.max[axis] = Math.max(this.max[axis], p[axis]);
      }
    }
    isEmpty() { return this.min.x > this.max.x; }
    getSize(v) { return v.set(this.max.x - this.min.x, this.max.y - this.min.y, this.max.z - this.min.z); }
    getCenter(v) { return v.copy(this.min).add(this.max).multiplyScalar(0.5); }
  }
  class Geometry {
    setFromPoints(points) { this.points = points; return this; }
  }
  class Line {
    constructor(geometry, material) { Object.assign(this, { geometry, material, userData: {} }); }
  }
  context.THREE = {
    Vector3, Box3, Group, BufferGeometry: Geometry, Line, LineSegments: Line,
    LineBasicMaterial: class {}, GridHelper: class { constructor() { this.position = new Vector3(); } },
  };
  context.clearStructureView = () => {};
  context.renderStaadJoints = () => {};
  context.renderStaadViewSummary = () => {};
  context.staadClassColor = () => 123;
  context.TYPE_COLORS = {};
  context.generatedLegend = '';
  context.scene = { add() {} };
  context.staadMemberLines = new Map();
  context.camera = { position: new Vector3(), aspect: 1, updateProjectionMatrix() {} };
  context.controls = { target: new Vector3(), update() {} };
  context.makeIndexLabel = text => ({ text, position: new Vector3() });
  loadViewerFunction(context, 'indexToLetters');
  loadViewerFunction(context, 'toThree');
  loadViewerFunction(context, 'renderStructure');
  const engineeringMembers = [];
  for (let j = 0; j < 2; j++) {
    for (let i = 0; i < 3; i++) {
      engineeringMembers.push({
        id: `${j}-${i}`, type: 'column', name: `column-${j}-${i}`,
        p1: [i * 2600, j * 3300, 100], p2: [i * 2600, j * 3300, 5100],
      });
    }
  }
  context.renderStructure(engineeringMembers);
  const previewDirection = {
    x: context.camera.position.x - context.controls.target.x,
    y: context.camera.position.y - context.controls.target.y,
    z: context.camera.position.z - context.controls.target.z,
  };
  assert.deepEqual(Array.from(context.labelsGroup.children, label => label.text), ['1', '1', '2', '2', '3', '3', 'A', 'A', 'B', 'B']);
  const staadMembers = engineeringMembers.map(member => ({
    ...member, p1: [member.p1[1], member.p1[2], member.p1[0]],
    p2: [member.p2[1], member.p2[2], member.p2[0]],
  }));
  context.renderStructure(staadMembers, {});
  const labels = Array.from(context.labelsGroup.children, label => ({
    text: label.text, position: [label.position.x, label.position.y, label.position.z],
  }));
  assert.deepEqual(labels, [
    { text: '1', position: [-312, 100, 0] },
    { text: '1', position: [3612, 100, 0] },
    { text: '2', position: [-312, 100, 2600] },
    { text: '2', position: [3612, 100, 2600] },
    { text: '3', position: [-312, 100, 5200] },
    { text: '3', position: [3612, 100, 5200] },
    { text: 'A', position: [0, 100, -312] },
    { text: 'A', position: [0, 100, 5512] },
    { text: 'B', position: [3300, 100, -312] },
    { text: 'B', position: [3300, 100, 5512] },
  ]);
  assert.ok(Math.abs(context.camera.position.x - context.controls.target.x + previewDirection.x) < 1e-8);
  assert.ok(Math.abs(context.camera.position.y - context.controls.target.y - previewDirection.y) < 1e-8);
  assert.ok(Math.abs(context.camera.position.z - context.controls.target.z + previewDirection.z) < 1e-8);
  for (const member of staadMembers) {
    const points = context.staadMemberLines.get(member.id).geometry.points;
    assert.deepEqual(Array.from(points, p => [p.x, p.y, p.z]), [member.p1, member.p2]);
  }
});
