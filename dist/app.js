'use strict';

(() => {
  const $ = selector => document.querySelector(selector);
  const $$ = selector => Array.from(document.querySelectorAll(selector));
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pretty = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  const number = value => Number.isFinite(Number(value)) && value !== null && value !== '' ? new Intl.NumberFormat('en', {maximumFractionDigits: 2}).format(Number(value)) : '—';
  const short = (value, n = 31) => String(value ?? '').length > n ? String(value).slice(0, n - 1) + '…' : String(value ?? '');
  const badge = status => `<span class="badge ${escape(status)}">${escape(status === 'rejected' ? 'Claim rejected' : status.charAt(0).toUpperCase() + status.slice(1))}</span>`;
  let state = null, capture = null, mode = 'empty', stage = 0, filter = 'all';
  let selection = null, busy = false, replayTimer = null, pollTimer = null, toastTimer = null;
  const details = new Map();

  async function request(url, options = {}) {
    const response = await fetch(url, {...options, headers: {'Content-Type':'application/json', ...options.headers}, signal: AbortSignal.timeout(15000)});
    if (!response.ok) {
      const body = await response.text();
      let message = body;
      try { const parsed = JSON.parse(body); message = parsed.error || parsed.detail || body; } catch (_) {}
      throw new Error(short(message || `Request failed (${response.status})`, 280));
    }
    return response.json();
  }

  function notify(message, error = false) {
    $('#toast').textContent = message;
    $('#toast').hidden = false;
    $('#toast').style.background = error ? '#884332' : '';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('#toast').hidden = true; }, error ? 7000 : 4000);
  }

  function setNotice(message) { $('#notice').textContent = message; $('#notice').hidden = !message; }
  function currentRecord(id) { return state?.records?.find(r => r.id === id); }
  function recordTitle(id) { return currentRecord(id)?.title || id; }
  function currentCandidate(id) { return state?.candidates?.find(c => c.id === id); }
  function cost(solution) { return solution?.feasible === false ? 'Infeasible' : number(solution?.cost); }
  function ids(value) { return Array.isArray(value) ? value.map(v => typeof v === 'object' ? v.id : v) : []; }
  function takeState(result) {
    const next = result?.state || result;
    if (!next || !Array.isArray(next.records)) throw new Error('The backend returned an unexpected run format.');
    state = next;
    if (selection?.type === 'candidate' && !currentCandidate(selection.id)) selection = null;
    if (!selection) {
      const fallback = state.candidates?.find(c => c.status === 'confirmed' && c.evidence?.method === 'provenance') || state.candidates?.[0];
      const id = state.next_candidate?.id || fallback?.id;
      if (id) selection = {type:'candidate', id};
    }
    render();
  }

  function render() {
    if (!state) return;
    const records = state.records || [], candidates = state.candidates || [];
    const unresolved = candidates.filter(c => c.status === 'unresolved').length;
    const confirmed = candidates.filter(c => c.status === 'confirmed').length;
    const rejected = candidates.filter(c => c.status === 'rejected').length;
    const running = state.agent?.status === 'running';
    const inReplay = Boolean(replayTimer);
    $('#connection-badge').className = `badge ${mode === 'live' ? 'live' : 'recorded'}`;
    $('#connection-badge').innerHTML = `<span class="dot"></span>${mode === 'live' ? 'Local backend connected' : 'Recorded run'}`;
    $('#release-title').textContent = state.title || 'Candidate dataset release';
    $('#project-description').textContent = state.description || 'Trace the evidence. Resolve the conflicts. Keep the most valuable data.';
    $('#revision').textContent = `REV ${state.revision ?? '—'} · ${state.run_id || 'unassigned run'}`;
    $('#train-count').textContent = number(records.filter(r => r.split === 'train').length);
    $('#eval-count').textContent = number(records.filter(r => r.split === 'eval').length);
    $('#unresolved-count').textContent = number(unresolved);
    $('#candidate-total').textContent = `of ${candidates.length} relationships`;
    $('#resolution-note').textContent = `${confirmed} confirmed · ${rejected} rejected`;
    const lower = state.bounds?.lower, upper = state.bounds?.upper;
    $('#bound-value').textContent = lower && upper ? cost(lower) === cost(upper) ? cost(lower) : `${cost(lower)}–${cost(upper)}` : '—';
    $('#bound-note').textContent = state.bounds?.gap === 0 ? 'Bounds agree under the current evidence' : 'Confirmed conflicts → all candidate conflicts';
    $('#engine-name').textContent = state.engine?.name || 'FalkorDB';
    $('#engine-method').textContent = state.engine?.native_maxflow ? ' · Native max flow + evidence graph' : ' · Evidence graph + repair solver';
    $('#engine-version').textContent = `${mode === 'recorded' ? 'Captured · ' : ''}${state.engine?.version || 'Version unreported'}`;
    $('#replay-strip').hidden = mode !== 'recorded';
    $('#replay-position').textContent = `${stage + 1} / ${capture?.snapshots?.length || 1}`;
    $('#replay-context').textContent = `Captured backend results. Replay does not execute tools or graph queries.`;
    $('#prev-button').disabled = stage <= 0 || inReplay;
    $('#next-button').disabled = stage >= (capture?.snapshots?.length || 1) - 1 || inReplay;
    $('#replay-end').disabled = stage >= (capture?.snapshots?.length || 1) - 1 || inReplay;
    $('#run-button').disabled = mode === 'live' ? busy || running || !unresolved : busy;
    $('#run-button').classList.toggle('busy', running || inReplay);
    $('#run-label').textContent = mode === 'recorded' ? inReplay ? 'Pause replay' : 'Replay verified run' : running ? 'Investigation running' : 'Run deterministic policy';
    $('#llm-button').hidden = mode !== 'live' || !state.provider_available;
    $('#llm-button').disabled = busy || running || !unresolved;
    $('#reset-button').hidden = mode !== 'live';
    $('#reset-button').disabled = busy || running;
    $('#queue-badge').textContent = `${candidates.length} relationships`;
    const agentStatus = state.agent?.status || 'idle';
    $('#agent-status').className = `badge ${agentStatus === 'idle' ? 'muted' : escape(agentStatus)}`;
    $('#agent-status').textContent = `${mode === 'recorded' ? 'Captured · ' : ''}${agentStatus}`;
    const agentMode = state.agent?.mode;
    $('#agent-message').textContent = (agentMode ? `${agentMode === 'llm' ? 'LLM agent' : 'Deterministic policy'}. ` : '') + (state.agent?.message || 'Tool calls and observed results are recorded below.');
    renderGraph();
    renderCandidates();
    renderInspector();
    renderTimeline();
    renderRelease();
  }

  function renderGraph() {
    const records = state.records || [], candidates = state.candidates || [];
    if (!records.length) return;
    const training = records.filter(r => r.split === 'train'), evaluation = records.filter(r => r.split === 'eval');
    const maxRows = Math.max(training.length, evaluation.length, 1), height = Math.max(300, maxRows * 71 + 65);
    const nodeWidth = 228, nodeHeight = 48, leftX = 22, rightX = 448, width = 700;
    const placement = new Map();
    [training, evaluation].forEach((pool, side) => pool.forEach((r, i) => placement.set(r.id, {x:side ? rightX : leftX, y:58 + i * 71 + (maxRows - pool.length) * 35.5})));
    const visible = candidates.filter(c => filter === 'all' || c.status === filter);
    $('#graph-count').textContent = `${visible.length} ${visible.length === 1 ? 'relationship' : 'relationships'}`;
    const selected = selection?.type === 'candidate' ? currentCandidate(selection.id) : null;
    const excluded = new Set(ids(state.release?.excluded));
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="group" aria-label="Bipartite graph of candidate training and evaluation conflicts"><title>Candidate dataset conflict graph</title><text class="graph-heading" x="${leftX}" y="28">TRAINING POOL · ${training.length}</text><text class="graph-heading" x="${rightX}" y="28">EVALUATION POOL · ${evaluation.length}</text>`;
    for (const c of visible) {
      const from = placement.get(c.train_id), to = placement.get(c.eval_id);
      if (!from || !to) continue;
      const x1 = from.x + nodeWidth, y1 = from.y + nodeHeight / 2, x2 = to.x, y2 = to.y + nodeHeight / 2;
      const d = `M${x1},${y1} C${x1 + 80},${y1} ${x2 - 80},${y2} ${x2},${y2}`;
      const isSelected = selection?.id === c.id && selection?.type === 'candidate';
      const label = `${c.status} relationship: ${recordTitle(c.train_id)} and ${recordTitle(c.eval_id)}`;
      svg += `<g data-candidate="${escape(c.id)}" role="button" tabindex="0" aria-label="${escape(label)}" aria-pressed="${isSelected}"><path class="graph-edge ${escape(c.status)} ${isSelected ? 'selected' : ''}" d="${d}"/><path class="edge-hit" d="${d}"/><title>${escape(label)}</title></g>`;
    }
    for (const r of records) {
      const p = placement.get(r.id); if (!p) continue;
      const active = selection?.type === 'record' && selection.id === r.id || selected && [selected.train_id, selected.eval_id].includes(r.id);
      const isExcluded = excluded.has(r.id);
      const tag = isExcluded ? 'EXCLUDED' : r.protected ? 'REQUIRED' : '';
      svg += `<g class="graph-node ${active ? 'selected' : ''} ${isExcluded ? 'excluded' : ''}" data-record="${escape(r.id)}" role="button" tabindex="0" aria-label="${escape(`${r.split} record ${r.title || r.id}, exclusion cost ${r.cost}${r.protected ? ', required' : ''}${isExcluded ? ', excluded from release' : ''}`)}" aria-pressed="${Boolean(active)}"><rect class="node-rect" x="${p.x}" y="${p.y}" rx="7" width="${nodeWidth}" height="${nodeHeight}"/><circle class="node-dot" cx="${p.x + 13}" cy="${p.y + 16}" r="2.5"/><text class="node-title" x="${p.x + 23}" y="${p.y + 20}">${escape(short(r.title || r.id, 31))}</text><text class="node-id" x="${p.x + 13}" y="${p.y + 36}">${escape(short(r.id, 25))}</text><text class="node-cost" text-anchor="end" x="${p.x + nodeWidth - 12}" y="${p.y + 36}">${escape(number(r.cost))} units</text>${tag ? `<text class="node-tag" x="${p.x + 13}" y="${p.y + 61}">${tag}</text>` : ''}<title>${escape(r.title || r.id)}</title></g>`;
    }
    svg += '</svg>';
    $('#graph').innerHTML = svg;
  }

  function renderCandidates() {
    const candidates = [...(state.candidates || [])];
    candidates.sort((a,b) => (a.id === state.next_candidate?.id ? -1 : b.id === state.next_candidate?.id ? 1 : 0));
    $('#candidate-list').innerHTML = candidates.length ? candidates.map((c, i) => `<button class="candidate-item ${selection?.id === c.id ? 'selected' : ''}" data-candidate="${escape(c.id)}" aria-pressed="${selection?.id === c.id}"><span class="candidate-index">${String(i+1).padStart(2,'0')}</span><span><span class="candidate-title">${escape(recordTitle(c.train_id))} <span class="subtle">↔</span> ${escape(recordTitle(c.eval_id))}</span><span class="candidate-subtitle">${c.id === state.next_candidate?.id && c.status === 'unresolved' ? '<span class="next-mark">UP NEXT</span>' : ''}${escape(c.hint || c.id)}</span></span><span class="candidate-right">${badge(c.status)}<span class="candidate-arrow" aria-hidden="true">↗</span></span></button>`).join('') : '<p class="empty-message">No candidate relationships in this run.</p>';
  }

  function renderInspector() {
    if (!selection) return;
    const target = $('#inspector');
    if (selection.type === 'record') {
      const r = currentRecord(selection.id); if (!r) return;
      const linked = (state.candidates || []).filter(c => c.train_id === r.id || c.eval_id === r.id);
      target.innerHTML = `<div class="detail-top"><span class="badge muted">${r.split === 'train' ? 'Training example' : 'Evaluation example'}</span><span class="detail-id mono">RECORD</span></div><h3>${escape(r.title || r.id)}</h3><div class="record-facts"><div><dt>Exclusion cost</dt><dd>${escape(number(r.cost))} units</dd></div><div><dt>Retention policy</dt><dd>${r.protected ? 'Required' : 'Negotiable'}</dd></div></div><div class="source-sha">${escape(r.id)}${r.content_sha256 ? `<br>SHA-256 ${escape(r.content_sha256)}` : ''}</div><hr class="detail-divider"><h4 class="detail-subheading">Connected relationships</h4>${linked.length ? linked.map(c => `<button class="file-row" data-candidate="${escape(c.id)}"><span><strong>${escape(c.id)}</strong><small>${escape(recordTitle(c.train_id === r.id ? c.eval_id : c.train_id))}</small></span>${badge(c.status)}</button>`).join('') : '<p class="detail-note">No candidate relationships connect to this record.</p>'}${r.content !== undefined ? sourceBlock('Source content', r.content) : '<p class="detail-note">Select a connected relationship to inspect its source material.</p>'}`;
      return;
    }
    const c = currentCandidate(selection.id); if (!c) return;
    const key = `${state.revision}:${c.id}`;
    const cached = mode === 'recorded' ? state.details?.[c.id] || capture?.details?.[c.id] : details.get(key);
    const detail = cached?.loading ? null : cached;
    const next = state.next_candidate?.id === c.id ? state.next_candidate : null;
    const train = detail?.train || currentRecord(c.train_id) || {}, evaluation = detail?.eval || currentRecord(c.eval_id) || {};
    let html = `<div class="detail-top">${badge(c.status)}<span class="detail-id mono">${escape(c.id)}</span></div>${pairCard(train, 'TRAINING', c.train_id)}${pairCard(evaluation, 'EVALUATION', c.eval_id)}<p class="detail-hint">${escape(c.hint || 'Inspect the source evidence for this relationship.')}</p>`;
    const assessment = detail?.assessment || c.evidence;
    if (assessment) html += `<h4 class="detail-subheading">Investigation finding</h4><div class="detail-result ${c.status === 'unresolved' ? 'uncertain' : ''}">${escape(typeof assessment === 'string' ? assessment : assessment.summary || assessment.reason || assessment.reasoning || assessment.verdict || pretty(assessment))}</div>`;
    if (next && c.status === 'unresolved') html += `<div class="detail-result uncertain">Next suggested investigation${next.method ? `: ${escape(next.method)}` : ''}.${next.impact !== undefined ? ` Priority estimate: ${escape(typeof next.impact === 'object' ? pretty(next.impact) : String(next.impact))}.` : ''}</div>`;
    if (c.status === 'unresolved' && mode === 'live') html += `<div class="detail-actions"><button class="button primary" id="investigate-selected" ${busy || state.agent?.status === 'running' ? 'disabled' : ''}>Investigate this relationship <span aria-hidden="true">↗</span></button></div><p class="detail-note">Runs the evidence tool selected by the local investigation policy.</p>`;
    if (detail?.lineage) {
      html += '<hr class="detail-divider"><h4 class="detail-subheading">Source provenance</h4>';
      for (const side of ['train','eval']) {
        const lineage = detail.lineage[side];
        if (lineage?.nodes?.length) html += `<div class="detail-note">${side === 'train' ? 'Training origin' : 'Evaluation origin'}</div>${lineage.nodes.map(n => `<div class="provenance-item"><span>${escape(typeof n === 'object' ? n.title || n.label || n.id || pretty(n) : n)}</span></div>`).join('')}`;
      }
      html += sourceBlock('Full provenance graph', detail.lineage);
    }
    html += '<hr class="detail-divider"><h4 class="detail-subheading">Inspect source material</h4>';
    if (detail) {
      html += train.content !== undefined ? sourceBlock('Training source', train.content) : '<p class="detail-note">Training source content was not included in this response.</p>';
      html += evaluation.content !== undefined ? sourceBlock('Evaluation source', evaluation.content) : '<p class="detail-note">Evaluation source content was not included in this response.</p>';
      if (assessment && typeof assessment === 'object') html += sourceBlock('Full assessment', assessment);
    } else html += `<p class="detail-note">${mode === 'live' ? 'Loading source evidence…' : 'Detailed source content was not included in this captured stage.'}</p>`;
    html += `<p class="detail-note">${mode === 'recorded' ? 'This is evidence from the captured run.' : 'Evidence refers to this run and revision.'} Similarity alone does not establish answer leakage.</p>`;
    target.innerHTML = html;
    if (mode === 'live' && !detail && !details.has(key)) loadDetail(c.id, key);
  }

  function pairCard(record, label, id) { return `<div class="pair-card"><div class="pair-label"><span>${label}</span><span>${escape(number(record.cost))} COST UNITS</span></div><strong>${escape(record.title || id)}</strong><div class="source-sha">${escape(id)}</div></div>`; }
  function sourceBlock(label, content) { return `<details class="source-details"><summary>${escape(label)}</summary><pre>${escape(pretty(content))}</pre></details>`; }

  async function loadDetail(id, key) {
    details.set(key, {loading:true});
    try {
      const data = await request(`/api/candidates/${encodeURIComponent(id)}`);
      details.set(key, data);
      if (selection?.id === id && `${state.revision}:${id}` === key) renderInspector();
    } catch (error) {
      details.set(key, {assessment:`Could not load source evidence: ${error.message}`});
      if (selection?.id === id) renderInspector();
    }
  }

  function renderTimeline() {
    const events = state.events || [];
    $('#timeline').innerHTML = events.length ? events.map(event => {
      let time = '';
      if (event.timestamp) { const parsed = new Date(event.timestamp); time = Number.isNaN(parsed.getTime()) ? short(event.timestamp,16) : parsed.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}); }
      return `<li class="timeline-item"><div class="event-head"><span class="event-tool">${escape(event.tool || 'event')}</span><time class="event-time" title="${escape(event.timestamp || '')}">${escape(time)}</time></div><p class="event-summary">${escape(event.summary || '')}</p>${event.candidate_id ? `<button class="event-detail" data-candidate="${escape(event.candidate_id)}">Inspect ${escape(event.candidate_id)} ↗</button>` : ''}${event.result ? sourceBlock('Tool result', event.result) : ''}</li>`;
    }).join('') : '<li class="empty-message">No activity recorded in this stage.</li>';
  }

  function renderRelease() {
    const release = state.release;
    $('#release-button').hidden = mode === 'recorded';
    $('#release-button').disabled = busy || state.agent?.status === 'running' || state.bounds?.upper?.feasible === false;
    $('#release-state').textContent = release ? `${release.verified ? 'Verified' : 'Verification incomplete'} · revision ${release.revision ?? state.revision}` : 'No release staged';
    $('#release-state').style.color = release?.verified ? 'var(--teal)' : '';
    if (release) {
      $('#manifest-title').textContent = release.verified ? 'The repair is ready for review.' : 'Review the staged repair.';
      const exclusions = ids(release.excluded), kept = ids(release.kept);
      $('#release-summary').textContent = `${exclusions.length} ${exclusions.length === 1 ? 'example excluded' : 'examples excluded'}${kept.length ? ` · ${kept.length} preserved` : ''} · ${number(release.cost)} declared cost units. ${typeof release.scope === 'string' ? release.scope : 'Verification covers the compiled evidence and declared retention policy.'}`;
      let checks = release.checks || {};
      if (!Array.isArray(checks)) checks = Object.entries(checks).map(([name,value]) => ({name,passed:typeof value === 'boolean' ? value : value?.passed ?? value?.ok,detail:value}));
      $('#release-checks').innerHTML = checks.map(check => {
        const passed = typeof check === 'string' ? null : check.passed ?? check.ok;
        const label = typeof check === 'string' ? check : check.name || check.check || check.id || pretty(check);
        return `<span class="check ${passed === false ? 'fail' : ''}" title="${escape(typeof check === 'object' ? pretty(check.detail ?? check) : check)}">${passed === false ? '×' : passed === true ? '✓' : '·'} ${escape(String(label).replace(/_/g,' '))}</span>`;
      }).join('');
    } else {
      $('#manifest-title').textContent = 'A release you can inspect.';
      $('#release-summary').textContent = mode === 'recorded' ? 'Advance to the captured release stage to inspect the verified manifests and repair decision.' : 'Stage candidate manifests after investigation. Verification checks the release against the evidence and declared retention policy.';
      $('#release-checks').innerHTML = '';
    }
    $$('[data-download]').forEach(button => { button.disabled = !release || (mode === 'recorded' && capture?.artifacts?.[button.dataset.download] === undefined); });
  }

  async function action(endpoint, body) {
    if (busy || mode !== 'live') return;
    busy = true; render();
    try { takeState(await request(endpoint, {method:'POST',body:JSON.stringify(body)})); startPolling(); }
    catch (error) { notify(error.message, true); }
    finally { busy = false; render(); }
  }

  function startPolling() {
    clearTimeout(pollTimer);
    if (mode !== 'live' || state?.agent?.status !== 'running') return;
    pollTimer = setTimeout(async () => {
      try { takeState(await request('/api/state')); startPolling(); }
      catch (error) { setNotice(`The local backend stopped responding. Last received results remain visible. ${error.message}`); }
    }, 950);
  }

  function chooseCandidate(id) {
    if (!currentCandidate(id)) return;
    selection = {type:'candidate',id}; renderGraph(); renderCandidates(); renderInspector();
    if (window.innerWidth < 901) $('.inspector-panel').scrollIntoView({behavior:'smooth',block:'start'});
  }

  function stopReplay() { clearInterval(replayTimer); replayTimer = null; }
  function showStage(index) {
    if (!capture?.snapshots?.length) return;
    stage = Math.max(0,Math.min(capture.snapshots.length - 1,index));
    takeState(capture.snapshots[stage]);
  }
  function toggleReplay() {
    if (replayTimer) { stopReplay(); render(); return; }
    showStage(0);
    replayTimer = setInterval(() => {
      if (stage >= capture.snapshots.length - 1) { stopReplay(); render(); return; }
      showStage(stage + 1);
      if (stage >= capture.snapshots.length - 1) { stopReplay(); render(); }
    }, 2000);
    render();
  }

  async function download(filename) {
    try {
      let blob;
      if (mode === 'recorded') {
        const content = capture.artifacts?.[filename];
        if (content === undefined) throw new Error('This artifact is not included in the captured run.');
        blob = new Blob([typeof content === 'string' ? content : JSON.stringify(content,null,2)], {type:filename.endsWith('.jsonl') ? 'application/x-ndjson' : 'application/json'});
      } else {
        const response = await fetch(`/api/artifacts/${encodeURIComponent(filename)}`);
        if (!response.ok) throw new Error(`Artifact unavailable (${response.status}).`);
        blob = await response.blob();
      }
      const url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      notify(`Downloaded ${filename}`);
    } catch (error) { notify(error.message,true); }
  }

  document.addEventListener('click', event => {
    const candidate = event.target.closest('[data-candidate]');
    if (candidate) { chooseCandidate(candidate.dataset.candidate); return; }
    const record = event.target.closest('[data-record]');
    if (record) { selection = {type:'record',id:record.dataset.record}; renderGraph(); renderCandidates(); renderInspector(); if (window.innerWidth < 901) $('.inspector-panel').scrollIntoView({behavior:'smooth',block:'start'}); return; }
    const downloadButton = event.target.closest('[data-download]');
    if (downloadButton && !downloadButton.disabled) { download(downloadButton.dataset.download); return; }
    if (event.target.closest('#investigate-selected')) action('/api/investigate',{candidate_id:selection.id,method:'auto'});
  });
  $('#graph').addEventListener('keydown', event => {
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-record],[data-candidate]')) { event.preventDefault(); event.target.dispatchEvent(new MouseEvent('click',{bubbles:true})); }
  });
  $$('[data-filter]').forEach(button => button.addEventListener('click', () => {
    filter = button.dataset.filter;
    $$('[data-filter]').forEach(b => { b.classList.toggle('active',b === button); b.setAttribute('aria-pressed',String(b === button)); });
    if (state) renderGraph();
  }));
  $('#run-button').addEventListener('click', () => mode === 'recorded' ? toggleReplay() : action('/api/run',{mode:'deterministic'}));
  $('#llm-button').addEventListener('click', () => action('/api/run',{mode:'llm'}));
  $('#release-button').addEventListener('click', () => action('/api/release',{conservative:true}));
  $('#reset-button').addEventListener('click', () => { selection = null; details.clear(); action('/api/reset',{}); });
  $('#prev-button').addEventListener('click', () => showStage(stage - 1));
  $('#next-button').addEventListener('click', () => showStage(stage + 1));
  $('#replay-end').addEventListener('click', () => showStage(capture.snapshots.length - 1));
  $('#about-button').addEventListener('click', () => $('#about-dialog').showModal());
  $('#bounds-help').addEventListener('click', () => $('#about-dialog').showModal());
  $('#close-dialog').addEventListener('click', () => $('#about-dialog').close());
  $('#dialog-done').addEventListener('click', () => $('#about-dialog').close());
  $('#about-dialog').addEventListener('click',event => { if(event.target === $('#about-dialog')) { const r = event.target.getBoundingClientRect(); if(event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) event.target.close(); } });
  $$('.nav a').forEach(link => link.addEventListener('click',() => { $$('.nav a').forEach(a => a.classList.toggle('active',a === link)); }));

  async function initialize() {
    try {
      const data = await request('/api/state');
      mode = 'live'; takeState(data); startPolling(); return;
    } catch (_) {}
    try {
      const data = await request('./demo-run.json');
      if (!data.recorded || !Array.isArray(data.snapshots) || !data.snapshots.length) throw new Error('No recorded execution included.');
      capture = data; mode = 'recorded'; showStage(data.snapshots.length - 1);
    } catch (_) {
      mode = 'empty';
      $('#connection-badge').className = 'badge muted';
      $('#connection-badge').innerHTML = '<span class="dot"></span>No backend connected';
      setNotice('No live backend or recorded run is available. Start the HOLDOUT backend using the project README and open its local URL. This page connects through /api/state; no data or results have been fabricated.');
      $('#run-label').textContent = 'Connect backend to investigate';
    }
  }
  initialize();
})();
