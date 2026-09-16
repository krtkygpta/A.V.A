/**
 * A.V.A — Google Stitch Monochromatic Web Client
 * Single Source of Truth Server Sync, Real-time WebSocket Tool Execution,
 * Accent Color Customization, and Full Responsive Layout.
 */

class AvaStitchClient {
  constructor() {
    this.ws = null;
    this.heartbeatTimer = null;
    this.reconnectAttempts = 0;
    this.reconnectTimer = null;
    this.currentConvId = null;
    this.conversations = [];
    this.activeTasks = [];
    this.activeToolCards = new Map(); // rpcId/tool -> DOM element
    this.currentGroupedContainer = null;
    this.currentGroupedBody = null;
    this.currentGroupedTitle = null;
    this.currentGroupedBadge = null;
    this.currentGroupedDot = null;
    this.currentTurnToolSteps = [];
    this.turnStartTime = null;

    // Settings & Identity
    this.protocol = localStorage.getItem('ava_protocol') || (window.location.protocol === 'https:' ? 'wss://' : 'ws://');
    this.host = localStorage.getItem('ava_host') || window.location.hostname || '127.0.0.1';
    this.port = localStorage.getItem('ava_port') || window.location.port || '8765';
    this.clientId = localStorage.getItem('ava_client_id') || 'web_workspace';
    this.deviceType = 'web';
    this.accentColor = localStorage.getItem('ava_accent_color') || '#FFFFFF';

    // DOM Bindings
    this.bindDOMElements();

    // Init Theme & Accent
    this.applyAccentColor(this.accentColor, false);

    // Init UI Listeners
    this.initEventListeners();

    // Connect & Initial Sync
    this.connectWebSocket();
    this.fetchConversations();
    this.pollBackgroundTasks();
    setInterval(() => this.pollBackgroundTasks(), 10000);
  }

  /* ── DOM Element Bindings ─────────────────────────────────────────────────── */
  bindDOMElements() {
    this.topbar = document.getElementById('stitch-topbar');
    this.headerConnBadge = document.getElementById('header-conn-badge');
    this.systemStatusDot = document.getElementById('system-status-dot');
    this.headerClientId = document.getElementById('header-client-id');
    this.btnOpenSettings = document.getElementById('btn-open-settings');
    this.btnMobileSidebar = document.getElementById('btn-mobile-sidebar');
    this.btnDesktopSidebarToggle = document.getElementById('btn-desktop-sidebar-toggle');
    this.sidebar = document.getElementById('stitch-sidebar');
    this.drawerBackdrop = document.getElementById('drawer-backdrop');

    // Sidebar
    this.btnNewChat = document.getElementById('btn-new-chat');
    this.searchThreadsInput = document.getElementById('search-threads-input');
    this.tasksCountBadge = document.getElementById('tasks-count-badge');
    this.tasksListContainer = document.getElementById('tasks-list-container');
    this.tasksEmptyState = document.getElementById('tasks-empty-state');
    this.conversationsFeed = document.getElementById('conversations-feed');
    this.groupToday = document.getElementById('group-today');
    this.groupYesterday = document.getElementById('group-yesterday');
    this.groupOlder = document.getElementById('group-older');
    this.listToday = document.getElementById('list-today');
    this.listYesterday = document.getElementById('list-yesterday');
    this.listOlder = document.getElementById('list-older');
    this.convEmptyHint = document.getElementById('conv-empty-hint');
    this.sidebarHostLink = document.getElementById('sidebar-host-link');
    this.footerHostLabel = document.getElementById('footer-host-label');

    // Chat Viewport
    this.sessionHashLabel = document.getElementById('session-hash-label');
    this.sessionModelPill = document.getElementById('session-model-pill');
    this.messageFeedScroll = document.getElementById('message-feed-scroll');
    this.messageFeed = document.getElementById('message-feed');
    this.emptyStateHero = document.getElementById('empty-state-hero');
    this.thinkingStrip = document.getElementById('thinking-strip');
    this.thinkingLabel = document.getElementById('thinking-label');
    this.suggestionChipsRow = document.getElementById('suggestion-chips-row');

    // Composer
    this.composerContainer = document.getElementById('composer-container');
    this.composerInput = document.getElementById('composer-input');
    this.btnSend = document.getElementById('btn-send');
    this.btnAttach = document.getElementById('btn-attach');
    this.fileInput = document.getElementById('file-input');
    this.btnMic = document.getElementById('btn-mic');
    this.voiceListeningStrip = document.getElementById('voice-listening-strip');
    this.btnVoiceCancel = document.getElementById('btn-voice-cancel');

    // Settings Modal
    this.settingsModalOverlay = document.getElementById('settings-modal-overlay');
    this.btnCloseSettings = document.getElementById('btn-close-settings');
    this.btnDiscardSettings = document.getElementById('btn-discard-settings');
    this.btnSaveSettings = document.getElementById('btn-save-settings');
    this.settingsProtocol = document.getElementById('settings-protocol');
    this.settingsHost = document.getElementById('settings-host');
    this.settingsPort = document.getElementById('settings-port');
    this.settingsClientId = document.getElementById('settings-client-id');
    this.btnPortDec = document.getElementById('btn-port-dec');
    this.btnPortInc = document.getElementById('btn-port-inc');
    this.diagLatencyVal = document.getElementById('diag-latency-val');
    this.diagStatusVal = document.getElementById('diag-status-val');
    this.btnTestPing = document.getElementById('btn-test-ping');
    this.accentSwatchesGrid = document.getElementById('accent-swatches-grid');

    if (this.headerClientId) this.headerClientId.textContent = this.clientId;
    if (this.footerHostLabel) this.footerHostLabel.textContent = `HOST: ${this.host}:${this.port}`;
  }

