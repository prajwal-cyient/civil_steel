/* Chat presentation only; engineering actions retain the existing guarded backend. */
(() => {
  'use strict';
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text) n.textContent = text; return n; };
  const icons = {
    back: 'M19 12H5m6-6-6 6 6 6', more: 'M5 12h.01M12 12h.01M19 12h.01',
    newChat: 'M12 5H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7M16 3l5 5M10 14l1-5 7-7 5 5-7 7-6 1Z',
    history: 'M3 11a9 9 0 1 1 2 7M3 4v7h7m2-4v5l3 2',
    plus: 'M12 5v14M5 12h14', send: 'M12 19V5m-6 6 6-6 6 6', stop: 'M6 6h12v12H6Z',
    remove: 'M6 6l12 12M6 18 18 6', document: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Zm0 0v6h6M8 13h8M8 17h6'
  };
  function iconButton(name, label) {
    const button = el('button', 'sc-icon'); button.type = 'button'; button.title = label; button.setAttribute('aria-label', label);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    for (const [key, value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.7','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'})) svg.setAttribute(key,value);
    const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', icons[name]); svg.append(path); button.append(svg); return button;
  }
  const toggle = el('button', '', 'Engineering chat'); toggle.id = 'structural-chat-toggle'; toggle.type = 'button'; toggle.setAttribute('aria-expanded', 'false');
  const panel = el('section'); panel.id = 'structural-chat'; panel.hidden = true; panel.setAttribute('aria-label', 'Structural engineering chat');
  const header = el('header', 'sc-header'), back = iconButton('back', 'Close chat'), heading = el('strong', 'sc-title', 'New chat');
  const more = iconButton('more', 'Chat options'), previous = iconButton('history', 'Chat history'), fresh = iconButton('newChat', 'New chat');
  more.setAttribute('aria-expanded', 'false'); more.setAttribute('aria-haspopup', 'menu'); header.append(back, heading, more, previous, fresh);
  const menu = el('div', 'sc-menu'); menu.hidden = true; menu.setAttribute('role', 'menu');
  function menuItem(text) { const b = el('button', '', text); b.type = 'button'; b.setAttribute('role', 'menuitem'); menu.append(b); return b; }
  const rename = menuItem('Rename'), share = menuItem('Share'), copy = menuItem('Copy chat');
  const renameForm = el('form', 'sc-rename'); renameForm.hidden = true;
  const titleInput = document.createElement('input'); titleInput.type = 'text'; titleInput.maxLength = 120; titleInput.setAttribute('aria-label', 'Chat title');
  const saveTitle = el('button', '', 'Save'); saveTitle.type = 'submit';
  const cancelTitle = el('button', '', 'Cancel'); cancelTitle.type = 'button'; renameForm.append(titleInput, saveTitle, cancelTitle);
  const conversations = el('div', 'sc-conversations'); conversations.hidden = true; conversations.setAttribute('aria-label', 'Saved conversations');
  const config = el('div', 'sc-config', 'Checking model configuration...');
  const history = el('div', 'sc-history'); history.setAttribute('role', 'log'); history.setAttribute('aria-live', 'polite');
  const progress = el('div', 'sc-progress'); progress.setAttribute('role', 'status');
  const file = document.createElement('input'); file.type = 'file'; file.multiple = true; file.hidden = true; file.setAttribute('aria-label', 'Attach files');
  const attachments = el('div', 'sc-attachments'); attachments.hidden = true; attachments.setAttribute('aria-label', 'Attached files');
  const form = el('form', 'sc-input'), attach = iconButton('plus', 'Attach files');
  const input = document.createElement('textarea'); input.rows = 1; input.placeholder = 'Ask about your structure...'; input.maxLength = 8000; input.setAttribute('aria-label', 'Chat message');
  const stop = iconButton('stop', 'Stop turn'); stop.hidden = true;
  const send = iconButton('send', 'Send'); send.type = 'submit'; form.append(attach, input, stop, send);
  panel.append(header, menu, renameForm, conversations, config, history, progress, file, attachments, form); document.body.append(toggle, panel);
  let sessionId = new URLSearchParams(location.search).get('chat'), turnId = null;
  let busy = false, ready = false, seen = new Set(), revision = 0, uploaded = null, selectedRun = null, lastViewerRun = null;
  let draftFiles = [], sessionPromise = null;
  const watchedTurns = new Map();
  const bridge = () => window.structuralChatBridge;
  const scroll = () => { history.scrollTop = history.scrollHeight; };
  function canonical(v) { return Array.isArray(v) ? v.map(canonical) : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map(k => [k, canonical(v[k])])) : v; }
  const equal = (a,b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
  function message(role, text) { const n = el('div', `sc-message ${role}`); n.append(el('small', '', role === 'user' ? 'You' : role === 'error' ? 'Error' : 'Assistant'), document.createTextNode(text)); history.append(n); scroll(); }
  async function request(url, options) { const r = await fetch(url, options); const data = await r.json().catch(() => ({})); if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || `HTTP ${r.status}`)); return data; }
  const jsonOptions = (method, data) => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  function remember(id) {
    sessionId = id; localStorage.setItem('structuralChatSession', id);
    const url = new URL(location.href); url.searchParams.set('chat', id);
    window.history.replaceState(null, '', url);
  }
  function setBusy(value) { busy = value; send.disabled = busy || !ready || draftFiles.some(f => f.uploading && !f.removed); attach.disabled = busy; file.disabled = busy; stop.hidden = !busy; send.hidden = busy; for (const button of attachments.querySelectorAll('button')) button.disabled = busy; }
  function discardCards() { for (const item of draftFiles) if (item.preview) URL.revokeObjectURL(item.preview); draftFiles = []; attachments.replaceChildren(); attachments.hidden = true; }
  async function ensureSession(token) {
    if (sessionId) return sessionId;
    if (!sessionPromise) sessionPromise = request('/api/v2/chat/sessions',{method:'POST'});
    const session = await sessionPromise;
    if (token === revision) remember(session.id);
    return session.id;
  }
  function fileCard(item, removable = false) {
    const card = el('div','sc-file-card'); card.dataset.attachmentId = item.id || ''; card.title = `${item.name}\n${item.notice || ''}`;
    const picture = el('div','sc-file-picture');
    if (item.preview) { const image = document.createElement('img'); image.src = item.preview; image.alt = item.name; picture.append(image); }
    else { const symbol = iconButton('document','File'); const svg = symbol.querySelector('svg'); picture.append(svg); }
    const label = el('div','sc-file-label'); label.append(el('strong','',item.name),el('span','',`${item.kind || item.name.split('.').pop() || 'FILE'} · ${Math.max(1,Math.ceil(item.size / 1024))} KB`));
    const status = el('small','sc-file-status',item.uploading ? 'Uploading...' : item.status === 'readable' ? 'Ready' : item.status || 'Attached'); status.title = item.notice || ''; label.append(status);
    card.append(picture,label);
    if (removable) { const remove = iconButton('remove',`Remove ${item.name}`); remove.classList.add('sc-file-remove'); remove.onclick = () => removeFile(item); remove.disabled = busy; card.append(remove); }
    return card;
  }
  function drawAttachments() { attachments.replaceChildren(...draftFiles.filter(f => !f.removed).map(f => fileCard(f,true))); attachments.hidden = !attachments.childElementCount; setBusy(busy); }
  function sentFiles(items) { if (!items?.length) return; const row = el('div','sc-sent-files'); row.append(...items.map(f => fileCard(f))); history.append(row); }
  async function removeFile(item) {
    const token = revision; item.removed = true;
    if (item.applied && bridge() && equal(bridge().getInput(),item.applied)) bridge().setInput(item.previous);
    if (uploaded === item.project_input) uploaded = null;
    drawAttachments(); file.value = ''; progress.textContent = `Removed ${item.name}`;
    if (!item.id || item.uploading) return; // An upload in flight deletes its result when it finishes.
    try { await request(`/api/v2/chat/sessions/${encodeURIComponent(item.session)}/attachments/${encodeURIComponent(item.id)}`,{method:'DELETE'}); }
    catch(e) { if (token === revision) { item.removed = false; drawAttachments(); message('error',`Could not remove ${item.name}: ${e.message}`); } }
  }
  function closeMenu() { menu.hidden = true; more.setAttribute('aria-expanded', 'false'); }
  function resetView() {
    proposalViews.clear();
    revision++; discardCards(); sessionPromise = null; sessionId = null; turnId = null; selectedRun = null; lastViewerRun = null; uploaded = null; file.value = ''; input.value = '';
    for (const [id, entry] of [...watchedTurns.entries()]) {
      if (!entry.nativeActive && entry.turn?.status !== 'RUNNING') {
        watchedTurns.delete(id);
        bridge()?.setTurnBusy(id, false);
      }
    }
    localStorage.removeItem('structuralChatSession'); history.replaceChildren(); seen.clear(); progress.textContent = ''; heading.textContent = 'New chat';
    closeMenu(); renameForm.hidden = true; conversations.hidden = true; setBusy(false);
    const url = new URL(location.href); url.searchParams.delete('chat'); window.history.replaceState(null, '', url);
  }
  function greeting() { message('assistant', 'Describe what you need, or attach files with +. Your current form inputs are included automatically.'); }
  function toolEvent(event) {
    if (!['tools_selected','tool_started','tool_completed','tool_error'].includes(event.type)) return;
    const box = el('details', 'sc-tool'); box.dataset.state = event.type;
    box.append(el('summary', '', `${event.type.replaceAll('_',' ')}: ${event.tool || (event.names || []).join(', ')}`));
    if (event.parameters) box.append(el('pre', '', JSON.stringify(event.parameters,null,2)));
    if (event.result || event.error) box.append(el('pre', '', JSON.stringify(event.result || event.error,null,2))); history.append(box);
  }
  const proposalViews = new Map();
  function showProposal(plan) {
    if (proposalViews.has(plan.id)) return;
    const box = el('section', 'sc-change-preview');
    box.append(el('strong', '', 'Proposed project change — approval required'));
    if (plan.source_run_id) box.append(el('p','','Source run: '+plan.source_run_id));
    for (const [group,count] of Object.entries(plan.physical_joint_counts || {})) {
      box.append(el('p','',`${group}: entire group (${count} physical joints)`));
    }
    const table = el('table', 'sc-change-table'), head = el('tr');
    for (const label of ['Group / section', 'Parameter', 'Current', 'Requested']) head.append(el('th', '', label));
    table.append(head);
    for (const change of plan.changes || []) {
      const row = el('tr');
      const cells = [change.joint_group || change.section, change.parameter,
        change.old_mm != null ? `${change.old_mm} mm` : JSON.stringify(change.old),
        change.new_mm != null ? `${change.new_mm} mm` : JSON.stringify(change.new)];
      for (const value of cells) row.append(el('td', '', value ?? 'Not defined'));
      table.append(row);
    }
    box.append(table, el('p', '', `Execution: ${(plan.steps || []).join(' → ') || 'Save inputs only; analysis required'}`),
      el('p', '', plan.notice || 'No changes have been applied.'));
    if (plan.run_policy && Object.keys(plan.run_policy).length) {
      const rules = el('details'); rules.append(el('summary','','Rules for the proposed execution'),el('pre','',JSON.stringify(plan.run_policy,null,2))); box.append(rules);
    }
    if (plan.candidate) {
      const inputs = el('details'); inputs.append(el('summary','','Inputs for proposed execution'),el('pre','',JSON.stringify(plan.candidate,null,2))); box.append(inputs);
    }
    const status = el('p', '', plan.status), approve = el('button', 'sc-change-approve', 'Approve & Apply'), reject = el('button', 'sc-change-reject', 'Reject');
    approve.type = reject.type = 'button';
    approve.disabled = reject.disabled = plan.status !== 'PENDING';
    async function act(approved) {
      const token = revision;
      approve.disabled = reject.disabled = true;
      try {
        const result = await request(`/api/v2/chat/sessions/${encodeURIComponent(plan.session_id)}/changes/${encodeURIComponent(plan.id)}/${approved ? 'approve' : 'reject'}`,
          {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(approved ? {digest:plan.digest,allow_native_tools:true} : {})});
        status.textContent = approved ? 'Approved; executing the stored plan.' : 'Rejected; nothing changed.';
        if (approved) {
          watchTurn(result.turn_id);
          if (token === revision) { turnId = result.turn_id; setBusy(true); await poll(turnId, token); }
        }
      } catch (error) {
        if (token === revision) { message('error',error.message); setBusy(false); }
        approve.disabled = reject.disabled = false;
      }
    }
    approve.onclick = () => act(true); reject.onclick = () => act(false);
    box.append(status, approve, reject); history.append(box); proposalViews.set(plan.id, box);
  }
  async function loadRun(id, token) {
    if (!id || !bridge()) return;
    const state = await request(`/api/v2/agentic/runs/${encodeURIComponent(id)}`); if (token !== revision) return;
    const active = await request('/api/v2/agentic/active'); if (token !== revision) return;
    // Viewing an older conversation must not replace another worker's live view.
    if (active && active.run_id !== id) return;
    if (state.readiness?.preview_only) bridge().showPreview(state.frame_candidate, id);
    else bridge().showRun(state, {active: active?.run_id === id});
    lastViewerRun = bridge()?.getRunId();
  }
  function watchTurn(id) {
    if (watchedTurns.has(id)) return watchedTurns.get(id);
    const entry = {turn: null, error: null, nativeActive: false, waiters: new Set()};
    watchedTurns.set(id, entry);
    bridge()?.setTurnBusy(id, true);
    const notify = () => { for (const resolve of entry.waiters) resolve(); entry.waiters.clear(); };
    // Keep ownership/progress monitoring alive when the user changes conversations.
    entry.task = (async () => {
      let ownedRun = null, displayedRun = null;
      try {
        while (true) {
          const turn = await request(`/api/v2/chat/turns/${encodeURIComponent(id)}`);
          entry.nativeActive = false;
          if (turn.run_id) {
            const active = await request('/api/v2/agentic/active');
            entry.nativeActive = active?.run_id === turn.run_id;
            if (entry.nativeActive) {
              ownedRun = turn.run_id;
              if (active.readiness?.preview_only) bridge()?.showPreview(active.frame_candidate, active.run_id);
              else bridge()?.showRun(active, {active: true});
              displayedRun = active.run_id;
            } else if (!active && (ownedRun || displayedRun !== turn.run_id)) {
              // A quick action may finish between polls. Show its latest saved
              // candidate while the model plans the next action in this turn.
              const state = await request(`/api/v2/agentic/runs/${encodeURIComponent(turn.run_id)}`);
              if (state.readiness?.preview_only) bridge()?.showPreview(state.frame_candidate, state.run_id);
              else bridge()?.showRun(state, {active: false});
              displayedRun = state.run_id;
              ownedRun = null;
            }
          }
          entry.turn = turn;
          // The solver is finished at Gate 3: release desktop controls so the
          // engineer can approve, but keep observing this unfinished request.
          bridge()?.setTurnBusy(id, turn.status === 'RUNNING' || entry.nativeActive);
          notify();
          if (!['RUNNING','AWAITING_APPROVAL'].includes(turn.status) && !entry.nativeActive) break;
          await new Promise(resolve => setTimeout(resolve, 1200));
        }
      } catch (error) { entry.error = error; }
      finally {
        bridge()?.setTurnBusy(id, false);
        notify();
        watchedTurns.delete(id);
      }
    })();
    return entry;
  }
  async function poll(id, token, shownAssistant = '') {
    const observed = watchTurn(id);
    let waitingDisplayed = false;
    while (token === revision) {
      if (observed.error) throw observed.error;
      const turn = observed.turn;
      if (!turn) { await new Promise(resolve => observed.waiters.add(resolve)); continue; }
      for (const event of turn.events) { const key = `${id}:${event.sequence}`; if (!seen.has(key)) { seen.add(key); toolEvent(event); } }
      if (turn.progress?.current_agent) progress.textContent = `${turn.progress.current_agent}: ${turn.progress.agents?.[turn.progress.current_agent]?.message || turn.progress.status}`;
      if (turn.status !== 'RUNNING' && turn.assistant && turn.assistant !== shownAssistant) {
        const text = shownAssistant && turn.assistant.startsWith(shownAssistant) ? turn.assistant.slice(shownAssistant.length).trim() : turn.assistant;
        if (text) message('assistant', text);
        shownAssistant = turn.assistant;
      }
      if (turn.status === 'AWAITING_APPROVAL' && !observed.nativeActive) {
        if (!waitingDisplayed && turn.run_id) {
          selectedRun = turn.run_id;
          await loadRun(turn.run_id, token);
          if (token !== revision) return;
          waitingDisplayed = true;
        }
        // Do not show a stop-worker action while there is no worker to stop.
        progress.textContent = 'Waiting for engineer approval — click Accept & Sign Off on the dashboard.';
        stop.hidden = true; send.hidden = false; send.disabled = true;
      } else if (turn.status !== 'RUNNING' && !observed.nativeActive) {
        if (turn.error) message('error', turn.error);
        for (const plan of turn.change_proposals || []) {
          const current = await request(`/api/v2/chat/sessions/${encodeURIComponent(plan.session_id)}/changes/${encodeURIComponent(plan.id)}`);
          if (token !== revision) return;
          showProposal(current);
        }
        if (turn.artifacts) {
          message('assistant', `Saved change files: ${turn.artifacts.files.join(', ')}\nFolder: ${turn.artifacts.directory}`);
        }
        if (turn.project_input && bridge()) { bridge().setInput(turn.project_input); if (uploaded) uploaded = turn.project_input; }
        if (turn.run_id) {
          selectedRun = turn.run_id;
          const link = el('a', 'sc-link', `Open output JSON: ${turn.run_id}`); link.href = `/api/v2/agentic/runs/${encodeURIComponent(turn.run_id)}`; link.target = '_blank'; link.rel = 'noopener'; history.append(link);
          await loadRun(turn.run_id, token).catch(e => { if (token === revision) message('error', `Output display: ${e.message}`); });
        }
        if (token !== revision) return;
        progress.textContent = turn.status === 'COMPLETED' ? 'Response complete' : turn.status; turnId = null; setBusy(false); scroll(); return;
      }
      await new Promise(resolve => observed.waiters.add(resolve));
    }
  }
  async function openSession(id) {
    resetView(); remember(id); const token = revision; setBusy(true);
    try {
      const session = await request(`/api/v2/chat/sessions/${encodeURIComponent(id)}`); if (token !== revision) return;
      heading.textContent = session.title || 'Chat'; selectedRun = session.run_id; lastViewerRun = bridge()?.getRunId();
      if (session.project_input?.grid && bridge()) bridge().setInput(session.project_input);
      draftFiles = (session.draft_attachments || []).map(f => ({...f,session:id})); drawAttachments();
      let pendingAssistant = '';
      for (const turn of session.turns) {
        message('user', turn.message); sentFiles(turn.attachments); for (const event of turn.events) { seen.add(`${turn.id}:${event.sequence}`); toolEvent(event); }
        if (turn.assistant) message('assistant', turn.assistant); if (turn.error) message('error', turn.error);
        if (turn.artifacts) message('assistant', `Saved change files: ${turn.artifacts.files.join(', ')}\nFolder: ${turn.artifacts.directory}`);
        if (['RUNNING','AWAITING_APPROVAL'].includes(turn.status)) { turnId = turn.id; pendingAssistant = turn.assistant || ''; }
      }
      for (const plan of session.pending_changes || []) showProposal(plan);
      if (!session.turns.length) greeting(); if (turnId) await poll(turnId,token,pendingAssistant); else setBusy(false);
    } catch(e) { if (token === revision) { message('error', `Could not restore chat: ${e.message}`); setBusy(false); } }
  }
  async function newConversation() {
    resetView(); greeting(); const token = revision; fresh.disabled = true; setBusy(true);
    try { await ensureSession(token); }
    catch(e) { if (token === revision) message('error',e.message); }
    finally { fresh.disabled = false; if (token === revision) setBusy(false); }
  }
  toggle.onclick = () => { panel.hidden = !panel.hidden; toggle.setAttribute('aria-expanded',String(!panel.hidden)); if (!panel.hidden) input.focus(); };
  back.onclick = () => { panel.hidden = true; closeMenu(); toggle.setAttribute('aria-expanded','false'); };
  fresh.onclick = newConversation;
  more.onclick = () => { menu.hidden = !menu.hidden; more.setAttribute('aria-expanded',String(!menu.hidden)); if (!menu.hidden) rename.focus(); };
  document.addEventListener('click',e => { if (!menu.contains(e.target) && !more.contains(e.target)) closeMenu(); });
  panel.addEventListener('keydown',e => { if (e.key === 'Escape') { closeMenu(); renameForm.hidden = true; conversations.hidden = true; input.focus(); } });
  previous.onclick = async () => {
    closeMenu(); conversations.hidden = !conversations.hidden; if (conversations.hidden) return;
    const token = revision; conversations.replaceChildren(el('p','','Loading conversations...'));
    try {
      const data = await request('/api/v2/chat/sessions'); if (token !== revision) return;
      conversations.replaceChildren(el('strong','','Saved conversations')); if (!data.sessions.length) conversations.append(el('p','','No saved conversations yet.'));
      for (const item of data.sessions) { const button = el('button','sc-saved',`${item.title}${item.running ? ' · Running' : item.awaiting_approval ? ' · Awaiting approval' : ''}`); button.type = 'button'; button.onclick = () => openSession(item.id); conversations.append(button); }
    } catch(e) { if (token === revision) conversations.replaceChildren(el('p','',e.message)); }
  };
  rename.onclick = () => { closeMenu(); if (!sessionId) { progress.textContent = 'Start a chat before renaming it.'; return; } titleInput.value = heading.textContent; renameForm.hidden = false; titleInput.focus(); titleInput.select(); };
  cancelTitle.onclick = () => { renameForm.hidden = true; };
  renameForm.onsubmit = async e => {
    e.preventDefault(); const id = sessionId, token = revision; if (!id || !titleInput.value.trim()) return;
    try { const data = await request(`/api/v2/chat/sessions/${encodeURIComponent(id)}`,jsonOptions('PATCH',{title:titleInput.value.trim()})); if (token === revision) { heading.textContent = data.title; renameForm.hidden = true; } }
    catch(e) { if (token === revision) message('error',e.message); }
  };
  async function clipboard(text) {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
    const area = document.createElement('textarea'); area.value = text; area.style.cssText = 'position:fixed;opacity:0'; document.body.append(area); area.select();
    try { if (!document.execCommand('copy')) throw new Error('Clipboard unavailable in this browser.'); } finally { area.remove(); }
  }
  share.onclick = async () => {
    closeMenu(); if (!sessionId) { progress.textContent = 'Start a chat before sharing it.'; return; }
    const token = revision, url = new URL(location.href); url.searchParams.set('chat',sessionId);
    try { if (navigator.share) await navigator.share({title:heading.textContent,url:url.href}); else await clipboard(url.href); if (token === revision) progress.textContent = 'Chat link ready. It opens for people with access to this app server.'; }
    catch(e) { if (e.name !== 'AbortError' && token === revision) message('error',e.message); }
  };
  copy.onclick = async () => {
    closeMenu(); if (!sessionId) { progress.textContent = 'Start a chat before copying it.'; return; }
    const id = sessionId, token = revision;
    try {
      const session = await request(`/api/v2/chat/sessions/${encodeURIComponent(id)}`);
      const text = [session.title || 'Chat',...session.turns.flatMap(t => [`You: ${t.message}`, ...(t.attachments || []).map(f => `File: ${f.name} (${f.status})`),
        ...t.events.filter(e => ['tool_completed','tool_error'].includes(e.type)).map(e => `${e.tool}: ${JSON.stringify(e.result || e.error)}`),
        ...(t.assistant ? [`Assistant: ${t.assistant}`] : []),...(t.error ? [`Error: ${t.error}`] : [])])].join('\n\n');
      await clipboard(text); if (token === revision) progress.textContent = 'Chat copied.';
    } catch(e) { if (token === revision) message('error',e.message); }
  };
  attach.onclick = () => file.click();
  file.onchange = async () => {
    const token = revision;
    const chosen = [...file.files]; file.value = '';
    for (const selected of chosen) {
      if (token !== revision) return;
      if (draftFiles.filter(f => !f.removed).length >= 8) { message('error','Attach at most eight files per message.'); break; }
      if (!selected.size || selected.size > 20 * 1024 * 1024) { message('error',`${selected.name}: choose a non-empty file of at most 20 MB.`); continue; }
      const item = {name:selected.name,size:selected.size,uploading:true,kind:selected.name.split('.').pop()};
      if (/^image\/(png|jpeg|webp|gif|bmp)$/.test(selected.type)) item.preview = URL.createObjectURL(selected);
      draftFiles.push(item); drawAttachments();
      try {
        const id = await ensureSession(token); if (token !== revision) return; item.session = id;
        const result = await request(`/api/v2/chat/sessions/${encodeURIComponent(id)}/attachments?filename=${encodeURIComponent(selected.name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:selected});
        Object.assign(item,result); item.uploading = false;
        if (item.removed) { await request(`/api/v2/chat/sessions/${encodeURIComponent(id)}/attachments/${encodeURIComponent(item.id)}`,{method:'DELETE'}); continue; }
        if (token !== revision) return;
        if (result.project_input) {
          // Replace a previous draft grid import, so removing the new card can restore the original form.
          for (const prior of draftFiles.filter(f => f !== item && f.applied && !f.removed)) await removeFile(prior);
          item.previous = bridge()?.getInput(); uploaded = result.project_input; selectedRun = null;
          bridge()?.setInput(uploaded); item.applied = bridge()?.getInput();
        }
        progress.textContent = result.notice || `Attached ${selected.name}`;
      } catch(e) { item.uploading = false; item.status = 'Upload failed'; item.notice = e.message; if (token === revision && !item.removed) message('error',`${selected.name}: ${e.message}`); }
      finally { if (token === revision) drawAttachments(); }
    }
  };
  form.onsubmit = async e => {
    e.preventDefault(); if (busy || !ready || send.disabled || !input.value.trim()) return;
    const token = revision, text = input.value.trim(); setBusy(true);
    const pending = `pending:${token}`;
    bridge()?.setTurnBusy(pending, true);
    try {
      if (!bridge() && !uploaded) throw new Error('The form/viewer has not loaded yet. Wait for it, or attach project JSON.');
      const inputs = bridge()?.getInput() || uploaded;
      const attached = draftFiles.filter(f => !f.removed && f.id);
      if (draftFiles.some(f => !f.removed && !f.id)) throw new Error('Remove failed uploads before sending.');
      const payload = {session_id:sessionId,message:text,project_input:inputs,allow_native_tools:true,attachment_ids:attached.map(f => f.id)};
      // Bind results only when their source inputs match. Never reuse stale solver evidence.
      const viewerRun = bridge()?.getRunId();
      const displayed = viewerRun && viewerRun !== lastViewerRun ? viewerRun : selectedRun || viewerRun;
      if (displayed) { const state = await request(`/api/v2/agentic/runs/${encodeURIComponent(displayed)}`); if (token !== revision) return; if (equal(state.raw_input,inputs)) payload.run_id = displayed; }
      if (!payload.session_id) { payload.session_id = await ensureSession(token); if (token !== revision) return; }
      progress.textContent = 'Waiting for tool selection...'; message('user',text); input.value = '';
      const result = await request('/api/v2/chat/messages',jsonOptions('POST',payload));
      watchTurn(result.turn_id);
      bridge()?.setTurnBusy(pending, false); // The observer now owns the request lock.
      if (token !== revision) return;
      sentFiles(attached); discardCards();
      remember(result.session_id); turnId = result.turn_id; if (heading.textContent === 'New chat') heading.textContent = text.slice(0,80); await poll(turnId,token);
    } catch(error) { if (token === revision) { message('error',error.message); progress.textContent = 'Open Chat history to check saved turn status.'; setBusy(false); } }
    finally { bridge()?.setTurnBusy(pending, false); }
  };
  input.onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } };
  stop.onclick = async () => { if (!turnId) return; const token = revision; try { await request(`/api/v2/chat/turns/${encodeURIComponent(turnId)}/cancel`,{method:'POST'}); } catch(e) { if (token === revision) message('error',e.message); } };
  if (bridge()) bridge().resetChat = () => { resetView(); greeting(); };
  (async () => {
    const params = new URLSearchParams(location.search);
    const navType = typeof performance !== 'undefined'
      ? performance.getEntriesByType?.('navigation')?.[0]?.type
      : null;
    const isDashboardReload = navType === 'reload' && typeof bridge()?.showRun === 'function';
    let cfg = null;
    try {
      cfg = await request('/api/v2/chat/config');
      ready = cfg.enabled;
      config.textContent = ready ? `Model: ${cfg.model}` : cfg.reason;
      setBusy(busy);
    } catch (e) {
      config.textContent = e.message;
    }
    const priorBoot = localStorage.getItem('structuralChatBootId');
    const serverRestarted = Boolean(cfg?.boot_id && priorBoot && priorBoot !== cfg.boot_id);
    if (cfg?.boot_id) localStorage.setItem('structuralChatBootId', cfg.boot_id);

    let hasActiveWorker = false;
    if (isDashboardReload && !serverRestarted) {
      try {
        const active = await request('/api/v2/agentic/active');
        hasActiveWorker = Boolean(active?.run_id);
      } catch (_) {}
    }

    const keepOnReload = !isDashboardReload || hasActiveWorker;
    if (!serverRestarted && keepOnReload && sessionId && params.get('new') !== '1') {
      await openSession(sessionId);
      return;
    }
    resetView();
    if (serverRestarted || (isDashboardReload && !hasActiveWorker)) {
      const cleanUrl = new URL(location.href);
      cleanUrl.searchParams.delete('chat');
      cleanUrl.searchParams.delete('run_id');
      cleanUrl.searchParams.delete('new');
      window.history.replaceState(null, '', cleanUrl);
    }
    greeting();
  })();
})();
