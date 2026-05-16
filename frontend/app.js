/* ═══════════════════════════════════════════════════
   SDIP Frontend — Application Logic
   ═══════════════════════════════════════════════════ */

const API = {
    auth: 'http://localhost:3001',
    docs: 'http://localhost:3002',
    audit: 'http://localhost:3006',
};

// ─── State ────────────────────────────────────────
let state = {
    accessToken: localStorage.getItem('sdip_token') || null,
    refreshToken: localStorage.getItem('sdip_refresh') || null,
    user: JSON.parse(localStorage.getItem('sdip_user') || 'null'),
};

// ─── Helpers ──────────────────────────────────────
function saveAuth(data) {
    state.accessToken = data.access_token;
    state.refreshToken = data.refresh_token;
    state.user = data.user;
    localStorage.setItem('sdip_token', data.access_token);
    localStorage.setItem('sdip_refresh', data.refresh_token);
    localStorage.setItem('sdip_user', JSON.stringify(data.user));
}

function clearAuth() {
    state.accessToken = null;
    state.refreshToken = null;
    state.user = null;
    localStorage.removeItem('sdip_token');
    localStorage.removeItem('sdip_refresh');
    localStorage.removeItem('sdip_user');
}

async function api(base, path, opts = {}) {
    const headers = { ...(opts.headers || {}) };
    if (state.accessToken) headers['Authorization'] = `Bearer ${state.accessToken}`;
    if (!(opts.body instanceof FormData) && opts.body) headers['Content-Type'] = 'application/json';

    const res = await fetch(`${base}${path}`, { ...opts, headers });
    if (res.status === 401 && state.refreshToken) {
        const refreshed = await tryRefresh();
        if (refreshed) {
            headers['Authorization'] = `Bearer ${state.accessToken}`;
            return fetch(`${base}${path}`, { ...opts, headers });
        } else {
            clearAuth(); showScreen('auth');
            return res;
        }
    }
    return res;
}

async function tryRefresh() {
    try {
        const res = await fetch(`${API.auth}/auth/refresh`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: state.refreshToken }),
        });
        if (res.ok) {
            const data = await res.json();
            state.accessToken = data.access_token;
            state.refreshToken = data.refresh_token;
            localStorage.setItem('sdip_token', data.access_token);
            localStorage.setItem('sdip_refresh', data.refresh_token);
            return true;
        }
    } catch(e) {}
    return false;
}

function toast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 3500);
}

