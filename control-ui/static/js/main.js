const API_BASE = 'http://localhost:5001/api';

let currentCluster = 'default';
let autoRefreshTimer = null;

document.addEventListener('DOMContentLoaded', () => {
    init();
});

function init() {
    setupEventListeners();
    loadClusterList();
    loadDashboard();
    startAutoRefresh();
}

function setupEventListeners() {
    // Cluster Management
    document.getElementById('btn-create-cluster').addEventListener('click', createCluster);
    document.getElementById('btn-delete-cluster').addEventListener('click', deleteCurrentCluster);

    // Broker Actions
    document.getElementById('btn-add-broker').addEventListener('click', addBroker);
    document.getElementById('btn-refresh').addEventListener('click', loadDashboard);

    // Topics
    document.getElementById('btn-refresh-topics').addEventListener('click', loadTopics);
    document.getElementById('create-topic-form').addEventListener('submit', createTopic);

    // Logs
    document.getElementById('btn-clear-logs').addEventListener('click', clearLogs);

    // Reset Docker
    document.getElementById('btn-reset-docker').addEventListener('click', resetDocker);
}

// --- Activity Logging ---
function log(message, type = 'info') {
    const logsList = document.getElementById('logs-list');
    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;
    const time = new Date().toLocaleTimeString();
    entry.innerHTML = `<span class="timestamp">[${time}]</span> ${message}`;
    logsList.appendChild(entry);
    logsList.scrollTop = logsList.scrollHeight;

    // Keep only last 50 entries
    while (logsList.children.length > 50) {
        logsList.removeChild(logsList.firstChild);
    }
}

function clearLogs() {
    const logsList = document.getElementById('logs-list');
    logsList.innerHTML = '<div class="log-entry info">Logs cleared</div>';
}

async function resetDocker() {
    if (!confirm('⚠️ This will:\n- Stop all containers\n- Prune unused containers, volumes, networks\n- Restart all services\n\nContinue?')) return;

    log('🔄 Starting Docker reset...', 'warning');
    showToast('Resetting Docker... This may take a minute', 'info');

    try {
        const res = await fetch(`${API_BASE}/cluster/reset`, { method: 'POST' });
        const data = await res.json();

        if (data.success) {
            log('✅ Docker reset complete!', 'success');
            if (data.actions) {
                data.actions.forEach(a => log(a, 'info'));
            }
            showToast('Docker reset complete!', 'success');
            setTimeout(loadDashboard, 2000);
        } else {
            log(`❌ Reset failed: ${data.error}`, 'error');
            showToast(data.error, 'error');
        }
    } catch (e) {
        log(`❌ Reset error: ${e.message}`, 'error');
        showToast('Reset failed - check logs', 'error');
    }
}

function startAutoRefresh() {
    if (autoRefreshTimer) clearInterval(autoRefreshTimer);
    autoRefreshTimer = setInterval(() => {
        loadDashboard(true); // silent refresh
    }, 5000);
}

// --- Clusters ---

async function loadClusterList() {
    try {
        const res = await fetch(`${API_BASE}/clusters`);
        const data = await res.json();
        if (data.success) {
            renderSidebar(data.clusters);
        }
    } catch (e) {
        console.error(e);
    }
}

function renderSidebar(clusters) {
    const container = document.getElementById('cluster-list');
    container.innerHTML = '';

    Object.keys(clusters).forEach(cid => {
        const info = clusters[cid];
        const el = document.createElement('div');
        el.className = `nav-item ${cid === currentCluster ? 'active' : ''}`;
        el.innerHTML = `
            <span>${info.name || cid}</span>
            <span class="badge-mini">${info.brokers}</span>
        `;
        el.onclick = () => switchCluster(cid);
        container.appendChild(el);
    });
}

