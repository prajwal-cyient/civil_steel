/* Render persisted execution times; the browser never starts an agent clock. */
(() => {
  let timings = {}, statuses = {}, workflowStatus = '', ticker = null;
  function format(seconds) {
    const whole = Math.floor(Math.max(0, Number(seconds) || 0));
    return `${String(Math.floor(whole / 60)).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`;
  }
  function running(timing) {
    return timing?.running && !['FAILED', 'CANCELLED', 'COMPLETED'].includes(workflowStatus);
  }
  function draw() {
    for (let n = 1; n <= 8; n++) {
      const id = `A${n}`, element = document.getElementById(`timer-${id}`);
      if (!element) continue;
      const timing = timings[id];
      if (!timing) {
        element.textContent = statuses[id] ? '--:--' : '00:00';
        element.title = statuses[id] ? 'Timing was not recorded for this saved stage.' : 'Not started';
        continue;
      }
      let seconds = Number(timing.elapsed_seconds) || 0;
      if (running(timing)) {
        const started = Date.parse(timing.started_at);
        if (Number.isFinite(started)) seconds += Math.max(0, (Date.now() - started) / 1000);
      }
      element.textContent = format(seconds);
      element.title = `Execution time across ${timing.attempts || 1} attempt(s); excludes time between stages and manual approval.`;
    }
  }
  window.AgentTimers = {
    update(nextTimings = {}, nextStatuses = {}, nextWorkflowStatus = '') {
      timings = nextTimings; statuses = nextStatuses; workflowStatus = nextWorkflowStatus;
      if (ticker !== null) { clearInterval(ticker); ticker = null; }
      draw();
      if (Object.values(timings).some(running)) ticker = setInterval(draw, 1000);
    },
  };
})();
