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
    let defaultHost = window.location.hostname || '127.0.0.1';
    if (defaultHost === 'localhost') defaultHost = '127.0.0.1';
    this.host = localStorage.getItem('ava_host') || defaultHost;

    const locPort = window.location.port;
    const isAvaPort = (locPort === '8765' || locPort === '8766');
    this.port = localStorage.getItem('ava_port') || (isAvaPort ? locPort : '8765');

    this.clientId = localStorage.getItem('ava_client_id') || 'web_workspace';
    this.deviceType = 'web';
    this.accentColor = localStorage.getItem('ava_accent_color') || '#FFFFFF';
    this.lastHeartbeatAck = Date.now();

    // DOM Bindings
    this.bindDOMElements();

    // Init Theme & Accent
    this.applyAccentColor(this.accentColor, false);

    // Init UI Listeners
    this.initEventListeners();
    this.initAttachments();

    // Life-cycle / Tab Resume Handlers
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        const isDead = !this.ws || this.ws.readyState !== WebSocket.OPEN || (Date.now() - this.lastHeartbeatAck > 35000);
        if (isDead) {
          console.log('[AVA-WS] Tab restored and connection stale — reconnecting...');
          this.connectWebSocket();
        }
      }
    });
    window.addEventListener('online', () => {
      console.log('[AVA-WS] Browser online event — reconnecting...');
      this.connectWebSocket();
    });
    window.addEventListener('offline', () => {
      this.updateConnectionState(false, 'NETWORK OFFLINE');
    });

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
    this.attachmentsBar = document.getElementById('composer-attachments-bar');
    this.pendingAttachments = [];
    this.btnMic = document.getElementById('btn-mic');
    this.voiceListeningStrip = document.getElementById('voice-listening-strip');
    this.btnVoiceCancel = document.getElementById('btn-voice-cancel');

    // Running Tasks Dock (Unified Composer Top Bar)
    this.runningTasksDock = document.getElementById('running-tasks-dock');
    this.tasksDockToggle = document.getElementById('tasks-dock-toggle');
    this.tasksDockTitle = document.getElementById('tasks-dock-title');
    this.tasksDockChevronIcon = document.getElementById('tasks-dock-chevron-icon');
    this.tasksDockList = document.getElementById('tasks-dock-list');
    this.isTasksDockCollapsed = localStorage.getItem('ava_tasks_dock_collapsed') === 'true';

    if (this.tasksDockToggle) {
      this.tasksDockToggle.addEventListener('click', (e) => {
        if (e.target.closest('.tasks-dock-cancel-btn')) return;
        this.isTasksDockCollapsed = !this.isTasksDockCollapsed;
        localStorage.setItem('ava_tasks_dock_collapsed', String(this.isTasksDockCollapsed));
        if (this.tasksDockList) {
          this.tasksDockList.classList.toggle('collapsed', this.isTasksDockCollapsed);
        }
        if (this.tasksDockChevronIcon) {
          this.tasksDockChevronIcon.textContent = this.isTasksDockCollapsed ? 'expand_more' : 'expand_less';
        }
      });
    }

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

    // Server Cognitive Settings (A.V.A 2.0)
    this.serverDualLayerToggle = document.getElementById('server-dual-layer-toggle');
    this.serverSentinelsToggle = document.getElementById('server-sentinels-toggle');
    this.serverModelName = document.getElementById('server-model-name');
    this.serverLayer2Model = document.getElementById('server-layer2-model');
    this.btnSaveServerConfig = document.getElementById('btn-save-server-config');

    // User Presence Toggle
    this.btnTogglePresence = document.getElementById('btn-toggle-presence');
    this.presenceDot = document.getElementById('presence-dot');
    this.presenceText = document.getElementById('presence-text');
    this.currentUserPresence = 'present';

    // Thinking Drawer & Expand Button
    this.btnToggleThinking = document.getElementById('btn-toggle-thinking');
    this.btnToggleThinkingDrawer = document.getElementById('btn-toggle-thinking-drawer');
    this.thinkingDrawer = document.getElementById('thinking-drawer');
    this.thinkingDrawerTicket = document.getElementById('thinking-drawer-ticket');
    this.thinkingDrawerLogs = document.getElementById('thinking-drawer-logs');
    this.thinkingExpandIcon = document.getElementById('thinking-expand-icon');
    this.currentTurnThoughts = [];

    // Sentinel Simulator & Flight Recorder
    this.btnOpenSentinel = document.getElementById('btn-open-sentinel');
    this.btnGotoSentinelSim = document.getElementById('btn-goto-sentinel-sim');
    this.sentinelModalOverlay = document.getElementById('sentinel-modal-overlay');
    this.btnCloseSentinel = document.getElementById('btn-close-sentinel');
    this.btnCloseSentinelFooter = document.getElementById('btn-close-sentinel-footer');
    this.simPreset = document.getElementById('sim-preset');
    this.simSentinel = document.getElementById('sim-sentinel');
    this.simSeverity = document.getElementById('sim-severity');
    this.simTitle = document.getElementById('sim-title');
    this.simMessage = document.getElementById('sim-message');
    this.btnTriggerSentinelEvent = document.getElementById('btn-trigger-sentinel-event');
    this.simResultBox = document.getElementById('sim-result-box');
    this.simResultBadge = document.getElementById('sim-result-badge');
    this.simResultEntryId = document.getElementById('sim-result-entry-id');
    this.simResultDiag = document.getElementById('sim-result-diag');
    this.btnRefreshTelemetry = document.getElementById('btn-refresh-telemetry');
    this.flightRecorderFeed = document.getElementById('flight-recorder-feed');

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
    let proto = this.protocol;
    if (!proto || !proto.startsWith('ws')) {
      proto = (window.location.protocol === 'https:' ? 'wss://' : 'ws://');
    }
    let host = this.host || window.location.hostname || '127.0.0.1';
    if (!host || host === 'localhost') host = '127.0.0.1';
    let port = this.port || '8765';
    return `${proto}${host}:${port}/ws/device`;
  }

  connectWebSocket() {
    // Clear any pending reconnect timer so it doesn't fire concurrently
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    // Clean up existing socket cleanly without triggering its onclose handler
    if (this.ws) {
      const oldWs = this.ws;
      this.ws = null;
      oldWs.onopen = null;
      oldWs.onmessage = null;
      oldWs.onerror = null;
      oldWs.onclose = null;
      try {
        if (oldWs.readyState === WebSocket.OPEN || oldWs.readyState === WebSocket.CONNECTING) {
          oldWs.close(1000, 'Reconnecting');
        }
      } catch (_) {}
    }

    const wsUrl = this.getWebSocketUrl();
    this.updateConnectionState(false, 'CONNECTING...');

    try {
      this.ws = new WebSocket(wsUrl);
    } catch (err) {
      console.warn('[AVA-WS] Failed to instantiate WebSocket:', err);
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.lastHeartbeatAck = Date.now();
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

    this.ws.onclose = (event) => {
      this.stopHeartbeat();
      this.updateConnectionState(false, 'OFFLINE');
      console.warn(`[AVA-WS] Socket closed (code: ${event?.code}, reason: '${event?.reason || ''}'). Reconnecting...`);
      this.scheduleReconnect();
    };

    this.ws.onerror = (err) => {
      console.warn('[AVA-WS] Socket error encountered:', err);
      // Let browser fire onclose naturally to avoid dual-close races
    };
  }

  sendWs(data) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify(data));
        return true;
      } catch (e) {
        console.warn('[AVA-WS] Send error:', e);
        return false;
      }
    }
    return false;
  }

  startHeartbeat() {
    this.stopHeartbeat();
    this.lastHeartbeatAck = Date.now();
    this.heartbeatTimer = setInterval(() => {
      // Zombie socket detection: if active for >36s with no ack received, reconnect
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        if (Date.now() - this.lastHeartbeatAck > 36000) {
          console.warn('[AVA-WS] Heartbeat timeout (zombie socket). Forcing reconnect...');
          this.connectWebSocket();
          return;
        }
        this.sendWs({ type: 'heartbeat', timestamp: Date.now() / 1000 });
      } else if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
        this.connectWebSocket();
      }
    }, 12000); // 12 seconds keeps connection fresh and avoids proxy/Windows timeouts
  }

  stopHeartbeat() {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  scheduleReconnect() {
    if (this.reconnectTimer) return;
    const baseDelay = Math.min(1000 * Math.pow(1.3, this.reconnectAttempts), 8000);
    const delay = Math.round(baseDelay + (Math.random() * 400));
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
      if (msg.presence) {
        this.setPresence(msg.presence, false);
      }
      // If server returns conversations on register, sync immediately
      if (Array.isArray(msg.conversations)) {
        this.syncConversationsWithServer(msg.conversations);
      } else {
        this.fetchConversations();
      }
    }
    else if (type === 'user_presence_updated') {
      this.setPresence(msg.presence || 'present', false);
    }
    else if (type === 'heartbeat_ack' || type === 'pong') {
      this.lastHeartbeatAck = Date.now();
    }
    else if (type === 'thinking') {
      const isThinking = msg.status === 'start';
      if (isThinking) {
        this.currentTurnThoughts = [];
      }
      if (msg.label) {
        const timeStr = new Date().toLocaleTimeString([], { hour12: false });
        this.currentTurnThoughts.push({
          time: timeStr,
          stage: msg.stage || 'Thinking',
          text: msg.label,
        });
      }
      this.showThinking(isThinking, msg.label || 'AVA is reasoning...', msg.ticket_id, msg.stage);
    }
    else if (type === 'blackboard_event') {
      this.handleBlackboardEvent(msg.event, msg.data);
    }
    else if (type === 'agent_step') {
      const st = msg.step || msg;
      const stepText = st.title || st.last_log_line || st.status || 'Executing step';
      const timeStr = new Date().toLocaleTimeString([], { hour12: false });
      this.currentTurnThoughts.push({
        time: timeStr,
        stage: 'Action',
        text: stepText,
      });
      this.handleAgentStep(st);
    }
    else if (type === 'sentinel_alert') {
      if (this.sentinelModalOverlay && !this.sentinelModalOverlay.classList.contains('hidden')) {
        this.fetchFlightRecorder();
      }
    }

    else if (type === 'assistant_response') {
      this.showThinking(false);
      this.finishActiveTurnGroup();
      const content = msg.content || '';
      const convId = msg.conversation_id || this.currentConvId;
      if (convId) this.setActiveConversationId(convId);
      const turnThoughts = [...this.currentTurnThoughts];
      this.renderAssistantMessage(content, msg.metadata || {}, turnThoughts);
      this.currentTurnThoughts = [];
      this.fetchConversations();
    }
    else if (type === 'tasks_updated') {
      this.activeTasks = Array.isArray(msg.tasks) ? msg.tasks : [];
      this.renderActiveTasks();
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
    // 1. Sidebar section
    if (this.tasksCountBadge) {
      this.tasksCountBadge.textContent = `[${this.activeTasks.length}]`;
    }

    if (this.tasksListContainer) {
      if (this.activeTasks.length === 0) {
        this.tasksListContainer.innerHTML = `
          <div class="task-empty-state">
            <span class="task-empty-title">No active background tasks</span>
            <span class="task-empty-sub">Queue idle. Ready for instruction dispatch.</span>
          </div>
        `;
      } else {
        this.tasksListContainer.innerHTML = this.activeTasks.map(t => {
          const pct = (t.progress != null && t.progress <= 1.0)
            ? Math.round(t.progress * 100)
            : (Math.round(t.progress) || 65);
          const status = (t.status || t.statusText || 'RUNNING').toUpperCase();
          const statusColor = status === 'COMPLETED' ? 'var(--color-green)' : (status === 'FAILED' ? 'var(--color-red)' : 'var(--accent-primary, #a855f7)');
          const title = t.command || t.title || t.name || t.tool || 'Task';
          return `
            <div class="task-item-card">
              <div class="task-item-top">
                <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 170px;" title="${this.escapeHtml(title)}">${this.escapeHtml(title)}</span>
                <span style="color: ${statusColor}; font-family:var(--font-mono); font-size:10px;">${this.escapeHtml(status)}</span>
              </div>
              <div class="task-progress-track">
                <div class="task-progress-fill" style="width: ${pct}%;"></div>
              </div>
            </div>
          `;
        }).join('');
      }
    }

    // 2. Composer Running Tasks Dock (Unified Top Section - matches current design language)
    if (this.runningTasksDock) {
      const runningList = (this.activeTasks || []).filter(t => {
        const st = (t.status || t.statusText || 'running').toLowerCase();
        return st === 'running' || st === 'pending';
      });

      if (runningList.length === 0) {
        this.runningTasksDock.classList.add('hidden');
      } else {
        this.runningTasksDock.classList.remove('hidden');
        if (this.tasksDockTitle) {
          const countText = runningList.length === 1 ? '1 task running' : `${runningList.length} tasks running`;
          this.tasksDockTitle.textContent = countText;
        }

        if (this.tasksDockChevronIcon) {
          this.tasksDockChevronIcon.textContent = this.isTasksDockCollapsed ? 'expand_more' : 'expand_less';
        }

        if (this.tasksDockList) {
          this.tasksDockList.classList.toggle('collapsed', !!this.isTasksDockCollapsed);
          this.tasksDockList.innerHTML = runningList.map(t => {
            const rawCmd = t.command || t.title || t.name || t.original_query || 'Running background task';
            const safeCmd = this.escapeHtml(rawCmd);
            const taskId = this.escapeHtml(String(t.id || ''));
            const taskType = this.escapeHtml(String(t.task_type || t.tool || 'task'));

            return `
              <div class="tasks-dock-item" data-task-id="${taskId}">
                <div class="tasks-dock-item-left">
                  <span class="material-symbols-outlined tasks-dock-spinner">progress_activity</span>
                  <span class="tasks-dock-command" title="${safeCmd}">${safeCmd}</span>
                </div>
                <div class="tasks-dock-item-right">
                  <span class="tasks-dock-type-badge">${taskType}</span>
                  <button class="tasks-dock-cancel-btn" data-cancel-id="${taskId}" title="Cancel Task">
                    <span class="material-symbols-outlined">close</span>
                  </button>
                </div>
              </div>
            `;
          }).join('');

          // Wire cancel buttons
          this.tasksDockList.querySelectorAll('.tasks-dock-cancel-btn').forEach(btn => {
            btn.onclick = (e) => {
              e.stopPropagation();
              const tid = btn.getAttribute('data-cancel-id');
              if (tid) this.cancelTask(tid);
            };
          });
        }
      }
    }
  }

  async cancelTask(taskId) {
    if (!taskId) return;
    try {
      const proto = (this.protocol === 'wss://' ? 'https://' : 'http://');
      const url = `${proto}${this.host}:${this.port}/tasks/cancel`;
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: taskId }),
      });
      if (res.ok) {
        await this.pollBackgroundTasks();
      }
    } catch (e) {
      console.warn('Failed to cancel task:', e);
    }
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

  renderUserMessage(text, attachments = []) {
    this.setEmptyState(false);
    const row = document.createElement('div');
    row.className = 'msg-row msg-row-user';

    let displayAttachments = Array.isArray(attachments) ? [...attachments] : [];
    let cleanText = text || '';

    if (displayAttachments.length === 0 && cleanText) {
      const imgRegex = /\[Attached Image:\s*([^\]]+?)\s*\(Server Path:\s*([^)]+?)\)\]/gi;
      let match;
      while ((match = imgRegex.exec(cleanText)) !== null) {
        displayAttachments.push({
          filename: match[1].trim(),
          server_path: match[2].trim(),
          url: `/uploads/${encodeURIComponent(match[1].trim())}`,
          is_image: true,
        });
      }
      const docRegex = /\[Attached Document:\s*([^\]]+?)\s*\(Server Path:\s*([^)]+?)\)\]/gi;
      while ((match = docRegex.exec(cleanText)) !== null) {
        displayAttachments.push({
          filename: match[1].trim(),
          server_path: match[2].trim(),
          url: `/uploads/${encodeURIComponent(match[1].trim())}`,
          is_image: false,
        });
      }
      cleanText = cleanText.replace(/\[Attached (Image|Document):\s*[^\]]+?\(Server Path:\s*[^)]+?\)\]/gi, '').trim();
    }

    let attHtml = '';
    if (displayAttachments.length > 0) {
      const items = displayAttachments.map(att => {
        const isImg = att.is_image || (att.filename && att.filename.match(/\.(png|jpg|jpeg|webp|gif|bmp)$/i));
        const src = att.url || (att.server_path ? `/uploads/${encodeURIComponent(att.filename || 'image.png')}` : '');
        if (isImg && src) {
          return `
            <a href="${src}" target="_blank" class="msg-attachment-thumb-box" title="${this.escapeHtml(att.filename || 'Image')}">
              <img src="${src}" class="msg-attachment-thumb" alt="${this.escapeHtml(att.filename || '')}" />
            </a>
          `;
        } else {
          const href = att.url || (att.filename ? `/uploads/${encodeURIComponent(att.filename)}` : '#');
          return `
            <a href="${href}" target="_blank" class="msg-doc-chip" title="${this.escapeHtml(att.server_path || att.filename || 'Document')}">
              <span class="material-symbols-outlined" style="font-size: 16px;">description</span>
              <span>${this.escapeHtml(att.filename || 'Document')}</span>
            </a>
          `;
        }
      }).join('');
      attHtml = `<div class="message-attachments-container">${items}</div>`;
    }

    let innerHtml = '';
    if (attHtml) innerHtml += attHtml;
    if (cleanText) innerHtml += `<div class="user-msg-text">${this.escapeHtml(cleanText)}</div>`;

    row.innerHTML = `<div class="user-card-bubble">${innerHtml}</div>`;
    this.messageFeed.appendChild(row);
    this.scrollToBottom();
  }

  renderAssistantMessage(markdownText, metadata = {}, thoughts = []) {
    this.setEmptyState(false);
    const row = document.createElement('div');
    row.className = 'msg-row msg-row-assistant';

    const nowStr = new Date().toISOString().substring(11, 19) + ' UTC';
    const renderedHtml = window.marked ? marked.parse(markdownText) : markdownText;

    // Sentinel Alert Badge Banner (proactive human assistant notification)
    const isAlert = metadata.is_sentinel_alert === true;
    const sev = (metadata.severity || 'WARN').toUpperCase();
    const alertBanner = isAlert ? `
      <div class="assistant-alert-banner ${sev === 'CRITICAL' ? 'critical' : ''}">
        <span class="material-symbols-outlined" style="font-size: 16px;">${sev === 'CRITICAL' ? 'crisis_alert' : 'warning'}</span>
        <span>${sev} Sentinel Alert // ${this.escapeHtml(metadata.sentinel || 'system_sentinel')}</span>
      </div>
    ` : '';

    // Collapsible Thinking Process Accordion for this turn
    let thinkingAccordionHtml = '';
    if (Array.isArray(thoughts) && thoughts.length > 0) {
      thinkingAccordionHtml = `
        <details class="turn-thinking-details">
          <summary class="turn-thinking-summary">
            <span class="material-symbols-outlined" style="font-size: 15px; color: var(--accent-primary, #a855f7);">psychology</span>
            <span>Layer 2 Thought Process (${thoughts.length} step${thoughts.length > 1 ? 's' : ''})</span>
            <span class="material-symbols-outlined turn-thinking-arrow" style="font-size: 16px; margin-left: auto;">expand_more</span>
          </summary>
          <div class="turn-thinking-content">
            ${thoughts.map(t => `
              <div class="turn-thought-step">
                <span class="thought-time">[${t.time}]</span>
                <span class="thought-stage">${t.stage}</span>
                <span class="thought-text">${this.escapeHtml(t.text)}</span>
              </div>
            `).join('')}
          </div>
        </details>
      `;
    }

    row.innerHTML = `
      <div class="assistant-meta-strip">
        <span>AVA // Active Stream</span>
        <span>•</span>
        <span>${nowStr}</span>
      </div>
      ${alertBanner}
      ${thinkingAccordionHtml}
      <div class="assistant-card-body">${renderedHtml}</div>
    `;

    row.querySelectorAll('pre code').forEach((block) => {
      if (window.hljs) hljs.highlightElement(block);
    });

    this.messageFeed.appendChild(row);
    this.scrollToBottom();
  }

  showThinking(show, labelText, ticketId, stage) {
    if (!this.thinkingStrip) return;
    this.thinkingStrip.classList.toggle('hidden', !show);
    if (this.thinkingLabel && labelText) {
      this.thinkingLabel.textContent = labelText;
    }
    if (ticketId && this.thinkingDrawerTicket) {
      this.thinkingDrawerTicket.textContent = `#${ticketId}`;
    }
    if (show && labelText && this.thinkingDrawerLogs) {
      const timeStr = new Date().toLocaleTimeString([], { hour12: false });
      const stageStr = stage || 'Exec';
      const entry = document.createElement('div');
      entry.className = 'thinking-log-entry';
      entry.innerHTML = `<span class="thinking-log-time">[${timeStr}]</span> <span class="thinking-log-stage">${stageStr}</span> ${this.escapeHtml(labelText)}`;
      this.thinkingDrawerLogs.appendChild(entry);
      this.thinkingDrawerLogs.scrollTop = this.thinkingDrawerLogs.scrollHeight;
    }
    if (show) this.scrollToBottom();
  }

  handleBlackboardEvent(event, data) {
    if (!data) return;
    if (event === 'step_updated' && data.step) {
      const s = data.step;
      const label = `${s.assigned_worker}: ${s.last_log_line || s.title}`;
      this.showThinking(true, label, data.ticket_id, s.status);
    } else if (event === 'thinking') {
      this.showThinking(true, data.text, data.ticket_id, data.stage);
    } else if (event === 'ticket_updated' && data.ticket && data.ticket.status === 'completed') {
      this.showThinking(false);
      if (this.thinkingDrawerTicket) {
        this.thinkingDrawerTicket.textContent = `#${data.ticket.ticket_id} (COMPLETED)`;
      }
    }
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

    // Record or update step
    const existingIdx = this.currentTurnToolSteps.findIndex(s => (s.tool || s.label) === toolName);
    if (existingIdx >= 0) {
      this.currentTurnToolSteps[existingIdx] = { ...this.currentTurnToolSteps[existingIdx], ...step };
    } else {
      this.currentTurnToolSteps.push(step);
    }

    const toolCount = this.currentTurnToolSteps.length;
    if (this.currentGroupedBadge) {
      this.currentGroupedBadge.textContent = `${toolCount} tool${toolCount > 1 ? 's' : ''}`;
    }
    const hasRunning = this.currentTurnToolSteps.some(s => (s.status || 'running') === 'running');
    if (this.currentGroupedDot) {
      this.currentGroupedDot.classList.toggle('running', hasRunning);
    }

    let card = this.activeToolCards.get(toolName);
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
      this.activeToolCards.set(toolName, card);
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

  /* ── Attachments Pipeline ────────────────────────────────────────────────── */
  initAttachments() {
    this.pendingAttachments = [];
    this.attachmentsBar = document.getElementById('composer-attachments-bar');

    if (this.btnAttach && this.fileInput) {
      this.btnAttach.addEventListener('click', () => {
        this.fileInput.click();
      });

      this.fileInput.addEventListener('change', (e) => {
        if (e.target.files && e.target.files.length > 0) {
          this.uploadAndAttachFiles(Array.from(e.target.files));
          this.fileInput.value = '';
        }
      });
    }

    // Drag and Drop onto composer card
    const card = document.getElementById('composer-card');
    if (card) {
      ['dragenter', 'dragover'].forEach(name => {
        card.addEventListener(name, (e) => {
          e.preventDefault();
          e.stopPropagation();
          card.classList.add('drag-over');
        });
      });

      ['dragleave', 'drop'].forEach(name => {
        card.addEventListener(name, (e) => {
          e.preventDefault();
          e.stopPropagation();
          card.classList.remove('drag-over');
        });
      });

      card.addEventListener('drop', (e) => {
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
          this.uploadAndAttachFiles(Array.from(e.dataTransfer.files));
        }
      });
    }

    // Clipboard Paste (Ctrl+V) listener on composer input
    if (this.composerInput) {
      this.composerInput.addEventListener('paste', (e) => {
        const items = e.clipboardData ? e.clipboardData.items : [];
        const filesToUpload = [];

        for (let i = 0; i < items.length; i++) {
          const item = items[i];
          if (item.kind === 'file') {
            const f = item.getAsFile();
            if (f) filesToUpload.push(f);
          }
        }

        if (filesToUpload.length > 0) {
          e.preventDefault();
          this.uploadAndAttachFiles(filesToUpload);
        }
      });
    }
  }

  async uploadAndAttachFiles(files) {
    if (!files || files.length === 0) return;

    for (const f of files) {
      const tempId = 'up_' + Math.random().toString(36).substring(2, 9);
      const isImg = f.type.startsWith('image/') || f.name.match(/\.(png|jpg|jpeg|webp|gif|bmp|svg)$/i);
      const localUrl = isImg ? URL.createObjectURL(f) : '';

      const pendingObj = {
        id: tempId,
        filename: f.name,
        size: f.size,
        mime: f.type,
        is_image: isImg,
        url: localUrl,
        uploading: true,
      };
      this.pendingAttachments.push(pendingObj);
      this.renderAttachmentsBar();

      const formData = new FormData();
      formData.append('file', f, f.name);

      try {
        const proto = (this.protocol === 'wss://' ? 'https://' : 'http://');
        const res = await fetch(`${proto}${this.host}:${this.port}/api/upload`, {
          method: 'POST',
          body: formData,
        });
        const data = await res.json();
        if (data.ok && data.files && data.files.length > 0) {
          const upFile = data.files[0];
          pendingObj.server_path = upFile.server_path;
          pendingObj.relative_path = upFile.relative_path;
          pendingObj.url = upFile.url;
          pendingObj.uploading = false;
        } else {
          this.removeAttachmentById(tempId);
          console.error('Upload failed:', data.error);
        }
      } catch (err) {
        console.error('Error uploading file:', err);
        this.removeAttachmentById(tempId);
      }
      this.renderAttachmentsBar();
    }
  }

  removeAttachmentById(id) {
    this.pendingAttachments = this.pendingAttachments.filter(a => a.id !== id);
    this.renderAttachmentsBar();
  }

  clearAttachments() {
    this.pendingAttachments = [];
    this.renderAttachmentsBar();
  }

  renderAttachmentsBar() {
    if (!this.attachmentsBar) return;
    if (this.pendingAttachments.length === 0) {
      this.attachmentsBar.classList.add('hidden');
      this.attachmentsBar.innerHTML = '';
      return;
    }

    this.attachmentsBar.classList.remove('hidden');
    this.attachmentsBar.innerHTML = this.pendingAttachments.map(att => {
      const sizeStr = att.size ? (att.size < 1048576 ? `${(att.size / 1024).toFixed(0)} KB` : `${(att.size / 1048576).toFixed(1)} MB`) : '';
      const thumbHtml = att.is_image && att.url
        ? `<img src="${att.url}" class="attachment-thumb" alt="" />`
        : `<div class="attachment-icon-box"><span class="material-symbols-outlined" style="font-size: 18px;">description</span></div>`;

      return `
        <div class="attachment-chip" id="chip-${att.id}">
          ${thumbHtml}
          <div class="attachment-info">
            <span class="attachment-name" title="${this.escapeHtml(att.filename)}">${this.escapeHtml(att.filename)}</span>
            <span class="attachment-size">${att.uploading ? 'UPLOADING...' : sizeStr}</span>
          </div>
          <button class="attachment-remove-btn" onclick="window.avaClient.removeAttachmentById('${att.id}')" title="Remove attachment">
            <span class="material-symbols-outlined" style="font-size: 16px;">close</span>
          </button>
        </div>
      `;
    }).join('');
  }

  /* ── User Command Dispatch ───────────────────────────────────────────────── */
  sendMessage(text) {
    const clean = (text || '').trim();
    if (!clean && (!this.pendingAttachments || this.pendingAttachments.length === 0)) return;

    const attachmentsToSend = this.pendingAttachments ? [...this.pendingAttachments] : [];

    this.finishActiveTurnGroup();
    this.turnStartTime = performance.now();
    this.renderUserMessage(clean, attachmentsToSend);
    this.showThinking(true, 'AVA is analyzing instruction...');

    const payload = {
      type: 'chat_message',
      message: clean,
      attachments: attachmentsToSend,
      conversation_id: this.currentConvId,
      device_id: this.clientId,
    };

    if (!this.sendWs(payload)) {
      const proto = (this.protocol === 'wss://' ? 'https://' : 'http://');
      fetch(`${proto}${this.host}:${this.port}/conversation/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).catch(err => console.warn('REST fallback failed:', err));
    }

    this.clearAttachments();

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

    if (this.btnSaveServerConfig) {
      this.btnSaveServerConfig.addEventListener('click', () => this.saveServerSettings());
    }

    if (this.btnTogglePresence) {
      this.btnTogglePresence.addEventListener('click', () => this.togglePresence());
    }

    if (this.btnToggleThinkingDrawer && this.thinkingDrawer) {
      this.btnToggleThinkingDrawer.addEventListener('click', () => this.toggleThinkingDrawer());
    }

    if (this.btnToggleThinking && this.thinkingDrawer) {
      this.btnToggleThinking.addEventListener('click', () => {
        const isHidden = this.thinkingDrawer.classList.toggle('hidden');
        if (this.thinkingExpandIcon) {
          this.thinkingExpandIcon.textContent = isHidden ? 'expand_more' : 'expand_less';
        }
      });
    }

    if (this.btnOpenSentinel) {
      this.btnOpenSentinel.addEventListener('click', () => this.openSentinelModal());
    }
    if (this.btnGotoSentinelSim) {
      this.btnGotoSentinelSim.addEventListener('click', () => {
        this.closeSettingsModal();
        this.openSentinelModal();
      });
    }
    if (this.btnCloseSentinel) {
      this.btnCloseSentinel.addEventListener('click', () => this.closeSentinelModal());
    }
    if (this.btnCloseSentinelFooter) {
      this.btnCloseSentinelFooter.addEventListener('click', () => this.closeSentinelModal());
    }
    if (this.sentinelModalOverlay) {
      this.sentinelModalOverlay.addEventListener('click', (e) => {
        if (e.target === this.sentinelModalOverlay) this.closeSentinelModal();
      });
    }
    if (this.simPreset) {
      this.simPreset.addEventListener('change', () => this.applySentinelPreset());
    }
    if (this.btnTriggerSentinelEvent) {
      this.btnTriggerSentinelEvent.addEventListener('click', () => this.triggerSentinelEvent());
    }
    if (this.btnRefreshTelemetry) {
      this.btnRefreshTelemetry.addEventListener('click', () => this.fetchFlightRecorder());
    }
  }

  togglePresence() {
    const next = this.currentUserPresence === 'present' ? 'away' : 'present';
    this.setPresence(next, true);
  }

  setPresence(presence, sendToServer = false) {
    this.currentUserPresence = presence;
    const isPresent = presence === 'present';
    if (this.presenceDot) {
      this.presenceDot.className = `presence-dot ${isPresent ? 'online' : 'away'}`;
    }
    if (this.presenceText) {
      this.presenceText.textContent = isPresent ? 'Present' : 'Away';
    }
    if (this.btnTogglePresence) {
      this.btnTogglePresence.setAttribute('title', `Presence: ${isPresent ? 'Present' : 'Away'} (Click to toggle)`);
    }
    if (sendToServer) {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'user_presence', presence }));
      } else {
        const proto = (this.protocol === 'wss://' ? 'https://' : 'http://');
        fetch(`${proto}${this.host}:${this.port}/api/presence`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ presence }),
        }).catch(() => {});
      }
    }
  }

  toggleThinkingDrawer() {
    if (!this.thinkingDrawer) return;
    const isHidden = this.thinkingDrawer.classList.toggle('hidden');
    if (!isHidden) {
      if (this.thinkingDrawerLogs && this.thinkingDrawerLogs.children.length <= 1) {
        this.fetchActiveJobThoughts();
      }
    }
  }

  async fetchActiveJobThoughts() {
    try {
      const proto = (this.protocol === 'wss://' ? 'https://' : 'http://');
      const res = await fetch(`${proto}${this.host}:${this.port}/api/blackboard/jobs`);
      if (res.ok) {
        const data = await res.json();
        const jobs = data.jobs || [];
        if (jobs.length > 0 && this.thinkingDrawerLogs) {
          const latest = jobs[0];
          if (this.thinkingDrawerTicket) {
            this.thinkingDrawerTicket.textContent = `#${latest.ticket_id} (${latest.status})`;
          }
          const html = (latest.thoughts || []).map(th => {
            const tStr = new Date(th.timestamp * 1000).toLocaleTimeString([], { hour12: false });
            return `<div class="thinking-log-entry"><span class="thinking-log-time">[${tStr}]</span> <span class="thinking-log-stage">${th.stage || 'Thought'}</span> ${this.escapeHtml(th.thought)}</div>`;
          }).join('');
          if (html) this.thinkingDrawerLogs.innerHTML = html;
        }
      }
    } catch (_) {}
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
    this.fetchServerSettings();
  }

  async fetchServerSettings() {
    try {
      const url = (window.location && window.location.origin && window.location.origin.startsWith('http'))
        ? `${window.location.origin}/api/settings`
        : `${this.protocol === 'wss://' ? 'https://' : 'http://'}${this.host}:${this.port}/api/settings`;
      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      if (!data || !data.settings) return;

      const s = data.settings;
      if (this.serverDualLayerToggle) {
        this.serverDualLayerToggle.value = s.dual_layer_enabled ? 'true' : 'false';
      }
      if (this.serverSentinelsToggle) {
        this.serverSentinelsToggle.value = s.sentinels_enabled ? 'true' : 'false';
      }
      if (this.serverModelName) {
        this.serverModelName.value = s.model_name || '';
      }
      if (this.serverLayer2Model) {
        this.serverLayer2Model.value = s.layer2_model || '';
      }
    } catch (e) {
      console.debug('Failed to fetch server settings:', e);
    }
  }

  async saveServerSettings() {
    if (!this.btnSaveServerConfig) return;
    const origText = this.btnSaveServerConfig.textContent;
    this.btnSaveServerConfig.textContent = 'SAVING...';
    this.btnSaveServerConfig.disabled = true;

    try {
      const url = (window.location && window.location.origin && window.location.origin.startsWith('http'))
        ? `${window.location.origin}/api/settings`
        : `${this.protocol === 'wss://' ? 'https://' : 'http://'}${this.host}:${this.port}/api/settings`;

      const body = {
        dual_layer_enabled: this.serverDualLayerToggle ? this.serverDualLayerToggle.value === 'true' : true,
        sentinels_enabled: this.serverSentinelsToggle ? this.serverSentinelsToggle.value === 'true' : true,
        model_name: this.serverModelName ? this.serverModelName.value.trim() : '',
        layer2_model: this.serverLayer2Model ? this.serverLayer2Model.value.trim() : '',
      };

      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (res.ok) {
        this.btnSaveServerConfig.textContent = 'SAVED ✓';
        setTimeout(() => {
          this.btnSaveServerConfig.textContent = origText;
          this.btnSaveServerConfig.disabled = false;
        }, 1500);
      } else {
        const errText = await res.text();
        console.error('Server error saving settings:', res.status, errText);
        this.btnSaveServerConfig.textContent = 'ERROR ⚠️';
        setTimeout(() => {
          this.btnSaveServerConfig.textContent = origText;
          this.btnSaveServerConfig.disabled = false;
        }, 2000);
      }
    } catch (e) {
      console.error('Failed to save server settings:', e);
      this.btnSaveServerConfig.textContent = 'FAILED ⚠️';
      setTimeout(() => {
        this.btnSaveServerConfig.textContent = origText;
        this.btnSaveServerConfig.disabled = false;
      }, 2000);
    }
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

  /* ── Sentinel Simulator & Flight Recorder Interface ─────────────────────── */
  openSentinelModal() {
    if (!this.sentinelModalOverlay) return;
    this.sentinelModalOverlay.classList.remove('hidden');
    this.fetchFlightRecorder();
  }

  closeSentinelModal() {
    if (this.sentinelModalOverlay) this.sentinelModalOverlay.classList.add('hidden');
  }

  applySentinelPreset() {
    if (!this.simPreset) return;
    const val = this.simPreset.value;
    if (val === 'thermal') {
      if (this.simSentinel) this.simSentinel.value = 'system_sentinel';
      if (this.simSeverity) this.simSeverity.value = 'CRITICAL';
      if (this.simTitle) this.simTitle.value = 'CPU Overheat / Runaway Process';
      if (this.simMessage) this.simMessage.value = 'Core package reached 98°C with 99% compute utilization.';
    } else if (val === 'memory') {
      if (this.simSentinel) this.simSentinel.value = 'system_sentinel';
      if (this.simSeverity) this.simSeverity.value = 'WARN';
      if (this.simTitle) this.simTitle.value = 'Memory Saturation Warning';
      if (this.simMessage) this.simMessage.value = 'RAM utilization sustained at 92.4% for 45 seconds.';
    } else if (val === 'git') {
      if (this.simSentinel) this.simSentinel.value = 'workspace_sentinel';
      if (this.simSeverity) this.simSeverity.value = 'WARN';
      if (this.simTitle) this.simTitle.value = 'Workspace Dirty Tree / Build Divergence';
      if (this.simMessage) this.simMessage.value = 'Uncommitted local modifications detected on main branch.';
    }
  }

  async triggerSentinelEvent() {
    if (!this.btnTriggerSentinelEvent) return;
    const origText = this.btnTriggerSentinelEvent.innerHTML;
    this.btnTriggerSentinelEvent.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px; margin-right:4px;">sync</span> DISPATCHING...';
    this.btnTriggerSentinelEvent.disabled = true;

    try {
      const url = (window.location && window.location.origin && window.location.origin.startsWith('http'))
        ? `${window.location.origin}/api/events/trigger`
        : `${this.protocol === 'wss://' ? 'https://' : 'http://'}${this.host}:${this.port}/api/events/trigger`;

      const payload = {
        sentinel: this.simSentinel ? this.simSentinel.value.trim() : 'system_sentinel',
        severity: this.simSeverity ? this.simSeverity.value : 'CRITICAL',
        title: this.simTitle ? this.simTitle.value.trim() : 'Simulated Alert',
        message: this.simMessage ? this.simMessage.value.trim() : 'Test message',
      };

      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        const data = await res.json();
        if (this.simResultBox) {
          this.simResultBox.classList.remove('hidden');
          if (payload.severity === 'CRITICAL') {
            this.simResultBox.classList.add('critical-breach');
          } else {
            this.simResultBox.classList.remove('critical-breach');
          }
        }
        if (this.simResultBadge) {
          this.simResultBadge.textContent = `${payload.severity} DISPATCHED`;
          this.simResultBadge.style.backgroundColor = payload.severity === 'CRITICAL' ? 'var(--color-red)' : 'var(--color-amber)';
        }
        if (this.simResultEntryId) {
          this.simResultEntryId.textContent = `#${data.entry_id || 'rec_telemetry'}`;
        }
        if (this.simResultDiag) {
          const diag = data.diagnosis || {};
          const diagStr = typeof diag === 'string' ? diag : (diag.diagnosis || JSON.stringify(diag, null, 2));
          this.simResultDiag.textContent = `[LAYER 2 CHIEF OF STAFF ROOT-CAUSE DIAGNOSIS]\n${diagStr}`;
        }
        this.fetchFlightRecorder();
      } else {
        const err = await res.text();
        alert('Failed to trigger event: ' + err);
      }
    } catch (e) {
      console.error('Trigger sentinel error:', e);
      alert('Error triggering sentinel event: ' + e.message);
    } finally {
      this.btnTriggerSentinelEvent.innerHTML = origText;
      this.btnTriggerSentinelEvent.disabled = false;
    }
  }

  async fetchFlightRecorder() {
    if (!this.flightRecorderFeed) return;
    try {
      const url = (window.location && window.location.origin && window.location.origin.startsWith('http'))
        ? `${window.location.origin}/api/blackboard/telemetry?limit=25`
        : `${this.protocol === 'wss://' ? 'https://' : 'http://'}${this.host}:${this.port}/api/blackboard/telemetry?limit=25`;

      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      const logs = data.telemetry || [];

      if (logs.length === 0) {
        this.flightRecorderFeed.innerHTML = '<div class="task-empty-state">Flight recorder ring buffer empty.</div>';
        return;
      }

      this.flightRecorderFeed.innerHTML = logs.map(entry => {
        const timeStr = entry.timestamp ? new Date(entry.timestamp * 1000).toLocaleTimeString([], { hour12: false }) : '--:--:--';
        const sev = (entry.level || 'INFO').toUpperCase();
        const badgeClass = sev === 'CRITICAL' ? 'crit' : (sev === 'WARN' ? 'warn' : 'info');
        const sName = entry.sentinel || 'sys';
        const d = entry.data || {};
        const title = d.title || d.alert_title || d.message || `${sName} reading`;
        const detail = (d.cpu_percent !== undefined)
          ? `CPU ${d.cpu_percent}% | RAM ${d.ram_percent}%`
          : (entry.entry_id || '');

        return `
          <div class="flight-log-row">
            <div class="flight-log-left">
              <span class="flight-log-time">${timeStr}</span>
              <span class="flight-log-badge ${badgeClass}">${sev}</span>
              <span class="flight-log-title">${this.escapeHtml(title)}</span>
            </div>
            <span class="flight-log-detail">${this.escapeHtml(detail)}</span>
          </div>
        `;
      }).join('');
    } catch (e) {
      console.debug('fetchFlightRecorder error:', e);
    }
  }

  renderSentinelAlertCard(alertData) {
    this.setEmptyState(false);
    const row = document.createElement('div');
    row.className = 'msg-row msg-row-sentinel';

    const sev = (alertData.severity || 'WARN').toUpperCase();
    const isCrit = sev === 'CRITICAL';
    const colorClass = isCrit ? 'sentinel-badge-crit' : (sev === 'WARN' ? 'sentinel-badge-warn' : 'sentinel-badge-info');
    const icon = isCrit ? 'crisis_alert' : (sev === 'WARN' ? 'warning' : 'info');
    const nowStr = new Date().toLocaleTimeString([], { hour12: false });
    
    const diag = alertData.diagnosis || {};
    const diagText = typeof diag === 'string' ? diag : (diag.diagnosis || '');

    row.innerHTML = `
      <div class="sentinel-feed-card ${isCrit ? 'critical' : ''}">
        <div class="sentinel-feed-header">
          <div class="sentinel-feed-title-col">
            <span class="material-symbols-outlined sentinel-feed-icon ${colorClass}">${icon}</span>
            <span class="sentinel-feed-badge ${colorClass}">${sev} // SENTINEL EVENT</span>
            <span class="sentinel-feed-id">${this.escapeHtml(alertData.sentinel || 'system_sentinel')}</span>
          </div>
          <span class="sentinel-feed-time">${nowStr}</span>
        </div>
        <div class="sentinel-feed-title">${this.escapeHtml(alertData.title || alertData.alert_title || 'Sentinel Alert')}</div>
        <div class="sentinel-feed-msg">${this.escapeHtml(alertData.message || '')}</div>
        ${diagText ? `
          <div class="sentinel-feed-diag">
            <span class="sentinel-diag-label">LAYER 2 CHIEF OF STAFF DIAGNOSIS</span>
            <span class="sentinel-diag-text">${this.escapeHtml(diagText)}</span>
          </div>
        ` : ''}
      </div>
    `;

    this.messageFeed.appendChild(row);
    this.scrollToBottom();
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
