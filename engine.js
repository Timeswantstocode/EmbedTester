/* engine.js — FAM Source Verifier (GitHub Pages edition) */

const SOURCES_URL = './sources.json';
const STORE_KEY   = 'famsv_state_v2';
const FAM_SANDBOX = 'allow-scripts allow-same-origin allow-forms allow-presentation allow-orientation-lock allow-pointer-lock allow-modals';
const FAM_ALLOW   = 'autoplay; encrypted-media; picture-in-picture; web-share; fullscreen';

// ─── STATE ───────────────────────────────────────────────────────────────────
let state = {
  providers: [],
  results:   {},
  meta:      null,   // { generated, tmdb_id, count }
  activeIdx: null,
};

// ─── PERSISTENCE ─────────────────────────────────────────────────────────────
function saveState() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch(e) {}
}
function loadState() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) {
      state = JSON.parse(raw);
      renderAll();
      log(`Session restored — ${state.providers.length} providers, ${Object.keys(state.results).length} results`, 'success');
    }
  } catch(e) {}
}
function clearSaved() {
  if (!confirm('Clear all saved data and results?')) return;
  localStorage.removeItem(STORE_KEY);
  state = { providers: [], results: {}, meta: null, activeIdx: null };
  renderAll();
  log('Cleared saved data', 'warn');
}

// ─── BACKUP / RESTORE ────────────────────────────────────────────────────────
function downloadState() {
  const data = JSON.stringify(state, null, 2);
  const blob = new Blob([data], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `fam-backup-${Date.now()}.json`;
  a.click();
  log('Backup downloaded', 'success');
}

function triggerUpload() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json';
  input.onchange = handleUpload;
  input.click();
}

function handleUpload(e) {
  const file = e.target.files[0];
  if (!file) return;

  if (!file.name.endsWith('.json')) {
    log('Error: Only .json files are allowed', 'error');
    alert('Please select a valid .json backup file.');
    e.target.value = '';
    return;
  }

  const reader = new FileReader();
  reader.onload = (evt) => {
    try {
      const imported = JSON.parse(evt.target.result);
      
      // Strict Validation
      if (!imported || typeof imported !== 'object' || !imported.results || !Array.isArray(imported.providers)) {
        throw new Error('Malformed backup file structure');
      }
      
      state = imported;
      saveState();
      renderAll();
      log(`Restored ${state.providers.length} providers from backup`, 'success');
      alert('Backup restored successfully!');
    } catch (err) {
      log('Restore failed: ' + err.message, 'error');
      alert('Invalid Backup: ' + err.message);
    } finally {
      e.target.value = ''; // Reset input
    }
  };
  reader.onerror = () => log('Error reading file', 'error');
  reader.readAsText(file);
}

// ─── LOGGING ─────────────────────────────────────────────────────────────────
function log(msg, type = 'info') {
  const box = document.getElementById('logBox');
  const div = document.createElement('div');
  div.className = 'log-line ' + type;
  div.textContent = '[' + new Date().toLocaleTimeString() + '] ' + msg;
  box.prepend(div);
}

// ─── OVERLAY ─────────────────────────────────────────────────────────────────
function showOverlay(title, sub) {
  document.getElementById('overlayTitle').textContent = title;
  document.getElementById('overlaySub').textContent = sub;
  document.getElementById('progFill').style.width = '0%';
  document.getElementById('progText').textContent = '';
  document.getElementById('overlay').classList.add('active');
}
function setProgress(pct, text) {
  document.getElementById('progFill').style.width = Math.min(100, pct) + '%';
  document.getElementById('progText').textContent = text;
}
function hideOverlay() {
  document.getElementById('overlay').classList.remove('active');
}

// ─── STATUS PILL ─────────────────────────────────────────────────────────────
function setStatus(label, cls = '') {
  const el = document.getElementById('statusPill');
  el.textContent = '● ' + label;
  el.className   = 'status-pill ' + cls;
}

// ─── TABS ────────────────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.querySelector(`[data-tab="${name}"]`).classList.add('active');
}

