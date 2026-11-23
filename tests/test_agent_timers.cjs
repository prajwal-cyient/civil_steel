const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const code = fs.readFileSync(path.join(__dirname,'../src/static/radiator_support/agent_timers.js'),'utf8');
function setup(now = 100000) {
  const elements = new Map(Array.from({length:8},(_,i)=>[`timer-A${i+1}`,{}]));
  let tick = null;
  const clock = {now};
  const context = vm.createContext({window:{}, document:{getElementById:id=>elements.get(id)},
    Date:{now:()=>clock.now,parse:Date.parse},setInterval:callback=>{tick=callback;return 1;},clearInterval:()=>{tick=null;}});
  vm.runInContext(code,context);
  return {update:context.window.AgentTimers.update,elements,clock,tick:()=>tick?.(),hasTicker:()=>tick!==null};
}
test('stage timer advances live from persisted start and freezes at actual elapsed time',()=>{
  const ui=setup();
  ui.update({A1:{running:true,started_at:new Date(65000).toISOString(),elapsed_seconds:0,attempts:1}},{A1:{status:'RUNNING'}},'RUNNING');
  assert.equal(ui.elements.get('timer-A1').textContent,'00:35');
  assert.equal(ui.elements.get('timer-A2').textContent,'00:00');
  ui.clock.now+=1000;ui.tick();
  assert.equal(ui.elements.get('timer-A1').textContent,'00:36');
  ui.update({A1:{running:false,elapsed_seconds:36.8,attempts:1}},{A1:{status:'PASSED'}},'RUNNING');
  ui.clock.now+=90000;ui.tick();
  assert.equal(ui.elements.get('timer-A1').textContent,'00:36');
  assert.equal(ui.hasTicker(),false);
});
test('refresh, retries and long solves use saved timing rather than page-open time',()=>{
  const ui=setup(200000);
  ui.update({A5:{running:true,started_at:new Date(185000).toISOString(),elapsed_seconds:1102,attempts:2}},{A5:{status:'RUNNING'}},'RUNNING');
  assert.equal(ui.elements.get('timer-A5').textContent,'18:37');
  ui.update({A5:{running:false,elapsed_seconds:3661,attempts:2}},{A5:{status:'PASSED'}},'PAUSED_AT_GATE3');
  assert.equal(ui.elements.get('timer-A5').textContent,'61:01');
});
test('A8 waiting time, failed stages and reset do not keep a browser clock running',()=>{
  const ui=setup();
  ui.update({A8:{running:false,elapsed_seconds:7,attempts:1}},{A8:{status:'AWAITING_APPROVAL'}},'PAUSED_AT_GATE3');
  ui.clock.now+=3600000;ui.tick();
  assert.equal(ui.elements.get('timer-A8').textContent,'00:07');
  ui.update({A5:{running:false,elapsed_seconds:30,attempts:1}},{A5:{status:'FAILED'}},'FAILED');
  assert.equal(ui.elements.get('timer-A5').textContent,'00:30');
  ui.update();
  assert.equal(ui.elements.get('timer-A5').textContent,'00:00');
  assert.equal(ui.hasTicker(),false);
});
test('older saved stages without timing show unavailable rather than invented durations',()=>{
  const ui=setup();ui.update({}, {A1:{status:'PASSED'}},'COMPLETED');
  assert.equal(ui.elements.get('timer-A1').textContent,'--:--');
  assert.equal(ui.elements.get('timer-A2').textContent,'00:00');
});
