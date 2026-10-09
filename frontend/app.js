const API = 'http://localhost:5000';
let allCands = [], filteredCands = [], selectedIds = new Set();
let selectedFiles = [];
let jdFile = null;
let activeCandidate = null;
let currentView = 'analyze';

// ── View router ──────────────────────────────────────────
const VIEWS = {
  analyze: 'upload-view',
  rankings: 'results-view',
  dashboard: 'dashboard-view',
  shortlist: 'shortlist-view',
  compare: 'compare-view'
};
function showView(name) {
  if (!VIEWS[name]) return;
  currentView = name;
  for (const [key, id] of Object.entries(VIEWS)) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.style.display = key === name ? (key === 'rankings' ? 'flex' : 'block') : 'none';
  }
  if (name === 'rankings') document.getElementById('results-view').style.flexDirection = 'column';
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.toggle('active', t.dataset.view === name));
  if (name === 'dashboard') renderDashboard();
  if (name === 'shortlist') renderPipeline();
  if (name === 'compare') renderComparePage();
  window.scrollTo(0, 0);
}
function showResultsView() { showView('rankings'); }
function showUploadView() { activeCandidate = null; showView('analyze'); }

function updateTabCounts() {
  const rank = document.getElementById('tab-count-rank');
  const short = document.getElementById('tab-count-short');
  const nShort = allCands.filter(c => c.status === 'shortlisted').length;
  rank.hidden = !allCands.length; rank.textContent = allCands.length;
  short.hidden = !nShort; short.textContent = nShort;
}

// ── Toasts (replaces alert popups) ───────────────────────
function toast(msg, type = 'info') {
  const wrap = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = 'toast toast-' + type;
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => el.classList.add('show'));
  setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 300); }, 4200);
}

// ── Avatar helpers ─────────────────────────────────────────
const avColors = ['#0E6B52', '#1D4FD7', '#5B5BD6', '#8A5A00', '#0E7490', '#B4232A', '#475569', '#7C3AED'];
function avColor(n) { let h = 0; for (let c of (n || 'X')) h = (h * 31 + c.charCodeAt(0)) % avColors.length; return avColors[h]; }
function initials(n) { if (!n || n === 'Unknown Candidate') return '?'; return n.split(' ').slice(0, 2).map(w => w[0]).join('').toUpperCase(); }
function scoreColor(s) {
  if (s >= 80) return { c: '#0B6B4A', bg: '#DDF0E5' };
  if (s >= 60) return { c: '#1D4FD7', bg: '#E3EBFD' };
  if (s >= 40) return { c: '#92580A', bg: '#FBEED3' };
  return { c: '#B4232A', bg: '#FAE7E7' };
}
function verdictClass(s) {
  if (s >= 80) return 'v-strong';
  if (s >= 60) return 'v-good';
  if (s >= 40) return 'v-partial';
  return 'v-weak';
}
function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }

// ── File handling ──────────────────────────────────────────
function initDropZone() {
  const dz = document.getElementById('drop-zone');
  const fi = document.getElementById('file-input');
  dz.onclick = () => fi.click();
  dz.ondragover = e => { e.preventDefault(); dz.classList.add('over'); };
  dz.ondragleave = () => dz.classList.remove('over');
  dz.ondrop = e => { e.preventDefault(); dz.classList.remove('over'); addFiles(e.dataTransfer.files); };
  fi.onchange = e => addFiles(e.target.files);
}

function addFiles(fileList) {
  for (const f of fileList) {
    const ext = f.name.split('.').pop().toLowerCase();
    if (['pdf', 'docx', 'doc', 'txt'].includes(ext) && !selectedFiles.find(x => x.name === f.name)) {
      selectedFiles.push(f);
    }
  }
  renderFileList();
}

function removeFile(idx) {
  selectedFiles.splice(idx, 1);
  renderFileList();
}

function renderFileList() {
  const el = document.getElementById('file-list');
  if (!selectedFiles.length) { el.innerHTML = ''; return; }
  el.innerHTML = selectedFiles.map((f, i) => `
    <div class="file-item">
      <span class="fdoc">${esc(f.name.split('.').pop().toUpperCase().slice(0, 4))}</span>
      <span class="fname">${esc(f.name)}</span>
      <span class="fremove" onclick="removeFile(${i})">✕</span>
    </div>`).join('');
}