function timeAgo(dateStr) {
    const d = new Date(dateStr);
    const now = new Date();
    const s = Math.floor((now - d) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s/60)}m ago`;
    if (s < 86400) return `${Math.floor(s/3600)}h ago`;
    return d.toLocaleDateString();
}

function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}

// ─── Screen Management ───────────────────────────
function showScreen(name) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(`${name}-screen`).classList.add('active');
}

function showPage(name) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(`page-${name}`).classList.add('active');
    document.querySelectorAll('.nav-link').forEach(n => n.classList.remove('active'));
    document.querySelector(`.nav-link[data-page="${name}"]`).classList.add('active');

    if (name === 'dashboard') loadDashboard();
    if (name === 'documents') loadDocuments();
    if (name === 'audit') loadAuditLogs();
    if (name === 'users') loadUsers();
}

// ─── Auth ─────────────────────────────────────────
document.querySelectorAll('.auth-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.auth-form').forEach(f => f.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(`${tab.dataset.tab}-form`).classList.add('active');
    });
});

document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.getElementById('login-btn');
    const errEl = document.getElementById('login-error');
    btn.disabled = true;
    btn.querySelector('.spinner').classList.remove('hidden');
    errEl.classList.add('hidden');

    try {
        const res = await fetch(`${API.auth}/auth/login`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email: document.getElementById('login-email').value,
                password: document.getElementById('login-password').value,
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error?.message || 'Login failed');
        saveAuth(data);
        enterApp();
        toast('Welcome back!', 'success');
    } catch (err) {
        errEl.textContent = err.message;
        errEl.classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.querySelector('.spinner').classList.add('hidden');
    }
});

document.getElementById('register-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.getElementById('reg-btn');
    const errEl = document.getElementById('reg-error');
    btn.disabled = true;
    btn.querySelector('.spinner').classList.remove('hidden');
    errEl.classList.add('hidden');

    try {
        const res = await fetch(`${API.auth}/auth/register`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email: document.getElementById('reg-email').value,
                password: document.getElementById('reg-password').value,
                display_name: document.getElementById('reg-name').value,
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error?.message || data.error?.details?.[0]?.msg || 'Registration failed');
        saveAuth(data);
        enterApp();
        toast('Account created!', 'success');
    } catch (err) {
        errEl.textContent = err.message;
        errEl.classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.querySelector('.spinner').classList.add('hidden');
    }
});

document.getElementById('logout-btn').addEventListener('click', () => {
    clearAuth();
    showScreen('auth');
    toast('Logged out', 'info');
});

function enterApp() {
    updateUserUI();
    showScreen('app');
    showPage('dashboard');
}

function updateUserUI() {
    if (!state.user) return;
    document.getElementById('user-name').textContent = state.user.display_name || state.user.email;
    document.getElementById('user-role').textContent = state.user.role;
    document.getElementById('user-avatar').textContent = (state.user.display_name || state.user.email)[0].toUpperCase();
    // Hide users page for non-admins
    const usersNav = document.getElementById('nav-users');
    if (state.user.role !== 'admin') usersNav.style.display = 'none';
    else usersNav.style.display = '';
}

// ─── Navigation ───────────────────────────────────
document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', () => showPage(link.dataset.page));
});

// ─── Dashboard ────────────────────────────────────
async function loadDashboard() {
    // Service health checks
    const services = [
        { name: 'Auth Service', url: `${API.auth}/health` },
        { name: 'Document Service', url: `${API.docs}/health` },
        { name: 'Audit Service', url: `${API.audit}/health` },
    ];
    const healthEl = document.getElementById('service-health');
    let healthHTML = '';
    for (const svc of services) {
        try {
            const res = await fetch(svc.url, { signal: AbortSignal.timeout(3000) });
            const ok = res.ok;
            healthHTML += `<div class="service-item"><span class="service-name">${svc.name}</span><span class="service-badge ${ok?'online':'offline'}">${ok?'Online':'Error'}</span></div>`;
        } catch {
            healthHTML += `<div class="service-item"><span class="service-name">${svc.name}</span><span class="service-badge offline">Offline</span></div>`;
        }
    }
    healthEl.innerHTML = healthHTML;

    // Load stats
    try {
        const docsRes = await api(API.docs, '/documents/?limit=1');
        if (docsRes.ok) { const d = await docsRes.json(); document.getElementById('stat-docs-val').textContent = d.total; }
    } catch { document.getElementById('stat-docs-val').textContent = '—'; }

    if (state.user?.role === 'admin') {
        try {
            const usersRes = await api(API.auth, '/auth/users?limit=1');
            if (usersRes.ok) { const d = await usersRes.json(); document.getElementById('stat-users-val').textContent = d.total; }
        } catch { document.getElementById('stat-users-val').textContent = '—'; }

        try {
            const auditRes = await api(API.audit, '/audit/stats?period=day');
            if (auditRes.ok) {
                const d = await auditRes.json();
                document.getElementById('stat-events-val').textContent = d.total_events;
                const securityCount = d.stats.filter(s => s.action?.startsWith('security.')).reduce((a,c) => a + Number(c.count), 0);
                document.getElementById('stat-security-val').textContent = securityCount;
            }
        } catch { document.getElementById('stat-events-val').textContent = '—'; }

        // Recent activity
        try {
            const logsRes = await api(API.audit, '/audit/logs?limit=8');
            if (logsRes.ok) {
                const d = await logsRes.json();
                const actEl = document.getElementById('recent-activity');
                if (d.logs.length === 0) {
                    actEl.innerHTML = '<p style="color:var(--text-muted)">No recent activity</p>';
                } else {
                    actEl.innerHTML = d.logs.map(log => `
                        <div class="activity-item">
                            <div class="activity-dot ${log.severity}"></div>
                            <div>
                                <div class="activity-text"><strong>${log.action}</strong></div>
                                <div class="activity-time">${timeAgo(log.timestamp)}</div>
                            </div>
                        </div>`).join('');
                }
            }
        } catch { document.getElementById('recent-activity').innerHTML = '<p style="color:var(--text-muted)">Could not load activity</p>'; }
    } else {
        document.getElementById('stat-users-val').textContent = '—';
        document.getElementById('stat-events-val').textContent = '—';
        document.getElementById('stat-security-val').textContent = '—';
        document.getElementById('recent-activity').innerHTML = '<p style="color:var(--text-muted)">Admin access required</p>';
    }
}

// ─── Documents ────────────────────────────────────
async function loadDocuments() {
    const listEl = document.getElementById('documents-list');
    listEl.innerHTML = '<div class="loading-pulse"></div>';
    try {
        const res = await api(API.docs, '/documents/?limit=50');
        if (!res.ok) throw new Error('Failed to load');
        const data = await res.json();
        if (data.documents.length === 0) {
            listEl.innerHTML = `<div class="empty-state">
                <svg width="64" height="64" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><path d="M13 2v7h7"/></svg>
                <h3>No documents yet</h3><p>Upload your first document to get started</p></div>`;
            return;
        }
        listEl.innerHTML = data.documents.map(doc => `
            <div class="doc-card glass" data-id="${doc.id}">
                <div class="doc-info">
                    <div class="doc-icon"><svg width="22" height="22" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><path d="M13 2v7h7"/></svg></div>
                    <div class="doc-meta">
                        <h4>${doc.title || doc.file_name}</h4>
                        <p>${doc.file_name} · ${formatBytes(doc.file_size)} · ${timeAgo(doc.created_at)}</p>
                    </div>
                </div>
                <div class="doc-actions">
                    <button class="btn btn-secondary btn-sm" onclick="downloadDoc('${doc.id}','${doc.file_name}')">Download</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteDoc('${doc.id}')">Delete</button>
                </div>
            </div>`).join('');
    } catch (err) {
        listEl.innerHTML = `<div class="empty-state"><h3>Error loading documents</h3><p>${err.message}</p></div>`;
    }
}

// Upload modal
const uploadModal = document.getElementById('upload-modal');
document.getElementById('upload-btn').addEventListener('click', () => uploadModal.classList.remove('hidden'));
document.getElementById('upload-modal-close').addEventListener('click', () => uploadModal.classList.add('hidden'));
document.querySelector('.modal-overlay')?.addEventListener('click', () => uploadModal.classList.add('hidden'));

const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('upload-file');
const fileInfo = document.getElementById('file-info');

dropzone.addEventListener('click', () => fileInput.click());
dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('drag-over'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
dropzone.addEventListener('drop', (e) => { e.preventDefault(); dropzone.classList.remove('drag-over'); fileInput.files = e.dataTransfer.files; showFileInfo(); });
fileInput.addEventListener('change', showFileInfo);

function showFileInfo() {
    if (fileInput.files.length) {
        fileInfo.textContent = `${fileInput.files[0].name} (${formatBytes(fileInput.files[0].size)})`;
        fileInfo.classList.remove('hidden');
    }
}

document.getElementById('upload-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.getElementById('upload-submit-btn');
    const errEl = document.getElementById('upload-error');
    if (!fileInput.files.length) { errEl.textContent = 'Please select a file'; errEl.classList.remove('hidden'); return; }
    btn.disabled = true;
    btn.querySelector('.spinner').classList.remove('hidden');
    errEl.classList.add('hidden');

    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    fd.append('title', document.getElementById('upload-title').value);
    fd.append('description', document.getElementById('upload-desc').value);

    try {
        const res = await api(API.docs, '/documents/upload', { method: 'POST', body: fd });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error?.message || 'Upload failed');
        uploadModal.classList.add('hidden');
        document.getElementById('upload-form').reset();
        fileInfo.classList.add('hidden');
        toast('Document encrypted & uploaded!', 'success');
        loadDocuments();
    } catch (err) {
        errEl.textContent = err.message;
        errEl.classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.querySelector('.spinner').classList.add('hidden');
    }
});

async function downloadDoc(id, filename) {
    try {
        const res = await api(API.docs, `/documents/${id}/download`);
        if (!res.ok) throw new Error('Download failed');
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename; a.click();
        URL.revokeObjectURL(url);
        toast('Download started', 'success');
    } catch (err) { toast(err.message, 'error'); }
}

async function deleteDoc(id) {
    if (!confirm('Delete this document? This cannot be undone.')) return;
    try {
        const res = await api(API.docs, `/documents/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('Delete failed');
        toast('Document deleted', 'success');
        loadDocuments();
    } catch (err) { toast(err.message, 'error'); }
}