function switchCluster(cid) {
    currentCluster = cid;
    // Update UI headers
    document.getElementById('current-cluster-title').textContent = (cid === 'default') ? 'Default Cluster' : `Cluster: ${cid}`;
    document.getElementById('btn-delete-cluster').style.display = (cid === 'default') ? 'none' : 'block';

    // Reload everything
    loadClusterList(); // re-render sidebar for active state
    loadDashboard();
}

async function createCluster() {
    const name = prompt("Enter unique cluster name (alphanumeric, e.g., 'beta'):");
    if (!name) return;

    try {
        showToast("Creating cluster...", "info");
        const res = await fetch(`${API_BASE}/clusters`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        });
        const data = await res.json();
        if (data.success) {
            showToast("Cluster created!", "success");
            loadClusterList();
            switchCluster(name);
        } else {
            showToast(data.error, "error");
        }
    } catch (e) {
        showToast("Failed to create cluster", "error");
    }
}

async function deleteCurrentCluster() {
    if (currentCluster === 'default') return;
    if (!confirm(`Permanently delete cluster "${currentCluster}" and all its data?`)) return;

    try {
        showToast("Deleting cluster...", "info");
        const res = await fetch(`${API_BASE}/clusters/${currentCluster}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showToast("Cluster deleted", "success");
            switchCluster('default');
        } else {
            showToast(data.error, "error");
        }
    } catch (e) {
        showToast("Error deleting cluster", "error");
    }
}

// --- Dashboard ---

async function loadDashboard(silent = false) {
    // Load Status
    if (!silent) log(`Loading dashboard for cluster: ${currentCluster}...`, 'info');
    try {
        const res = await fetch(`${API_BASE}/cluster/status?cluster_id=${currentCluster}`);
        const data = await res.json();

        if (data.success) {
            updateStats(data);
            updateBrokers(data.containers, data.kafka_servers);
            if (!silent) log(`Dashboard loaded: ${data.broker_count} brokers, ${data.topic_count} topics`, 'success');

            // Should we load topics?
            if (!silent) loadTopics();
        } else {
            log(`Dashboard load failed: ${data.error || 'Unknown error'}`, 'error');
        }
    } catch (e) {
        log(`Connection error: ${e.message}`, 'error');
        if (!silent) showToast("Connection error", "error");
    }
}

function updateStats(data) {
    document.getElementById('broker-count').textContent = data.broker_count;
    document.getElementById('topic-count').textContent = data.topic_count;

    const zk = data.containers['zookeeper'];
    const zkEl = document.getElementById('zk-status');
    if (zk && zk.status === 'running') {
        zkEl.textContent = "Running";
        zkEl.style.color = "var(--success)";
    } else {
        zkEl.textContent = "Down";
        zkEl.style.color = "var(--danger)";
    }

    const badge = document.getElementById('cluster-status-badge');
    if (data.broker_count > 0) {
        badge.textContent = "Active";
        badge.className = "status-badge healthy";
    } else {
        badge.textContent = "Empty";
        badge.className = "status-badge warning";
    }
}

function updateBrokers(containers, bootstrap) {
    const list = document.getElementById('brokers-list');
    list.innerHTML = '';

    // Sort logic
    const names = Object.keys(containers).filter(n => n.startsWith('kafka') && n !== 'kafka-ui');
    names.sort();

    if (names.length === 0) {
        list.innerHTML = '<div class="empty-state">No brokers in this cluster.</div>';
        return;
    }

    names.forEach(name => {
        const c = containers[name];
        const isRunning = c.status === 'running';

        const card = document.createElement('div');
        card.className = `broker-card ${isRunning ? 'running' : 'stopped'}`;
        card.innerHTML = `
            <div class="header">
                <h3>${name}</h3>
                <span class="status-dot"></span>
            </div>
            <div class="info">
                Status: ${c.status}
            </div>
            <div class="actions">
                ${isRunning
                ? `<button onclick="stopBroker('${name}')" class="btn-action stop">Stop</button>`
                : `<button onclick="startBroker('${name}')" class="btn-action start">Start</button>`
            }
                <button onclick="deleteBroker('${name}')" class="btn-action delete">🗑</button>
            </div>
        `;
        list.appendChild(card);
    });
}

// --- Broker Actions ---

async function addBroker() {
    if (!confirm(`Add new broker to ${currentCluster}?`)) return;
    log(`Adding broker to cluster: ${currentCluster}...`, 'info');
    try {
        showToast("Adding broker...", "info");
        const res = await fetch(`${API_BASE}/brokers/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cluster_id: currentCluster })
        });
        const data = await res.json();
        if (data.success) {
            log(`Broker ${data.broker?.name || 'new'} created on port ${data.broker?.port || 'N/A'}`, 'success');
            showToast("Broker queued for creation", "success");
            setTimeout(loadDashboard, 3000);
        } else {
            log(`Failed to add broker: ${data.error}`, 'error');
            showToast(data.error, "error");
        }
    } catch (e) {
        log(`Error adding broker: ${e.message}`, 'error');
        showToast("Failed to add broker", "error");
    }
}

