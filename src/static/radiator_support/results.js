function resultElement(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function resultLabel(key) {
  return String(key).replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\b\w/g, character => character.toUpperCase());
}

function resultValue(value) {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return String(value);
    if (Number.isInteger(value)) return String(value);
    if (value !== 0 && Math.abs(value) < 0.001) return value.toExponential(3);
    return value.toLocaleString('en-US', { maximumFractionDigits: 5 });
  }
  if (Array.isArray(value)) return value.map(resultValue).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function resultTable(parent, records, columns, columnLabel = resultLabel) {
  if (!records.length) {
    parent.append(resultElement('p', 'No results available.'));
    return;
  }
  const keys = (columns || [...new Set(records.flatMap(record => Object.keys(record)))])
    .filter(key => key !== 'connected_forces');
  const wrapper = resultElement('div', undefined, 'result-table-wrap');
  const table = resultElement('table');
  const head = resultElement('thead');
  const heading = resultElement('tr');
  for (const key of keys) {
    const cell = resultElement('th', columnLabel(key));
    cell.scope = 'col';
    if (key === 'extreme') cell.classList.add('result-extreme');
    heading.append(cell);
  }
  head.append(heading);
  table.append(head);
  const body = resultElement('tbody');
  for (const record of records) {
    const row = resultElement('tr');
    const connectedForces = Array.isArray(record.connected_forces) ? record.connected_forces : null;
    const detailRow = connectedForces ? resultElement('tr') : null;
    if (detailRow) {
      detailRow.hidden = true;
      const detailCell = resultElement('td');
      detailCell.colSpan = keys.length;
      detailCell.append(resultElement('h3', 'Connected Forces'));
      resultTable(detailCell, connectedForces.map(entry => ({
        beam: entry.beam,
        end: entry.end,
        ...(Array.isArray(entry.forces)
          ? Object.fromEntries(['Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz'].map((component, index) => [component, entry.forces[index]]))
          : entry.forces && typeof entry.forces === 'object' ? entry.forces : { forces: entry.forces }),
      })));
      detailRow.append(detailCell);
    }
    for (const key of keys) {
      const value = record[key];
      const cell = resultElement('td', resultValue(value));
      if (key === 'extreme') cell.classList.add('result-extreme');
      if (key === 'extreme' && detailRow) {
        const link = resultElement('a', resultValue(value));
        link.href = '#';
        link.className = 'result-extreme-link';
        link.setAttribute('aria-expanded', 'false');
        link.addEventListener('click', event => {
          event.preventDefault();
          detailRow.hidden = !detailRow.hidden;
          link.setAttribute('aria-expanded', String(!detailRow.hidden));
        });
        cell.replaceChildren(link);
      }
      const component = String(record.extreme ?? '').replace(/^(max|min)[\s_]+/i, '').replace(/[\s_]/g, '').toLowerCase();
      if (typeof value === 'number' && component === key.replace(/[\s_]/g, '').toLowerCase()) {
        cell.classList.add('result-governing');
      }
      cell.title = typeof value === 'object' ? JSON.stringify(value) : String(value ?? '');
      if (typeof value === 'string') cell.classList.add('result-text');
      if ((key === 'maximum' || key === 'minimum') && typeof value === 'number' && value > 1) {
        cell.classList.add('result-failure');
      }
      if (key === 'exceeds_limit' && value === true) {
        cell.classList.add('result-failure');
        cell.replaceChildren(resultElement('strong', resultValue(value)));
      }
      row.append(cell);
    }
    body.append(row);
    if (detailRow) body.append(detailRow);
  }
  table.append(body);
  wrapper.append(table);
  parent.append(wrapper);
}

function resultSection(parent, title, open = false) {
  const section = resultElement('details');
  section.open = open;
  section.append(resultElement('summary', title));
  parent.append(section);
  return section;
}

