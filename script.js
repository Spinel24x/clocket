let isAdmin = false;

// ==================== Init ====================
async function init() {
    // فقط توی dashboard.html چک کن، نه login.html
    if (window.location.pathname === '/login.html' || window.location.pathname === '/login') {
        setupLoginForm();
        return;
    }
    
    try {
        const res = await fetch('/api/me');
        if (!res.ok) {
            window.location.href = '/login.html';
            return;
        }
        const user = await res.json();
        isAdmin = user.is_admin;
        
        document.getElementById('userDisplay').textContent = 
            '👤 ' + user.username + (isAdmin ? ' (Admin)' : '');

        const healthRes = await fetch('/health');
        const health = await healthRes.json();
        if (health.cf_domain && health.cf_domain !== 'not set') {
            document.getElementById('cfStatus').innerHTML = 
                '<span class="badge badge-active">🌐 Domain OK</span>';
        } else {
            document.getElementById('cfStatus').innerHTML = 
                '<span class="badge badge-inactive">⚠️ No Domain</span>';
        }

        setupTabs();
        setupForms();

        if (isAdmin) {
            loadConfigs();
            loadUsers();
        } else {
            document.querySelectorAll('.tab-btn').forEach(btn => {
                if (btn.dataset.tab !== 'me') btn.style.display = 'none';
            });
            loadMyConfig();
        }
    } catch (err) {
        window.location.href = '/login.html';
    }
}

// ==================== Login Form ====================
function setupLoginForm() {
    const form = document.getElementById('loginForm');
    if (!form) return;
    
    form.addEventListener('submit', async function(e) {
        e.preventDefault();
        const errorBox = document.getElementById('errorBox');
        if (errorBox) errorBox.style.display = 'none';
        
        const formData = new FormData();
        formData.append('username', document.getElementById('username').value);
        formData.append('password', document.getElementById('password').value);
        
        try {
            const res = await fetch('/api/login', { method: 'POST', body: formData });
            const data = await res.json();
            
            if (res.ok && data.success) {
                window.location.href = '/dashboard.html';
            } else {
                if (errorBox) {
                    errorBox.textContent = data.detail || 'Login failed';
                    errorBox.style.display = 'block';
                }
            }
        } catch (err) {
            if (errorBox) {
                errorBox.textContent = 'Connection error';
                errorBox.style.display = 'block';
            }
        }
    });
}

// ==================== Tabs ====================
function setupTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
        });
    });
}

// ==================== Toast ====================
function showToast(message, type) {
    type = type || 'success';
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.className = 'toast ' + type;
    toast.style.display = 'block';
    setTimeout(function() { toast.style.display = 'none'; }, 3000);
}

// ==================== Forms ====================
function setupForms() {
    const configForm = document.getElementById('addConfigForm');
    if (configForm) {
        configForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const formData = new FormData();
            formData.append('name', document.getElementById('cfgName').value);
            formData.append('remarks', document.getElementById('cfgRemarks').value);
            formData.append('traffic_limit_gb', document.getElementById('cfgTraffic').value);
            formData.append('expire_days', document.getElementById('cfgExpire').value);

            const res = await fetch('/api/configs', { method: 'POST', body: formData });
            const data = await res.json();

            if (data.success) {
                showToast('✅ Config created!');
                document.getElementById('cfgName').value = '';
                document.getElementById('cfgRemarks').value = '';
                document.getElementById('cfgTraffic').value = '0';
                document.getElementById('cfgExpire').value = '0';
                loadConfigs();
            } else {
                showToast('Error: ' + (data.detail || 'Failed'), 'error');
            }
        });
    }

    const userForm = document.getElementById('addUserForm');
    if (userForm) {
        userForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const formData = new FormData();
            formData.append('username', document.getElementById('newUsername').value);
            formData.append('password', document.getElementById('newPassword').value);
            const cid = document.getElementById('assignConfigId').value;
            if (cid) formData.append('config_id', cid);

            const res = await fetch('/api/users', { method: 'POST', body: formData });
            const data = await res.json();

            if (data.success) {
                showToast('✅ User added!');
                document.getElementById('newUsername').value = '';
                document.getElementById('newPassword').value = '';
                document.getElementById('assignConfigId').value = '';
                loadUsers();
            } else {
                showToast(data.detail || 'Error', 'error');
            }
        });
    }
}

// ==================== Logout ====================
async function logout() {
    await fetch('/api/logout', { method: 'POST' });
    window.location.href = '/login.html';
}