// ─── LOAD SOURCES (fetch sources.json from repo) ─────────────────────────────
async function startLoad() {
  if (state.providers.length > 0) {
    if (!confirm('Reload latest sources.json? Current results will be kept.')) return;
  }

  showOverlay('Loading Sources', 'Fetching sources.json from repo...');
  setStatus('Loading...', 'active');
  document.getElementById('loadBtn').classList.add('is-loading');

  try {
    // Cache-bust so GitHub Pages always serves fresh file
    const res = await fetch(SOURCES_URL + '?t=' + Date.now());
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const data = await res.json();

    state.meta = {
      generated: data.generated,
      tmdb_id:   data.tmdb_id,
      count:     data.count,
    };

    // Merge — preserve existing results, update provider metadata
    const existing = {};
    for (const p of state.providers) existing[p.name] = p;

    state.providers = data.providers.map(p => ({
      ...p,
      status: state.results[p.name]?.status || existing[p.name]?.status || 'idle',
    }));

    setProgress(100, 'Done');
    log(`Loaded ${state.providers.length} providers (generated ${fmtDate(data.generated)})`, 'success');
    setStatus('Ready', 'pass');

    renderAll();
    saveState();

    // Show meta banner
    const banner = document.getElementById('metaBanner');
    if (banner) {
      banner.textContent = `sources.json · ${state.providers.length} providers · generated ${fmtDate(data.generated)} · TMDB ${data.tmdb_id}`;
      banner.style.display = 'block';
    }
  } catch (err) {
    log('Failed to load sources.json: ' + err.message, 'error');
    setStatus('Error', 'fail');
  } finally {
    hideOverlay();
    document.getElementById('loadBtn').classList.remove('is-loading');
  }
}

// ─── RENDER ──────────────────────────────────────────────────────────────────
function renderAll() {
  renderProviderList();
  renderResults();
  renderStats();
}

function renderProviderList() {
  const list  = document.getElementById('providerList');
  const count = document.getElementById('providerCount');

  if (state.providers.length === 0) {
    list.innerHTML = '<div class="empty-state">Click <strong>Load Sources</strong> to pull the latest sources.json</div>';
    count.textContent = '0 found';
    return;
  }

  count.textContent = state.providers.length + ' providers';
  list.innerHTML = '';

  state.providers.forEach((p, idx) => {
    const status = state.results[p.name]?.status || p.status || 'idle';
    const div = document.createElement('div');
    div.className = 'provider-item ' + status;

    const sourceBadge = p.source === 'known_pattern'
      ? '<span class="chip green">✓ pattern</span>'
      : p.source === 'scraped'
      ? '<span class="chip accent">⟳ scraped</span>'
      : p.source === 'ai_gemma_batch'
      ? '<span class="chip accent">✧ AI Batch</span>'
      : '<span class="chip yellow">~ fallback</span>';

    const statusLabel = status === 'pass' 
      ? '<span class="chip green" style="margin-left:8px">WORKING</span>' 
      : status === 'fail' 
      ? '<span class="chip red" style="margin-left:8px">BROKEN</span>' 
      : '';

    div.innerHTML = `
      <div class="pi-dot ${status}"></div>
      <div class="pi-info">
        <div class="pi-name"><a href="#" onclick="window.open('${esc(p.homepage)}','_blank'); return false;" style="color:var(--text);text-decoration:none;">${esc(p.name)}</a> ${sourceBadge} ${statusLabel}</div>
        <div class="pi-url">${p.homepage}</div>
        <div class="pi-embed"><strong>Movie:</strong> ${p.embed || '<span style="color:var(--muted)">None</span>'}</div>
        ${p.tv_embed ? `<div class="pi-embed"><strong>TV:</strong> ${p.tv_embed}</div>` : ''}
        ${p.customizations ? `<div class="pi-embed" style="color:var(--yellow)"><strong>Custom:</strong> ${esc(p.customizations)}</div>` : ''}
        ${p.llm_profile ? `<div class="pi-embed" style="color:var(--accent); font-size:10px; margin-top:4px;"><strong>LLM Profile Available</strong></div>` : ''}
      </div>
      <div class="pi-actions">
        <button class="btn-xs" style="background:rgba(255,255,255,0.05);" onclick="openDocsModal('${esc(p.name)}')">Docs</button>
        <button class="btn-xs" style="background:var(--accent-dim); color:var(--accent); border-color:var(--accent);" onclick="openNotesModal('${esc(p.name)}')">Notes</button>
        ${p.embed ? `<button class="btn-xs test" onclick="openInLab(${idx})">Test</button>` : ''}
      </div>
    `;
    list.appendChild(div);
  });

  renderStats();
}