function resultData(parent, data) {
  if (!data || typeof data !== 'object') {
    parent.append(resultElement('p', resultValue(data)));
  } else if (Array.isArray(data)) {
    resultTable(parent, data.map(item => item && typeof item === 'object' ? item : { value: item }));
  } else {
    const entries = Object.entries(data);
    if (!entries.length) {
      parent.append(resultElement('p', 'No results available.'));
    } else if (entries.every(([, value]) => value && typeof value === 'object' && !Array.isArray(value))) {
      const flatRecords = entries.every(([, value]) => Object.values(value).every(field => field === null || typeof field !== 'object' || Array.isArray(field)));
      if (flatRecords) {
        resultTable(parent, entries.map(([extreme, record]) => ({ extreme: resultLabel(extreme), ...record })));
      } else {
        for (const [key, value] of entries) resultData(resultSection(parent, resultLabel(key)), value);
      }
    } else {
      resultTable(parent, entries.map(([field, value]) => ({ field: resultLabel(field), value })));
    }
  }
}

function openResultPanel(kind) {
  const panel = document.getElementById(`${kind}-results-panel`);
  const content = document.getElementById(`${kind}-results-content`);
  content.replaceChildren();
  document.getElementById('results-pane').hidden = false;
  document.getElementById('results-divider').hidden = false;
  panel.hidden = false;
  panel.open = true;
  return content;
}

function renderIntegrationStatus(content, result, analysis = false) {
  resultTable(content, [{
    created: result.created ?? 0,
    failed: result.failed ?? 0,
    ...(analysis ? { property_failures: result.property_assignment_failed ?? 0, load_case_failures: result.load_cases_failed ?? 0 } : {}),
  }]);
  if (analysis) {
    const status = result.analysis_status;
    const failed = result.analysis_failed || result.analysis_timeout || (status?.NoOfErrors > 0);
    const warning = status?.NoOfWarnings > 0 || result.warnings?.length > 0;
    const text = result.analysis_timeout ? 'Analysis timed out.'
      : status?.ReturnString || (result.analysis_failed ? 'Analysis failed.'
        : result.analysis_complete ? 'Analysis complete.' : 'Analysis completion not reported.');
    content.append(resultElement('p', text, `result-notice ${failed ? 'result-failure' : warning ? 'result-warning' : ''}`));
    if (status && typeof status === 'object') resultData(resultSection(content, 'Analysis Status'), status);
  }
  for (const key of ['errors', 'property_errors', 'load_errors', 'warnings']) {
    if (result[key]?.length) resultData(resultSection(content, `${resultLabel(key)} (${result[key].length})`, true), result[key]);
  }
}

function memberName2type(name) {
  if (name.startsWith('bracing-long')) return 'bracing_longitudinal';
  if (name.startsWith('bracing-trans')) return 'bracing_transverse';
  if (name.startsWith('beam-x')) return 'beam_longitudinal';
  if (name.startsWith('beam-y')) return 'beam_transverse';
  return 'column';
}

function staadLoadCases(result) {
  return [...new Set((result.load_envelopes || []).flatMap(envelope => envelope.loadCases || []).map(String))];
}

function renderStaadLoadCases(result) {
  const select = document.getElementById('staad-load-case');
  select.replaceChildren();
  for (const id of staadLoadCases(result)) {
    const option = resultElement('option', result[id]?.load_case_title || `Load case ${id}`);
    option.value = id;
    select.append(option);
  }
  select.disabled = !select.options.length;
  document.getElementById('staad-view-controls').hidden = false;
}

function renderStaadJointGroups(result) {
  const select = document.getElementById('idea-joint-group');
  const names = Object.keys(result.joint_groups || {});
  const placeholder = resultElement('option', names.length ? 'Select a joint group' : 'No joint groups available');
  placeholder.value = '';
  select.replaceChildren(placeholder);
  for (const name of names) {
    const option = resultElement('option', name);
    option.value = name;
    select.append(option);
  }
  select.value = '';
  select.disabled = !names.length;
}

function renderIdeaEnvelopes(result) {
  const select = document.getElementById('idea-envelope');
  // IDEA StatiCa connection design only uses strength envelopes.
  const envelopes = (result.load_envelopes || []).filter(envelope => envelope.envelopeType === 'STRENGTH');
  const placeholder = resultElement('option', envelopes.length ? 'Select an envelope' : 'No envelopes available');
  placeholder.value = '';
  select.replaceChildren(placeholder);
  for (const envelope of envelopes) {
    const option = resultElement('option', `Envelope ${envelope.envelopeId}${envelope.envelopeType ? ` - ${envelope.envelopeType}` : ''}`);
    option.value = String(envelope.envelopeId);
    select.append(option);
  }
  select.value = '';
  select.disabled = !envelopes.length;
}