// ==================== Load Configs ====================
async function loadConfigs() {
    if (!isAdmin) return;
    const res = await fetch('/api/configs');
    const configs = await res.json();
    const container = document.getElementById('configsList');
    if (!container) return;

    if (!configs.length) {
        container.innerHTML = '<p style="color:#888;text-align:center;padding:20px;">No configs yet.</p>';
        return;
    }

    container.innerHTML = configs.map(c => `
        <div class="config-item ${c.enabled ? '' : 'disabled'}">
            <div class="config-info">
                <strong>${c.name || 'Unnamed'}</strong>
                <span class="badge ${c.enabled ? 'badge-active' : 'badge-inactive'}">
                    ${c.enabled ? 'Active' : 'Disabled'}
                </span>
                ${c.remarks ? '<br><small>📝 ' + c.remarks + '</small>' : ''}
                <br><code class="uuid-text">${c.uuid}</code>
                <br><small>📊 ${c.traffic_used_gb}/${c.traffic_limit_gb || '∞'} GB</small>
                <br><small>📅 ${c.expire_at || 'No expiry'}</small>
                ${c.domain_set 
                    ? '<br><small style="color:#2ecc71;">✅ Link ready</small>' 
                    : '<br><small style="color:#e74c3c;">⚠️ Set CF_DOMAIN</small>'}
            </div>
            <div class="config-actions">
                ${c.vless_link ? `<button class="btn-sm btn-copy" onclick="copyLink('${c.vless_link.replace(/'/g, "\\'")}')">📋 Copy</button>` : ''}
                <button class="btn-sm btn-edit" onclick="editConfig(${c.id}, '${(c.name||'').replace(/'/g, "\\'")}', '${(c.remarks||'').replace(/'/g, "\\'")}', ${c.traffic_limit_gb})">✏️</button>
                <button class="btn-sm btn-toggle" onclick="toggleConfig(${c.id})">
                    ${c.enabled ? '⏸ Disable' : '▶️ Enable'}
                </button>
                <button class="btn-sm btn-delete" onclick="deleteConfig(${c.id})">🗑</button>
            </div>
        </div>
    `).join('');
}

async function toggleConfig(id) {
    await fetch('/api/configs/' + id + '/toggle', { method: 'PATCH' });
    loadConfigs();
}

async function deleteConfig(id) {
    if (!confirm('Delete this config?')) return;
    await fetch('/api/configs/' + id, { method: 'DELETE' });
    showToast('Config deleted');
    loadConfigs();
}

async function editConfig(id, name, remarks, traffic) {
    const n = prompt('Config name:', name) || '';
    const r = prompt('Remarks:', remarks) || '';
    const t = prompt('Traffic limit (GB):', traffic) || '0';
    
    const formData = new FormData();
    formData.append('name', n);
    formData.append('remarks', r);
    formData.append('traffic_limit_gb', t);

    const res = await fetch('/api/configs/' + id, { method: 'PUT', body: formData });
    const data = await res.json();
    
    if (data.success) {
        showToast('✅ Config updated!');
        loadConfigs();
    } else {
        showToast('Error updating', 'error');
    }
}

function copyLink(link) {
    navigator.clipboard.writeText(link).then(function() {
        showToast('📋 VLESS link copied!');
    }).catch(function() {
        prompt('Copy this link:', link);
    });
}

// ==================== Load Users ====================
async function loadUsers() {
    if (!isAdmin) return;
    const res = await fetch('/api/users');
    const users = await res.json();
    const container = document.getElementById('usersList');
    if (!container) return;

    if (!users.length) {
        container.innerHTML = '<p style="color:#888;text-align:center;padding:20px;">No users yet.</p>';
        return;
    }

    container.innerHTML = users.map(u => `
        <div class="user-item">
            <div class="user-info-text">
                <strong>👤 ${u.username} ${u.is_admin ? '<span style="color:#f39c12;">(Admin)</span>' : ''}</strong>
                ${u.config_name 
                    ? '<br><small>Config: ' + u.config_name + '</small>' 
                    : '<br><small style="color:#888;">No config</small>'}
            </div>
            <div class="user-actions">
                ${!u.is_admin ? '<button class="btn-sm btn-delete" onclick="deleteUser(' + u.id + ')">🗑</button>' : ''}
            </div>
        </div>
    `).join('');
}

async function deleteUser(id) {
    if (!confirm('Delete this user?')) return;
    await fetch('/api/users/' + id, { method: 'DELETE' });
    showToast('User deleted');
    loadUsers();
}

// ==================== My Config ====================
async function loadMyConfig() {
    const res = await fetch('/api/my-config');
    const data = await res.json();
    const container = document.getElementById('myConfig');
    if (!container) return;

    if (!data.has_config) {
        container.innerHTML = '<p style="color:#888;text-align:center;padding:20px;">No config assigned. Contact admin.</p>';
        return;
    }

    container.innerHTML = `
        <p><strong>Name:</strong> ${data.name || 'Unnamed'}</p>
        ${data.remarks ? '<p><strong>Remarks:</strong> ' + data.remarks + '</p>' : ''}
        <p><strong>UUID:</strong> <code style="color:#7c5cfc;">${data.uuid}</code></p>
        ${data.vless_link 
            ? `<p style="color:#2ecc71;margin-top:15px;">✅ Your VLESS link is ready</p>
               <button class="btn-primary" onclick="copyLink('${data.vless_link.replace(/'/g, "\\'")}')">📋 Copy VLESS Link</button>` 
            : '<p style="color:#e74c3c;margin-top:15px;">⚠️ Domain not configured</p>'}
    `;
}

// ==================== Start ====================
document.addEventListener('DOMContentLoaded', init);