  /* ── Accent Color System ─────────────────────────────────────────────────── */
  applyAccentColor(hex, save = true) {
    this.accentColor = hex;
    if (save) localStorage.setItem('ava_accent_color', hex);

    const isLight = this.getLuminance(hex) > 0.4;
    const onColor = isLight ? '#1A1C1C' : '#FFFFFF';

    document.documentElement.style.setProperty('--accent-color', hex);
    document.documentElement.style.setProperty('--on-accent-color', onColor);

    this.renderAccentSwatches();
  }

  getLuminance(hex) {
    const clean = hex.replace('#', '');
    const r = parseInt(clean.substring(0, 2), 16) / 255;
    const g = parseInt(clean.substring(2, 4), 16) / 255;
    const b = parseInt(clean.substring(4, 6), 16) / 255;
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }

  renderAccentSwatches() {
    if (!this.accentSwatchesGrid) return;
    const presets = [
      { name: 'Pure White', hex: '#FFFFFF' },
      { name: 'Emerald', hex: '#10B981' },
      { name: 'Violet', hex: '#8B5CF6' },
      { name: 'Cyan', hex: '#06B6D4' },
      { name: 'Amber', hex: '#F59E0B' },
      { name: 'Crimson', hex: '#EF4444' },
    ];

    this.accentSwatchesGrid.innerHTML = presets.map(p => {
      const isSelected = p.hex.toLowerCase() === this.accentColor.toLowerCase();
      const onColor = this.getLuminance(p.hex) > 0.4 ? '#1A1C1C' : '#FFFFFF';
      return `
        <div class="accent-swatch ${isSelected ? 'active' : ''}" 
             style="background-color: ${p.hex}; color: ${onColor};" 
             data-hex="${p.hex}" 
             title="${p.name}">
          ${isSelected ? '<span class="material-symbols-outlined accent-swatch-check">check</span>' : ''}
        </div>
      `;
    }).join('');

    this.accentSwatchesGrid.querySelectorAll('.accent-swatch').forEach(el => {
      el.addEventListener('click', () => {
        const hex = el.dataset.hex;
        if (hex) this.applyAccentColor(hex, true);
      });
    });
  }

  /* ── WebSocket Connection & Lifecycle ────────────────────────────────────── */
  getWebSocketUrl() {
    const proto = this.protocol || (window.location.protocol === 'https:' ? 'wss://' : 'ws://');
    return `${proto}${this.host}:${this.port}/ws/device`;
  }

  connectWebSocket() {
    if (this.ws) {
      try { this.ws.close(); } catch (_) {}
      this.ws = null;
    }

    const wsUrl = this.getWebSocketUrl();
    this.updateConnectionState(false, 'CONNECTING...');

    try {
      this.ws = new WebSocket(wsUrl);
    } catch (err) {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.updateConnectionState(true, 'ONLINE');
      this.startHeartbeat();

      // Handshake: Device Registration
      const regMsg = {
        type: 'register',
        device_id: this.clientId,
        name: this.clientId,
        device_type: this.deviceType,
        capabilities: ['voice', 'location', 'screenshot', 'system_monitor'],
        tool_catalog: {},
        metadata: { client: 'ava_web_stitch', v: '2.4.0' }
      };
      this.sendWs(regMsg);

      // Trigger sync on connect (Server is primary!)
      this.fetchConversations();
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        this.handleWsMessage(msg);
      } catch (err) {
        console.warn('[AVA-WS] Failed to parse message:', err);
      }
    };