function renderStats() {
  const total    = state.providers.length;
  const passed   = Object.values(state.results).filter(r => r.status === 'pass').length;
  const failed   = Object.values(state.results).filter(r => r.status === 'fail').length;
  const tested   = passed + failed;

  document.getElementById('stTotal').textContent   = total;
  document.getElementById('stScanned').textContent = tested;
  document.getElementById('stPass').textContent    = passed;
  document.getElementById('stFail').textContent    = failed;
}

function renderResults() {
  const list  = document.getElementById('resultsTable');
  const chip  = document.getElementById('resultsChip');
  const entries = Object.entries(state.results);

  if (entries.length === 0) {
    list.innerHTML = '<div class="empty-state">No results yet — test providers in the Lab tab.</div>';
    chip.textContent = '0 completed';
    return;
  }

  chip.textContent = entries.length + ' completed';
  list.innerHTML = '';

  // Sort: pass first, then fail
  entries.sort((a, b) => (a[1].status === 'pass' ? -1 : 1));

  entries.forEach(([name, r]) => {
    const div = document.createElement('div');
    div.className = 'result-row ' + r.status;
    
    div.innerHTML = `
      <div class="rr-provider-group">
        <button class="rr-provider-btn" onclick="openDocsModal('${esc(name)}')">
          ${esc(name)}
          <div style="font-size:9px; font-weight:400; opacity:0.7; margin-top:2px;">click for docs</div>
        </button>
        <button class="rr-notes-btn" onclick="openNotesModal('${esc(name)}')">View Notes</button>
      </div>
      <div class="rr-time">${r.time || ''}</div>
    `;
    list.appendChild(div);
  });
}

// ─── TEST LAB ────────────────────────────────────────────────────────────────
function openInLab(idx) {
  state.activeIdx = idx;
  const p = state.providers[idx];
  switchTab('lab');

  document.getElementById('labUrl').value = p.embed || '';
  document.getElementById('activeTestInfo').innerHTML = `
    <div style="font-size:15px;font-weight:700;margin-bottom:5px;">${esc(p.name)}</div>
    <div class="pi-url" style="margin-bottom:4px;">${p.homepage}</div>
    <div class="pi-embed"><strong>Movie:</strong> ${p.embed || 'None'}</div>
    ${p.tv_embed ? `<div class="pi-embed"><strong>TV:</strong> ${p.tv_embed}</div>` : ''}
    ${p.customizations ? `<div style="margin-top:8px;font-size:10px;color:var(--yellow);background:rgba(255,204,0,0.1);padding:6px;border-radius:6px;"><strong>Customization:</strong><br>${esc(p.customizations)}</div>` : ''}
    ${p.llm_profile ? `<div style="margin-top:8px;font-size:10px;color:var(--accent);background:rgba(0,255,204,0.05);padding:8px;border-radius:6px;border:1px solid rgba(0,255,204,0.1);"><strong>LLM Provider Documentation:</strong><br><div style="white-space:pre-wrap;margin-top:4px;color:var(--text)">${esc(p.llm_profile)}</div></div>` : ''}
    <div style="margin-top:8px;font-size:10px;color:var(--muted)">Source: ${p.source || 'unknown'}</div>
    
    <div style="margin-top:15px; background: rgba(255,255,255,0.02); padding:10px; border-radius:8px; border: 1px solid var(--border);">
      <label style="font-size:11px; color:var(--muted); margin-bottom:5px; display:block;">Optional Notes (Reason for Pass/Fail):</label>
      <textarea id="labNotes" style="width:100%; height:60px; background:rgba(255,255,255,0.05); border:1px solid var(--border); border-radius:6px; color:var(--text); padding:8px; font-size:12px; font-family:inherit; resize:none;" placeholder="Enter specific feedback or reasons here..."></textarea>
    </div>
  `;

  labLoad();
  document.getElementById('labResultBtns').style.display = 'flex';
}