// ── JD file handling ───────────────────────────────────────
function initJdInput() {
  const fi = document.getElementById('jd-file-input');
  if (!fi) return;
  fi.onchange = e => {
    jdFile = e.target.files[0] || null;
    const el = document.getElementById('jd-file-list');
    el.innerHTML = jdFile ? `<div class="file-item"><span class="fdoc">JD</span><span class="fname">${esc(jdFile.name)}</span><span class="fremove" onclick="clearJdFile()">✕</span></div>` : '';
  };
}
function clearJdFile() {
  jdFile = null;
  const fi = document.getElementById('jd-file-input');
  if (fi) fi.value = '';
  document.getElementById('jd-file-list').innerHTML = '';
  const box = document.getElementById('jd-summary');
  box.innerHTML = '';
  box.hidden = true;
}
async function parseJdFile() {
  const pasted = document.getElementById('jd-input').value.trim();
  if (!jdFile && !pasted) { toast('Attach a JD file or paste JD text first.', 'warn'); return; }
  const box = document.getElementById('jd-summary');
  box.hidden = false;
  box.textContent = 'Parsing JD...';
  try {
    let data;
    if (jdFile) {
      const form = new FormData();
      form.append('jd_file', jdFile);
      if (pasted) form.append('job_description', pasted);
      const r = await fetch(API + '/parse-jd', { method: 'POST', body: form });
      data = await r.json();
    } else {
      const r = await fetch(API + '/parse-jd', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_description: pasted }) });
      data = await r.json();
    }
    if (data.error) { box.textContent = data.error; return; }
    // Auto-fill form from understanding
    if (data.required_skills && data.required_skills.length && !document.getElementById('skills-input').value.trim()) {
      document.getElementById('skills-input').value = data.required_skills.join(', ');
    }
    if (data.min_experience && !parseInt(document.getElementById('exp-input').value)) {
      document.getElementById('exp-input').value = data.min_experience;
    }
    if (data.job_title && !document.getElementById('title-input').value.trim()) {
      document.getElementById('title-input').value = data.job_title;
    }
    if (data.job_description && !pasted) {
      document.getElementById('jd-input').value = (data.job_description || '').slice(0, 4000);
    }
    const pts = (data.key_points || []).slice(0, 4).map(p => '• ' + p).join('\n');
    box.innerHTML = `<b>${esc(data.job_title || 'JD parsed')}</b> — ${esc(data.summary || '')}`
      + `<br/><span style="font-size:12px">Skills: ${esc((data.required_skills || []).join(', ') || '—')} · Min exp: ${data.min_experience || 0}y · Education: ${esc(data.education_required || '—')}</span>`
      + (pts ? `<pre>${esc(pts)}</pre>` : '');
    toast('JD parsed — role requirements auto-filled.', 'success');
  } catch (e) {
    box.textContent = 'Cannot connect to backend. Make sure the API is running on port 5000.';
  }
}

// ── Analysis ───────────────────────────────────────────────
async function runAnalysis() {
  const jd = document.getElementById('jd-input').value.trim();
  const skills = document.getElementById('skills-input').value.trim();
  const exp = parseInt(document.getElementById('exp-input').value) || 0;

  if (!selectedFiles.length) { toast('Please upload at least one resume.', 'warn'); return; }
  if (!skills && !jd && !jdFile) { toast('Provide a JD (paste text or upload PDF) or enter required skills.', 'warn'); return; }

  const loader = document.getElementById('loader');
  const loadStep = document.getElementById('load-step');
  loader.classList.add('on');
  loadStep.textContent = `Analyzing ${selectedFiles.length} resume(s)...`;

  const form = new FormData();
  selectedFiles.forEach(f => form.append('resumes', f));
  form.append('job_description', jd);
  form.append('required_skills', skills);
  form.append('required_experience', exp);
  if (jdFile) form.append('jd_file', jdFile);

  try {
    const r = await fetch(API + '/analyze-batch', { method: 'POST', body: form });
    const data = await r.json();
    if (data.error) { toast(data.error, 'error'); loader.classList.remove('on'); return; }
    allCands = data.candidates || [];
    filteredCands = [...allCands];
    selectedIds.clear();
    if ((data.errors || []).length) toast(`${data.errors.length} file(s) skipped: ${data.errors[0].file} — ${data.errors[0].error}`, 'warn');
    if (data.jd_understanding && data.jd_understanding.summary) {
      toast('JD understood: ' + data.jd_understanding.summary, 'success');
    } else if (data.skills_auto_extracted && data.required_skills) {
      toast('Skills auto-extracted from JD: ' + data.required_skills.join(', '), 'info');
    }
    const dups = allCands.filter(c => c.is_duplicate).length;
    if (dups > 0) toast(dups + ' possible duplicate resume(s) detected (same email/phone).', 'warn');
    updateTabCounts();
    showView('rankings');
    buildSkillFilters();
    renderCards();
  } catch (e) {
    toast('Cannot connect to backend. Make sure the API is running on port 5000.', 'error');
  }
  loader.classList.remove('on');
}

// ── Load existing candidates ───────────────────────────────
async function loadExisting() {
  try {
    const r = await fetch(API + '/candidates');
    const data = await r.json();
    document.getElementById('api-status').classList.add('ok');
    if (Array.isArray(data) && data.length > 0) {
      allCands = data;
      filteredCands = [...allCands];
      updateTabCounts();
      showView('rankings');
      buildSkillFilters();
      renderCards();
    } else {
      showView('analyze');
    }
  } catch (e) { showView('analyze'); }
}

// ── Filters ────────────────────────────────────────────────
function buildSkillFilters() {
  const counts = {};
  allCands.forEach(c => (c.skills_found || []).forEach(s => { counts[s] = (counts[s] || 0) + 1; }));
  const el = document.getElementById('skill-filters');
  const skills = Object.keys(counts).sort();
  el.innerHTML = skills.slice(0, 20).map(s => {
    const id = 'fs-' + s.replace(/[^a-z0-9]/gi, '');
    return `<div class="filter-opt">
      <input type="checkbox" id="${id}" value="${esc(s)}" onchange="applyFilters()"/>
      <label for="${id}">${esc(s)}</label><span class="cnt">${counts[s]}</span>
    </div>`;
  }).join('');
}

