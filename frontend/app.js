/**
 * HummusLink - Cross-platform sync bridge PWA
 * Vanilla JavaScript, no build step, no dependencies.
 */

class HummusLink {
    constructor() {
        this.ws = null;
        this.isPhone = /iPhone|iPad|iPod|Android|Mobile/i.test(navigator.userAgent);
        this.deviceType = this.isPhone ? 'phone' : 'pc';
        this.targetLabel = this.isPhone ? 'PC' : 'Phone';
        this.selfLabel = this.isPhone ? 'Phone' : 'PC';
        this.deviceId = localStorage.getItem('hummuslink_device_id') || this.generateId();
        this.deviceName = localStorage.getItem('hummuslink_device_name') || (this.isPhone ? 'iPhone' : 'My PC');
        this.connected = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 50;
        this.clipboard = '';
        this.clipboardHistory = [];
        this.files = [];
        this.feedItems = [];
        this.selectionMode = false;
        this.selected = new Set();
        this.fileQuery = '';
        this.currentTab = 'share';

        // Persist device ID
        localStorage.setItem('hummuslink_device_id', this.deviceId);

        // Pairing token: capture from URL on first visit (QR scan), then keep in localStorage.
        const params = new URLSearchParams(location.search);
        const urlToken = params.get('token');
        if (urlToken) {
            localStorage.setItem('hummuslink_token', urlToken);
            params.delete('token');
            const cleaned = params.toString();
            history.replaceState(null, '', location.pathname + (cleaned ? '?' + cleaned : ''));
        }
        this.token = localStorage.getItem('hummuslink_token') || '';

        // Surface share-target redirect
        if (params.get('shared') === '1') {
            setTimeout(() => this.showToast('Shared content received', 'success'), 400);
            params.delete('shared');
            history.replaceState(null, '', location.pathname);
        }

        this.init();
    }

    generateId() {
        const prefix = this.isPhone ? 'phone_' : 'pc_';
        return prefix + Math.random().toString(36).substr(2, 12);
    }

    init() {
        this.updateLabels();
        this.bindEvents();
        this.switchTab('share');
        this.connect();
        this.registerServiceWorker();
        this.fetchClipboard();
        this.fetchFiles();
        this.fetchFeed();
        this.checkForUpdate(); // baseline the running build, then watch for new ones
    }

    // Auto-update: the server exposes a content hash of the frontend at
    // /api/version. We remember the hash we booted with, and whenever the app
    // comes back to the foreground we re-check — if the server has a newer
    // build we reload (the index is served no-store with version-stamped asset
    // URLs, so the reload always pulls fresh JS/CSS). This means a code change
    // lands by itself, with no delete-and-re-add of the home-screen app.
    async checkForUpdate() {
        try {
            const r = await fetch('/api/version', { cache: 'no-store' });
            if (!r.ok) return;
            const { version } = await r.json();
            if (!version) return;
            if (!this._appVersion) {
                this._appVersion = version; // first run: record what we're running
                return;
            }
            if (version !== this._appVersion) {
                this._appVersion = version;
                location.reload();
            }
        } catch (e) {
            /* offline or server down — try again next time */
        }
    }

    updateLabels() {
        const sendBtn = document.getElementById('share-send-btn');
        if (sendBtn) sendBtn.textContent = `Send to ${this.targetLabel}`;
        const clipTitle = document.getElementById('clipboard-title');
        if (clipTitle) clipTitle.textContent = `${this.targetLabel} Clipboard`;
        const clipSendBtn = document.getElementById('clipboard-send-btn');
        if (clipSendBtn) clipSendBtn.textContent = `Send ${this.selfLabel} Clipboard to ${this.targetLabel}`;
        const nameInput = document.getElementById('device-name-input');
        if (nameInput && !localStorage.getItem('hummuslink_device_name')) {
            nameInput.value = this.deviceName;
        }
    }

    // ==================== WebSocket ====================

    connect() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const tokenQs = this.token ? `&token=${encodeURIComponent(this.token)}` : '';
        const wsUrl = `${protocol}//${location.host}/ws/${this.deviceId}?device_name=${encodeURIComponent(this.deviceName)}&device_type=${this.deviceType}${tokenQs}`;

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
            this.showToast(`Connected to ${this.targetLabel}`, 'success');
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
        this.showToast(`Not connected to ${this.targetLabel}`, 'error');
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
                break;

            case 'text_share':
                this.showToast(`Text received from ${this.targetLabel}`);
                this.fetchFeed();
                break;