function labLoad() {
  const url = document.getElementById('labUrl').value.trim();
  if (!url) return;

  const useSandbox = document.getElementById('useSandbox').checked;
  const box = document.getElementById('labSandbox');
  
  box.innerHTML = `<iframe
    src="${esc(url)}"
    ${useSandbox ? `sandbox="${FAM_SANDBOX}"` : ''}
    allow="${FAM_ALLOW}"
    referrerpolicy="no-referrer"
  ></iframe>`;

  log(`Loaded: ${url} (Sandbox: ${useSandbox ? 'ON' : 'OFF'})`, 'info');
}

function labClear() {
  document.getElementById('labUrl').value = '';
  document.getElementById('labSandbox').innerHTML = `
    <div class="embed-placeholder">
      <div class="ep-icon">📺</div>
      <div>Enter an embed URL and click Load</div>
    </div>`;
  document.getElementById('labResultBtns').style.display = 'none';
  state.activeIdx = null;
  document.getElementById('activeTestInfo').innerHTML = '<div class="muted-text">No provider selected</div>';
}

function labMark(status) {
  const url   = document.getElementById('labUrl').value.trim();
  const notes = document.getElementById('labNotes').value.trim();
  const idx   = state.activeIdx;
  const name  = idx !== null ? state.providers[idx]?.name : url;

  if (!name) { log('No provider active', 'warn'); return; }

  state.results[name] = {
    status,
    embed: url,
    notes: notes || (status === 'pass' ? 'Video played' : 'Failed test'),
    time:  new Date().toLocaleTimeString(),
  };

  if (idx !== null) state.providers[idx].status = status;

  saveState();
  renderProviderList();
  renderResults();
  log(`${name} → ${status.toUpperCase()}`, status === 'pass' ? 'success' : 'error');

  // Auto-advance to next untested provider
  if (idx !== null) {
    const next = state.providers.findIndex((p, i) => i > idx && !state.results[p.name]);
    if (next !== -1) openInLab(next);
    else { labClear(); switchTab('results'); }
  }
}

// ─── EXPORT ──────────────────────────────────────────────────────────────────
function exportResults() {
  const rows = [['Provider', 'Homepage', 'Movie Embed', 'TV Embed', 'Customizations', 'LLM Profile', 'Source', 'Status', 'Notes', 'Time']];

  for (const p of state.providers) {
    const r = state.results[p.name] || {};
    rows.push([p.name, p.homepage, p.embed, p.tv_embed, p.customizations, p.llm_profile, p.source, r.status || 'untested', r.notes || '', r.time || '']);
  }

  const csv = rows.map(r => r.map(c => `"${String(c||'').replace(/"/g,'""')}"`).join(',')).join('\n');
  const a = document.createElement('a');
  a.href     = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = 'fam-results-' + Date.now() + '.csv';
  a.click();
  log('Exported CSV', 'success');
}

// ─── UTILS ───────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function fmtDate(iso) {
  try { return new Date(iso).toLocaleString(); } catch(e) { return iso; }
}

// ─── MODALS ──────────────────────────────────────────────────────────────────
let activeModalProvider = null;

function openDocsModal(name) {
  const p = state.providers.find(prov => prov.name === name);
  if (!p) return;
  document.getElementById('docsModalTitle').textContent = `${p.name} Docs`;
  document.getElementById('docsModalContent').textContent = p.llm_profile || 'No LLM profile generated for this provider.';
  document.getElementById('docsModal').classList.add('active');
}

function openNotesModal(name) {
  activeModalProvider = name;
  const r = state.results[name];
  document.getElementById('notesModalTitle').textContent = `Notes for ${name}`;
  document.getElementById('notesModalInput').value = r ? (r.notes || '') : '';
  document.getElementById('notesModal').classList.add('active');
}

function closeModal(id) {
  document.getElementById(id).classList.remove('active');
  if (id === 'notesModal') activeModalProvider = null;
}

document.getElementById('saveNotesBtn')?.addEventListener('click', () => {
  if (!activeModalProvider) return;
  const newNotes = document.getElementById('notesModalInput').value;
  if (!state.results[activeModalProvider]) {
    state.results[activeModalProvider] = { status: 'idle', embed: '', notes: newNotes, time: new Date().toLocaleTimeString() };
  } else {
    state.results[activeModalProvider].notes = newNotes;
  }
  saveState();
  closeModal('notesModal');
  log(`Updated notes for ${activeModalProvider}`, 'success');
});

// ─── INIT ────────────────────────────────────────────────────────────────────
window.onload = loadState;
