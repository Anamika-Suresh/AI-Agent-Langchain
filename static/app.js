/* ==========================================================================
   AI FOUNDER EXTRACTION PLATFORM - FRONTEND INTERACTIVE LOGIC
   ========================================================================== */

let activeWebSocket = null;
let currentActiveJobId = null;
let currentExtractedFounders = [];

// DOM Content Loaded Initializer
document.addEventListener("DOMContentLoaded", () => {
  fetchSystemStats();
  loadDirectoryFounders();
  loadJobHistory();
});

// Tab Switcher
function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.view-section').forEach(sec => sec.classList.remove('active'));

  const activeBtn = document.querySelector(`.tab-btn[onclick="switchTab('${tabId}')"]`);
  const activeSec = document.getElementById(`tab-${tabId}`);

  if (activeBtn) activeBtn.classList.add('active');
  if (activeSec) activeSec.classList.add('active');

  if (tabId === 'directory') loadDirectoryFounders();
  if (tabId === 'history') loadJobHistory();
  if (tabId === 'analytics') fetchSystemStats();
}

// Preset URL Helper
function fillUrl(url) {
  const input = document.getElementById('target-url-input');
  if (input) input.value = url;
}

// Fetch System Stats & Database Status
async function fetchSystemStats() {
  try {
    const res = await fetch('/api/stats');
    if (!res.ok) return;
    const data = await res.json();

    document.getElementById('stat-total-jobs').innerText = data.total_jobs || 0;
    document.getElementById('stat-total-founders').innerText = data.total_founders || 0;
    document.getElementById('stat-success-rate').innerText = `${data.success_rate || 0}%`;
    document.getElementById('stat-running-jobs').innerText = data.running_jobs || 0;

    const dbLabel = document.getElementById('db-type-label');
    if (dbLabel) dbLabel.innerText = data.database_type || "PostgreSQL";

    const diagDb = document.getElementById('diag-db-engine');
    if (diagDb) diagDb.innerText = data.database_type || "PostgreSQL";
  } catch (err) {
    console.error("Failed to fetch system stats:", err);
  }
}

// Submit Extraction Job
async function handleExtractSubmit(event) {
  event.preventDefault();

  const urlInput = document.getElementById('target-url-input');
  const submitBtn = document.getElementById('extract-submit-btn');
  const targetUrl = urlInput.value.trim();

  if (!targetUrl) return;

  // UI state updating
  submitBtn.disabled = true;
  submitBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Initializing Agent...`;

  resetConsoleView();

  try {
    const res = await fetch('/api/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: targetUrl })
    });

    if (!res.ok) {
      const errData = await res.json();
      throw new Error(errData.detail || "Failed to create extraction job");
    }

    const jobData = await res.json();
    currentActiveJobId = jobData.job_id;

    updateConsoleStatus("RUNNING", `Agent executing on ${targetUrl}`);
    appendLogEntry("INFO", "SYSTEM", `Job #${currentActiveJobId.slice(0, 8)} created. Connecting WebSocket stream...`);

    // Connect WebSocket
    connectWebSocket(currentActiveJobId);

  } catch (err) {
    alert(`Error: ${err.message}`);
    updateConsoleStatus("FAILED", err.message);
    submitBtn.disabled = false;
    submitBtn.innerHTML = `<i class="fa-solid fa-robot"></i> Launch AI Agent`;
  }
}