async function startBroker(name) {
    log(`Starting broker: ${name}...`, 'info');
    await fetch(`${API_BASE}/brokers/${name}/start`, { method: 'POST' });
    log(`Broker ${name} start requested`, 'success');
    loadDashboard(true);
}

async function stopBroker(name) {
    if (!confirm('Stop broker?')) return;
    log(`Stopping broker: ${name}...`, 'warning');
    await fetch(`${API_BASE}/brokers/${name}/stop`, { method: 'POST' });
    log(`Broker ${name} stopped`, 'info');
    loadDashboard(true);
}

async function deleteBroker(name) {
    if (!confirm('Delete broker permanently?')) return;
    log(`Deleting broker: ${name}...`, 'warning');
    await fetch(`${API_BASE}/brokers/${name}`, { method: 'DELETE' });
    setTimeout(loadDashboard, 2000);
}

// --- Topics ---

async function loadTopics() {
    try {
        const res = await fetch(`${API_BASE}/topics/list?cluster_id=${currentCluster}`);
        const data = await res.json();
        const list = document.getElementById('topics-list');
        list.innerHTML = '';

        if (data.topics && data.topics.length) {
            data.topics.forEach(t => {
                const row = document.createElement('div');
                row.className = 'topic-row';
                row.innerHTML = `
                    <span class="name">${t.name}</span>
                    <span class="meta">${t.partitions} partitions</span>
                    <button onclick="deleteTopic('${t.name}')" class="btn-icon delete">🗑</button>
                `;
                list.appendChild(row);
            });
        } else {
            list.innerHTML = '<div class="empty-state">No topics found</div>';
        }
    } catch (e) {
        console.error(e);
    }
}

async function createTopic(e) {
    e.preventDefault();
    const name = document.getElementById('new-topic-name').value;
    const p = document.getElementById('new-topic-partitions').value;
    const r = document.getElementById('new-topic-rep').value;

    try {
        const res = await fetch(`${API_BASE}/topics/create`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                cluster_id: currentCluster,
                name: name,
                partitions: p,
                replication_factor: r
            })
        });
        const data = await res.json();
        if (data.success) {
            showToast("Topic created", "success");
            document.getElementById('create-topic-form').reset();
            loadTopics();
        } else {
            showToast(data.error, "error");
        }
    } catch (err) {
        showToast("Error creating topic", "error");
    }
}

async function deleteTopic(name) {
    if (!confirm("Delete topic?")) return;
    await fetch(`${API_BASE}/topics/delete/${name}?cluster_id=${currentCluster}`, { method: 'DELETE' });
    loadTopics();
}


// --- Utils ---
function showToast(msg, type = 'info') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = `toast ${type} show`;
    setTimeout(() => t.className = 'toast', 3000);
}
