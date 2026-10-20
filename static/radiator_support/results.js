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

function resultTable(parent, records, columns) {
  if (!records.length) {
    parent.append(resultElement('p', 'No results available.'));
    return;
  }
  const keys = columns || [...new Set(records.flatMap(record => Object.keys(record)))];
  const wrapper = resultElement('div', undefined, 'result-table-wrap');
  const table = resultElement('table');
  const head = resultElement('thead');
  const heading = resultElement('tr');
  for (const key of keys) {
    const cell = resultElement('th', resultLabel(key));
    cell.scope = 'col';
    heading.append(cell);
  }
  head.append(heading);
  table.append(head);
  const body = resultElement('tbody');
  for (const record of records) {
    const row = resultElement('tr');
    for (const key of keys) {
      const value = record[key];
      const cell = resultElement('td', resultValue(value));
      cell.title = typeof value === 'object' ? JSON.stringify(value) : String(value ?? '');
      if (typeof value === 'string') cell.classList.add('result-text');
      if ((key === 'maximum' || key === 'minimum') && typeof value === 'number' && value > 1) {
        cell.classList.add('result-failure');
      }
      row.append(cell);
    }
    body.append(row);
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

function renderStaadResults(result) {
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
    resultData(section, summary);
  }
}

function renderTeklaResults(result) {
  const content = openResultPanel('tekla');
  renderIntegrationStatus(content, result);
  const extra = Object.fromEntries(Object.entries(result).filter(([key]) => !['created', 'failed', 'errors'].includes(key)));
  if (Object.keys(extra).length) resultData(resultSection(content, 'Additional Results', true), extra);
}