// Connect WebSocket for Live Log Streaming
function connectWebSocket(jobId) {
  if (activeWebSocket) {
    activeWebSocket.close();
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/extract/${jobId}`;

  activeWebSocket = new WebSocket(wsUrl);

  activeWebSocket.onopen = () => {
    appendLogEntry("INFO", "WEBSOCKET", "Real-time status stream established.");
  };

  activeWebSocket.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleWebSocketEvent(data);
    } catch (e) {
      console.error("Error parsing WS message:", e);
    }
  };

  activeWebSocket.onerror = (err) => {
    console.error("WebSocket error:", err);
    appendLogEntry("ERROR", "WEBSOCKET", "WebSocket connection error encountered.");
  };

  activeWebSocket.onclose = () => {
    console.log("WebSocket connection closed.");
  };
}

// Handle incoming WebSocket messages
function handleWebSocketEvent(data) {
  if (data.type === "agent_log") {
    appendLogEntry(data.step_type, data.step_type, data.message);
  } else if (data.type === "founders_update") {
    currentExtractedFounders = data.founders || [];
    renderLiveFounders(currentExtractedFounders);
  } else if (data.type === "completed") {
    updateConsoleStatus("COMPLETED", "Extraction finished successfully!");
    appendLogEntry("MERGE", "SUCCESS", `Extraction complete. ${data.total_founders} founder(s) verified.`);
    
    currentExtractedFounders = data.founders || [];
    renderLiveFounders(currentExtractedFounders);

    document.getElementById('live-export-btns').style.display = 'flex';
    document.getElementById('extract-submit-btn').disabled = false;
    document.getElementById('extract-submit-btn').innerHTML = `<i class="fa-solid fa-robot"></i> Launch AI Agent`;
    
    fetchSystemStats();
  } else if (data.type === "failed") {
    updateConsoleStatus("FAILED", `Extraction failed: ${data.error}`);
    appendLogEntry("ERROR", "FAILED", `Error: ${data.error}`);

    document.getElementById('extract-submit-btn').disabled = false;
    document.getElementById('extract-submit-btn').innerHTML = `<i class="fa-solid fa-robot"></i> Launch AI Agent`;
    
    fetchSystemStats();
  }
}

// Reset Console UI
function resetConsoleView() {
  const logsContainer = document.getElementById('console-logs');
  logsContainer.innerHTML = '';
  
  const foundersContainer = document.getElementById('live-founders-container');
  foundersContainer.innerHTML = `
    <div class="empty-state">
      <i class="fa-solid fa-user-gear empty-icon"></i>
      <p>Extracting founder details...</p>
      <span style="font-size: 0.8rem; max-width: 250px;">AI Agent is browsing the site for team and founder information.</span>
    </div>
  `;

  document.getElementById('live-founders-count').innerText = "0";
  document.getElementById('live-export-btns').style.display = 'none';
  currentExtractedFounders = [];
}

// Update Console Status Badge
function updateConsoleStatus(status, textMsg) {
  const pulseDot = document.getElementById('status-pulse-dot');
  const statusLabel = document.getElementById('job-status-label');
  const consoleTitleText = document.getElementById('console-status-text');

  pulseDot.className = `pulse-dot ${status.toLowerCase()}`;
  statusLabel.innerText = status;
  if (consoleTitleText) consoleTitleText.innerText = textMsg || `Agent Terminal - ${status}`;
}

// Append log entry to terminal
function appendLogEntry(stepType, tagText, message) {
  const logsContainer = document.getElementById('console-logs');
  const timeStr = new Date().toLocaleTimeString('en-US', { hour12: false });

  const entry = document.createElement('div');
  entry.className = 'log-entry';
  entry.innerHTML = `
    <span class="log-time">${timeStr}</span>
    <span class="log-tag ${stepType}">${tagText}</span>
    <span class="log-msg">${escapeHtml(message)}</span>
  `;

  logsContainer.appendChild(entry);
  logsContainer.scrollTop = logsContainer.scrollHeight;
}

// Render Live Founders in Console Panel
function renderLiveFounders(founders) {
  const container = document.getElementById('live-founders-container');
  document.getElementById('live-founders-count').innerText = founders.length;

  if (!founders || founders.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <i class="fa-solid fa-user-slash empty-icon"></i>
        <p>No founders identified on visited pages yet.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = founders.map(f => `
    <div class="founder-card">
      <div class="founder-header">
        <div>
          <div class="founder-name">${escapeHtml(f.name)}</div>
          ${f.role ? `<span class="founder-role">${escapeHtml(f.role)}</span>` : ''}
        </div>
        ${f.linkedin_url ? `
          <a href="${escapeHtml(f.linkedin_url)}" target="_blank" rel="noopener" class="linkedin-link">
            <i class="fa-brands fa-linkedin"></i> LinkedIn
          </a>
        ` : ''}
      </div>

      ${f.bio ? `<div class="founder-bio">${escapeHtml(f.bio)}</div>` : ''}

      ${f.previous_experience ? `
        <div class="founder-meta-block">
          <div class="founder-meta-title">Previous Experience</div>
          <div>${escapeHtml(f.previous_experience)}</div>
        </div>
      ` : ''}

      ${f.evidence && f.evidence.length > 0 ? `
        <div class="founder-meta-block" style="border-left-color: var(--cyan-accent); margin-top: 0.5rem;">
          <div class="founder-meta-title">Evidence Quote</div>
          <div style="font-style: italic;">"${escapeHtml(f.evidence[0])}"</div>
        </div>
      ` : ''}
    </div>
  `).join('');
}

// Load Directory Founders Table
async function loadDirectoryFounders(query = '') {
  try {
    const url = query ? `/api/founders?q=${encodeURIComponent(query)}` : '/api/founders';
    const res = await fetch(url);
    if (!res.ok) return;

    const founders = await res.json();
    const tbody = document.getElementById('founders-table-body');

    if (!founders || founders.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="6" style="text-align: center; color: var(--text-muted); padding: 3rem;">
            No founder records match your query in PostgreSQL database.
          </td>
        </tr>
      `;
      return;
    }

    tbody.innerHTML = founders.map(f => `
      <tr>
        <td style="font-weight: 600; font-family: 'Outfit';">${escapeHtml(f.name)}</td>
        <td>${f.role ? `<span class="founder-role">${escapeHtml(f.role)}</span>` : '<span style="color: var(--text-muted);">-</span>'}</td>
        <td>
          ${f.linkedin_url ? `
            <a href="${escapeHtml(f.linkedin_url)}" target="_blank" class="linkedin-link">
              <i class="fa-brands fa-linkedin"></i> Profile
            </a>
          ` : '<span style="color: var(--text-muted);">-</span>'}
        </td>
        <td style="max-width: 320px; font-size: 0.85rem; color: var(--text-secondary);">
          ${f.bio ? escapeHtml(f.bio.slice(0, 160)) + (f.bio.length > 160 ? '...' : '') : '<span style="color: var(--text-muted);">-</span>'}
        </td>
        <td style="max-width: 250px; font-size: 0.8rem; font-style: italic; color: var(--text-muted);">
          ${f.evidence && f.evidence.length > 0 ? `"${escapeHtml(f.evidence[0].slice(0, 100))}..."` : '-'}
        </td>
        <td style="font-size: 0.8rem; color: var(--text-muted);">
          ${f.created_at ? new Date(f.created_at).toLocaleDateString() : '-'}
        </td>
      </tr>
    `).join('');

  } catch (err) {
    console.error("Failed to load directory founders:", err);
  }
}