function readNum(id, fallback) {
  const el = document.getElementById(id);
  if (!el) return fallback;
  const v = parseFloat(el.value);
  return isNaN(v) ? fallback : v;
}

function onScorePreset() {
  const sel = document.getElementById('filter-score-preset');
  const inp = document.getElementById('filter-score');
  if (!sel || !inp) return;
  if (sel.value === 'custom') { inp.focus(); inp.select(); return; }
  inp.value = sel.value;
  applyFilters();
}

function onScoreInput() {
  const sel = document.getElementById('filter-score-preset');
  const inp = document.getElementById('filter-score');
  if (!sel || !inp) { applyFilters(); return; }
  let v = parseFloat(inp.value);
  if (isNaN(v)) v = 0;
  v = Math.max(0, Math.min(100, v));
  const match = [...sel.options].find(o => o.value !== 'custom' && parseFloat(o.value) === v);
  sel.value = match ? match.value : 'custom';
  applyFilters();
}

function onExpPreset() {
  const sel = document.getElementById('filter-exp-preset');
  const inp = document.getElementById('filter-exp');
  if (!sel || !inp) return;
  if (sel.value === 'custom') { inp.focus(); inp.select(); return; }
  inp.value = sel.value;
  applyFilters();
}

function onExpInput() {
  const sel = document.getElementById('filter-exp-preset');
  const inp = document.getElementById('filter-exp');
  if (!sel || !inp) { applyFilters(); return; }
  let v = parseFloat(inp.value);
  if (isNaN(v)) v = 0;
  v = Math.max(0, Math.min(30, v));
  const match = [...sel.options].find(o => o.value !== 'custom' && parseFloat(o.value) === v);
  sel.value = match ? match.value : 'custom';
  applyFilters();
}

function applyFilters() {
  const search = (document.getElementById('search-input')?.value || '').toLowerCase();
  const minScore = readNum('filter-score', 0);
  const minExp = readNum('filter-exp', 0);

  const checkedSkills = [];
  document.querySelectorAll('#skill-filters input:checked').forEach(cb => checkedSkills.push(cb.value));
  const checkedStatus = [];
  document.querySelectorAll('#status-filters input:checked').forEach(cb => checkedStatus.push(cb.value));

  filteredCands = allCands.filter(c => {
    if (search && !(`${c.name} ${c.email} ${(c.skills_found || []).join(' ')}`).toLowerCase().includes(search)) return false;
    if (c.final_score < minScore) return false;
    if ((c.experience_years || 0) < minExp) return false;
    if (checkedStatus.length && !checkedStatus.includes(c.status || '')) return false;
    if (checkedSkills.length > 0) {
      const cSkills = new Set((c.skills_found || []).map(s => s.toLowerCase()));
      if (!checkedSkills.some(s => cSkills.has(s.toLowerCase()))) return false;
    }
    return true;
  });
  renderCards();
}

function clearFilters() {
  document.getElementById('search-input').value = '';
  document.getElementById('filter-score').value = 0;
  document.getElementById('filter-exp').value = 0;
  const sp = document.getElementById('filter-score-preset');
  const ep = document.getElementById('filter-exp-preset');
  if (sp) sp.value = '0';
  if (ep) ep.value = '0';
  document.querySelectorAll('#skill-filters input, #status-filters input').forEach(cb => cb.checked = false);
  filteredCands = [...allCands];
  renderCards();
}

function sortCands(by) {
  const m = { score: (a, b) => b.final_score - a.final_score, name: (a, b) => (a.name || '').localeCompare(b.name || ''), exp: (a, b) => (b.experience_years || 0) - (a.experience_years || 0) };
  filteredCands.sort(m[by] || m.score);
  renderCards();
}

