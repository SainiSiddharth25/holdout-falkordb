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
  let sentinelData = null, sentinelTimer = null;
  const details = new Map();

  async function request(url, options = {}) {
    const response = await fetch(url, {...options, headers: {'Content-Type':'application/json', ...options.headers}, signal: AbortSignal.timeout(url.startsWith('/api/') ? 180000 : 15000)});
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
  function suggestedCandidate() { return state?.planning ? state.planning.next_candidate : state?.next_candidate; }
  function currentRecord(id) { return state?.records?.find(r => r.id === id); }
  function recordTitle(id) { return currentRecord(id)?.title || id; }
  function currentCandidate(id) { return state?.candidates?.find(c => c.id === id); }
  function cost(solution) { return solution?.feasible === false ? 'Infeasible' : number(solution?.cost); }
  function ids(value) { return Array.isArray(value) ? value.map(v => typeof v === 'object' ? v.id : v) : []; }
  function takeState(result) {
    const next = result?.state || result;
    if (!next || !Array.isArray(next.records)) throw new Error('The backend returned an unexpected run format.');
    if (state && state.run_id !== next.run_id) details.clear();
    state = next;
    if (next.sentinel_watch) sentinelData = next.sentinel_watch;
    if (selection?.type === 'candidate' && !currentCandidate(selection.id)) selection = null;
    if (!selection) {
      const fallback = state.candidates?.find(c => c.status === 'confirmed' && c.evidence?.method === 'provenance') || state.candidates?.[0];
      const id = suggestedCandidate()?.id || fallback?.id;
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
    $('#import-section').hidden = mode !== 'live';
    $('#import-button').disabled = mode !== 'live' || busy || running;
    $('#active-policy').textContent = `Active conflict rule: ${state.separation_policy?.label || 'Answer-leakage separation (legacy snapshot)'}. ${state.separation_policy?.scope || ''}`;
    if (state.import_summary) {
      const imported = state.import_summary;
      $('#import-result').textContent = `Current snapshot: ${imported.record_count} imported records, ${imported.candidate_count} discovered candidates, ${imported.source_reference_count} unverified source references, ${imported.captured_git_versions ?? 0} captured Git file versions, cost policy ${imported.cost_policy}.`;
    } else {
      $('#import-result').textContent = `Current captured fixture: ${records.length} records and ${candidates.length} supplied candidate hypotheses. Import or load an example to create a new independent run.`;
    }
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
    $('#agent-message').textContent = (agentMode ? `${agentMode === 'llm' ? 'LLM agent' : agentMode === 'graph_guided' ? 'Graph-guided policy (no LLM)' : agentMode === 'coordinated' ? 'Two MCP specialists (no LLM)' : 'Deterministic baseline'}. ` : '') + (state.agent?.message || 'Tool calls and observed results are recorded below.');
    renderPlanning();
    renderMemory();
    renderCoordination();
    renderOrganization();
    renderSentinel();
    renderGraph();
    renderCandidates();
    renderInspector();
    renderTimeline();
    renderRelease();
  }

  function renderMemory() {
    const live = mode === 'live';
    const m = state?.memory || {counts:{episodic:0,semantic:0,procedural:0,stale:0},current_total:0};
    $('#memory-load').disabled = !live || busy;
    $('#memory-kind').disabled = !live || busy;
    $('#memory-badge').textContent = live ? `${number(m.current_total)} current memories` : 'Recorded overview only';
    const counts = m.counts || {};
    $('#memory-metrics').innerHTML = ['episodic','semantic','procedural','stale'].map(kind => `<div class="memory-metric"><strong>${number(counts[kind] || 0)}</strong><span>${escape(kind)}</span></div>`).join('');
    if (!live) $('#memory-results').innerHTML = '<p class="empty-message">Memory retrieval needs the local FalkorDB backend. A recorded UI snapshot cannot attest current evidence.</p>';
  }

  async function loadMemories() {
    if (mode !== 'live' || busy) return;
    $('#memory-results').textContent = 'Checking current evidence receipts in FalkorDB…';
    try {
      const kind = $('#memory-kind').value;
      const query = kind ? `?kind=${encodeURIComponent(kind)}&limit=60` : '?limit=60';
      const response = await request('/api/memory'+query);
      const items = response.items || [];
      if (!items.length) { $('#memory-results').textContent = 'No valid memories yet. Run an evidence investigation first.'; return; }
      $('#memory-results').innerHTML = items.map(m => {
        const content = m.content || {};
        const summary = m.kind === 'semantic' ? content.finding : m.kind === 'episodic' ? `${(content.attempted_methods || []).join(' → ')} · ${content.last_reason || ''}` : `Previously tried: ${(content.method_sequence || []).join(' → ')}. Evidence must be independently rechecked.`;
        return `<article class="memory-item"><div><span class="badge muted">${escape(m.kind)}</span> <button class="text-button" data-candidate="${escape(m.candidate_id)}">${escape(m.candidate_id)} ↗</button> <span class="badge verified">${escape(m.validity)}</span></div><p>${escape(summary)}</p><small>Evidence receipt ${escape(short(m.binding?.evidence_sha256,20))} · Recorded revision ${number(m.revision_observed)}</small></article>`;
      }).join('');
    } catch (error) { $('#memory-results').textContent = 'Memory unavailable: ' + error.message; }
  }

  function renderCoordination() {
    const data = state.coordination || {};
    const tasks = Array.isArray(data.tasks) ? data.tasks : [];
    const counts = data.counts || {};
    $('#coordination-badge').textContent = `${tasks.length} graph tasks`;
    $('#coordination-summary').textContent = `${number(counts.pending || 0)} waiting · ${number(counts.leased || 0)} claimed · ${number(counts.done || 0)} done`;
    const noteworthy=tasks.filter(t => t.handoffs > 0 || t.state === 'leased').slice(0, 15);
    $('#coordination-tasks').innerHTML = noteworthy.length ? noteworthy.map(t =>
      `<article class="memory-item"><div><strong>${escape(t.id)}</strong> <span class="badge muted">${escape(t.state)}</span> <span class="badge muted">${escape(t.role)} specialist</span></div><p>${escape(t.owner || 'Unclaimed')} · ${number(t.handoffs)} handoffs · ${number(t.attempts)} claims</p></article>`
    ).join('') : '<p class="empty-message">No active claims or handoffs yet. Stage 4 task transitions will appear here.</p>';
  }

  function renderOrganization() {
    const org = state.organization;
    const live = mode === 'live';
    const blocked = busy || state.agent?.status === 'running' || !live;
    const gate = org?.gate;
    const policySelect = $('#org-policy');
    const reviewerSelect = $('#org-reviewer');
    if (!org) {
      $('#org-badge').textContent = 'Recorded / unavailable';
      $('#org-project').textContent = 'Company Brain requires the local Stage 5 backend; recorded replay has no live approval authority.';
      $('#org-gate').textContent = 'No governance status in this recorded snapshot.';
      policySelect.innerHTML = '';
      reviewerSelect.innerHTML = '';
      $('#org-source').textContent = 'No verified organization graph is available in this capture.';
    } else {
      $('#org-badge').textContent = gate?.status?.replaceAll('_', ' ') || 'Not staged';
      $('#org-project').innerHTML = `<strong>${escape(org.project?.title || org.project?.id || 'Synthetic project')}</strong> · ` +
        (org.datasets_and_owners || []).map(r => `${escape(r.dataset.title || r.dataset.id)} → ${escape(r.owner.display_name || r.owner.id)}`).join(' · ');
      const selected = gate?.policy?.id;
      const old = policySelect.value;
      policySelect.innerHTML = (org.policy_options || []).map(p => `<option value="${escape(p.id)}">${escape(p.title)}</option>`).join('');
      policySelect.value = (org.policy_options || []).some(p => p.id === old) ? old : selected;
      if (!policySelect.value) policySelect.value = selected;
      const reviewerOld = reviewerSelect.value;
      reviewerSelect.innerHTML = (org.people || []).map(p => `<option value="${escape(p.id)}">${escape(p.display_name)} · ${escape(p.role)}</option>`).join('');
      if ((org.people || []).some(p => p.id === reviewerOld)) reviewerSelect.value = reviewerOld;
      else reviewerSelect.value = (org.people || []).find(p => p.role === gate?.policy?.reviewer_role)?.id || '';
      const status = gate?.status?.replaceAll('_',' ') || 'unknown';
      const reasons = (gate?.reasons || []).map(r => `<li>${escape(r)}</li>`).join('');
      $('#org-gate').innerHTML = `<p><strong>Governance: ${escape(status)}</strong> · ${gate?.publishable ? 'Eligible under this demo policy' : 'Not eligible'}.</p>` +
        `<p>Retained: ${number(gate?.retained?.train)} training / ${number(gate?.retained?.eval)} evaluation · ${number(gate?.unresolved)} unresolved candidates.</p>` +
        (reasons ? `<ul>${reasons}</ul>` : '') +
        (gate?.approval ? `<p>Last demo decision: ${escape(gate.approval.decision)} by ${escape(gate.approval.actor_id)}.</p>` : '') +
        `<small>${escape(gate?.scope || '')}</small>`;
      $('#org-source').innerHTML = (org.decision_sources || []).map(d => `<p><strong>${escape(d.id)}</strong> · ${escape(d.source_path)} · SHA-256 ${escape(d.source_sha256?.slice(0,16))}…<br><small>${escape(d.source_excerpt || '')}</small></p>`).join('') || '<p>No decision sources linked to the active policy.</p>';
    }
    $('#org-policy-apply').disabled = blocked || !org || policySelect.value === gate?.policy?.id;
    const canReview = !blocked && org && gate && !['not_staged','blocked'].includes(gate.status);
    $('#org-approve').disabled = !canReview;
    $('#org-reject').disabled = !canReview;
    policySelect.disabled = blocked || !org;
    reviewerSelect.disabled = !canReview;
  }

  function renderPlanning() {
    const live = mode === 'live', running = state.agent?.status === 'running';
    const p = state.planning;
    $('#planning-section').hidden = !live && !p;
    for (const id of ['plan-button','guided-button','coordinated-button','decision-example']) $("#" + id).disabled = !live || busy || running;
    $('#plan-cap').disabled = !live || busy || running;
    $('#plan-download').disabled = !live || !p || busy || running;
    $('#plan-badge').className = `badge ${p?.stop?.cost_stable ? 'verified' : p ? 'unresolved' : 'muted'}`;
    $('#plan-badge').textContent = p ? p.stop.cost_stable ? 'Cost-stable · not all claims resolved' : `Plan · revision ${p.binding.revision}` : 'Not calculated for this revision';
    if (!p) {
      $('#plan-status').className = 'planner-status';
      $('#plan-status').textContent = 'Calculate a plan for the current evidence revision. Evidence changes invalidate earlier scores; page refreshes alone do not rerun scenarios.';
      $('#plan-results').innerHTML = '';
      return;
    }
    $('#plan-status').className = `planner-status ${p.stop.cost_stable ? 'cost-stable' : ''}`;
    $('#plan-status').textContent = p.stop.reason;
    const count = p.coverage, lower = cost(p.bounds.lower), upper = cost(p.bounds.upper);
    let html = `<div class="plan-metrics"><div><span>Confirmed-only optimum</span><strong>${escape(lower)}</strong></div><div><span>Conservative optimum</span><strong>${escape(upper)}</strong></div><div><span>Cost uncertainty</span><strong>${number(p.bounds.gap)}</strong></div><div><span>Scored / actionable</span><strong>${count.evaluated} / ${count.actionable}</strong></div></div>`;
    if (!count.complete_actionable_scan && !p.stop.stop)
      html += `<p class="plan-warning">Partial scan: ${count.not_evaluated} actionable claims were not scored. This is not a global-best ranking.${count.time_limited ? ' The planner time budget was reached between operations.' : ''}${count.report_limited ? ' The certificate-report size budget was reached.' : ''}</p>`;
    if (p.ranked.length) {
      html += '<div class="plan-table-wrap"><table class="plan-table"><caption>Hypothetical cost effects; no verdict is changed by this table.</caption><thead><tr><th>Claim</th><th>If rejected</th><th>If confirmed</th><th>Effort / score</th><th>Decision</th></tr></thead><tbody>';
      for (const row of p.ranked) {
        const best = row.candidate_id === p.next_candidate?.id;
        const rejection = row.rejection.restores_feasibility ? 'Restores feasibility' : row.rejection.saving === null ? 'Still infeasible' : `${cost(row.rejection)} cost · save ${number(row.rejection.saving)}`;
        const confirmation = row.confirmation.proves_infeasibility ? 'Protected conflict' : `${cost(row.confirmation)} lower bound · +${number(row.confirmation.uplift)}`;
        html += `<tr class="${best ? 'plan-recommended' : ''}"><td><button class="text-button" data-candidate="${escape(row.candidate_id)}">${escape(row.candidate_id)} ↗</button><small>${escape(row.train_id)} × ${escape(row.eval_id)}</small></td><td>${escape(rejection)}</td><td>${escape(confirmation)}</td><td>${number(row.effort_units)} units / ${number(row.score)}</td><td>${best ? '<span class="badge verified">Next recommended</span>' : ''}<small>${escape(row.reason)}</small></td></tr>`;
      }
      html += '</tbody></table></div>';
      if (count.evaluated > p.ranked.length) html += `<p class="small-text subtle">Showing ${p.ranked.length} of ${count.evaluated} evaluated claims. The full local report includes all evaluated scenarios and certificates.</p>`;
    }
    if (p.blocked.length) html += `<details class="planner-explanation"><summary>${count.blocked} unresolved claims need new evidence, not repeated methods</summary>${p.blocked.map(b => `<p><button class="text-button" data-candidate="${escape(b.candidate_id)}">${escape(b.candidate_id)}</button> ${escape(b.reason)}</p>`).join('')}</details>`;
    html += `<p class="small-text subtle plan-footnote">${p.work.scenario_solves} scenario solve requests · ${number(p.work.seconds * 1000)} ms counterfactual analysis (baseline solve time excluded) · capped at ${p.work.time_budget_seconds} seconds between native operations. Ranking does not assert a FalkorDB speed advantage.</p>`;
    $('#plan-results').innerHTML = html;
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
    candidates.sort((a,b) => (a.id === suggestedCandidate()?.id ? -1 : b.id === suggestedCandidate()?.id ? 1 : 0));
    $('#candidate-list').innerHTML = candidates.length ? candidates.map((c, i) => `<button class="candidate-item ${selection?.id === c.id ? 'selected' : ''}" data-candidate="${escape(c.id)}" aria-pressed="${selection?.id === c.id}"><span class="candidate-index">${String(i+1).padStart(2,'0')}</span><span><span class="candidate-title">${escape(recordTitle(c.train_id))} <span class="subtle">↔</span> ${escape(recordTitle(c.eval_id))}</span><span class="candidate-subtitle">${c.id === suggestedCandidate()?.id && c.status === 'unresolved' ? '<span class="next-mark">UP NEXT</span>' : ''}${escape(c.hint || c.id)}</span></span><span class="candidate-right">${badge(c.status)}<span class="candidate-arrow" aria-hidden="true">↗</span></span></button>`).join('') : '<p class="empty-message">No candidate relationships in this run.</p>';
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
    const key = `${state.run_id}:${state.revision}:${c.id}`;
    const cached = mode === 'recorded' ? state.details?.[c.id] || capture?.details?.[c.id] : details.get(key);
    const detail = cached?.loading ? null : cached;
    const next = suggestedCandidate()?.id === c.id ? suggestedCandidate() : null;
    const train = detail?.train || currentRecord(c.train_id) || {}, evaluation = detail?.eval || currentRecord(c.eval_id) || {};
    let html = `<div class="detail-top">${badge(c.status)}<span class="detail-id mono">${escape(c.id)}</span></div>${pairCard(train, 'TRAINING', c.train_id)}${pairCard(evaluation, 'EVALUATION', c.eval_id)}<p class="detail-hint">${escape(c.hint || 'Inspect the source evidence for this relationship.')}</p>`;
    const assessment = detail?.assessment || c.evidence;
    if (assessment) html += `<h4 class="detail-subheading">Investigation finding</h4><div class="detail-result ${c.status === 'unresolved' ? 'uncertain' : ''}">${escape(typeof assessment === 'string' ? assessment : assessment.summary || assessment.reason || assessment.reasoning || assessment.verdict || pretty(assessment))}</div>`;
    if (detail?.review) {
      const review = detail.review;
      html += `<div class="evidence-layers"><h4 class="detail-subheading">Observation → policy → action</h4><dl><dt>Complete-content identity</dt><dd>${review.identity.identical ? 'Identical captured bytes — verified' : 'Different captured bytes — verified'}</dd><dt>Answer relevance</dt><dd>${escape(review.answer_relevance)}</dd><dt>Selected conflict rule</dt><dd>${escape(review.policy.label)}</dd><dt>Release implication</dt><dd>${escape(review.release_implication)}</dd></dl></div>`;
      html += '<h4 class="detail-subheading">Method availability</h4><ul class="method-availability">' + Object.entries(review.methods).map(([name, info]) => `<li><strong>${escape(name)}</strong><span class="method-state ${escape(info.state)}">${escape(info.state)}</span><p>${escape(info.reason)}</p></li>`).join('') + '</ul>';
      if (c.status === 'unresolved' && !review.pending_methods.length)
        html += '<p class="detail-result uncertain">No untried applicable method remains for this snapshot. More model calls cannot supply missing captures or answer declarations. Conservative repair still covers this candidate.</p>';
    }
    if (next && c.status === 'unresolved') html += `<div class="detail-result uncertain">Next suggested investigation${next.method ? `: ${escape(next.method)}` : ''}.${next.impact !== undefined ? ` Priority estimate: ${escape(typeof next.impact === 'object' ? pretty(next.impact) : String(next.impact))}.` : ''}</div>`;
    if (c.status === 'unresolved' && mode === 'live') html += `<div class="detail-actions"><button class="button primary" id="investigate-selected" ${busy || state.agent?.status === 'running' || (detail?.review && !detail.review.pending_methods.length) ? 'disabled' : ''}>Investigate this relationship <span aria-hidden="true">↗</span></button></div><p class="detail-note">Runs the evidence tool selected by the local investigation policy.</p>`;
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
      if (selection?.id === id && `${state.run_id}:${state.revision}:${id}` === key) renderInspector();
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
    const review = release?.review || state.repair_review;
    const reviewTarget = $('#release-review');
    reviewTarget.innerHTML = '';
    if (review) {
      reviewTarget.innerHTML = `<h3>${release ? 'Verified retention by split' : 'Conservative repair preview — not yet staged'}</h3><div class="split-review">${['train','eval'].map(split => {
        const row = review.by_split[split];
        return `<div><span>${split === 'train' ? 'Training' : 'Evaluation'} retained</span><strong>${number(row.retained)} <small>/ ${number(row.input)}</small></strong><p>${number(row.excluded)} excluded · ${number(row.exclusion_cost)} cost units · ${number(row.protected_retained)} protected retained</p></div>`;
      }).join('')}</div><p class="detail-note">Confirmed covered: ${review.conflicts.confirmed_covered}/${review.conflicts.confirmed} · Unresolved covered: ${review.conflicts.unresolved_covered}/${review.conflicts.unresolved} · Rejected claims: ${review.conflicts.rejected}</p>`;
      reviewTarget.innerHTML += review.warnings.map(warning => `<div class="release-warning" role="status"><strong>Dataset usability warning</strong><p>${escape(warning.message)}</p></div>`).join('');
      reviewTarget.innerHTML += `<p class="detail-note">${escape(review.note)}</p>`;
      if (review.unresolved.length)
        reviewTarget.innerHTML += `<details class="unresolved-review"><summary>Why ${review.unresolved.length} candidate claims remain unresolved</summary>${review.unresolved.map(item => `<div><button class="text-button" data-candidate="${escape(item.candidate_id)}">${escape(item.candidate_id)} ↗</button><p>${escape(item.reason)}</p><small>Covered by: ${escape(item.covered_by.join(', ') || 'none')}</small></div>`).join('')}</details>`;
    }
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

  function renderSentinel() {
    const watching = mode === 'live' && sentinelData?.state !== 'disabled';
    const w = watching ? sentinelData : null;
    $('#sentinel-scan').disabled = !watching || busy;
    $('#sentinel-promote').disabled = !watching || busy || state?.agent?.status === 'running' || !w?.latest_digest || w?.state !== 'pending';
    $('#sentinel-badge').className = 'badge ' + (w?.stale_release ? 'unresolved' : w?.state === 'pending' ? 'muted' : w?.state === 'current' ? 'verified' : 'muted');
    $('#sentinel-badge').textContent = !w ? 'Local backend only' : w.stale_release ? 'Source changed · review required' : w.state === 'pending' ? 'New snapshot available' : w.state === 'current' ? 'Sources current' : 'Source unavailable';
    if (!w) { $('#sentinel-summary').textContent = 'Connect to the local backend to observe files.'; $('#sentinel-events').textContent = ''; return; }
    const d = w.delta || {}, keys = d.affected_record_keys || [];
    $('#sentinel-summary').innerHTML = w.error
      ? `<div class="memory-metric"><strong>Source unavailable</strong><span>${escape(w.error)}</span></div>`
      : `<div class="memory-metric"><strong>${escape(w.bound_to_active_run ? 'Bound' : 'Not yet bound')}</strong><span>To the active investigation</span></div>`+
        `<div class="memory-metric"><strong>${number(d.added || 0)}</strong><span>New records</span></div>`+
        `<div class="memory-metric"><strong>${number(d.modified || 0)}</strong><span>Modified records</span></div>`+
        `<div class="memory-metric"><strong>${number(d.removed || 0)}</strong><span>Removed records</span></div>`;
    const impacted = w.affected_candidate_count ?? w.affected_candidate_ids?.length ?? 0;
    const note = w.stale_release ? '<p class="detail-note">Current watched release is stale. Further agent, staging, artifact, and approval actions are blocked. Promote new inputs to investigate them as a new run.</p>' :
      '<p class="detail-note">Only a review proposal. Old evidence and approvals never transfer automatically.</p>';
    const events = (w.events || []).slice(-4).reverse().map(e => `<article class="memory-item"><strong>${escape(e.kind)}</strong><p>${escape(e.at)}</p></article>`).join('');
    $('#sentinel-events').innerHTML = note + (d.affected_record_keys?.length ? `<p class="detail-note">Changed record keys: ${escape(keys.slice(0,8).join(', '))}${d.truncated ? '…' : ''}. ${number(impacted)} connected candidate relationships on the active graph.</p>` : '') + events;
  }

  async function reloadSentinel() {
    if (mode !== 'live' || busy) return;
    try { sentinelData = await request('/api/sentinel'); renderSentinel(); }
    catch (e) { $('#sentinel-badge').textContent = 'Watch unavailable'; }
  }

  async function parseDatasetFile(file) {
    if (!file) throw new Error('Select both training and evaluation files.');
    if (file.size > 3 * 1024 * 1024) throw new Error('File exceeds 3 MiB; use CLI prepare for larger data.');
    const text = await file.text();
    let rows;
    try { rows = file.name.toLowerCase().endsWith('.json') ? JSON.parse(text) : text.split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line)); }
    catch (_) { throw new Error(`Invalid JSON or JSONL: ${file.name}`); }
    if (!Array.isArray(rows) || !rows.length || rows.length > 5000 || rows.some(r => !r || typeof r !== 'object' || Array.isArray(r)))
      throw new Error('Each file must contain 1–5000 JSON object records.');
    return rows;
  }

  $('#sentinel-scan').addEventListener('click', async () => {
    try { sentinelData = await request('/api/sentinel/scan',{method:'POST',body:'{}'}); renderSentinel(); notify('Watched sources checked.'); }
    catch(error) { notify(error.message,true); }
  });
  $('#sentinel-promote').addEventListener('click', async () => {
    if (!sentinelData?.latest_digest || mode !== 'live' || busy) return;
    if (!window.confirm('Promote watched inputs into a NEW release investigation? Old evidence and demo approvals will not be reused.')) return;
    busy = true; render();
    try {
      const data = await request('/api/sentinel/promote', {method:'POST',body:JSON.stringify({
        expected_digest:sentinelData.latest_digest, expected_run_id:state.run_id,
        policy:$('#cost-policy').value,separation_policy:$('#separation-policy').value
      })});
      selection=null;details.clear();takeState(data);notify('New watched snapshot promoted. Investigate its candidates.');
    } catch(error) { notify(error.message,true); }
    finally { busy=false; render(); }
  });

  $('#separation-policy').addEventListener('change', () => {
    $('#separation-policy-help').textContent = $('#separation-policy').value === 'exact_record'
      ? 'Exact-record: forbid only byte-identical complete contents. Renamed answers and partial overlaps are outside this rule. Different contents are not proof of answer independence. Takes effect on the next import only.'
      : 'Answer-leakage: bounded answer evidence is required. Identical boilerplate alone is insufficient. Cost weights are a separate choice. Takes effect on the next import only.';
  });

  $('#import-button').addEventListener('click', async () => {
    if (busy || mode !== 'live' || state?.agent?.status === 'running') return;
    busy = true; render();
    try {
      const [train, evaluation] = await Promise.all([
        parseDatasetFile($('#train-file').files[0]), parseDatasetFile($('#eval-file').files[0])
      ]);
      const title = `Imported release · ${new Date().toLocaleDateString()}`;
      const data = await request('/api/import', { method:'POST', body:JSON.stringify({train,evaluation,policy:$('#cost-policy').value,separation_policy:$('#separation-policy').value,title}) });
      details.clear(); selection = null; takeState(data);
      const info = data.import_summary || {};
      $('#import-result').textContent = `Imported ${info.record_count || train.length + evaluation.length} records and discovered ${info.candidate_count ?? 0} possible conflicts. Source references: ${info.source_reference_count ?? 0}; captured Git versions: ${info.captured_git_versions ?? 0}. These are retrieval hypotheses, not proven leakage. Review evidence before exporting.`;
      notify('New dataset snapshot imported; evidence graph is ready.');
    } catch (error) { notify(error.message, true); }
    finally { busy = false; render(); }
  });

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
  $('#org-policy').addEventListener('change', () => {
    if (!state?.organization) return;
    $('#org-policy-apply').disabled = busy || mode !== 'live' ||
      $('#org-policy').value === state.organization.gate?.policy?.id;
  });
  $('#org-policy-apply').addEventListener('click', () =>
    action('/api/organization/policy', {policy_id:$('#org-policy').value}));
  $('#org-approve').addEventListener('click', () => {
    if (window.confirm('Record an UNAUTHENTICATED local demo approval for this verified release and selected policy?'))
      action('/api/organization/review',{actor_id:$('#org-reviewer').value,decision:'approved'});
  });
  $('#org-reject').addEventListener('click', () =>
    action('/api/organization/review',{actor_id:$('#org-reviewer').value,decision:'rejected'}));
  $('#memory-load').addEventListener('click', loadMemories);
  $('#plan-button').addEventListener('click', () => {
    const cap = Number($('#plan-cap').value);
    if (!Number.isInteger(cap) || cap < 1 || cap > 128) { notify('Choose between 1 and 128 claims.', true); return; }
    action('/api/plan', {expected_revision:state.revision, max_candidates:cap, refresh:true});
  });
  $('#guided-button').addEventListener('click', () => action('/api/run', {mode:'graph_guided'}));
  $('#coordinated-button').addEventListener('click', () => action('/api/run', {mode:'coordinated'}));
  $('#decision-example').addEventListener('click', () => {
    if (window.confirm('Load the controlled sensitivity example as a new active run? Existing files remain saved.')) {
      selection = null; details.clear(); action('/api/examples/decision-impact', {});
    }
  });
  $('#plan-download').addEventListener('click', async () => {
    try {
      const response = await fetch('/api/plan/report');
      if (!response.ok) throw new Error('Calculate a current plan before downloading.');
      const blob = await response.blob(), url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = `holdout-planning-${state.run_id}-r${state.revision}.json`;
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch(error) { notify(error.message,true); }
  });
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
      mode = 'live'; takeState(data); startPolling();
      sentinelTimer = setInterval(reloadSentinel, 10000); return;
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