    this.ws.onclose = () => {
      this.stopHeartbeat();
      this.updateConnectionState(false, 'OFFLINE');
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      try { this.ws.close(); } catch (_) {}
    };
  }

  sendWs(data) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
      return true;
    }
    return false;
  }

  startHeartbeat() {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      this.sendWs({ type: 'heartbeat', timestamp: Date.now() / 1000 });
    }, 25000);
  }

  stopHeartbeat() {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  scheduleReconnect() {
    if (this.reconnectTimer) return;
    const delay = Math.min(1000 * Math.pow(1.5, this.reconnectAttempts), 15000);
    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connectWebSocket();
    }, delay);
  }

  updateConnectionState(isOnline, text) {
    if (this.systemStatusDot) {
      this.systemStatusDot.className = `status-pulse-dot ${isOnline ? '' : 'offline'}`;
    }
    if (this.headerConnBadge) {
      this.headerConnBadge.textContent = text || (isOnline ? 'CONNECTED // LOCAL' : 'OFFLINE');
    }
    if (this.diagStatusVal) {
      this.diagStatusVal.textContent = isOnline ? 'ONLINE' : 'OFFLINE';
      this.diagStatusVal.className = `diag-val ${isOnline ? 'text-green' : 'text-red'}`;
    }
  }

  /* ── WebSocket Message Dispatcher ────────────────────────────────────────── */
  handleWsMessage(msg) {
    const type = msg.type;

    if (type === 'registered') {
      this.updateConnectionState(true, 'CONNECTED // LOCAL');
      // If server returns conversations on register, sync immediately
      if (Array.isArray(msg.conversations)) {
        this.syncConversationsWithServer(msg.conversations);
      } else {
        this.fetchConversations();
      }
    }
    else if (type === 'heartbeat_ack') {
      // alive
    }
    else if (type === 'thinking') {
      const isThinking = msg.status === 'start';
      this.showThinking(isThinking, msg.label || 'AVA is reasoning...');
    }
    else if (type === 'agent_step') {
      this.handleAgentStep(msg.step || msg);
    }
    else if (type === 'assistant_response') {
      this.showThinking(false);
      this.finishActiveTurnGroup();
      const content = msg.content || '';
      const convId = msg.conversation_id || this.currentConvId;
      if (convId) this.setActiveConversationId(convId);
      this.renderAssistantMessage(content);
      this.fetchConversations();
    }
    else if (type === 'task_started' || type === 'task_completed' || type === 'task_update') {
      this.pollBackgroundTasks();
    }
    else if (type === 'error') {
      this.showThinking(false);
      this.finishActiveTurnGroup();
      const errMsg = msg.message || 'An error occurred while processing your request.';
      this.renderAssistantMessage(`⚠️ **Error**: ${errMsg}`);
    }
  }

  /* ── Server-Primary Conversation Sync ────────────────────────────────────── */
  async fetchConversations() {
    try {
      const proto = (this.protocol === 'wss://' ? 'https://' : 'http://');
      const url = `${proto}${this.host}:${this.port}/conversation/list?limit=50`;
      const res = await fetch(url, { method: 'GET' });
      if (res.ok) {
        const data = await res.json();
        const convList = data.conversations || [];
        this.syncConversationsWithServer(convList);
      }
    } catch (err) {
      console.warn('[AVA] fetchConversations error:', err);
    }
  }

  syncConversationsWithServer(serverConvs) {
    this.conversations = serverConvs || [];

    // SERVER AS PRIMARY ENFORCEMENT:
    // If the active conversation does not exist on the server, clear it!
    if (this.currentConvId) {
      const existsOnServer = this.conversations.some(c => c.id === this.currentConvId);
      if (!existsOnServer) {
        this.currentConvId = null;
        this.clearChatFeed();
        this.setEmptyState(true);
        this.setSessionLabel('SESSION #NEW // IDLE');
      }
    }

    this.renderConversations();
  }

  renderConversations(filterQuery = '') {
    const query = (filterQuery || '').trim().toLowerCase();
    const filtered = query 
      ? this.conversations.filter(c => (c.name || '').toLowerCase().includes(query) || (c.summary || '').toLowerCase().includes(query))
      : this.conversations;

    const now = Date.now();
    const startOfToday = new Date().setHours(0, 0, 0, 0);
    const startOfYesterday = startOfToday - 86400000;

    const todayItems = [];
    const yesterdayItems = [];
    const olderItems = [];

    filtered.forEach(conv => {
      const ts = conv.timestamp || now;
      if (ts >= startOfToday) {
        todayItems.push(conv);
      } else if (ts >= startOfYesterday) {
        yesterdayItems.push(conv);
      } else {
        olderItems.push(conv);
      }
    });

    this.renderGroup(this.listToday, this.groupToday, todayItems);
    this.renderGroup(this.listYesterday, this.groupYesterday, yesterdayItems);
    this.renderGroup(this.listOlder, this.groupOlder, olderItems);

    if (this.convEmptyHint) {
      const hasAny = filtered.length > 0;
      this.convEmptyHint.classList.toggle('hidden', hasAny);
    }
  }

  renderGroup(containerEl, groupEl, items) {
    if (!containerEl || !groupEl) return;
    if (items.length === 0) {
      groupEl.classList.add('hidden');
      containerEl.innerHTML = '';
      return;
    }
    groupEl.classList.remove('hidden');
    containerEl.innerHTML = items.map(c => {
      const isActive = c.id === this.currentConvId;
      const title = c.name || c.id;
      const summary = c.summary || c.last_message || 'Active thread';
      const timeStr = this.formatRelativeTime(c.timestamp || c.created_at);
      return `
        <div class="conv-item ${isActive ? 'active' : ''}" data-conv-id="${this.escapeHtml(c.id)}">
          <div class="conv-item-left">
            <span class="conv-title">${this.escapeHtml(title)}</span>
            <span class="conv-meta">${this.escapeHtml(timeStr)} // ${this.escapeHtml(summary)}</span>
          </div>
          <div class="conv-actions">
            <button class="btn-conv-delete" data-del-id="${this.escapeHtml(c.id)}" title="Delete Conversation">
              <span class="material-symbols-outlined" style="font-size:16px;">delete</span>
            </button>
          </div>
        </div>
      `;
    }).join('');

    containerEl.querySelectorAll('.conv-item').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('.btn-conv-delete')) return;
        const cid = el.dataset.convId;
        if (cid) this.loadConversation(cid);
      });
    });

    containerEl.querySelectorAll('.btn-conv-delete').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const cid = btn.dataset.delId;
        if (cid && confirm('Delete conversation from server?')) {
          this.deleteConversation(cid);
        }
      });
    });
  }

  formatRelativeTime(ts) {
    if (!ts) return 'Active';
    const time = typeof ts === 'number' ? ts : new Date(ts).getTime();
    if (!time || isNaN(time)) return 'Active';
    const date = new Date(time);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  async loadConversation(convId) {
    this.setActiveConversationId(convId);
    this.clearChatFeed();
    this.setEmptyState(false);
    this.showThinking(true, 'Loading conversation...');

    try {
      const proto = (this.protocol === 'wss://' ? 'https://' : 'http://');
      const url = `${proto}${this.host}:${this.port}/conversation/history?id=${encodeURIComponent(convId)}`;
      const res = await fetch(url);
      this.showThinking(false);

      if (res.ok) {
        const data = await res.json();
        const msgs = data.messages || [];
        if (msgs.length === 0) {
          this.setEmptyState(true);
        } else {
          msgs.forEach(m => {
            const role = (m.role || '').toLowerCase();
            const content = m.content || '';
            const toolSteps = m.tool_steps || m.toolSteps || [];
            if (role === 'user') {
              this.renderUserMessage(content);
            } else if (role === 'assistant') {
              if (toolSteps.length > 0) {
                this.renderGroupedToolsHistorical(toolSteps, m.duration || m.durationSeconds);
              }
              this.renderAssistantMessage(content);
            }
          });
        }
      } else if (res.status === 404) {
        // Doesn't exist on server; discard locally
        this.conversations = this.conversations.filter(c => c.id !== convId);
        this.currentConvId = null;
        this.clearChatFeed();
        this.setEmptyState(true);
        this.renderConversations();
      }
    } catch (err) {
      this.showThinking(false);
      console.warn('[AVA] loadConversation error:', err);
    }

    this.renderConversations();
    this.closeMobileDrawer();
  }

  async deleteConversation(convId) {
    try {
      const proto = (this.protocol === 'wss://' ? 'https://' : 'http://');
      const url = `${proto}${this.host}:${this.port}/conversation/delete`;
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: convId })
      });
      if (res.ok) {
        if (this.currentConvId === convId) {
          this.currentConvId = null;
          this.clearChatFeed();
          this.setEmptyState(true);
          this.setSessionLabel('SESSION #NEW // IDLE');
        }
        this.fetchConversations();
      }
    } catch (err) {
      console.warn('[AVA] deleteConversation error:', err);
    }
  }

  startNewConversation() {
    this.currentConvId = null;
    this.clearChatFeed();
    this.setEmptyState(true);
    this.setSessionLabel('SESSION #NEW // READY');
    this.renderConversations();
    this.closeMobileDrawer();
    if (this.composerInput) this.composerInput.focus();
  }

  setActiveConversationId(id) {
    this.currentConvId = id;
    const shortId = id ? (id.length > 8 ? id.substring(0, 8).toUpperCase() : id.toUpperCase()) : 'NEW';
    this.setSessionLabel(`SESSION #${shortId} // ACTIVE STREAM`);
  }

  setSessionLabel(text) {
    if (this.sessionHashLabel) this.sessionHashLabel.textContent = text;
  }

  /* ── Background Tasks Polling ────────────────────────────────────────────── */
  async pollBackgroundTasks() {
    try {
      const proto = (this.protocol === 'wss://' ? 'https://' : 'http://');
      const url = `${proto}${this.host}:${this.port}/tasks/active`;
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        this.activeTasks = data.tasks || [];
        this.renderActiveTasks();
      }
    } catch (_) {
      // fallback
    }
  }

  renderActiveTasks() {
    if (!this.tasksListContainer || !this.tasksCountBadge) return;
    this.tasksCountBadge.textContent = `[${this.activeTasks.length}]`;

    if (this.activeTasks.length === 0) {
      this.tasksListContainer.innerHTML = `
        <div class="task-empty-state">
          <span class="task-empty-title">No active background tasks</span>
          <span class="task-empty-sub">Queue idle. Ready for instruction dispatch.</span>
        </div>
      `;
      return;
    }

    this.tasksListContainer.innerHTML = this.activeTasks.map(t => `
      <div class="task-item-card">
        <div class="task-item-top">
          <span>${this.escapeHtml(t.name || t.tool || 'Task')}</span>
          <span style="color: var(--color-green); font-family:var(--font-mono); font-size:10px;">${this.escapeHtml(t.status || 'RUNNING')}</span>
        </div>
        <div class="task-progress-track">
          <div class="task-progress-fill" style="width: ${t.progress || 65}%;"></div>
        </div>
      </div>
    `).join('');
  }

  /* ── Chat Feed Rendering ─────────────────────────────────────────────────── */
  clearChatFeed() {
    if (!this.messageFeed) return;
    const hero = this.emptyStateHero;
    this.messageFeed.innerHTML = '';
    if (hero) this.messageFeed.appendChild(hero);
    this.activeToolCards.clear();
    this.currentGroupedContainer = null;
    this.currentGroupedBody = null;
    this.currentGroupedTitle = null;
    this.currentGroupedBadge = null;
    this.currentGroupedDot = null;
    this.currentTurnToolSteps = [];
    this.turnStartTime = null;
  }

  setEmptyState(isEmpty) {
    if (this.emptyStateHero) {
      this.emptyStateHero.classList.toggle('hidden', !isEmpty);
    }
    if (this.suggestionChipsRow) {
      this.suggestionChipsRow.classList.toggle('hidden', !isEmpty);
    }
  }

  scrollToBottom() {
    if (this.messageFeedScroll) {
      this.messageFeedScroll.scrollTop = this.messageFeedScroll.scrollHeight;
    }
  }

  renderUserMessage(text) {
    this.setEmptyState(false);
    const row = document.createElement('div');
    row.className = 'msg-row msg-row-user';
    row.innerHTML = `<div class="user-card-bubble">${this.escapeHtml(text)}</div>`;
    this.messageFeed.appendChild(row);
    this.scrollToBottom();
  }

  renderAssistantMessage(markdownText) {
    this.setEmptyState(false);
    const row = document.createElement('div');
    row.className = 'msg-row msg-row-assistant';

    const nowStr = new Date().toISOString().substring(11, 19) + ' UTC';
    const renderedHtml = window.marked ? marked.parse(markdownText) : markdownText;

    row.innerHTML = `
      <div class="assistant-meta-strip">
        <span>AVA // Active Stream</span>
        <span>•</span>
        <span>${nowStr}</span>
      </div>
      <div class="assistant-card-body">${renderedHtml}</div>
    `;

    row.querySelectorAll('pre code').forEach((block) => {
      if (window.hljs) hljs.highlightElement(block);
    });

    this.messageFeed.appendChild(row);
    this.scrollToBottom();
  }

  showThinking(show, labelText) {
    if (!this.thinkingStrip) return;
    this.thinkingStrip.classList.toggle('hidden', !show);
    if (this.thinkingLabel && labelText) {
      this.thinkingLabel.textContent = labelText;
    }
    if (show) this.scrollToBottom();
  }

  handleAgentStep(step) {
    if (!step) return;
    if (step.status === 'thinking' || !step.tool) {
      if (step.label) {
        this.showThinking(true, step.label);
      }
      return;
    }

    const toolName = step.tool;
    const status = step.status || 'running';
    const isCompleted = status === 'completed';
    const badgeClass = isCompleted ? 'success' : 'running';
    const badgeText = isCompleted ? 'EXIT 0' : 'RUNNING';

    // Ensure grouped division exists for current turn
    if (!this.currentGroupedContainer) {
      if (!this.turnStartTime) {
        this.turnStartTime = performance.now();
      }
      const container = document.createElement('div');
      container.className = 'grouped-tools-container';
      container.innerHTML = `
        <div class="grouped-tools-header">
          <div class="grouped-tools-left">
            <span class="grouped-status-dot running"></span>
            <span class="grouped-tools-title">AVA working...</span>
            <span class="grouped-tools-badge">1 tool</span>
          </div>
          <span class="material-symbols-outlined grouped-chevron">expand_more</span>
        </div>
        <div class="grouped-tools-body"></div>
      `;

      const header = container.querySelector('.grouped-tools-header');
      const body = container.querySelector('.grouped-tools-body');
      header.addEventListener('click', () => {
        const isCollapsed = body.classList.toggle('collapsed');
        container.classList.toggle('expanded', !isCollapsed);
      });

      this.messageFeed.appendChild(container);
      this.currentGroupedContainer = container;
      this.currentGroupedBody = body;
      this.currentGroupedTitle = container.querySelector('.grouped-tools-title');
      this.currentGroupedBadge = container.querySelector('.grouped-tools-badge');
      this.currentGroupedDot = container.querySelector('.grouped-status-dot');
    }

    // Record step — keyed by unique call index so repeated tools don't overwrite.
    // A "completed" step pairs with the most recent unmatched "running" step of the same tool.
    let matchedIdx = -1;
    if (isCompleted) {
      for (let i = this.currentTurnToolSteps.length - 1; i >= 0; i--) {
        const s = this.currentTurnToolSteps[i];
        if (s.tool === toolName && !s._completed) {
          matchedIdx = i;
          break;
        }
      }
    }

    if (matchedIdx >= 0) {
      this.currentTurnToolSteps[matchedIdx] = { ...this.currentTurnToolSteps[matchedIdx], ...step, _completed: true };
    } else {
      this.currentTurnToolSteps.push({ ...step, _callIndex: this.currentTurnToolSteps.length, _completed: isCompleted });
    }

    const toolCount = this.currentTurnToolSteps.length;
    if (this.currentGroupedBadge) {
      this.currentGroupedBadge.textContent = `${toolCount} tool${toolCount > 1 ? 's' : ''}`;
    }
    const hasRunning = this.currentTurnToolSteps.some(s => !s._completed);
    if (this.currentGroupedDot) {
      this.currentGroupedDot.classList.toggle('running', hasRunning);
    }

    const cardKey = matchedIdx >= 0 ? `_call_${this.currentTurnToolSteps[matchedIdx]._callIndex}` : `_call_${this.currentTurnToolSteps[this.currentTurnToolSteps.length - 1]._callIndex}`;
    let card = this.activeToolCards.get(cardKey);
    if (!card) {
      card = document.createElement('div');
      card.className = 'tool-execution-card';
      // Individual tool call is minimized by default (collapsed class on tool-card-body)
      card.innerHTML = `
        <div class="tool-card-header">
          <div class="tool-card-left">
            <span class="material-symbols-outlined tool-terminal-icon">terminal</span>
            <span class="tool-name-text">${this.escapeHtml(toolName)}</span>
            <span class="tool-badge-pill ${badgeClass}">${badgeText}</span>
          </div>
          <span class="material-symbols-outlined" style="font-size:18px; color:var(--text-dim);">expand_more</span>
        </div>
        <div class="tool-card-body collapsed">
          <span class="tool-section-label">PARAMETERS</span>
          <div class="tool-code-preview tool-params-block"></div>
          <div class="tool-output-row">
            <span class="tool-section-label">OUTPUT STREAM</span>
            <button class="btn-tool-copy">
              <span class="material-symbols-outlined" style="font-size:12px;">content_copy</span>
              <span>COPY</span>
            </button>
          </div>
          <div class="tool-code-preview tool-output-block"></div>
        </div>
      `;

      const header = card.querySelector('.tool-card-header');
      const body = card.querySelector('.tool-card-body');
      header.addEventListener('click', () => {
        body.classList.toggle('collapsed');
      });

      const btnCopy = card.querySelector('.btn-tool-copy');
      btnCopy.addEventListener('click', (e) => {
        e.stopPropagation();
        const outBlock = card.querySelector('.tool-output-block');
        if (outBlock) {
          navigator.clipboard.writeText(outBlock.textContent);
          btnCopy.innerHTML = `<span class="material-symbols-outlined" style="font-size:12px;">check</span><span>COPIED</span>`;
          setTimeout(() => {
            btnCopy.innerHTML = `<span class="material-symbols-outlined" style="font-size:12px;">content_copy</span><span>COPY</span>`;
          }, 2000);
        }
      });

      this.currentGroupedBody.appendChild(card);
      this.activeToolCards.set(cardKey, card);
    }

    const badge = card.querySelector('.tool-badge-pill');
    if (badge) {
      badge.className = `tool-badge-pill ${badgeClass}`;
      badge.textContent = badgeText;
    }

    const paramsBlock = card.querySelector('.tool-params-block');
    if (paramsBlock && step.arguments) {
      paramsBlock.textContent = typeof step.arguments === 'object' ? JSON.stringify(step.arguments, null, 2) : String(step.arguments);
    }

    const outputBlock = card.querySelector('.tool-output-block');
    if (outputBlock) {
      if (step.result) {
        outputBlock.textContent = typeof step.result === 'object' ? JSON.stringify(step.result, null, 2) : String(step.result);
      } else if (step.rawResult || step.raw_result) {
        outputBlock.textContent = String(step.rawResult || step.raw_result);
      } else if (isCompleted) {
        outputBlock.textContent = '[INFO] Execution completed successfully.\n[STATUS] Exit code 0';
      }
    }

    this.scrollToBottom();
  }

  finishActiveTurnGroup() {
    if (this.currentGroupedContainer) {
      const elapsedSec = this.turnStartTime 
        ? ((performance.now() - this.turnStartTime) / 1000).toFixed(1) 
        : (Math.max(1, this.currentTurnToolSteps.length) * 1.2).toFixed(1);

      if (this.currentGroupedTitle) {
        this.currentGroupedTitle.textContent = `AVA worked for ${elapsedSec}s`;
      }
      if (this.currentGroupedDot) {
        this.currentGroupedDot.classList.remove('running');
      }
      // Grouped tool division is collapsed / minimized by default
      if (this.currentGroupedBody) {
        this.currentGroupedBody.classList.add('collapsed');
      }
      this.currentGroupedContainer.classList.remove('expanded');

      this.currentGroupedContainer = null;
      this.currentGroupedBody = null;
      this.currentGroupedTitle = null;
      this.currentGroupedBadge = null;
      this.currentGroupedDot = null;
      this.activeToolCards.clear();
      this.currentTurnToolSteps = [];
      this.turnStartTime = null;
    }
  }

  renderGroupedToolsHistorical(steps, durationSec) {
    if (!steps || steps.length === 0) return;
    const durationText = (durationSec && durationSec > 0)
      ? `${Number(durationSec).toFixed(1)}s` 
      : `${(steps.length * 1.2).toFixed(1)}s`;

    const container = document.createElement('div');
    container.className = 'grouped-tools-container';
    container.innerHTML = `
      <div class="grouped-tools-header">
        <div class="grouped-tools-left">
          <span class="grouped-status-dot"></span>
          <span class="grouped-tools-title">AVA worked for ${durationText}</span>
          <span class="grouped-tools-badge">${steps.length} tool${steps.length > 1 ? 's' : ''}</span>
        </div>
        <span class="material-symbols-outlined grouped-chevron">expand_more</span>
      </div>
      <div class="grouped-tools-body collapsed"></div>
    `;

    const header = container.querySelector('.grouped-tools-header');
    const body = container.querySelector('.grouped-tools-body');
    header.addEventListener('click', () => {
      const isCollapsed = body.classList.toggle('collapsed');
      container.classList.toggle('expanded', !isCollapsed);
    });

    steps.forEach(s => {
      const toolName = s.tool || s.label || 'tool';
      const card = document.createElement('div');
      card.className = 'tool-execution-card';
      const argsText = s.arguments 
        ? (typeof s.arguments === 'object' ? JSON.stringify(s.arguments, null, 2) : String(s.arguments)) 
        : '{}';
      const resText = s.result 
        ? (typeof s.result === 'object' ? JSON.stringify(s.result, null, 2) : String(s.result)) 
        : (s.rawResult || s.raw_result || '[INFO] Execution completed successfully.\n[STATUS] Exit code 0');

      card.innerHTML = `
        <div class="tool-card-header">
          <div class="tool-card-left">
            <span class="material-symbols-outlined tool-terminal-icon">terminal</span>
            <span class="tool-name-text">${this.escapeHtml(toolName)}</span>
            <span class="tool-badge-pill success">EXIT 0</span>
          </div>
          <span class="material-symbols-outlined" style="font-size:18px; color:var(--text-dim);">expand_more</span>
        </div>
        <div class="tool-card-body collapsed">
          <span class="tool-section-label">PARAMETERS</span>
          <div class="tool-code-preview">${this.escapeHtml(argsText)}</div>
          <div class="tool-output-row">
            <span class="tool-section-label">OUTPUT STREAM</span>
          </div>
          <div class="tool-code-preview">${this.escapeHtml(resText)}</div>
        </div>
      `;

      const cardHeader = card.querySelector('.tool-card-header');
      const cardBody = card.querySelector('.tool-card-body');
      cardHeader.addEventListener('click', () => {
        cardBody.classList.toggle('collapsed');
      });
      body.appendChild(card);
    });

    this.messageFeed.appendChild(container);
  }

  /* ── User Command Dispatch ───────────────────────────────────────────────── */
  sendMessage(text) {
    const clean = (text || '').trim();
    if (!clean) return;

    this.finishActiveTurnGroup();
    this.turnStartTime = performance.now();
    this.renderUserMessage(clean);
    this.showThinking(true, 'AVA is analyzing instruction...');

    const payload = {
      type: 'chat_message',
      message: clean,
      conversation_id: this.currentConvId,
      device_id: this.clientId
    };

    if (!this.sendWs(payload)) {
      const proto = (this.protocol === 'wss://' ? 'https://' : 'http://');
      fetch(`${proto}${this.host}:${this.port}/conversation/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      }).catch(err => console.warn('REST fallback failed:', err));
    }

    if (this.composerInput) {
      this.composerInput.value = '';
      this.composerInput.style.height = 'auto';
    }
  }

  /* ── Event Listeners ─────────────────────────────────────────────────────── */
  initEventListeners() {
    if (this.btnSend && this.composerInput) {
      this.btnSend.addEventListener('click', () => {
        this.sendMessage(this.composerInput.value);
      });

      this.composerInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          this.sendMessage(this.composerInput.value);
        }
      });

      this.composerInput.addEventListener('input', () => {
        this.composerInput.style.height = 'auto';
        this.composerInput.style.height = `${Math.min(this.composerInput.scrollHeight, 160)}px`;
      });
    }

    if (this.btnNewChat) {
      this.btnNewChat.addEventListener('click', () => this.startNewConversation());
    }

    if (this.searchThreadsInput) {
      this.searchThreadsInput.addEventListener('input', (e) => {
        this.renderConversations(e.target.value);
      });
    }

    window.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (this.searchThreadsInput) {
          this.searchThreadsInput.focus();
          this.searchThreadsInput.select();
        }
      }
    });

    document.querySelectorAll('.suggestion-chip, .hero-action-card').forEach(chip => {
      chip.addEventListener('click', () => {
        const prompt = chip.dataset.prompt;
        if (prompt) this.sendMessage(prompt);
      });
    });

    if (this.btnMobileSidebar) {
      this.btnMobileSidebar.addEventListener('click', () => this.toggleMobileDrawer());
    }
    if (this.drawerBackdrop) {
      this.drawerBackdrop.addEventListener('click', () => this.closeMobileDrawer());
    }

    if (this.btnDesktopSidebarToggle) {
      this.btnDesktopSidebarToggle.addEventListener('click', () => {
        if (this.sidebar) this.sidebar.classList.toggle('collapsed');
      });
    }

    if (this.btnOpenSettings) {
      this.btnOpenSettings.addEventListener('click', () => this.openSettingsModal());
    }
    if (this.sidebarHostLink) {
      this.sidebarHostLink.addEventListener('click', () => this.openSettingsModal());
    }
    if (this.btnCloseSettings) {
      this.btnCloseSettings.addEventListener('click', () => this.closeSettingsModal());
    }
    if (this.btnDiscardSettings) {
      this.btnDiscardSettings.addEventListener('click', () => this.closeSettingsModal());
    }
    if (this.settingsModalOverlay) {
      this.settingsModalOverlay.addEventListener('click', (e) => {
        if (e.target === this.settingsModalOverlay) this.closeSettingsModal();
      });
    }

    if (this.btnPortDec && this.settingsPort) {
      this.btnPortDec.addEventListener('click', () => {
        this.settingsPort.value = Math.max(1, parseInt(this.settingsPort.value || 8765) - 1);
      });
    }
    if (this.btnPortInc && this.settingsPort) {
      this.btnPortInc.addEventListener('click', () => {
        this.settingsPort.value = parseInt(this.settingsPort.value || 8765) + 1;
      });
    }

    if (this.btnTestPing) {
      this.btnTestPing.addEventListener('click', () => this.testLatency());
    }

    if (this.btnSaveSettings) {
      this.btnSaveSettings.addEventListener('click', () => this.saveSettings());
    }
  }

  toggleMobileDrawer() {
    if (!this.sidebar) return;
    const isOpen = this.sidebar.classList.toggle('open');
    if (this.drawerBackdrop) this.drawerBackdrop.classList.toggle('hidden', !isOpen);
  }

  closeMobileDrawer() {
    if (this.sidebar) this.sidebar.classList.remove('open');
    if (this.drawerBackdrop) this.drawerBackdrop.classList.add('hidden');
  }

  /* ── Settings Modal Logic ─────────────────────────────────────────────────── */
  openSettingsModal() {
    if (!this.settingsModalOverlay) return;
    if (this.settingsProtocol) this.settingsProtocol.value = this.protocol;
    if (this.settingsHost) this.settingsHost.value = this.host;
    if (this.settingsPort) this.settingsPort.value = this.port;
    if (this.settingsClientId) this.settingsClientId.value = this.clientId;
    this.renderAccentSwatches();
    this.settingsModalOverlay.classList.remove('hidden');
    this.testLatency();
  }

  closeSettingsModal() {
    if (this.settingsModalOverlay) this.settingsModalOverlay.classList.add('hidden');
  }

  saveSettings() {
    if (this.settingsProtocol) this.protocol = this.settingsProtocol.value;
    if (this.settingsHost) this.host = this.settingsHost.value.trim() || '127.0.0.1';
    if (this.settingsPort) this.port = this.settingsPort.value.trim() || '8765';
    if (this.settingsClientId) this.clientId = this.settingsClientId.value.trim() || 'web_workspace';

    localStorage.setItem('ava_protocol', this.protocol);
    localStorage.setItem('ava_host', this.host);
    localStorage.setItem('ava_port', this.port);
    localStorage.setItem('ava_client_id', this.clientId);

    if (this.headerClientId) this.headerClientId.textContent = this.clientId;
    if (this.footerHostLabel) this.footerHostLabel.textContent = `HOST: ${this.host}:${this.port}`;

    this.closeSettingsModal();
    this.connectWebSocket();
    this.fetchConversations();
  }

  async testLatency() {
    if (!this.diagLatencyVal) return;
    this.diagLatencyVal.textContent = 'Measuring...';
    const start = performance.now();
    try {
      const proto = (this.protocol === 'wss://' ? 'https://' : 'http://');
      const url = `${proto}${this.host}:${this.port}/health`;
      const res = await fetch(url, { cache: 'no-store' });
      if (res.ok) {
        const ms = Math.round(performance.now() - start);
        this.diagLatencyVal.textContent = `${ms} ms`;
      } else {
        this.diagLatencyVal.textContent = 'Err (HTTP)';
      }
    } catch (_) {
      this.diagLatencyVal.textContent = 'Offline';
    }
  }

  escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\"/g, '&quot;')
      .replace(/\'/g, '&#039;');
  }
}

// Instantiate on document ready
document.addEventListener('DOMContentLoaded', () => {
  window.avaClient = new AvaStitchClient();
});
