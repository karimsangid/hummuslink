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
        this.sharedItems = JSON.parse(localStorage.getItem('hummuslink_shared') || '[]');
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
                this.addFeedItem('clipboard', data.content, `From ${this.targetLabel}`);
                break;

            case 'text_share':
                this.addFeedItem('text', data.content, `From ${this.targetLabel}`);
                this.showToast(`Text received from ${this.targetLabel}`);
                this.renderShareTab();
                break;

            case 'file_ready':
                this.addFeedItem('file', data.filename, `From ${this.targetLabel}`, data.url);
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
        this.showToast(`Clipboard sent to ${this.targetLabel}`, 'success');
    }

    shareText(text) {
        if (!text || !text.trim()) return;
        this.send({
            type: 'text_share',
            content: text.trim(),
            from: this.deviceId,
        });
        this.addFeedItem('text', text.trim(), `To ${this.targetLabel}`);
        this.showToast(`Sent to ${this.targetLabel}`, 'success');
    }

    async uploadFile(file) {
        if (!file) return;
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
            this.addFeedItem('file', meta.filename, `To ${this.targetLabel}`, meta.url);
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

        container.innerHTML = this.sharedItems.slice(0, 50).map((item, i) => {
            const isFile = item.type === 'file' && item.url;
            const action = isFile
                ? `onclick="window.open('${item.url}', '_blank')"`
                : `onclick="app.copyFeedItem(${i})"`;
            const hint = isFile ? 'Tap to download' : 'Tap to copy';
            return `
            <div class="feed-item clickable" ${action} title="${hint}">
                <div class="feed-icon">${this.getFeedIcon(item.type)}</div>
                <div class="feed-body">
                    <div class="feed-text">${this.escapeHtml(item.preview)}</div>
                    <div class="feed-meta">
                        <span class="feed-direction">${item.direction}</span>
                        <span>${this.timeAgo(item.timestamp)}</span>
                    </div>
                </div>
                <div class="feed-action">${isFile ? '&#128229;' : '&#128203;'}</div>
            </div>`;
        }).join('');
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

        container.innerHTML = `<div class="file-grid">${this.files.map(f => {
            const thumb = f.thumb_url
                ? `<img src="${f.thumb_url}" alt="${this.escapeHtml(f.filename)}" loading="lazy">`
                : this.getFileIcon(f.filename);
            return `
            <div class="file-card" onclick="app.openPreview('${f.file_id}', '${this.escapeAttr(f.filename)}')">
                <div class="file-thumb">${thumb}</div>
                <div class="file-name">${this.escapeHtml(f.filename)}</div>
                <div class="file-size">${this.formatSize(f.size)}</div>
            </div>`;
        }).join('')}</div>`;
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
        const isImage = imageExts.includes(ext);
        const isPdf = ext === 'pdf';
        const url = `/api/files/${fileId}`;

        this._previewFile = { fileId, filename };

        const titleEl = document.getElementById('preview-title');
        const bodyEl = document.getElementById('preview-body');
        if (titleEl) titleEl.textContent = filename;

        if (isImage) {
            bodyEl.innerHTML = `<img class="preview-image" src="${url}" alt="${this.escapeHtml(filename)}">`;
        } else if (isPdf) {
            // iframe keeps the PDF inline + scrollable; Close/Save are the escape hatches
            bodyEl.innerHTML = `<iframe class="preview-frame" src="${url}" title="${this.escapeHtml(filename)}"></iframe>`;
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
    }

    closePreview() {
        const modal = document.getElementById('preview-modal');
        if (modal) modal.classList.add('hidden');
        const bodyEl = document.getElementById('preview-body');
        if (bodyEl) bodyEl.innerHTML = ''; // stop the iframe/img from holding the file
        document.body.classList.remove('modal-open');
        this._previewFile = null;
    }

    async saveFile(fileId, filename) {
        fileId = fileId || (this._previewFile && this._previewFile.fileId);
        filename = filename || (this._previewFile && this._previewFile.filename);
        if (!fileId) return;
        try {
            const resp = await fetch(`/api/files/${fileId}`);
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

    copyFeedItem(index) {
        const item = this.sharedItems[index];
        if (!item || !item.preview) return;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(item.preview).then(() => {
                this.showToast('Copied to clipboard', 'success');
            }).catch(() => {
                this.fallbackCopy(item.preview);
            });
        } else {
            this.fallbackCopy(item.preview);
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