// ─── Audit Logs ───────────────────────────────────
async function loadAuditLogs() {
    const tbody = document.getElementById('audit-tbody');
    tbody.innerHTML = '<tr><td colspan="5"><div class="loading-pulse"></div></td></tr>';
    const severity = document.getElementById('audit-severity').value;
    const params = severity ? `?severity=${severity}&limit=50` : '?limit=50';
    try {
        const res = await api(API.audit, `/audit/logs${params}`);
        if (!res.ok) throw new Error('Failed to load');
        const data = await res.json();
        if (data.logs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:2rem">No audit logs found</td></tr>';
            return;
        }
        tbody.innerHTML = data.logs.map(log => `<tr>
            <td>${new Date(log.timestamp).toLocaleString()}</td>
            <td><strong>${log.action}</strong></td>
            <td style="font-size:0.78rem">${log.user_id ? log.user_id.substring(0,8)+'...' : '—'}</td>
            <td><span class="severity-badge ${log.severity}">${log.severity}</span></td>
            <td>${log.ip_address || '—'}</td>
        </tr>`).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" style="color:var(--red);padding:1rem">${err.message}</td></tr>`;
    }
}

document.getElementById('audit-refresh-btn').addEventListener('click', loadAuditLogs);
document.getElementById('audit-severity').addEventListener('change', loadAuditLogs);

// ─── Users ────────────────────────────────────────
async function loadUsers() {
    const tbody = document.getElementById('users-tbody');
    tbody.innerHTML = '<tr><td colspan="5"><div class="loading-pulse"></div></td></tr>';
    try {
        const res = await api(API.auth, '/auth/users?limit=50');
        if (!res.ok) throw new Error('Admin access required');
        const data = await res.json();
        tbody.innerHTML = data.users.map(u => `<tr>
            <td><strong>${u.display_name || '—'}</strong></td>
            <td>${u.email}</td>
            <td><span class="severity-badge ${u.role === 'admin' ? 'warning' : 'info'}">${u.role}</span></td>
            <td><span class="service-badge ${u.is_active ? 'online' : 'offline'}">${u.is_active ? 'Active' : 'Disabled'}</span></td>
            <td>${new Date(u.created_at).toLocaleDateString()}</td>
        </tr>`).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" style="color:var(--red);padding:1rem">${err.message}</td></tr>`;
    }
}

// ─── Init ─────────────────────────────────────────
if (state.accessToken && state.user) {
    enterApp();
} else {
    showScreen('auth');
}
