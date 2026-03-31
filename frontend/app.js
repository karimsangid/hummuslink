/**
 * HummusLink - Cross-platform sync bridge PWA
 * Vanilla JavaScript, no build step, no dependencies.
 */

class HummusLink {
    constructor() {
        this.ws = null;
        this.deviceId = localStorage.getItem('hummuslink_device_id') || this.generateId();
        this.deviceName = localStorage.getItem('hummuslink_device_name') || 'iPhone';
        this.connected = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 50;
        this.clipboard = '';
        this.clipboardHistory = [];
        this.files = [];
        this.sharedItems = JSON.parse(localStorage.getItem('hummuslink_shared') || '[]');
        this.currentTab = 'share';

        // Persist device ID
        localStorage.setItem('hummuslink_device_id', this.deviceId);

        this.init();
    }

    generateId() {
        return 'phone_' + Math.random().toString(36).substr(2, 12);
    }

    init() {
        this.bindEvents();
        this.switchTab('share');
        this.connect();
        this.registerServiceWorker();
        this.fetchClipboard();
        this.fetchFiles();
    }

    // ==================== WebSocket ====================

    connect() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${location.host}/ws/${this.deviceId}?device_name=${encodeURIComponent(this.deviceName)}&device_type=phone`;

        try {
            this.ws = new WebSocket(wsUrl);
        } catch (e) {
            console.error('WebSocket creation failed:', e);
            this.scheduleReconnect();
            return;
        }

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.connected = true;
            this.reconnectAttempts = 0;
            this.updateConnectionStatus();
            this.showToast('Connected to PC', 'success');
        };

        this.ws.onclose = () => {
            console.log('WebSocket disconnected');
            this.connected = false;
            this.updateConnectionStatus();
            this.scheduleReconnect();
        };

        this.ws.onerror = (err) => {
            console.error('WebSocket error:', err);
        };

        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            } catch (e) {
                console.error('Failed to parse message:', e);
            }
        };
    }

    scheduleReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) return;
        const delay = Math.min(1000 * Math.pow(1.5, this.reconnectAttempts), 30000);
        this.reconnectAttempts++;
        console.log(`Reconnecting in ${Math.round(delay / 1000)}s (attempt ${this.reconnectAttempts})`);
        setTimeout(() => this.connect(), delay);
    }

    send(message) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
            return true;
        }
        this.showToast('Not connected to PC', 'error');
        return false;
    }

    handleMessage(data) {
        switch (data.type) {
            case 'ping':
                this.send({ type: 'pong' });
                break;

            case 'pong':
                break;

            case 'clipboard_sync':
                this.clipboard = data.content || '';
                this.addToClipboardHistory(data.content, data.from || 'pc');
                this.renderClipboardTab();
                this.addFeedItem('clipboard', data.content, 'From PC');
                break;

            case 'text_share':
                this.addFeedItem('text', data.content, 'From PC');
                this.showToast('Text received from PC');
                this.renderShareTab();
                break;

            case 'file_ready':
                this.addFeedItem('file', data.filename, 'From PC', data.url);
                this.showToast(`File received: ${data.filename}`);
                this.fetchFiles();
                break;

            case 'device_connected':
                this.showToast(`${data.device_name} connected`);
                this.updateConnectionStatus();
                break;

            case 'device_disconnected':
                this.updateConnectionStatus();
                break;

            case 'notification':
                this.showToast(`${data.title}: ${data.body}`);
                break;
        }
    }

    // ==================== Features ====================

    syncClipboard(text) {
        if (!text) return;
        this.send({
            type: 'clipboard_sync',
            content: text,
            from: this.deviceId,
        });
        this.addToClipboardHistory(text, 'phone');
        this.showToast('Clipboard sent to PC', 'success');
    }

    shareText(text) {
        if (!text || !text.trim()) return;
        this.send({
            type: 'text_share',
            content: text.trim(),
            from: this.deviceId,
        });
        this.addFeedItem('text', text.trim(), 'To PC');
        this.showToast('Sent to PC', 'success');
    }

    async uploadFile(file) {
        if (!file) return;
        const formData = new FormData();
        formData.append('file', file);
        formData.append('from_device', this.deviceId);

        this.showToast(`Uploading ${file.name}...`);

        try {
            const resp = await fetch(`/api/files/upload?from_device=${encodeURIComponent(this.deviceId)}`, {
                method: 'POST',
                body: formData,
            });

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.error || 'Upload failed');
            }

            const meta = await resp.json();
            this.addFeedItem('file', meta.filename, 'To PC', meta.url);
            this.showToast(`Uploaded: ${meta.filename}`, 'success');
            this.fetchFiles();
        } catch (e) {
            this.showToast(`Upload failed: ${e.message}`, 'error');
        }
    }

    async fetchClipboard() {
        try {
            const resp = await fetch('/api/clipboard');
            if (resp.ok) {
                const data = await resp.json();
                this.clipboard = data.content || '';
                this.clipboardHistory = data.history || [];
                this.renderClipboardTab();
            }
        } catch (e) {
            console.error('Failed to fetch clipboard:', e);
        }
    }

    async fetchFiles() {
        try {
            const resp = await fetch('/api/files');
            if (resp.ok) {
                const data = await resp.json();
                this.files = data.files || [];
                this.renderFilesTab();
            }
        } catch (e) {
            console.error('Failed to fetch files:', e);
        }
    }

    // ==================== UI Rendering ====================

    renderShareTab() {
        const container = document.getElementById('share-feed');
        if (!container) return;

        if (this.sharedItems.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">&#128228;</div>
                    <div class="empty-state-text">No activity yet.<br>Share something to get started!</div>
                </div>`;
            return;
        }

        container.innerHTML = this.sharedItems.slice(0, 50).map(item => `
            <div class="feed-item" ${item.url ? `onclick="window.open('${item.url}', '_blank')"` : ''}>
                <div class="feed-icon">${this.getFeedIcon(item.type)}</div>
                <div class="feed-body">
                    <div class="feed-text">${this.escapeHtml(item.preview)}</div>
                    <div class="feed-meta">
                        <span class="feed-direction">${item.direction}</span>
                        <span>${this.timeAgo(item.timestamp)}</span>
                    </div>
                </div>
            </div>
        `).join('');
    }

    renderFilesTab() {
        const container = document.getElementById('file-list');
        if (!container) return;

        if (this.files.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">&#128193;</div>
                    <div class="empty-state-text">No files yet.<br>Upload or transfer files to see them here.</div>
                </div>`;
            return;
        }

        container.innerHTML = `<div class="file-grid">${this.files.map(f => `
            <div class="file-card" onclick="app.downloadFile('${f.file_id}', '${this.escapeHtml(f.filename)}')">
                <div class="file-thumb">${this.getFileIcon(f.filename)}</div>
                <div class="file-name">${this.escapeHtml(f.filename)}</div>
                <div class="file-size">${this.formatSize(f.size)}</div>
            </div>
        `).join('')}</div>`;
    }

    renderClipboardTab() {
        const contentEl = document.getElementById('clipboard-content');
        const historyEl = document.getElementById('clipboard-history');
        if (!contentEl) return;

        if (this.clipboard) {
            contentEl.innerHTML = `<div class="clipboard-content">${this.escapeHtml(this.clipboard)}</div>`;
        } else {
            contentEl.innerHTML = `<div class="clipboard-empty">Clipboard is empty</div>`;
        }

        if (historyEl) {
            if (this.clipboardHistory.length === 0) {
                historyEl.innerHTML = `<div class="empty-state"><div class="empty-state-text">No clipboard history</div></div>`;
            } else {
                historyEl.innerHTML = this.clipboardHistory.map(item => `
                    <div class="clipboard-history-item" onclick="app.copyToPhone('${this.escapeAttr(item.content)}')">
                        <div class="clipboard-history-text">${this.escapeHtml(item.content)}</div>
                        <div class="clipboard-history-time">${this.timeAgo(item.timestamp)}</div>
                    </div>
                `).join('');
            }
        }
    }

    renderSettingsTab() {
        const devicesEl = document.getElementById('settings-devices');
        if (!devicesEl) return;

        fetch('/api/devices').then(r => r.json()).then(data => {
            const devices = data.devices || [];
            if (devices.length === 0) {
                devicesEl.innerHTML = `<div class="setting-item"><span class="setting-label" style="color:var(--text-muted)">No devices connected</span></div>`;
            } else {
                devicesEl.innerHTML = devices.map(d => `
                    <div class="device-list-item">
                        <div class="device-icon">${d.type === 'pc' ? '&#128187;' : '&#128241;'}</div>
                        <div class="device-info">
                            <div class="device-name">${this.escapeHtml(d.name)}</div>
                            <div class="device-type">${d.type}</div>
                        </div>
                    </div>
                `).join('');
            }
        }).catch(() => {});

        fetch('/api/storage').then(r => r.json()).then(data => {
            const el = document.getElementById('settings-storage');
            if (el) el.textContent = `${data.total_mb} MB (${data.file_count} files)`;
        }).catch(() => {});
    }

    // ==================== Navigation ====================

    switchTab(tabName) {
        this.currentTab = tabName;

        // Update tab buttons
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === tabName);
        });

        // Update tab content
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.toggle('active', content.id === `tab-${tabName}`);
        });

        // Render the active tab
        switch (tabName) {
            case 'share': this.renderShareTab(); break;
            case 'files': this.renderFilesTab(); break;
            case 'clipboard': this.renderClipboardTab(); break;
            case 'settings': this.renderSettingsTab(); break;
        }
    }

    // ==================== Actions ====================

    sendShareInput() {
        const input = document.getElementById('share-input');
        if (!input) return;
        const text = input.value.trim();
        if (text) {
            this.shareText(text);
            input.value = '';
        }
    }

    triggerFileUpload() {
        document.getElementById('file-input').click();
    }

    triggerCameraUpload() {
        document.getElementById('camera-input').click();
    }

    handleFileSelect(event) {
        const files = event.target.files;
        if (files) {
            for (let i = 0; i < files.length; i++) {
                this.uploadFile(files[i]);
            }
        }
        event.target.value = '';
    }

    downloadFile(fileId, filename) {
        const a = document.createElement('a');
        a.href = `/api/files/${fileId}`;
        a.download = filename;
        a.click();
    }

    async copyToPhone(text) {
        try {
            await navigator.clipboard.writeText(text);
            this.showToast('Copied to clipboard', 'success');
        } catch (e) {
            // Fallback for iOS
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            this.showToast('Copied to clipboard', 'success');
        }
    }

    async sendClipboardToPC() {
        try {
            const text = await navigator.clipboard.readText();
            if (text) {
                this.syncClipboard(text);
            } else {
                this.showToast('Clipboard is empty', 'error');
            }
        } catch (e) {
            this.showToast('Cannot read clipboard - tap and paste manually', 'error');
        }
    }

    copyPCClipboard() {
        if (this.clipboard) {
            this.copyToPhone(this.clipboard);
        }
    }

    updateDeviceName() {
        const input = document.getElementById('device-name-input');
        if (input) {
            this.deviceName = input.value.trim() || 'iPhone';
            localStorage.setItem('hummuslink_device_name', this.deviceName);
            this.showToast('Device name updated', 'success');
            // Reconnect with new name
            if (this.ws) {
                this.ws.close();
            }
        }
    }

    // ==================== Helpers ====================

    addFeedItem(type, preview, direction, url) {
        const item = {
            type,
            preview: (preview || '').substring(0, 500),
            direction,
            url: url || null,
            timestamp: new Date().toISOString(),
        };
        this.sharedItems.unshift(item);
        if (this.sharedItems.length > 100) {
            this.sharedItems = this.sharedItems.slice(0, 100);
        }
        localStorage.setItem('hummuslink_shared', JSON.stringify(this.sharedItems));
        if (this.currentTab === 'share') this.renderShareTab();
    }

    addToClipboardHistory(content, source) {
        if (!content) return;
        this.clipboardHistory.unshift({
            content,
            source,
            timestamp: new Date().toISOString(),
        });
        if (this.clipboardHistory.length > 20) {
            this.clipboardHistory = this.clipboardHistory.slice(0, 20);
        }
    }

    updateConnectionStatus() {
        const dot = document.getElementById('status-dot');
        const count = document.getElementById('device-count');
        if (dot) {
            dot.classList.toggle('connected', this.connected);
        }
        if (count && this.connected) {
            fetch('/api/devices').then(r => r.json()).then(data => {
                count.textContent = (data.devices || []).length;
            }).catch(() => {});
        } else if (count) {
            count.textContent = '0';
        }
    }

    showToast(message, type) {
        const container = document.getElementById('toast-container');
        if (!container) return;
        const toast = document.createElement('div');
        toast.className = `toast ${type || ''}`;
        toast.textContent = message;
        container.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }

    getFeedIcon(type) {
        switch (type) {
            case 'text': return '&#128172;';
            case 'file': return '&#128196;';
            case 'clipboard': return '&#128203;';
            default: return '&#128228;';
        }
    }

    getFileIcon(filename) {
        const ext = (filename || '').split('.').pop().toLowerCase();
        const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'heif', 'bmp'];
        if (imageExts.includes(ext)) return '&#127748;';
        const videoExts = ['mp4', 'mov', 'avi', 'mkv'];
        if (videoExts.includes(ext)) return '&#127910;';
        const audioExts = ['mp3', 'wav', 'aac', 'm4a'];
        if (audioExts.includes(ext)) return '&#127925;';
        return '&#128196;';
    }

    formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
        if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
        return (bytes / 1073741824).toFixed(1) + ' GB';
    }

    timeAgo(isoString) {
        if (!isoString) return '';
        const diff = (Date.now() - new Date(isoString).getTime()) / 1000;
        if (diff < 5) return 'just now';
        if (diff < 60) return Math.floor(diff) + 's ago';
        if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
        return Math.floor(diff / 86400) + 'd ago';
    }

    escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    escapeAttr(str) {
        if (!str) return '';
        return str.replace(/'/g, "\\'").replace(/"/g, '&quot;').replace(/\n/g, '\\n');
    }

    // ==================== Events ====================

    bindEvents() {
        // Tab navigation
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => this.switchTab(btn.dataset.tab));
        });

        // Share input - send on Enter (but allow Shift+Enter for newlines)
        document.getElementById('share-input')?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendShareInput();
            }
        });

        // File inputs
        document.getElementById('file-input')?.addEventListener('change', (e) => this.handleFileSelect(e));
        document.getElementById('camera-input')?.addEventListener('change', (e) => this.handleFileSelect(e));

        // Upload area drag and drop
        const uploadArea = document.getElementById('upload-area');
        if (uploadArea) {
            uploadArea.addEventListener('dragover', (e) => {
                e.preventDefault();
                uploadArea.classList.add('dragover');
            });
            uploadArea.addEventListener('dragleave', () => {
                uploadArea.classList.remove('dragover');
            });
            uploadArea.addEventListener('drop', (e) => {
                e.preventDefault();
                uploadArea.classList.remove('dragover');
                if (e.dataTransfer.files.length) {
                    for (const file of e.dataTransfer.files) {
                        this.uploadFile(file);
                    }
                }
            });
        }

        // Visibility change - reconnect when app comes back to foreground (critical for iOS)
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                if (!this.connected || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
                    console.log('App became visible, reconnecting...');
                    this.reconnectAttempts = 0;
                    this.connect();
                }
                // Refresh data
                this.fetchClipboard();
                this.fetchFiles();
            }
        });

        // Device name input
        document.getElementById('device-name-input')?.addEventListener('change', () => this.updateDeviceName());
    }

    registerServiceWorker() {
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js').then(() => {
                console.log('Service worker registered');
            }).catch((err) => {
                console.error('Service worker registration failed:', err);
            });
        }
    }
}

// Initialize the app
let app;
document.addEventListener('DOMContentLoaded', () => {
    app = new HummusLink();
});
