// Check login status
async function init() {
    if (window.location.pathname === '/login.html') return;
    
    try {
        var res = await fetch('/api/me');
        if (!res.ok) {
            window.location.href = '/login.html';
            return;
        }
        loadConfigs();
        setupTabs();
        setupForms();
    } catch (err) {
        window.location.href = '/login.html';
    }
}

function setupTabs() {}
function showToast(msg, type) {
    var toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = msg;
    toast.className = 'toast ' + (type || 'success');
    toast.style.display = 'block';
    setTimeout(function() { toast.style.display = 'none'; }, 3000);
}

function setupForms() {
    var form = document.getElementById('addConfigForm');
    if (form) {
        form.addEventListener('submit', async function(e) {
            e.preventDefault();
            var fd = new FormData();
            fd.append('name', document.getElementById('cfgName').value);
            fd.append('remarks', document.getElementById('cfgRemarks').value);

            var res = await fetch('/api/configs', { method: 'POST', body: new URLSearchParams(fd) });
            var data = await res.json();
            
            if (data.success) {
                showToast('Config created!');
                document.getElementById('cfgName').value = '';
                document.getElementById('cfgRemarks').value = '';
                loadConfigs();
            }
        });
    }
}

async function loadConfigs() {
    var res = await fetch('/api/configs');
    var configs = await res.json();
    var container = document.getElementById('configsList');
    if (!container) return;
    
    if (!configs.length) {
        container.innerHTML = '<p style="color:#888;text-align:center;padding:20px;">No configs yet.</p>';
        return;
    }
    
    container.innerHTML = configs.map(function(c) {
        return '<div class="config-item' + (c.enabled ? '' : ' disabled') + '">' +
            '<div class="config-info">' +
            '<strong>' + (c.name || 'Unnamed') + '</strong> ' +
            '<span class="badge ' + (c.enabled ? 'badge-active' : 'badge-inactive') + '">' + (c.enabled ? 'Active' : 'Disabled') + '</span>' +
            (c.remarks ? '<br><small>' + c.remarks + '</small>' : '') +
            '<br><code class="uuid-text">' + c.uuid + '</code>' +
            (c.domain_set ? '<br><small style="color:#2ecc71">Ready</small>' : '<br><small style="color:#e74c3c">No Domain</small>') +
            '</div>' +
            '<div class="config-actions">' +
            (c.vless_link ? '<button class="btn-sm btn-copy" onclick="copyLink(\'' + c.vless_link.replace(/'/g, "\\'") + '\')">Copy</button>' : '') +
            '<button class="btn-sm btn-toggle" onclick="toggleConfig(' + c.id + ')">' + (c.enabled ? 'Disable' : 'Enable') + '</button>' +
            '<button class="btn-sm btn-delete" onclick="deleteConfig(' + c.id + ')">Del</button>' +
            '</div></div>';
    }).join('');
}

async function toggleConfig(id) {
    await fetch('/api/configs/' + id + '/toggle', { method: 'PATCH' });
    loadConfigs();
}

async function deleteConfig(id) {
    if (!confirm('Delete?')) return;
    await fetch('/api/configs/' + id, { method: 'DELETE' });
    showToast('Deleted');
    loadConfigs();
}

function copyLink(link) {
    navigator.clipboard.writeText(link).then(function() {
        showToast('Copied!');
    }).catch(function() {
        prompt('Copy:', link);
    });
}

async function logout() {
    await fetch('/api/logout', { method: 'POST' });
    window.location.href = '/login.html';
}

document.addEventListener('DOMContentLoaded', init);