function staadForceTable(parent, title, forces, units) {
  parent.append(resultElement('h3', title));
  if (!Array.isArray(forces)) {
    parent.append(resultElement('p', 'Not available for this load case.'));
    return;
  }
  resultTable(parent, [Object.fromEntries(['Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz'].map((component, index) => [
    `${component} (${index < 3 ? units?.force || 'force unit not reported' : units?.moment || 'moment unit not reported'})`,
    forces[index],
  ]))], undefined, key => key);
}

function renderStaadTooltip(parent, result, loadCaseId, member, nodeId) {
  parent.replaceChildren();
  const loadCase = result[loadCaseId];
  parent.append(resultElement('strong', member ? `Beam ${member.id}: ${member.name}` : `Joint ${nodeId}`));
  parent.append(resultElement('p', loadCase ? `Load case ${loadCaseId}: ${loadCase.load_case_title}` : 'No load case available.'));
  if (member) {
    parent.append(resultElement('p', [
      `Type: ${memberName2type(member.name)}`,
      `Start node: ${member.start} | End node: ${member.end}`,
      `Material: ${member.material} | Profile: ${member.profile} | Class: ${member.class}`,
    ].join('\n')));
    const basis = result.results_local_or_global === 0 ? 'local' : result.results_local_or_global === 1 ? 'global' : 'basis not reported';
    staadForceTable(parent, `Start node ${member.start} forces (${basis})`, loadCase?.forces?.[member.id]?.[0], result.units);
    staadForceTable(parent, `End node ${member.end} forces (${basis})`, loadCase?.forces?.[member.id]?.[1], result.units);
  } else {
    parent.append(resultElement('p', 'Click to highlight connected members. Click empty space to clear.'));
  }
  const supportIds = new Set((result.support_nodes || []).map(String));
  for (const id of member ? [member.start, member.end] : [nodeId]) {
    if (supportIds.has(String(id))) {
      staadForceTable(parent, `Support ${id} reactions (global)`, loadCase?.support_reactions?.[id]?.[0], result.units);
    }
  }
}

function renderStaadResults(result) {
  renderStaadLoadCases(result);
  renderStaadJointGroups(result);
  renderIdeaEnvelopes(result);
  const content = openResultPanel('staad');
  renderIntegrationStatus(content, result, true);
  content.append(resultElement('h3', 'Load Envelopes'));
  resultTable(content, result.load_envelopes || []);
  content.append(resultElement('h3', 'Member Design Ratios'));
  const minimums = result.member_type_min_ratios || {};
  const maximums = result.member_type_max_ratios || {};
  resultTable(content, [...new Set([...Object.keys(minimums), ...Object.keys(maximums)])].map(type => ({
    member_type: resultLabel(type), minimum: minimums[type], maximum: maximums[type],
  })));
  content.append(resultElement('h3', 'Envelope Summary Results'));
  const envelopes = Object.entries(result.envelope_summary_results || {});
  if (!envelopes.length) content.append(resultElement('p', 'No envelope summaries available.'));
  for (const [id, summary] of envelopes) {
    const metadata = (result.load_envelopes || []).find(envelope => String(envelope.envelopeId) === id);
    const section = resultSection(content, `Envelope ${id}${metadata ? ` - ${metadata.envelopeType}` : ''}`, true);
    const {
      node_displacements_limits: displacements = {},
      node_displacements_limits_exceed_col_height_200: exceedsLimits = {},
      ...otherResults
    } = summary;
    resultData(section, otherResults);
    const displacementSection = resultSection(section, 'Displacements Exceed Limits (Col Height/200)?');
    resultTable(displacementSection, ['min_X', 'max_X', 'min_Z', 'max_Z'].map(extreme => ({
      extreme: resultLabel(extreme),
      displacement: displacements[extreme],
      exceeds_limit: exceedsLimits[extreme],
    })));
  }
}

function renderTeklaResults(result) {
  const content = openResultPanel('tekla');
  renderIntegrationStatus(content, result);
  const extra = Object.fromEntries(Object.entries(result).filter(([key]) => !['created', 'failed', 'errors'].includes(key)));
  if (Object.keys(extra).length) resultData(resultSection(content, 'Additional Results', true), extra);
}