// ── Render candidate cards ─────────────────────────────────
function renderCards() {
  const el = document.getElementById('card-list');
  document.getElementById('cand-count').textContent = `${filteredCands.length} candidate${filteredCands.length !== 1 ? 's' : ''}`;
  updateCompareBtn();

  if (!filteredCands.length) {
    el.innerHTML = '<div class="empty-state"><div class="icon">—</div><div class="title">No candidates found</div><div class="sub">Adjust your filters or upload resumes</div></div>';
    return;
  }

  el.innerHTML = filteredCands.map(c => {
    const sc = scoreColor(c.final_score);
    const isActive = activeCandidate && activeCandidate.id === c.id;
    const isChecked = selectedIds.has(c.id);
    const status = c.status || '';
    const topSkills = (c.skills_found || []).slice(0, 3);
    const str1 = (c.strengths || [])[0] || '';
    const wk1 = (c.weaknesses || [])[0] || '';

    return `<div class="cand-card${isActive ? ' active' : ''}" style="border-left-color:${sc.c}" onclick="selectCandidate('${c.id}')">
      <div class="card-check" onclick="event.stopPropagation()">
        <input type="checkbox" ${isChecked ? 'checked' : ''} onchange="toggleCheck('${c.id}',this.checked)" aria-label="Select candidate"/>
      </div>
      <div class="card-body">
        <div class="card-top">
          <div class="card-avatar" style="background:${avColor(c.name)}">${esc(initials(c.name))}</div>
          <div class="card-name">${esc(c.name || 'Unknown')}</div>
          <div class="card-score" style="color:${sc.c}">${c.final_score}%</div>
        </div>
        <div class="card-meta">
          <span>${esc(c.education || '—')}</span>
          <span class="sep">·</span>
          <span>${c.experience_years ? c.experience_years + ' yrs exp' : 'No exp listed'}</span>
          <span class="sep">·</span>
          <span class="verdict ${verdictClass(c.final_score)}">${esc(c.recommendation?.label || '—')}</span>
          ${status ? `<span class="status-tag st-${status}">${status}</span>` : ''}
        </div>
        <div class="card-skills">${topSkills.map(s => `<span class="skill-pill">${esc(s)}</span>`).join('')}</div>
        ${str1 || wk1 ? `<div class="card-assess">
          ${str1 ? `<span class="strength-text">✓ ${esc(str1)}</span>` : ''}
          ${wk1 ? `<span class="weakness-text">✕ ${esc(wk1)}</span>` : ''}
        </div>` : ''}
        <div class="card-actions" onclick="event.stopPropagation()">
          <button class="card-btn view" onclick="selectCandidate('${c.id}')">Details</button>
          <button class="card-btn shortlist${status === 'shortlisted' ? ' on' : ''}" onclick="setStatus('${c.id}','shortlisted')">Shortlist</button>
          <button class="card-btn reject${status === 'rejected' ? ' on' : ''}" onclick="setStatus('${c.id}','rejected')">Reject</button>
          <button class="card-btn maybe${status === 'maybe' ? ' on' : ''}" onclick="setStatus('${c.id}','maybe')">Maybe</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

// ── Select & Detail Panel ──────────────────────────────────
function selectCandidate(id) {
  const c = allCands.find(x => x.id === id);
  if (!c) return;
  activeCandidate = c;
  renderCards();
  renderDetail(c);
}
function gotoCandidate(id) {
  showView('rankings');
  selectCandidate(id);
}

function renderDetail(c) {
  const panel = document.getElementById('detail-col');
  panel.classList.add('open');
  const skillsPct = c.skills_match_pct || c.keyword_score || 0;
  const expPct = c.exp_match_pct || 100;
  const status = c.status || '';

  document.getElementById('detail-content').innerHTML = `
    <div class="detail-header">
      <button class="detail-close" onclick="closeDetail()" aria-label="Close details">✕</button>
      <div class="detail-name">${esc(c.name || 'Unknown')}</div>
      <div class="detail-meta">${esc([c.email, c.phone, c.education].filter(Boolean).join(' · '))}</div>
      ${(c.links && (c.links.linkedin || c.links.github)) ? `<div class="detail-links">${c.links.linkedin ? `<a href="${c.links.linkedin.startsWith('http') ? c.links.linkedin : 'https://' + c.links.linkedin}" target="_blank" rel="noopener">LinkedIn</a>` : ''}${c.links.github ? `<a href="${c.links.github.startsWith('http') ? c.links.github : 'https://' + c.links.github}" target="_blank" rel="noopener">GitHub</a>` : ''}</div>` : ''}
      <div><span class="verdict ${verdictClass(c.final_score)}">${esc(c.recommendation?.label || '—')}</span></div>
      <div class="detail-score-row">
        <div class="d-score-box main"><div class="d-score-val">${c.final_score}%</div><div class="d-score-lbl">Overall match</div></div>
        <div class="d-score-box"><div class="d-score-val" style="color:var(--blue)">${skillsPct}%</div><div class="d-score-lbl">Skills</div></div>
        <div class="d-score-box"><div class="d-score-val" style="color:var(--brand-dark)">${c.experience_years || 0}y</div><div class="d-score-lbl">Experience</div></div>
      </div>
    </div>

    <div class="detail-section">
      <div class="ds-title">Match Breakdown</div>
      <div class="match-bar">
        <div class="mb-label"><span class="mb-name">Skills Match</span><span class="mb-val" style="color:${skillsPct >= 60 ? 'var(--green)' : 'var(--red)'}">${skillsPct}%</span></div>
        <div class="mb-track"><div class="mb-fill" style="width:${skillsPct}%;background:${skillsPct >= 60 ? 'var(--green)' : 'var(--red)'}"></div></div>
      </div>
      <div class="match-bar">
        <div class="mb-label"><span class="mb-name">Experience match</span><span class="mb-val" style="color:${expPct >= 60 ? 'var(--green)' : 'var(--amber)'}">${expPct}%</span></div>
        <div class="mb-track"><div class="mb-fill" style="width:${expPct}%;background:${expPct >= 60 ? 'var(--green)' : 'var(--amber)'}"></div></div>
      </div>
      ${c.score_breakdown ? `<div class="score-note">Keyword ${c.score_breakdown.keyword_score}% · Semantic ${c.score_breakdown.semantic_score}% · Experience ${c.score_breakdown.experience_score}% · Education ${c.score_breakdown.education_score}%${c.score_breakdown.must_have_penalty ? ` · <span class="dup">−${c.score_breakdown.must_have_penalty} must-have gap</span>` : ''}${c.skills_auto_extracted ? ' · <b>skills auto-extracted from JD</b>' : ''}${c.is_duplicate ? ' · <span class="dup">possible duplicate</span>' : ''}</div>` : ''}
    </div>

    <div class="detail-section">
      <div class="ds-title">Missing Skills</div>
      <div class="pill-list">${(c.critical_missing || []).map(s => `<span class="pill pill-red">★ ${esc(s)} · must-have</span>`).join('')}${(c.missing_skills || []).filter(s => !(c.critical_missing || []).includes(s)).map(s => `<span class="pill pill-red">${esc(s)}</span>`).join('') || ((c.critical_missing || []).length ? '' : '<span style="font-size:12px;color:var(--green)">✓ No missing skills</span>')}</div>
    </div>

    <div class="detail-section">
      <div class="ds-title">Matched Skills</div>
      <div class="pill-list">${(c.matched_skills || []).map(s => `<span class="pill pill-green">${esc(s)}</span>`).join('') || '<span style="font-size:12px;color:var(--ink-3)">—</span>'}</div>
    </div>

    <div class="detail-section">
      <div class="ds-title">Strengths</div>
      <div class="pill-list">${(c.strengths || []).map(s => `<span class="pill pill-green">${esc(s)}</span>`).join('') || '<span style="font-size:12px;color:var(--ink-3)">—</span>'}</div>
    </div>

    <div class="detail-section">
      <div class="ds-title">Weaknesses</div>
      <div class="pill-list">${(c.weaknesses || []).map(s => `<span class="pill pill-orange">${esc(s)}</span>`).join('') || '<span style="font-size:12px;color:var(--ink-3)">—</span>'}</div>
    </div>

    <div class="detail-section">
      <div class="ds-title">Resume Summary</div>
      <div class="summary-text">${esc(c.summary || 'No summary available.')}</div>
    </div>

    <div class="detail-section">
      <div class="ds-title">Recommendation</div>
      <div class="summary-text">${esc(c.recommendation?.message || '—')}</div>
    </div>

    <div class="detail-actions">
      <button class="d-action shortlist${status === 'shortlisted' ? ' on' : ''}" onclick="setStatus('${c.id}','shortlisted')">✓ Shortlist</button>
      <button class="d-action reject${status === 'rejected' ? ' on' : ''}" onclick="setStatus('${c.id}','rejected')">✕ Reject</button>
      <button class="d-action maybe${status === 'maybe' ? ' on' : ''}" onclick="setStatus('${c.id}','maybe')">? Maybe</button>
    </div>`;
}

function closeDetail() {
  document.getElementById('detail-col').classList.remove('open');
  activeCandidate = null;
  renderCards();
}

// ── Shortlist status ───────────────────────────────────────
async function setStatus(id, status) {
  const c = allCands.find(x => x.id === id);
  if (!c) return;
  const newStatus = c.status === status ? null : status; // toggle
  try {
    await fetch(API + '/candidates/' + id + '/status', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus || 'null' })
    });
    c.status = newStatus;
    const fc = filteredCands.find(x => x.id === id);
    if (fc) fc.status = newStatus;
    if (activeCandidate && activeCandidate.id === id) activeCandidate.status = newStatus;
    updateTabCounts();
    if (currentView === 'shortlist') renderPipeline();
    else { renderCards(); if (activeCandidate && activeCandidate.id === id) renderDetail(activeCandidate); }
    toast(newStatus ? `Marked as ${newStatus}.` : 'Decision cleared.', 'success');
  } catch (e) {
    toast('Failed to update status.', 'error');
  }
}

// ── Checkbox / Compare entry ───────────────────────────────
function toggleCheck(id, checked) {
  if (checked) selectedIds.add(id); else selectedIds.delete(id);
  updateCompareBtn();
  renderCards();
}
function updateCompareBtn() {
  const btn = document.getElementById('compare-btn');
  btn.classList.toggle('visible', selectedIds.size === 2);
}
function openCompare() {
  if (selectedIds.size !== 2) return;
  showView('compare');
}

// ── Compare page ───────────────────────────────────────────
function renderComparePage() {
  const a = document.getElementById('cmp-a'), b = document.getElementById('cmp-b');
  const sorted = [...allCands].sort((x, y) => (y.final_score || 0) - (x.final_score || 0));
  if (!sorted.length) {
    a.innerHTML = '<option value="">No candidates</option>';
    b.innerHTML = '<option value="">No candidates</option>';
    document.getElementById('compare-result').innerHTML = '<div class="empty-state"><div class="icon">—</div><div class="title">No candidates to compare</div><div class="sub">Run an analysis first</div></div>';
    return;
  }
  const opts = sorted.map(c => `<option value="${c.id}">${esc(c.name || 'Unknown')} — ${c.final_score}%</option>`).join('');
  const pre = [...selectedIds];
  a.innerHTML = opts; b.innerHTML = opts;
  a.value = pre[0] && sorted.find(c => c.id === pre[0]) ? pre[0] : sorted[0].id;
  b.value = pre[1] && sorted.find(c => c.id === pre[1]) ? pre[1] : (sorted[1] || sorted[0]).id;
  runCompare();
}

async function runCompare() {
  const id1 = document.getElementById('cmp-a').value;
  const id2 = document.getElementById('cmp-b').value;
  const box = document.getElementById('compare-result');
  if (!id1 || !id2) { box.innerHTML = ''; return; }
  if (id1 === id2) { box.innerHTML = '<div class="empty-state"><div class="icon">—</div><div class="title">Pick two different candidates</div></div>'; return; }
  box.innerHTML = '<div class="empty-state"><div class="title">Comparing...</div></div>';
  try {
    const r = await fetch(API + '/compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id1, id2 })
    });
    const data = await r.json();
    if (data.error) { box.innerHTML = `<div class="empty-state"><div class="title">${esc(data.error)}</div></div>`; return; }
    renderCompareResult(data);
  } catch (e) {
    box.innerHTML = '<div class="empty-state"><div class="title">Comparison failed</div><div class="sub">Check backend connection</div></div>';
  }
}

function sigBars(c) {
  const bd = c.score_breakdown || {};
  const rows = [
    ['Skills', bd.keyword_score ?? c.keyword_score ?? 0, 'var(--brand)'],
    ['Semantic', bd.semantic_score ?? c.semantic_score ?? 0, 'var(--blue)'],
    ['Experience', bd.experience_score ?? 0, 'var(--amber)'],
    ['Education', bd.education_score ?? 0, '#7C3AED']
  ];
  return rows.map(([lbl, v, col]) => `
    <div class="match-bar">
      <div class="mb-label"><span class="mb-name">${lbl}</span><span class="mb-val">${v}%</span></div>
      <div class="mb-track"><div class="mb-fill" style="width:${Math.min(100, v)}%;background:${col}"></div></div>
    </div>`).join('');
}

function renderCompareResult(data) {
  const c1 = data.candidate_1, c2 = data.candidate_2;
  const winner = (c1.final_score || 0) >= (c2.final_score || 0) ? c1 : c2;
  document.getElementById('compare-result').innerHTML = `
    <div class="compare-grid">
      ${[c1, c2].map(c => `
        <div class="compare-card${c.id === winner.id ? ' winner' : ''}">
          ${c.id === winner.id ? '<div class="winner-tag">★ Higher match</div>' : ''}
          <div class="cc-name">${esc(c.name || 'Unknown')}</div>
          <div class="cc-score">${c.final_score}% match</div>
          <div class="cc-section"><div class="cc-label">Profile</div>
            <div style="font-size:13px">${c.experience_years || 0} years · ${esc(c.education || '—')} · <span class="verdict ${verdictClass(c.final_score)}">${esc(c.recommendation?.label || '')}</span></div>
          </div>
          <div class="cc-section"><div class="cc-label">Signal scores</div>${sigBars(c)}</div>
          <div class="cc-section"><div class="cc-label">Matched Skills (${(c.matched_skills || []).length})</div>
            <div class="pill-list">${(c.matched_skills || []).map(s => `<span class="pill pill-green">${esc(s)}</span>`).join('') || 'None'}</div></div>
          <div class="cc-section"><div class="cc-label">Missing Skills (${(c.missing_skills || []).length})</div>
            <div class="pill-list">${(c.missing_skills || []).map(s => `<span class="pill pill-red">${esc(s)}</span>`).join('') || 'None'}</div></div>
          <div class="cc-section"><button class="btn-sm" onclick="gotoCandidate('${c.id}')">Open full details</button></div>
        </div>`).join('')}
    </div>
    <div class="recommendation-box"><b>Recommendation — </b>${esc(data.recommendation || 'Review both candidates carefully.')}</div>`;
}

// ── Dashboard (graphs) ─────────────────────────────────────
function renderDashboard() {
  const grid = document.getElementById('stat-grid');
  const n = allCands.length;
  if (!n) {
    grid.innerHTML = '';
    for (const id of ['chart-dist', 'chart-verdict', 'chart-skills', 'chart-signals']) {
      document.getElementById(id).innerHTML = '<div class="empty-state"><div class="icon">—</div><div class="title">No data yet</div><div class="sub">Run an analysis to see charts</div></div>';
    }
    document.getElementById('dash-sub').textContent = 'Run an analysis to populate charts.';
    return;
  }
  const scores = allCands.map(c => c.final_score || 0);
  const avg = (scores.reduce((a, b) => a + b, 0) / n);
  const strong = scores.filter(s => s >= 80).length;
  const short = allCands.filter(c => c.status === 'shortlisted').length;
  const top = [...allCands].sort((a, b) => (b.final_score || 0) - (a.final_score || 0))[0];
  document.getElementById('dash-sub').textContent = `${n} candidate${n !== 1 ? 's' : ''} screened · top match ${top.name || 'Unknown'} (${top.final_score}%)`;

  const stats = [
    ['Candidates', n, 'Total screened'],
    ['Avg score', avg.toFixed(1) + '%', 'Across all candidates'],
    ['Strong matches', strong, 'Score ≥ 80%'],
    ['Shortlisted', short, 'Marked for interview']
  ];
  grid.innerHTML = stats.map(([v, big, sub]) =>
    `<div class="stat-card"><div class="stat-val">${big}</div><div class="stat-lbl">${v}</div><div class="stat-sub">${sub}</div></div>`).join('');

  // 1. Score distribution bands
  const bands = [['0–19', 0, 19], ['20–39', 20, 39], ['40–59', 40, 59], ['60–79', 60, 79], ['80–100', 80, 100]];
  const bcounts = bands.map(([, lo, hi]) => scores.filter(s => s >= lo && s <= hi).length);
  const bmax = Math.max(...bcounts, 1);
  document.getElementById('chart-dist').innerHTML = '<div class="hbar-list">' + bands.map(([lbl], i) => `
    <div class="hbar-row"><span class="hbar-lbl">${lbl}</span>
      <div class="hbar-track"><div class="hbar-fill" style="width:${(bcounts[i] / bmax * 100).toFixed(1)}%"></div></div>
      <span class="hbar-val">${bcounts[i]}</span></div>`).join('') + '</div>';

  // 2. Verdict donut (SVG)
  const vm = [
    ['Strong', scores.filter(s => s >= 80).length, '#0B6B4A'],
    ['Good', scores.filter(s => s >= 60 && s < 80).length, '#1D4FD7'],
    ['Partial', scores.filter(s => s >= 40 && s < 60).length, '#92580A'],
    ['Weak', scores.filter(s => s < 40).length, '#B4232A']
  ];
  const R = 54, C = 2 * Math.PI * R;
  let off = 0;
  const segs = vm.map(([lbl, cnt, col]) => {
    const frac = n ? cnt / n : 0;
    const s = `<circle cx="70" cy="70" r="${R}" fill="none" stroke="${col}" stroke-width="18"
      stroke-dasharray="${(frac * C).toFixed(1)} ${C.toFixed(1)}" stroke-dashoffset="${(-off * C).toFixed(1)}"
      transform="rotate(-90 70 70)"/>`;
    off += frac;
    return s;
  }).join('');
  document.getElementById('chart-verdict').innerHTML = `
    <div class="donut-wrap">
      <svg width="140" height="140" viewBox="0 0 140 140">${segs}
        <text x="70" y="66" text-anchor="middle" class="donut-num">${n}</text>
        <text x="70" y="84" text-anchor="middle" class="donut-cap">candidates</text>
      </svg>
      <div class="donut-legend">${vm.map(([lbl, cnt, col]) =>
        `<div class="legend-row"><span class="legend-dot" style="background:${col}"></span>${lbl}<span class="legend-val">${cnt}</span></div>`).join('')}</div>
    </div>`;

  // 3. Top skills in pool
  const freq = {};
  allCands.forEach(c => (c.skills_found || []).forEach(s => { freq[s] = (freq[s] || 0) + 1; }));
  const topSkills = Object.entries(freq).sort((a, b) => b[1] - a[1]).slice(0, 10);
  const smax = topSkills.length ? topSkills[0][1] : 1;
  document.getElementById('chart-skills').innerHTML = topSkills.length ? '<div class="hbar-list">' +
    topSkills.map(([s, cnt]) => `
      <div class="hbar-row"><span class="hbar-lbl skill">${esc(s)}</span>
        <div class="hbar-track"><div class="hbar-fill green" style="width:${(cnt / smax * 100).toFixed(1)}%"></div></div>
        <span class="hbar-val">${cnt}</span></div>`).join('') + '</div>'
    : '<div class="empty-state"><div class="title">No skills detected</div></div>';

  // 4. Average signal scores
  const sig = [['Skills', 'keyword_score'], ['Semantic', 'semantic_score'], ['Experience', 'experience_score'], ['Education', 'education_score']];
  const rows = sig.map(([lbl, key]) => {
    const vals = allCands.map(c => (c.score_breakdown && c.score_breakdown[key] != null) ? c.score_breakdown[key] : (c[key] || 0));
    return [lbl, vals.reduce((a, b) => a + b, 0) / (vals.length || 1)];
  });
  document.getElementById('chart-signals').innerHTML = '<div class="hbar-list">' + rows.map(([lbl, v]) => `
    <div class="hbar-row"><span class="hbar-lbl">${lbl}</span>
      <div class="hbar-track"><div class="hbar-fill blue" style="width:${Math.min(100, v).toFixed(1)}%"></div></div>
      <span class="hbar-val">${v.toFixed(1)}%</span></div>`).join('') + '</div>';
}

// ── Shortlist pipeline ─────────────────────────────────────
const PIPE = [
  ['pending', 'Undecided', ''],
  ['shortlisted', 'Shortlisted', 'shortlisted'],
  ['maybe', 'Maybe', 'maybe'],
  ['rejected', 'Rejected', 'rejected']
];
function renderPipeline() {
  const grid = document.getElementById('pipe-grid');
  if (!allCands.length) {
    grid.innerHTML = '<div class="empty-state"><div class="icon">—</div><div class="title">No candidates yet</div><div class="sub">Run an analysis to start deciding</div></div>';
    return;
  }
  grid.innerHTML = PIPE.map(([key, title, st]) => {
    const list = allCands.filter(c => (c.status || '') === st).sort((a, b) => (b.final_score || 0) - (a.final_score || 0));
    return `<div class="pipe-col">
      <div class="pipe-head"><span>${title}</span><span class="pipe-count">${list.length}</span></div>
      <div class="pipe-body">${list.map(c => {
        const sc = scoreColor(c.final_score);
        return `<div class="pipe-card" onclick="gotoCandidate('${c.id}')">
          <div class="pipe-top"><span class="pipe-name">${esc(c.name || 'Unknown')}</span>
          <span class="pipe-score" style="color:${sc.c}">${c.final_score}%</span></div>
          <div class="pipe-meta">${c.experience_years || 0} yrs · ${esc(c.education || '—')}</div>
          <div class="pipe-move" onclick="event.stopPropagation()">
            ${st !== 'shortlisted' ? `<button class="mini-btn ok" onclick="setStatus('${c.id}','shortlisted')">Shortlist</button>` : ''}
            ${st !== 'maybe' ? `<button class="mini-btn warn" onclick="setStatus('${c.id}','maybe')">Maybe</button>` : ''}
            ${st !== 'rejected' ? `<button class="mini-btn bad" onclick="setStatus('${c.id}','rejected')">Reject</button>` : ''}
            ${st ? `<button class="mini-btn" onclick="setStatus('${c.id}','${st}')">Undo</button>` : ''}
          </div>
        </div>`;
      }).join('') || '<div class="pipe-empty">—</div>'}</div>
    </div>`;
  }).join('');
}

// ── Excel export (auto-fitted columns) ─────────────────────
// NOTE: plain CSV cannot store column widths — Excel always opens
// it with one default width, which is why text looked overlapped.
// So this exports a real .xls workbook (HTML-table based, no
// dependency) with per-column widths computed from the data,
// wrapped text and a frozen header row. It opens directly in Excel.
function exportExcel(shortlistedOnly = false) {
  const rows = shortlistedOnly ? allCands.filter(c => c.status === 'shortlisted') : filteredCands;
  if (!rows.length) { toast(shortlistedOnly ? 'No shortlisted candidates to export.' : 'Nothing to export.', 'warn'); return; }
  const cols = [
    { h: 'Name',            get: c => c.name || '' },
    { h: 'Email',           get: c => c.email || '' },
    { h: 'Phone',           get: c => c.phone || '' },
    { h: 'Education',       get: c => c.education || '' },
    { h: 'Exp (yrs)',       get: c => c.experience_years ?? '' },
    { h: 'Score %',         get: c => c.final_score ?? '' },
    { h: 'Keyword %',       get: c => c.keyword_score ?? (c.score_breakdown ? c.score_breakdown.keyword_score : '') ?? '' },
    { h: 'Semantic %',      get: c => c.semantic_score ?? (c.score_breakdown ? c.score_breakdown.semantic_score : '') ?? '' },
    { h: 'Matched skills',  get: c => (c.matched_skills || []).join('; ') },
    { h: 'Missing skills',  get: c => (c.missing_skills || []).join('; ') },
    { h: 'Verdict',         get: c => (c.recommendation || {}).label || '' },
    { h: 'Status',          get: c => c.status || 'undecided' },
    { h: 'Summary',         get: c => c.summary || '' }
  ];
  const data = rows.map(c => cols.map(col => String(col.get(c) ?? '')));
  // Auto-fit: width from longest value in each column (capped so one
  // long summary can't blow out the sheet; text still wraps inside).
  const widths = cols.map((col, j) => {
    let mx = col.h.length;
    for (const r of data) mx = Math.max(mx, Math.min(r[j].length, 70));
    const chars = Math.max(12, Math.min(mx + 4, 55));
    return Math.round(chars * 7 + 8); // px
  });
  const x = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const th = 'background:#101E30;color:#FFFFFF;font-weight:bold;white-space:normal;vertical-align:middle;border:1px solid #8494A7;padding:6px;';
  const td = 'white-space:normal;word-wrap:break-word;vertical-align:top;border:1px solid #C7D0DB;padding:5px 6px;';
  const html =
    '<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40">' +
    '<head><meta charset="UTF-8"/></head><body>' +
    '<table border="1" cellspacing="0" cellpadding="0">' +
    '<colgroup>' + widths.map(w => `<col width="${w}"/>`).join('') + '</colgroup>' +
    '<thead><tr>' + cols.map(c => `<th style="${th}">${x(c.h)}</th>`).join('') + '</tr></thead>' +
    '<tbody>' + data.map(r => '<tr>' + r.map(v => `<td style="${td}">${x(v)}</td>`).join('') + '</tr>').join('') + '</tbody>' +
    '</table></body></html>';
  const blob = new Blob(['\ufeff' + html], { type: 'application/vnd.ms-excel;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = shortlistedOnly ? 'shortlisted.xls' : 'candidates.xls';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  toast(`Exported ${rows.length} candidate${rows.length !== 1 ? 's' : ''} to Excel — columns auto-fitted.`, 'success');
}
// Backwards-compatible alias (old buttons / cached pages)
function exportCsv(shortlistedOnly = false) { return exportExcel(shortlistedOnly); }

// ── Clear all / New analysis ───────────────────────────────
async function clearAll() {
  if (!confirm('Delete ALL candidates? This cannot be undone.')) return;
  try {
    await fetch(API + '/clear', { method: 'DELETE' });
    allCands = []; filteredCands = []; selectedIds.clear();
    activeCandidate = null;
    updateTabCounts();
    showView('analyze');
    toast('All candidates deleted.', 'info');
  } catch (e) { toast('Failed to clear.', 'error'); }
}

function newAnalysis() {
  selectedFiles = [];
  renderFileList();
  clearJdFile();
  showView('analyze');
}

// ── Init ───────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initDropZone();
  initJdInput();
  const dz = document.getElementById('drop-zone');
  dz.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); document.getElementById('file-input').click(); } });
  document.querySelector('.brand').addEventListener('keydown', e => { if (e.key === 'Enter') showView('analyze'); });
  showView('analyze');
  loadExisting();
});