            case 'file_ready':
                this.showToast(`File received: ${data.filename}`);
                this.fetchFeed();
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
        this.showToast(`Clipboard sent to ${this.targetLabel}`, 'success');
    }

    shareText(text) {
        if (!text || !text.trim()) return;
        this.send({
            type: 'text_share',
            content: text.trim(),
            from: this.deviceId,
        });
        this.showToast(`Sent to ${this.targetLabel}`, 'success');
        // Server records it in the feed while handling the WS message.
        setTimeout(() => this.fetchFeed(), 400);
    }

    async uploadFile(file) {
        if (!file) return;
        // Big files go chunked: survives iOS suspending the PWA mid-transfer.
        if (file.size > 12 * 1024 * 1024) return this.uploadFileChunked(file);
        const progressId = `up_${Math.random().toString(36).substr(2, 9)}`;
        this.showUploadProgress(progressId, file.name, file.size);

        let lastTime = Date.now();
        let lastLoaded = 0;
        let smoothedSpeed = 0;

        try {
            const meta = await new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open('POST', `/api/files/upload?from_device=${encodeURIComponent(this.deviceId)}`);

                xhr.upload.addEventListener('progress', (e) => {
                    if (!e.lengthComputable) return;
                    const now = Date.now();
                    const dt = (now - lastTime) / 1000;
                    if (dt >= 0.25) {
                        const inst = (e.loaded - lastLoaded) / dt;
                        smoothedSpeed = smoothedSpeed ? smoothedSpeed * 0.6 + inst * 0.4 : inst;
                        lastTime = now;
                        lastLoaded = e.loaded;
                    }
                    this.updateUploadProgress(progressId, e.loaded, e.total, smoothedSpeed);
                });

                xhr.addEventListener('load', () => {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        try { resolve(JSON.parse(xhr.responseText)); }
                        catch (err) { reject(new Error('Invalid server response')); }
                    } else {
                        let msg = `Upload failed (${xhr.status})`;
                        try { msg = JSON.parse(xhr.responseText).error || msg; } catch (err) {}
                        reject(new Error(msg));
                    }
                });
                xhr.addEventListener('error', () => reject(new Error('Network error')));
                xhr.addEventListener('abort', () => reject(new Error('Upload aborted')));

                const formData = new FormData();
                formData.append('file', file);
                xhr.send(formData);
            });

            this.completeUploadProgress(progressId, true, meta.filename);
            this.fetchFeed();
            this.fetchFiles();
        } catch (e) {
            this.completeUploadProgress(progressId, false, e.message);
        }
    }

    // Chunked, resumable upload. If the transfer dies (screen lock, app switch,
    // network blip) re-sending the same file resumes from the last good chunk.
    async uploadFileChunked(file) {
        const progressId = `up_${Math.random().toString(36).substr(2, 9)}`;
        this.showUploadProgress(progressId, file.name, file.size);
        const resumeKey = `hlup_${file.name}_${file.size}_${file.lastModified}`;
        let lastTime = Date.now();
        let smoothedSpeed = 0;

        try {
            let uploadId = localStorage.getItem(resumeKey);
            let chunkSize = 4 * 1024 * 1024;
            let totalChunks = 0;
            const received = new Set();

            if (uploadId) {
                const r = await fetch(`/api/upload/status/${uploadId}`, { cache: 'no-store' });
                if (r.ok) {
                    const s = await r.json();
                    chunkSize = s.chunk_size;
                    totalChunks = s.total_chunks;
                    (s.received || []).forEach(i => received.add(i));
                } else {
                    uploadId = null;
                    localStorage.removeItem(resumeKey);
                }
            }
            if (!uploadId) {
                const r = await fetch('/api/upload/init', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename: file.name, size: file.size, from_device: this.deviceId }),
                });
                if (!r.ok) throw new Error(`Upload init failed (${r.status})`);
                const d = await r.json();
                uploadId = d.upload_id;
                chunkSize = d.chunk_size;
                totalChunks = d.total_chunks;
                localStorage.setItem(resumeKey, uploadId);
            }

            for (let i = 0; i < totalChunks; i++) {
                if (received.has(i)) continue;
                const blob = file.slice(i * chunkSize, Math.min((i + 1) * chunkSize, file.size));
                let attempt = 0;
                for (;;) {
                    try {
                        const r = await fetch(`/api/upload/chunk/${uploadId}/${i}`, { method: 'POST', body: blob });
                        if (r.status === 404) {
                            localStorage.removeItem(resumeKey);
                            throw new Error('Upload session expired - send again to restart');
                        }
                        if (!r.ok) throw new Error(`Chunk failed (${r.status})`);
                        break;
                    } catch (e) {
                        if ((e.message || '').startsWith('Upload session expired')) throw e;
                        attempt++;
                        if (attempt >= 5) throw e;
                        await new Promise(res => setTimeout(res, 1000 * attempt));
                    }
                }
                received.add(i);
                const loaded = Math.min(received.size * chunkSize, file.size);
                const now = Date.now();
                const dt = (now - lastTime) / 1000;
                if (dt > 0) {
                    const inst = blob.size / dt;
                    smoothedSpeed = smoothedSpeed ? smoothedSpeed * 0.6 + inst * 0.4 : inst;
                    lastTime = now;
                }
                this.updateUploadProgress(progressId, loaded, file.size, smoothedSpeed);
            }

            const fin = await fetch(`/api/upload/complete/${uploadId}`, { method: 'POST' });
            if (!fin.ok) {
                let msg = `Finalize failed (${fin.status})`;
                try { msg = (await fin.json()).error || msg; } catch (e) {}
                throw new Error(msg);
            }
            const meta = await fin.json();
            localStorage.removeItem(resumeKey);
            this.completeUploadProgress(progressId, true, meta.filename);
            this.fetchFeed();
            this.fetchFiles();
        } catch (e) {
            this.completeUploadProgress(progressId, false, e.message);
        }
    }

    showUploadProgress(id, filename, totalBytes) {
        const container = document.getElementById('upload-progress-container');
        if (!container) return;
        const el = document.createElement('div');
        el.className = 'upload-progress';
        el.id = id;
        el.innerHTML =
            '<div class="upload-progress-row">' +
                '<span class="upload-progress-name"></span>' +
                '<span class="upload-progress-percent">0%</span>' +
            '</div>' +
            '<div class="upload-progress-bar"><div class="upload-progress-fill"></div></div>' +
            '<div class="upload-progress-meta">' +
                '<span class="upload-progress-bytes"></span>' +
                '<span class="upload-progress-speed"></span>' +
            '</div>';
        el.querySelector('.upload-progress-name').textContent = filename;
        el.querySelector('.upload-progress-bytes').textContent = `0 B / ${this.formatSize(totalBytes)}`;
        container.appendChild(el);
    }

    updateUploadProgress(id, loaded, total, speed) {
        const el = document.getElementById(id);
        if (!el) return;
        const pct = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0;
        el.querySelector('.upload-progress-fill').style.width = pct + '%';
        el.querySelector('.upload-progress-percent').textContent = pct + '%';
        el.querySelector('.upload-progress-bytes').textContent =
            `${this.formatSize(loaded)} / ${this.formatSize(total)}`;
        const speedEl = el.querySelector('.upload-progress-speed');
        if (speed && speed > 0) {
            const remaining = total > loaded ? (total - loaded) / speed : 0;
            speedEl.textContent = `${this.formatSize(speed)}/s · ${this.formatDuration(remaining)} left`;
        } else {
            speedEl.textContent = '';
        }
    }

    completeUploadProgress(id, success, msg) {
        const el = document.getElementById(id);
        if (!el) return;
        if (success) {
            el.classList.add('success');
            el.querySelector('.upload-progress-fill').style.width = '100%';
            el.querySelector('.upload-progress-percent').textContent = 'Done';
            el.querySelector('.upload-progress-speed').textContent = '';
            setTimeout(() => el.remove(), 1800);
        } else {
            el.classList.add('error');
            el.querySelector('.upload-progress-percent').textContent = 'Failed';
            el.querySelector('.upload-progress-speed').textContent = msg || '';
            el.style.cursor = 'pointer';
            el.addEventListener('click', () => el.remove());
            setTimeout(() => el.remove(), 8000);
        }
    }

    formatDuration(seconds) {
        if (!isFinite(seconds) || seconds <= 0) return '';
        if (seconds < 60) return `${Math.ceil(seconds)}s`;
        const m = Math.floor(seconds / 60);
        const s = Math.ceil(seconds % 60);
        if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
        const h = Math.floor(m / 60);
        return `${h}h ${m % 60}m`;
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
            const resp = await fetch('/api/files?limit=200');
            if (resp.ok) {
                const data = await resp.json();
                this.files = data.files || [];
                this.renderFilesTab();
            }
        } catch (e) {
            console.error('Failed to fetch files:', e);
        }
    }

    async fetchFeed() {
        try {
            const resp = await fetch('/api/feed');
            if (resp.ok) {
                const data = await resp.json();
                this.feedItems = data.feed || [];
                if (this.currentTab === 'share') this.renderShareTab();
            }
        } catch (e) {
            console.error('Failed to fetch feed:', e);
        }
    }

    // ==================== UI Rendering ====================

    renderShareTab() {
        const container = document.getElementById('share-feed');
        if (!container) return;

        if (this.feedItems.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">&#128228;</div>
                    <div class="empty-state-text">No activity yet.<br>Share something to get started!</div>
                </div>`;
            return;
        }

        container.innerHTML = this.feedItems.map((item, i) => {
            const isFile = item.kind === 'file';
            const available = !isFile || item.available !== false;
            let action = '';
            let hint = '';
            if (isFile && available) {
                action = `onclick="app.openPreview('${item.file_id}', '${this.escapeAttr(item.content)}')"`;
                hint = 'Tap to open';
            } else if (!isFile) {
                action = `onclick="app.copyFeedItem(${i})"`;
                hint = 'Tap to copy';
            }
            const expired = isFile && !available ? ' <span class="feed-expired">(expired)</span>' : '';
            return `
            <div class="feed-item${action ? ' clickable' : ''}" ${action} title="${hint}">
                <div class="feed-icon">${this.getFeedIcon(item.kind)}</div>
                <div class="feed-body">
                    <div class="feed-text">${this.escapeHtml(item.content)}${expired}</div>
                    <div class="feed-meta">
                        <span class="feed-direction">${this.feedDirection(item.from_device)}</span>
                        <span>${this.timeAgo(item.timestamp)}</span>
                    </div>
                </div>
                <div class="feed-action">${isFile ? '&#128229;' : '&#128203;'}</div>
            </div>`;
        }).join('');
    }

    feedDirection(from) {
        if (!from) return '';
        if (from === this.deviceId) return 'Sent';
        if (from.startsWith('phone')) return 'From Phone';
        if (from.startsWith('agent')) return 'From Agent';
        if (from.startsWith('ios-shortcut')) return 'From Shortcut';
        if (from.startsWith('share-target')) return 'Shared';
        if (from.startsWith('pc')) return 'From PC';
        return 'From ' + from;
    }

    renderFilesTab() {
        const container = document.getElementById('file-list');
        if (!container) return;

        const selBtn = document.getElementById('select-toggle');
        if (selBtn) selBtn.textContent = this.selectionMode ? 'Done' : 'Select';
        this.renderSelectBar();

        const q = this.fileQuery.trim().toLowerCase();
        const shown = q
            ? this.files.filter(f => (f.filename || '').toLowerCase().includes(q))
            : this.files;

        if (shown.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">&#128193;</div>
                    <div class="empty-state-text">${q ? 'No files match your search.' : 'No files yet.<br>Upload or transfer files to see them here.'}</div>
                </div>`;
            return;
        }

        container.innerHTML = `<div class="file-grid">${shown.map(f => {
            const thumb = f.thumb_url
                ? `<img src="${f.thumb_url}" alt="${this.escapeHtml(f.filename)}" loading="lazy">`
                : this.getFileIcon(f.filename);
            const ext = (f.filename || '').split('.').pop().toLowerCase();
            const playBadge = ['mp4', 'mov', 'm4v', 'webm', 'avi', 'mkv'].includes(ext)
                ? '<div class="file-play-badge">&#9654;</div>'
                : '';
            const pinBadge = f.pinned ? '<div class="file-pin-badge">&#128204;</div>' : '';
            const checked = this.selected.has(f.file_id);
            const check = this.selectionMode
                ? `<div class="file-check${checked ? ' on' : ''}">${checked ? '&#10003;' : ''}</div>`
                : '';
            const onclick = this.selectionMode
                ? `app.toggleSelect('${f.file_id}')`
                : `app.openPreview('${f.file_id}', '${this.escapeAttr(f.filename)}')`;
            return `
            <div class="file-card${checked ? ' selected' : ''}" onclick="${onclick}">
                <div class="file-thumb">${thumb}${playBadge}${pinBadge}${check}</div>
                <div class="file-name">${this.escapeHtml(f.filename)}</div>
                <div class="file-size">${this.formatSize(f.size)}</div>
            </div>`;
        }).join('')}</div>`;
    }

    renderSelectBar() {
        const bar = document.getElementById('select-bar-container');
        if (!bar) return;
        if (!this.selectionMode) {
            bar.innerHTML = '';
            return;
        }
        const n = this.selected.size;
        bar.innerHTML = `
            <div class="select-bar">
                <span class="select-bar-count">${n} selected</span>
                <button class="btn btn-secondary select-bar-btn" onclick="app.downloadSelectedZip()" ${n ? '' : 'disabled'}>Zip</button>
                <button class="btn btn-secondary select-bar-btn select-bar-danger" onclick="app.deleteSelected()" ${n ? '' : 'disabled'}>Delete</button>
            </div>`;
    }

    toggleSelectionMode() {
        this.selectionMode = !this.selectionMode;
        this.selected.clear();
        this.renderFilesTab();
    }

    toggleSelect(fileId) {
        if (this.selected.has(fileId)) this.selected.delete(fileId);
        else this.selected.add(fileId);
        this.renderFilesTab();
    }

    async downloadSelectedZip() {
        if (!this.selected.size) return;
        if (this.selected.size === 1) {
            const id = [...this.selected][0];
            const f = this.files.find(x => x.file_id === id);
            return this.saveUrl(`/api/files/${id}`, f ? f.filename : 'file');
        }
        this.showToast('Building zip...', 'success');
        await this.saveUrl(`/api/files/zip?ids=${[...this.selected].join(',')}`, 'hummuslink-files.zip');
    }

    async deleteSelected() {
        if (!this.selected.size) return;
        if (!confirm(`Delete ${this.selected.size} file(s)?`)) return;
        for (const id of [...this.selected]) {
            try { await fetch(`/api/files/${id}`, { method: 'DELETE' }); } catch (e) {}
        }
        this.selected.clear();
        this.showToast('Deleted', 'success');
        this.fetchFiles();
    }

    async togglePin() {
        const pf = this._previewFile;
        if (!pf) return;
        const cur = this.files.find(x => x.file_id === pf.fileId);
        const want = !(cur && cur.pinned);
        try {
            const r = await fetch(`/api/files/${pf.fileId}/pin`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pinned: want }),
            });
            if (!r.ok) throw new Error();
            this.showToast(want ? 'Pinned - survives cleanup' : 'Unpinned', 'success');
            await this.fetchFiles();
            this.updatePinButton();
        } catch (e) {
            this.showToast('Pin failed', 'error');
        }
    }

    updatePinButton() {
        const btn = document.getElementById('preview-pin-btn');
        if (!btn) return;
        if (!this._previewFile) return;
        const cur = this.files.find(x => x.file_id === this._previewFile.fileId);
        btn.textContent = cur && cur.pinned ? 'Unpin' : 'Pin';
        // The file already lives on this PC's disk — offer Explorer instead of a re-download.
        const reveal = document.getElementById('preview-reveal-btn');
        if (reveal) reveal.style.display = this.isPhone ? 'none' : '';
    }

    async revealFile() {
        const pf = this._previewFile;
        if (!pf) return;
        try {
            const r = await fetch(`/api/files/${pf.fileId}/reveal`, { method: 'POST' });
            if (!r.ok) throw new Error();
        } catch (e) {
            this.showToast('Could not open Explorer', 'error');
        }
    }

    async clearUnpinned() {
        if (!confirm('Delete ALL unpinned files? Pinned files are kept.')) return;
        try {
            const r = await fetch('/api/files/clear-unpinned', { method: 'POST' });
            if (!r.ok) throw new Error();
            const d = await r.json();
            this.showToast(`Deleted ${d.deleted} file(s)`, 'success');
            this.fetchFiles();
            this.renderSettingsTab();
        } catch (e) {
            this.showToast('Cleanup failed', 'error');
        }
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

    // ==================== File preview ====================
    // iOS Safari ignores the <a download> attribute and has no chrome in a
    // standalone PWA, so tapping a file just navigated the whole app to the raw
    // file with no way to exit or save. Instead we render an in-app viewer with
    // explicit Close + Save controls.

    openPreview(fileId, filename) {
        const ext = (filename || '').split('.').pop().toLowerCase();
        const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'heif', 'bmp'];
        const videoExts = ['mp4', 'mov', 'm4v', 'webm'];
        const audioExts = ['mp3', 'wav', 'aac', 'm4a'];
        const isImage = imageExts.includes(ext);
        const isVideo = videoExts.includes(ext);
        const isAudio = audioExts.includes(ext);
        const isPdf = ext === 'pdf';
        const url = `/api/files/${fileId}`;

        // Opening a second file without closing the first would otherwise leave
        // the previous document's observers wired to detached nodes.
        if (this._pdfObserver) { this._pdfObserver.disconnect(); this._pdfObserver = null; }
        if (this._pdfPageObserver) { this._pdfPageObserver.disconnect(); this._pdfPageObserver = null; }
        this._pdf = null;

        this._previewFile = { fileId, filename };

        const titleEl = document.getElementById('preview-title');
        const bodyEl = document.getElementById('preview-body');
        if (titleEl) titleEl.textContent = filename;

        if (isImage) {
            bodyEl.innerHTML = `<img class="preview-image" src="${url}" alt="${this.escapeHtml(filename)}">`;
        } else if (isVideo) {
            // playsinline keeps iOS from hijacking into the fullscreen player
            bodyEl.innerHTML = `<video class="preview-video" src="${url}" controls playsinline preload="metadata"></video>`;
        } else if (isAudio) {
            bodyEl.innerHTML = `<div class="preview-audio-wrap">
                <div class="preview-fallback-icon">&#127925;</div>
                <audio class="preview-audio" src="${url}" controls preload="metadata"></audio>
            </div>`;
        } else if (isPdf) {
            // Server-rendered page images, NOT an iframe. iOS Safari in a
            // standalone PWA renders an iframed PDF as page 1 only, unscrollable.
            bodyEl.innerHTML = `<div class="pdf-loading">Opening PDF&hellip;</div>`;
            this.openPdf(fileId, filename);
        } else if (this.isTextExt(ext)) {
            bodyEl.innerHTML = `<div class="pdf-loading">Loading&hellip;</div>`;
            this.openText(fileId, filename);
        } else {
            bodyEl.innerHTML = `<div class="preview-fallback">
                <div class="preview-fallback-icon">${this.getFileIcon(filename)}</div>
                <div>No inline preview for this file type.</div>
                <div class="preview-fallback-sub">Tap Save to open or store it.</div>
            </div>`;
        }

        const modal = document.getElementById('preview-modal');
        if (modal) modal.classList.remove('hidden');
        document.body.classList.add('modal-open');
        this.updatePinButton();
    }

    isTextExt(ext) {
        return [
            'txt', 'md', 'markdown', 'json', 'csv', 'tsv', 'log', 'yml', 'yaml',
            'ini', 'cfg', 'conf', 'toml', 'xml', 'html', 'htm', 'css', 'js',
            'ts', 'tsx', 'jsx', 'py', 'sh', 'bat', 'ps1', 'sql', 'rs', 'go',
            'java', 'c', 'h', 'cpp', 'rb', 'php', 'swift', 'kt', 'env', 'srt',
        ].includes(ext);
    }

    // ==================== PDF quick view ====================
    // Pages are rendered server-side (PyMuPDF) and streamed in as plain <img>.
    // That behaves identically on iOS, Android and desktop, unlike an <iframe>,
    // and lets us lazy-load so a 200-page deck doesn't stall the modal.

    async openPdf(fileId, filename) {
        const bodyEl = document.getElementById('preview-body');
        let info;
        try {
            const r = await fetch(`/api/files/${fileId}/pdf`);
            if (!r.ok) throw new Error('no renderer');
            info = await r.json();
        } catch (e) {
            this.renderPdfFallback(fileId, filename);
            return;
        }
        // Bail if the user closed or switched files while we were fetching.
        if (!this._previewFile || this._previewFile.fileId !== fileId) return;

        if (info.encrypted || !info.pages) {
            this.renderPdfFallback(fileId, filename, info.encrypted
                ? 'This PDF is password-protected.'
                : 'This PDF has no readable pages.');
            return;
        }

        // Pick a render width from the viewport, then let the server snap it to
        // its ladder so we don't mint a new cache entry per device width.
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const target = Math.round((bodyEl.clientWidth || 800) * dpr);
        const ladder = (info.widths || [1400]).slice().sort((a, b) => a - b);
        // Round UP to the next rung: rounding to the *nearest* renders below the
        // device's pixel density and small PDF type goes visibly soft.
        const width = ladder.find(w => w >= target) || ladder[ladder.length - 1];

        this._pdf = { fileId, pages: info.pages, width, current: 1 };

        const pages = info.sizes.map((s, i) => {
            // Reserve the exact aspect ratio up front so scrolling doesn't jump
            // as images arrive.
            const ratio = s.h && s.w ? (s.h / s.w) * 100 : 129.4;
            return `<div class="pdf-page" data-page="${i}" style="padding-bottom:${ratio.toFixed(3)}%">
                <div class="pdf-page-num">${i + 1}</div>
            </div>`;
        }).join('');

        bodyEl.innerHTML = `
            <div class="pdf-viewer" id="pdf-viewer">
                <div class="pdf-pages">${pages}</div>
                ${info.truncated ? `<div class="pdf-note">Showing the first ${info.pages} of ${info.total_pages} pages. Save the file to read the rest.</div>` : ''}
            </div>
            <div class="pdf-hud" id="pdf-hud">
                <button class="pdf-hud-btn" onclick="app.pdfStep(-1)" aria-label="Previous page">&#8593;</button>
                <span class="pdf-hud-count" id="pdf-hud-count">1 / ${info.pages}</span>
                <button class="pdf-hud-btn" onclick="app.pdfStep(1)" aria-label="Next page">&#8595;</button>
            </div>`;

        this.mountPdfLazyLoad();
    }

    mountPdfLazyLoad() {
        const viewer = document.getElementById('pdf-viewer');
        if (!viewer || !this._pdf) return;
        const { fileId, width, pages } = this._pdf;
        const nodes = viewer.querySelectorAll('.pdf-page');

        const load = (el) => {
            if (el.dataset.loaded) return;
            el.dataset.loaded = '1';
            const n = Number(el.dataset.page);
            const img = new Image();
            img.className = 'pdf-page-img';
            img.alt = `Page ${n + 1}`;
            img.decoding = 'async';
            img.onload = () => el.classList.add('ready');
            img.onerror = () => {
                el.dataset.loaded = '';
                el.classList.add('failed');
            };
            img.src = `/api/files/${fileId}/pdf/${n}?w=${width}`;
            el.appendChild(img);
        };

        // rootMargin pre-renders a screen ahead and behind so scrolling feels
        // continuous rather than page-by-page pop-in.
        this._pdfObserver = new IntersectionObserver((entries) => {
            for (const e of entries) if (e.isIntersecting) load(e.target);
        }, { root: viewer, rootMargin: '150% 0px' });
        nodes.forEach(n => this._pdfObserver.observe(n));

        // Separate observer purely for the "3 / 11" readout. Two pages are often
        // on screen at once, so track every ratio and report the most-visible one
        // rather than whichever callback happened to fire last.
        const countEl = document.getElementById('pdf-hud-count');
        const ratios = new Map();
        this._pdfPageObserver = new IntersectionObserver((entries) => {
            for (const e of entries) {
                ratios.set(Number(e.target.dataset.page), e.intersectionRatio);
            }
            let bestPage = 0;
            let bestRatio = -1;
            for (const [p, r] of ratios) {
                if (r > bestRatio + 0.001) { bestRatio = r; bestPage = p; }
            }
            const n = bestPage + 1;
            this._pdf.current = n;
            if (countEl) countEl.textContent = `${n} / ${pages}`;
        }, { root: viewer, threshold: [0, 0.25, 0.5, 0.75, 1] });
        nodes.forEach(n => this._pdfPageObserver.observe(n));

        // Page 1 is always worth having immediately — don't wait for the observer.
        if (nodes[0]) load(nodes[0]);
    }

    pdfStep(delta) {
        if (!this._pdf) return;
        const next = Math.min(Math.max(this._pdf.current + delta, 1), this._pdf.pages);
        const viewer = document.getElementById('pdf-viewer');
        const el = viewer && viewer.querySelector(`.pdf-page[data-page="${next - 1}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    renderPdfFallback(fileId, filename, reason) {
        const bodyEl = document.getElementById('preview-body');
        if (!bodyEl) return;
        // No server-side renderer (or an unreadable file): hand it to the browser
        // with an inline disposition, which at least works on desktop.
        bodyEl.innerHTML = `
            <div class="preview-fallback">
                <div class="preview-fallback-icon">&#128196;</div>
                <div>${this.escapeHtml(reason || "Can't render this PDF here.")}</div>
                <div class="preview-fallback-sub">Open it in the browser, or Save to send it elsewhere.</div>
                <a class="pdf-open-link" href="/api/files/${fileId}?inline=1" target="_blank" rel="noopener">Open PDF</a>
            </div>`;
    }

    // ==================== Text quick view ====================

    async openText(fileId, filename) {
        const bodyEl = document.getElementById('preview-body');
        try {
            const r = await fetch(`/api/files/${fileId}/text`);
            if (!r.ok) throw new Error('not text');
            const data = await r.json();
            if (!this._previewFile || this._previewFile.fileId !== fileId) return;
            bodyEl.innerHTML = `
                <div class="text-viewer">
                    <pre class="text-viewer-pre">${this.escapeHtml(data.text)}</pre>
                    ${data.truncated ? '<div class="pdf-note">Preview truncated. Save the file to read all of it.</div>' : ''}
                </div>`;
        } catch (e) {
            if (!this._previewFile || this._previewFile.fileId !== fileId) return;
            bodyEl.innerHTML = `<div class="preview-fallback">
                <div class="preview-fallback-icon">${this.getFileIcon(filename)}</div>
                <div>No inline preview for this file type.</div>
                <div class="preview-fallback-sub">Tap Save to open or store it.</div>
            </div>`;
        }
    }

    closePreview() {
        const modal = document.getElementById('preview-modal');
        if (modal) modal.classList.add('hidden');
        // Disconnect before clearing the DOM so the observers don't leak across opens.
        if (this._pdfObserver) { this._pdfObserver.disconnect(); this._pdfObserver = null; }
        if (this._pdfPageObserver) { this._pdfPageObserver.disconnect(); this._pdfPageObserver = null; }
        this._pdf = null;
        const bodyEl = document.getElementById('preview-body');
        if (bodyEl) bodyEl.innerHTML = ''; // stop the images/video from holding the file
        document.body.classList.remove('modal-open');
        this._previewFile = null;
    }

    async saveFile(fileId, filename) {
        fileId = fileId || (this._previewFile && this._previewFile.fileId);
        filename = filename || (this._previewFile && this._previewFile.filename);
        if (!fileId) return;
        await this.saveUrl(`/api/files/${fileId}`, filename);
    }

    async saveUrl(url, filename) {
        try {
            const resp = await fetch(url);
            if (!resp.ok) throw new Error('fetch failed');
            const blob = await resp.blob();
            const file = new File([blob], filename, { type: blob.type || 'application/octet-stream' });

            // iOS: the share sheet is the real "Save to Files" / "Save to Books" path
            if (navigator.canShare && navigator.canShare({ files: [file] })) {
                try {
                    await navigator.share({ files: [file], title: filename });
                } catch (e) {
                    if (e && e.name === 'AbortError') return; // user dismissed the sheet
                    throw e;
                }
                return;
            }

            // Desktop / browsers without file-share: blob download
            const objUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = objUrl;
            a.download = filename;
            a.click();
            setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
        } catch (e) {
            this.showToast('Save failed', 'error');
        }
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

    copyFeedItem(index) {
        const item = this.feedItems[index];
        if (!item || !item.content) return;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(item.content).then(() => {
                this.showToast('Copied to clipboard', 'success');
            }).catch(() => {
                this.fallbackCopy(item.content);
            });
        } else {
            this.fallbackCopy(item.content);
        }
    }

    fallbackCopy(text) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try {
            document.execCommand('copy');
            this.showToast('Copied to clipboard', 'success');
        } catch (e) {
            this.showToast('Copy failed', 'error');
        }
        document.body.removeChild(ta);
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
        if (ext === 'pdf') return '&#128220;';
        if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return '&#128230;';
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

        // File search
        document.getElementById('file-search')?.addEventListener('input', (e) => {
            this.fileQuery = e.target.value || '';
            this.renderFilesTab();
        });

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

        // Paste a file/screenshot anywhere on the page to upload it (PC: Ctrl+V)
        document.addEventListener('paste', (e) => {
            const items = (e.clipboardData && e.clipboardData.items) || [];
            let grabbed = false;
            for (const it of items) {
                if (it.kind !== 'file') continue;
                const f = it.getAsFile();
                if (!f) continue;
                const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
                const name = (f.name && f.name !== 'image.png') ? f.name : `pasted-${ts}.png`;
                this.uploadFile(new File([f], name, { type: f.type }));
                grabbed = true;
            }
            if (grabbed) {
                e.preventDefault();
                if (this.currentTab !== 'files') this.switchTab('files');
            }
        });

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
                this.fetchFeed();
                // Pull a newer build if one was deployed while backgrounded
                this.checkForUpdate();
            }
        });

        // Device name input
        document.getElementById('device-name-input')?.addEventListener('change', () => this.updateDeviceName());

        // Close the file preview on Escape (hardware keyboards / iPad)
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') this.closePreview();
        });
    }

    registerServiceWorker() {
        // SW disabled — was caching stale assets and breaking page loads.
        // Aggressively unregister any leftover SW + clear caches on every load.
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.getRegistrations().then((regs) => {
                regs.forEach((reg) => reg.unregister().catch(() => {}));
            }).catch(() => {});
        }
        if (typeof caches !== 'undefined') {
            caches.keys().then((names) => {
                names.forEach((n) => caches.delete(n).catch(() => {}));
            }).catch(() => {});
        }
    }
}

// Initialize the app
let app;
document.addEventListener('DOMContentLoaded', () => {
    app = new HummusLink();
});