// Search Handler
let searchTimeout = null;
function handleDirectorySearch() {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => {
    const q = document.getElementById('directory-search-input').value.trim();
    loadDirectoryFounders(q);
  }, 300);
}

// Load Extraction Job History Table
async function loadJobHistory() {
  try {
    const res = await fetch('/api/jobs');
    if (!res.ok) return;

    const jobs = await res.json();
    const tbody = document.getElementById('history-table-body');

    if (!jobs || jobs.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="6" style="text-align: center; color: var(--text-muted); padding: 3rem;">
            No extraction history recorded yet.
          </td>
        </tr>
      `;
      return;
    }

    tbody.innerHTML = jobs.map(j => `
      <tr>
        <td style="font-family: 'JetBrains Mono'; font-size: 0.82rem; color: var(--cyan-accent);">${j.id.slice(0, 8)}...</td>
        <td style="font-weight: 500;">
          <a href="${escapeHtml(j.url)}" target="_blank" style="color: var(--text-primary); text-decoration: none;">
            ${escapeHtml(j.url)} <i class="fa-solid fa-arrow-up-right-from-square" style="font-size: 0.75rem; color: var(--text-muted);"></i>
          </a>
        </td>
        <td><span class="status-badge ${j.status}">${j.status}</span></td>
        <td style="font-weight: 600; text-align: center;">${j.total_founders || 0}</td>
        <td style="font-size: 0.82rem; color: var(--text-muted);">${new Date(j.created_at).toLocaleString()}</td>
        <td>
          <div style="display: flex; gap: 0.4rem;">
            <button class="action-btn" onclick="openJobModal('${j.id}')" title="View Details"><i class="fa-solid fa-eye"></i></button>
            <a href="/api/export/${j.id}?format=json" class="action-btn" title="Download JSON"><i class="fa-solid fa-download"></i></a>
            <button class="action-btn" onclick="deleteJobRecord('${j.id}')" style="color: var(--rose-accent);" title="Delete"><i class="fa-solid fa-trash"></i></button>
          </div>
        </td>
      </tr>
    `).join('');

  } catch (err) {
    console.error("Failed to load job history:", err);
  }
}

// Modal View Details
async function openJobModal(jobId) {
  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    if (!res.ok) return;

    const data = await res.json();
    document.getElementById('modal-job-title').innerText = `Job #${data.id.slice(0, 8)} Details (${data.url})`;

    const modalBody = document.getElementById('modal-job-body');
    modalBody.innerHTML = `
      <div style="margin-bottom: 1.5rem;">
        <span class="status-badge ${data.status}">${data.status}</span>
        <span style="color: var(--text-muted); margin-left: 1rem; font-size: 0.85rem;">
          Created: ${new Date(data.created_at).toLocaleString()}
        </span>
      </div>

      <h4 style="font-family: 'Outfit'; font-size: 1.1rem; margin-bottom: 0.85rem;">Extracted Founders (${data.founders ? data.founders.length : 0})</h4>
      <div style="display: flex; flex-direction: column; gap: 0.85rem; margin-bottom: 2rem;">
        ${data.founders && data.founders.length > 0 ? data.founders.map(f => `
          <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border-color); padding: 1rem; border-radius: 12px;">
            <div style="display: flex; justify-content: space-between;">
              <strong style="font-family: 'Outfit'; font-size: 1.05rem;">${escapeHtml(f.name)}</strong>
              ${f.linkedin_url ? `<a href="${escapeHtml(f.linkedin_url)}" target="_blank" class="linkedin-link"><i class="fa-brands fa-linkedin"></i> LinkedIn</a>` : ''}
            </div>
            ${f.role ? `<div class="founder-role" style="margin-top: 0.25rem;">${escapeHtml(f.role)}</div>` : ''}
            ${f.bio ? `<p style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.5rem;">${escapeHtml(f.bio)}</p>` : ''}
          </div>
        `).join('') : '<p style="color: var(--text-muted);">No founders extracted for this job.</p>'}
      </div>

      <h4 style="font-family: 'Outfit'; font-size: 1.1rem; margin-bottom: 0.85rem;">Agent Logs</h4>
      <div style="background: var(--term-bg); border: 1px solid var(--border-color); border-radius: 12px; padding: 1rem; font-family: 'JetBrains Mono'; font-size: 0.8rem; max-height: 250px; overflow-y: auto;">
        ${data.logs && data.logs.length > 0 ? data.logs.map(l => `
          <div style="margin-bottom: 0.4rem; color: #cbd5e1;">
            <span style="color: var(--text-muted);">${new Date(l.timestamp).toLocaleTimeString()}</span>
            <span style="color: var(--cyan-accent); font-weight: 600;">[${l.step_type}]</span>
            ${escapeHtml(l.message)}
          </div>
        `).join('') : '<p style="color: var(--text-muted);">No log entries recorded.</p>'}
      </div>
    `;

    document.getElementById('job-modal').classList.add('active');

  } catch (err) {
    console.error("Failed to load job details for modal:", err);
  }
}

function closeModal() {
  document.getElementById('job-modal').classList.remove('active');
}

// Delete Job Record
async function deleteJobRecord(jobId) {
  if (!confirm("Are you sure you want to delete this job record and its extracted data?")) return;
  try {
    const res = await fetch(`/api/jobs/${jobId}`, { method: 'DELETE' });
    if (res.ok) {
      loadJobHistory();
      fetchSystemStats();
    }
  } catch (err) {
    alert(`Failed to delete job: ${err.message}`);
  }
}

// Export Current Job Data
function exportCurrentJob(format) {
  if (!currentActiveJobId) return;
  window.location.href = `/api/export/${currentActiveJobId}?format=${format}`;
}

// Utility: Escape HTML
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
