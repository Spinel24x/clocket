async function init() {
    try {
        var res = await fetch('/api/me');
        if (!res.ok) {
            window.location.href = '/login.html';
            return;
        }
        loadConfigs();
        document.getElementById('addConfigForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            var formData = new URLSearchParams();
            formData.append('name', document.getElementById('cfgName').value);
            formData.append('remarks', document.getElementById('cfgRemarks').value);
            
            var res = await fetch('/api/configs', { method: 'POST', body: formData });
            var data = await res.json();
            if (data.success) {
                showToast('✅ Config created!');
                document.getElementById('cfgName').value = '';
                document.getElementById('cfgRemarks').value = '';
                loadConfigs();
            } else {
                showToast('Error', 'error');
            }
        });
    } catch (err) {
        window.location.href = '/login.html';
    }
}

function showToast(message, type) {
    var toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = 'toast ' + (type || 'success');
    toast.style.display = 'block';
    setTimeout(function() { toast.style.display = 'none'; }, 3000);
}

async function loadConfigs() {
    var res = await fetch('/api/configs');
    var configs = await res.json();
    var container = document.getElementById('configsList');
    
    if (!configs.length) {
        container.innerHTML = '<p style="color:#888;text-align:center;padding:20px;">No configs yet. Create one above.</p>';
        return;
    }
    
    container.innerHTML = configs.map(function(c) {
        var link = c.vless_link || '';
        return '<div class="config-item' + (c.enabled ? '' : ' disabled') + '">' +
            '<div class="config-info">' +
            '<strong>' + (c.name || 'Unnamed') + '</strong> ' +
            '<span class="badge ' + (c.enabled ? 'badge-active' : 'badge-inactive') + '">' + (c.enabled ? 'Active' : 'Disabled') + '</span>' +
            '<br><code class="uuid-text">' + c.uuid + '</code>' +
            (link ? '<br><small style="color:#2ecc71;">✅ VLESS link ready</small>' : '<br><small style="color:#e74c3c;">⚠️ Domain not set</small>') +
            '</div>' +
            '<div class="config-actions">' +
            (link ? '<button class="btn-sm btn-copy" onclick="copyLink(\'' + link.replace(/'/g, "\\'") + '\')">📋 Copy</button>' : '') +
            '<button class="btn-sm btn-toggle" onclick="toggleConfig(' + c.id + ')">' + (c.enabled ? '⏸ Disable' : '▶️ Enable') + '</button>' +
            '<button class="btn-sm btn-delete" onclick="deleteConfig(' + c.id + ')">🗑</button>' +
            '</div></div>';
    }).join('');
}

async function toggleConfig(id) {
    await fetch('/api/configs/' + id + '/toggle', { method: 'PATCH' });
    loadConfigs();
}

async function deleteConfig(id) {
    if (!confirm('Delete this config?')) return;
    await fetch('/api/configs/' + id, { method: 'DELETE' });
    showToast('🗑 Config deleted');
    loadConfigs();
}

function copyLink(link) {
    navigator.clipboard.writeText(link).then(function() {
        showToast('📋 VLESS link copied!');
    }).catch(function() {
        prompt('Copy this link:', link);
    });
}

async function logout() {
    await fetch('/api/logout', { method: 'POST' });
    window.location.href = '/login.html';
}

document.addEventListener('DOMContentLoaded', init);