function renderIdeaStaticaGroups(results) {
  const content = openResultPanel('idea');
  for (const [group, result] of Object.entries(results)) {
    const section = resultSection(content, group, true);
    renderIdeaStaticaResults(result, section);
  }
}

function renderIdeaStaticaResults(result, parent = null) {
  const content = parent || openResultPanel('idea');
  content.append(resultElement('p', `Connection ${result.connection_id}: ${result.passed ? 'all checks passed.' : 'one or more checks failed.'}`,
    `result-notice ${result.passed ? 'result-success' : 'result-failure'}`));

  content.append(resultElement('h3', 'Check Summary'));
  const checks = result.check_summary?.summary || [];
  resultTable(content, checks.map(check => ({
    name: check.name,
    check_value: check.check_value,
    check_status: check.check_status,
    skipped: check.skipped,
  })), undefined, key => ({ name: 'Name', check_value: 'Check Value', check_status: 'Check Status', skipped: 'Skipped' })[key]);
  // Flag failed, non-skipped checks so they stand out in the table.
  content.querySelectorAll('tbody tr').forEach((row, index) => {
    if (checks[index] && !checks[index].check_status && !checks[index].skipped) row.classList.add('result-failure');
  });

  content.append(resultElement('h3', 'Operation Parameters'));
  // Some parameters (e.g. booleans) come back with an empty value; show
  // the expression IDEA StatiCa resolved instead.
  resultTable(content, (result.check_summary?.operation_parameters || []).map(param => ({
    description: param.description + '(' + param.key + ')',
    value: param.expression // param.value === '' ? param.expression : param.value,
  })), undefined, key => ({ description: 'Description', value: 'Value' })[key]);
}

function initializePaneDividers() {
  const app = document.getElementById('app');
  const input = document.getElementById('left-pane');
  const results = document.getElementById('results-pane');
  for (const [pane, variable, direction, minimum] of [
    [input, '--input-width', 1, 260],
    [results, '--results-width', -1, 240],
  ]) {
    const divider = resultElement('div', undefined, 'pane-divider');
    divider.id = pane === results ? 'results-divider' : 'input-divider';
    divider.setAttribute('role', 'separator');
    divider.setAttribute('aria-orientation', 'vertical');
    divider.setAttribute('aria-label', pane === input ? 'Resize input panel' : 'Resize results panel');
    divider.setAttribute('aria-controls', pane.id);
    divider.tabIndex = 0;
    divider.hidden = pane.hidden;
    if (pane === input) pane.after(divider);
    else pane.before(divider);
    const setWidth = width => {
      const other = pane === input ? results : input;
      const otherWidth = other.hidden ? 0 : other.getBoundingClientRect().width;
      const maximum = Math.max(minimum, app.clientWidth - otherWidth - 180);
      const nextWidth = Math.round(Math.max(minimum, Math.min(width, maximum)));
      app.style.setProperty(variable, `${nextWidth}px`);
      divider.setAttribute('aria-valuenow', String(nextWidth));
      divider.setAttribute('aria-valuemin', String(minimum));
      divider.setAttribute('aria-valuemax', String(Math.round(maximum)));
    };
    let startX = 0;
    let startWidth = 0;
    divider.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault();
      startX = event.clientX;
      startWidth = pane.getBoundingClientRect().width;
      divider.setPointerCapture(event.pointerId);
      document.body.classList.add('resizing-pane');
    });
    divider.addEventListener('pointermove', event => {
      if (divider.hasPointerCapture(event.pointerId)) setWidth(startWidth + direction * (event.clientX - startX));
    });
    const finish = event => {
      if (divider.hasPointerCapture(event.pointerId)) divider.releasePointerCapture(event.pointerId);
      document.body.classList.remove('resizing-pane');
    };
    divider.addEventListener('pointerup', finish);
    divider.addEventListener('pointercancel', finish);
    divider.addEventListener('lostpointercapture', () => document.body.classList.remove('resizing-pane'));
    divider.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      setWidth(pane.getBoundingClientRect().width + direction * (event.key === 'ArrowRight' ? 20 : -20));
    });
  }
}

initializePaneDividers();
