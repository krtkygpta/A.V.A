/**
 * A.V.A — Minimalist Codex-Style Web App (Desktop & Mobile Installable PWA)
 * Handles live terminal synchronization, PWA installation, tool call streaming, and slash commands.
 */

// Register Service Worker for PWA
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((err) => {
      console.log('ServiceWorker registration skipped:', err);
    });
  });
}

// Configure Marked with Highlight.js
if (window.marked) {
  marked.setOptions({
    highlight: function (code, lang) {
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(code, { language: lang }).value;
      }
      return hljs.highlightAuto(code).value;
    },
    breaks: true,
  });
}

class AVACodexApp {
  constructor() {
    this.ws = null;
    this.reconnectTimer = null;
    this.tasks = [];
    this.toolStartTimeMap = new Map();
    this.deferredInstallPrompt = null;

    // DOM Elements
    this.viewport = document.getElementById("chat-viewport");
    this.feed = document.getElementById("message-feed");
    this.input = document.getElementById("composer-input");
    this.btnSubmit = document.getElementById("btn-submit");
    this.connBadge = document.getElementById("conn-badge");
    this.connLabel = document.getElementById("conn-label");
    this.thinkingRow = document.getElementById("thinking-row");
    this.thinkingText = document.getElementById("thinking-text");
    this.taskBadge = document.getElementById("task-badge");
    this.btnScrollBottom = document.getElementById("btn-scroll-bottom");
    this.headerTitle = document.getElementById("header-title");
    this.desktopStatusText = document.getElementById("desktop-status-text");
    this.btnPwaInstall = document.getElementById("btn-pwa-install");

    // Sidebars & Panels
    this.desktopSidebar = document.getElementById("desktop-sidebar");
    this.tasksPanel = document.getElementById("tasks-panel");
    this.tasksPanelBody = document.getElementById("tasks-panel-body");
    this.drawerOverlay = document.getElementById("drawer-overlay");
    this.conversationsListEl = document.getElementById("conversations-list");

    // Attachments & Uploads
    this.composerCard = document.getElementById("composer-card");
    this.composerAttachments = document.getElementById("composer-attachments");
    this.btnAttachFile = document.getElementById("btn-attach-file");
    this.filePicker = document.getElementById("file-picker");
    this.dropzoneOverlay = document.getElementById("dropzone-overlay");
    this.imageModal = document.getElementById("image-viewer-modal");
    this.modalImgTarget = document.getElementById("modal-img-target");
    this.btnCloseModal = document.getElementById("btn-close-modal");

    // Task Live Watcher
    this.taskWatcherModal = document.getElementById("task-watcher-modal");
    this.watcherTitle = document.getElementById("watcher-title");
    this.watcherBadgeStatus = document.getElementById("watcher-badge-status");
    this.watcherBadgeSpec = document.getElementById("watcher-badge-spec");
    this.watcherBadgeSession = document.getElementById("watcher-badge-session");
    this.watcherFeed = document.getElementById("watcher-feed");
    this.watcherPulseDot = document.getElementById("watcher-pulse-dot");
    this.watcherActivityLabel = document.getElementById("watcher-activity-label");
    this.btnCloseWatcher = document.getElementById("btn-close-watcher");
    this.btnWatcherRefresh = document.getElementById("btn-watcher-refresh");
    this.btnWatcherDone = document.getElementById("btn-watcher-done");
    this.activeWatchedTaskId = null;
    this.watcherPollTimer = null;

    // State
    this.conversations = [];
    this.activeConversationId = null;
    this.pendingAttachments = [];
    window.avaApp = this;

    // Action Buttons
    this.btnMobileMenu = document.getElementById("btn-mobile-menu");
    this.btnSidebarNew = document.getElementById("btn-sidebar-new");
    this.btnMobileNew = document.getElementById("btn-mobile-new");
    this.btnToggleTasksPanel = document.getElementById("btn-toggle-tasks-panel");
    this.btnCloseTasksPanel = document.getElementById("btn-close-tasks-panel");

    this.initEventListeners();
    this.initDragDropAndPaste();
    this.initPwaInstall();
    this.fetchInitialHistory();
    this.fetchInitialConversations();
    this.connectWebSocket();
    this.startCountdownTicker();
  }

  async fetchInitialHistory() {
    try {
      const res = await fetch("/api/history");
      if (res.ok) {
        const data = await res.json();
        const list = data.history || data.messages || [];
        if (list.length > 0) {
          this.renderHistory(list);
        }
      }
    } catch (e) {
      console.log("Initial history fetch fallback:", e);
    }
  }

  async fetchInitialConversations() {
    try {
      const res = await fetch("/api/conversations");
      if (res.ok) {
        const data = await res.json();
        if (data.conversations && data.conversations.length > 0) {
          this.renderConversations(data.conversations);
        }
      }
    } catch (e) {
      console.log("Initial conversations fetch fallback:", e);
    }
  }

  initEventListeners() {
    // Send message on Enter (Shift+Enter for newline)
    this.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this.sendUserMessage();
      }
    });

    // Global Shortcuts: Ctrl+N / Cmd+N for new conversation
    window.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        this.handleNewConversation();
      }
      if (e.key === "Escape") {
        if (this.taskWatcherModal && !this.taskWatcherModal.classList.contains("hidden")) {
          this.closeTaskWatcher();
          return;
        }
        this.closeAllDrawers();
        if (this.imageModal) this.imageModal.classList.add("hidden");
      }
    });

    // Auto-grow textarea
    this.input.addEventListener("input", () => {
      this.input.style.height = "auto";
      this.input.style.height = Math.min(this.input.scrollHeight, 140) + "px";
    });

    this.btnSubmit.addEventListener("click", () => this.sendUserMessage());

    // Navigation & Command Items
    document.querySelectorAll(".nav-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        const cmd = btn.dataset.cmd;
        if (cmd) {
          if (cmd === "/tasks") {
            this.toggleTasksPanel(true);
            this.closeAllDrawers();
          } else if (cmd === "/new") {
            this.handleNewConversation();
          } else {
            this.input.value = cmd;
            this.closeAllDrawers();
            this.sendUserMessage();
          }
        }
      });
    });

    // Quick Slash Chips
    document.querySelectorAll(".slash-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const cmd = chip.dataset.cmd;
        if (cmd) {
          if (cmd === "/tasks") {
            this.toggleTasksPanel(true);
          } else if (cmd === "/new") {
            this.handleNewConversation();
          } else {
            this.input.value = cmd;
            this.sendUserMessage();
          }
        }
      });
    });

    // Panel Toggles
    if (this.btnMobileMenu) {
      this.btnMobileMenu.addEventListener("click", () => {
        this.desktopSidebar.classList.toggle("open");
        this.drawerOverlay.classList.toggle("hidden");
      });
    }

    if (this.btnToggleTasksPanel) {
      this.btnToggleTasksPanel.addEventListener("click", () => {
        this.toggleTasksPanel();
      });
    }

    if (this.btnCloseTasksPanel) {
      this.btnCloseTasksPanel.addEventListener("click", () => {
        this.toggleTasksPanel(false);
      });
    }

    if (this.drawerOverlay) {
      this.drawerOverlay.addEventListener("click", () => {
        this.closeAllDrawers();
      });
    }

    // New Chat buttons
    if (this.btnSidebarNew) this.btnSidebarNew.addEventListener("click", () => this.handleNewConversation());
    if (this.btnMobileNew) this.btnMobileNew.addEventListener("click", () => this.handleNewConversation());

    // Task Watcher buttons
    if (this.btnCloseWatcher) this.btnCloseWatcher.addEventListener("click", () => this.closeTaskWatcher());
    if (this.btnWatcherDone) this.btnWatcherDone.addEventListener("click", () => this.closeTaskWatcher());
    if (this.btnWatcherRefresh) {
      this.btnWatcherRefresh.addEventListener("click", () => {
        if (this.activeWatchedTaskId) this.fetchTaskWatch(this.activeWatchedTaskId);
      });
    }
    if (this.taskWatcherModal) {
      this.taskWatcherModal.addEventListener("click", (e) => {
        if (e.target === this.taskWatcherModal) this.closeTaskWatcher();
      });
    }

    // Scroll Detection for Floating Button
    if (this.viewport && this.btnScrollBottom) {
      this.viewport.addEventListener("scroll", () => {
        const scrollDiff = this.viewport.scrollHeight - this.viewport.scrollTop - this.viewport.clientHeight;
        if (scrollDiff > 120) {
          this.btnScrollBottom.classList.remove("hidden");
        } else {
          this.btnScrollBottom.classList.add("hidden");
        }
      });
      this.btnScrollBottom.addEventListener("click", () => this.scrollToBottom());
    }
  }

  initDragDropAndPaste() {
    // File Picker Trigger
    if (this.btnAttachFile && this.filePicker) {
      this.btnAttachFile.addEventListener("click", () => this.filePicker.click());
      this.filePicker.addEventListener("change", (e) => {
        if (e.target.files && e.target.files.length > 0) {
          this.handleFilesUpload(Array.from(e.target.files));
          this.filePicker.value = "";
        }
      });
    }

    // Drag and Drop (Window / App overlay)
    let dragCounter = 0;
    window.addEventListener("dragenter", (e) => {
      e.preventDefault();
      dragCounter++;
      if (this.dropzoneOverlay) this.dropzoneOverlay.classList.remove("hidden");
    });

    window.addEventListener("dragleave", (e) => {
      e.preventDefault();
      dragCounter--;
      if (dragCounter <= 0 && this.dropzoneOverlay) {
        this.dropzoneOverlay.classList.add("hidden");
        dragCounter = 0;
      }
    });

    window.addEventListener("dragover", (e) => {
      e.preventDefault();
    });

    window.addEventListener("drop", (e) => {
      e.preventDefault();
      dragCounter = 0;
      if (this.dropzoneOverlay) this.dropzoneOverlay.classList.add("hidden");
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        this.handleFilesUpload(Array.from(e.dataTransfer.files));
      }
    });

    // Clipboard Paste (Images or copied files)
    document.addEventListener("paste", async (e) => {
      const clipboardData = e.clipboardData || window.clipboardData;
      if (!clipboardData) return;

      const items = clipboardData.items;
      const filesToUpload = [];

      if (items) {
        for (let i = 0; i < items.length; i++) {
          if (items[i].type && (items[i].type.startsWith("image/") || items[i].kind === "file")) {
            const file = items[i].getAsFile();
            if (file) filesToUpload.push(file);
          }
        }
      }

      if (filesToUpload.length === 0 && clipboardData.files && clipboardData.files.length > 0) {
        filesToUpload.push(...Array.from(clipboardData.files));
      }

      if (filesToUpload.length > 0) {
        e.preventDefault();
        this.handleFilesUpload(filesToUpload);
      }
    });

    // Image Zoom Modal close
    if (this.imageModal) {
      this.imageModal.addEventListener("click", () => {
        this.imageModal.classList.add("hidden");
      });
    }
    if (this.btnCloseModal) {
      this.btnCloseModal.addEventListener("click", (e) => {
        e.stopPropagation();
        this.imageModal.classList.add("hidden");
      });
    }
  }

  async handleFilesUpload(files) {
    if (!files || files.length === 0) return;

    for (const file of files) {
      const isImg = file.type.startsWith("image/") || /\.(png|jpe?g|webp|gif|svg|bmp)$/i.test(file.name);
      const tempAttachment = {
        filename: file.name,
        is_image: isImg,
        loading: true,
        previewUrl: isImg ? URL.createObjectURL(file) : "",
      };

      this.pendingAttachments.push(tempAttachment);
      this.renderComposerAttachments();

      try {
        const formData = new FormData();
        formData.append("file", file);

        const res = await fetch("/api/upload", {
          method: "POST",
          body: formData,
        });

        if (!res.ok) throw new Error("Upload failed: " + res.statusText);
        const data = await res.json();

        tempAttachment.loading = false;
        tempAttachment.path = data.path;
        tempAttachment.url = data.url;
        tempAttachment.filename = data.filename;
        tempAttachment.is_image = data.is_image;
        tempAttachment.size = data.size;
      } catch (err) {
        console.error("File upload error:", err);
        tempAttachment.loading = false;
        tempAttachment.error = true;
      }

      this.renderComposerAttachments();
    }
  }

  renderComposerAttachments() {
    if (!this.composerAttachments) return;

    if (this.pendingAttachments.length === 0) {
      this.composerAttachments.classList.add("hidden");
      this.composerAttachments.innerHTML = "";
      return;
    }

    this.composerAttachments.classList.remove("hidden");
    this.composerAttachments.innerHTML = "";

    this.pendingAttachments.forEach((att, idx) => {
      const chip = document.createElement("div");
      chip.className = `composer-attach-chip ${att.is_image ? "" : "doc"}`;

      if (att.loading) {
        chip.innerHTML = `<div class="chip-spinner"></div>`;
      } else if (att.is_image) {
        chip.innerHTML = `
          <img src="${att.url || att.previewUrl}" alt="${this.escapeHtml(att.filename)}">
          <button class="chip-remove-btn" title="Remove" data-idx="${idx}">×</button>
        `;
      } else {
        chip.innerHTML = `
          <span>📄</span>
          <span class="doc-name">${this.escapeHtml(att.filename)}</span>
          <button class="chip-remove-btn" title="Remove" data-idx="${idx}">×</button>
        `;
      }

      const removeBtn = chip.querySelector(".chip-remove-btn");
      if (removeBtn) {
        removeBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          this.removeAttachment(idx);
        });
      }

      this.composerAttachments.appendChild(chip);
    });
  }

  removeAttachment(index) {
    this.pendingAttachments.splice(index, 1);
    this.renderComposerAttachments();
  }

  openImageModal(src) {
    if (!this.imageModal || !this.modalImgTarget) return;
    this.modalImgTarget.src = src;
    this.imageModal.classList.remove("hidden");
  }

  initPwaInstall() {
    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault();
      this.deferredInstallPrompt = e;
      if (this.btnPwaInstall) {
        this.btnPwaInstall.classList.remove("hidden");
        this.btnPwaInstall.addEventListener("click", async () => {
          if (this.deferredInstallPrompt) {
            this.deferredInstallPrompt.prompt();
            const { outcome } = await this.deferredInstallPrompt.userChoice;
            if (outcome === "accepted") {
              this.btnPwaInstall.classList.add("hidden");
            }
            this.deferredInstallPrompt = null;
          }
        });
      }
    });

    window.addEventListener("appinstalled", () => {
      if (this.btnPwaInstall) this.btnPwaInstall.classList.add("hidden");
      console.log("A.V.A PWA was installed successfully");
    });
  }

  toggleTasksPanel(forceState) {
    const isCurrentlyOpen = this.tasksPanel.classList.contains("open");
    const newState = forceState !== undefined ? forceState : !isCurrentlyOpen;

    if (newState) {
      this.tasksPanel.classList.add("open");
      if (window.innerWidth <= 768) {
        this.drawerOverlay.classList.remove("hidden");
      }
      this.sendAction("get_tasks");
    } else {
      this.tasksPanel.classList.remove("open");
      if (window.innerWidth <= 768 && !this.desktopSidebar.classList.contains("open")) {
        this.drawerOverlay.classList.add("hidden");
      }
    }
  }

  closeAllDrawers() {
    this.desktopSidebar.classList.remove("open");
    this.tasksPanel.classList.remove("open");
    this.drawerOverlay.classList.add("hidden");
  }

  handleNewConversation() {
    this.sendAction("new_conversation");
    this.clearChatDisplay();
    this.closeAllDrawers();
  }

  clearChatDisplay() {
    this.feed.innerHTML = `
      <div class="session-divider">
        <span>New Session Started</span>
      </div>
    `;
    this.scrollToBottom();
  }

  connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    this.setConnectionStatus("connecting");
    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      this.setConnectionStatus("online");
      clearTimeout(this.reconnectTimer);
    };

    this.ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        this.handleEvent(payload);
      } catch (err) {
        console.error("Failed to parse event:", err);
      }
    };

    this.ws.onclose = () => {
      this.setConnectionStatus("offline");
      this.reconnectTimer = setTimeout(() => this.connectWebSocket(), 2000);
    };

    this.ws.onerror = () => {
      this.ws.close();
    };
  }

  setConnectionStatus(status) {
    this.connBadge.className = `conn-badge ${status}`;
    if (status === "online") {
      this.connLabel.textContent = "online";
      if (this.desktopStatusText) this.desktopStatusText.textContent = "Ready";
    } else if (status === "connecting") {
      this.connLabel.textContent = "connecting";
      if (this.desktopStatusText) this.desktopStatusText.textContent = "Connecting...";
    } else {
      this.connLabel.textContent = "offline";
      if (this.desktopStatusText) this.desktopStatusText.textContent = "Offline";
    }
  }

  sendUserMessage() {
    const text = this.input.value.trim();
    const readyAttachments = this.pendingAttachments.filter((a) => !a.loading && !a.error);

    if (!text && readyAttachments.length === 0) return;

    const payloadAttachments = readyAttachments.map((a) => ({
      path: a.path,
      url: a.url,
      filename: a.filename,
      is_image: a.is_image,
      size: a.size,
    }));

    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({
        action: "send_text",
        text: text,
        attachments: payloadAttachments,
      }));
    } else {
      // Fallback to HTTP POST /api/send
      fetch("/api/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text, attachments: payloadAttachments }),
      }).catch(console.error);
    }

    this.input.value = "";
    this.input.style.height = "auto";
    this.pendingAttachments = [];
    this.renderComposerAttachments();
  }

  sendAction(action, data = {}) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ action: action, ...data }));
  }

  handleEvent(event) {
    const { type, data } = event;

    switch (type) {
      case "init":
        const initList = (data && (data.history || data.messages)) || [];
        if (initList.length > 0) {
          this.renderHistory(initList);
        }
        if (data && data.conversations) {
          this.renderConversations(data.conversations);
        }
        if (data && data.tasks) {
          this.updateTasks(data.tasks);
        }
        break;

      case "msg:user":
        this.appendUserMessage(data.text, data.attachments || []);
        break;

      case "msg:assistant":
        this.appendAssistantMessage(data.text);
        this.hideThinking();
        break;

      case "tool:start":
        this.appendToolStart(data.name, data.args, data.tool_id);
        break;

      case "tool:result":
        this.updateToolResult(data.name, data.result, data.tool_id, data.error);
        break;

      case "status:change":
        if (data.processing) {
          this.showThinking(data.status || "Thinking...");
          if (this.desktopStatusText) this.desktopStatusText.textContent = data.status || "Processing...";
        } else {
          this.hideThinking();
          if (this.desktopStatusText) this.desktopStatusText.textContent = "Ready";
        }
        break;

      case "msg:system":
        this.appendSystemMessage(data.text, data.priority);
        break;

      case "tasks:sync":
        if (data.tasks) {
          this.updateTasks(data.tasks);
        }
        break;

      case "conversations:sync":
        if (data.conversations) {
          this.renderConversations(data.conversations);
        }
        break;

      case "event:conversation_switched":
        this.activeConversationId = data.conversation_id;
        if (data.name && this.headerTitle) {
          this.headerTitle.textContent = data.name;
        }
        if (data.history) {
          this.renderHistory(data.history);
        }
        this.updateActiveConvHighlight();
        this.closeAllDrawers();
        break;

      case "event:new_conversation":
        this.clearChatDisplay();
        if (this.headerTitle) this.headerTitle.textContent = "A.V.A Portal";
        this.fetchInitialConversations();
        break;

      case "event:clear_chat":
        this.clearChatDisplay();
        break;
    }
  }

  renderConversations(convList) {
    this.conversations = convList || [];
    if (!this.conversationsListEl) return;

    if (this.conversations.length === 0) {
      this.conversationsListEl.innerHTML = `<div class="conv-empty-hint">No past conversations</div>`;
      return;
    }

    this.conversationsListEl.innerHTML = "";
    for (const conv of this.conversations) {
      const item = document.createElement("button");
      item.className = `conv-item ${conv.is_current || conv.id === this.activeConversationId ? 'active' : ''}`;
      item.dataset.id = conv.id;

      const title = conv.name && conv.name !== "New Conversation" ? conv.name : `Chat ${conv.date || conv.id.slice(0, 8)}`;
      const summary = conv.summary || (conv.message_count ? `${conv.message_count} messages` : "");

      item.innerHTML = `
        <div class="conv-item-top">
          <span class="conv-name">${this.escapeHtml(title)}</span>
          <span class="conv-date">${this.escapeHtml(conv.time || conv.date || '')}</span>
        </div>
        ${summary ? `<div class="conv-summary">${this.escapeHtml(summary)}</div>` : ''}
      `;

      item.addEventListener("click", () => {
        this.switchConversation(conv.id);
      });

      this.conversationsListEl.appendChild(item);
    }
  }

  async switchConversation(convId) {
    if (!convId) return;
    this.activeConversationId = convId;
    this.updateActiveConvHighlight();

    // Send switch event over WebSocket
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action: "switch_conversation", conversation_id: convId }));
    } else {
      // Fallback to HTTP API
      try {
        const res = await fetch(`/api/conversations/${encodeURIComponent(convId)}/select`, { method: "POST" });
        if (res.ok) {
          const data = await res.json();
          if (data.history) this.renderHistory(data.history);
          if (data.name && this.headerTitle) this.headerTitle.textContent = data.name;
        }
      } catch (e) {
        console.error("Failed to switch conversation:", e);
      }
    }
  }

  updateActiveConvHighlight() {
    if (!this.conversationsListEl) return;
    const items = this.conversationsListEl.querySelectorAll(".conv-item");
    items.forEach((it) => {
      if (it.dataset.id === this.activeConversationId) {
        it.classList.add("active");
      } else {
        it.classList.remove("active");
      }
    });
  }

  renderHistory(historyList) {
    this.feed.innerHTML = `
      <div class="session-divider">
        <span>Conversation Loaded</span>
      </div>
    `;

    for (const msg of historyList) {
      if (msg.role === "user") {
        this.appendUserMessage(msg.content, msg.attachments || []);
      } else if (msg.role === "assistant") {
        this.appendAssistantMessage(msg.content);
      } else if (msg.role === "tool") {
        const toolName = msg.name || "tool";
        const toolArgs = msg.args || {};
        this.appendToolStart(toolName, toolArgs, msg.tool_call_id);
        this.updateToolResult(toolName, msg.content, msg.tool_call_id, false);
      }
    }
  }

  appendUserMessage(text, attachments = []) {
    let clean = text || "";
    const colonIdx = clean.indexOf(":");
    if (colonIdx > 0 && colonIdx < 15 && !clean.startsWith("http")) {
      clean = clean.substring(colonIdx + 1).trim();
    }

    // Extract any embedded local paths or uploaded files if attachments were empty
    let displayAttachments = [...attachments];
    if (displayAttachments.length === 0 && clean) {
      const pathRegex = /(?:(?:[a-zA-Z]:\\[^\n\r]+)|(?:\/uploads\/[^\s\n\r]+))/g;
      const matchedPaths = clean.match(pathRegex);
      if (matchedPaths) {
        for (const p of matchedPaths) {
          const isImg = /\.(png|jpe?g|webp|gif|svg|bmp)$/i.test(p);
          const fname = p.split(/[\\/]/).pop();
          const webUrl = p.startsWith("/uploads") ? p : `/uploads/${fname}`;
          displayAttachments.push({
            path: p,
            url: webUrl,
            filename: fname,
            is_image: isImg,
          });
          clean = clean.replace(p, "").replace(/\[Attached (?:File|Document):?\s*\]/gi, "").trim();
        }
      }
    }

    if (clean === "Please inspect the attached file(s):" && displayAttachments.length > 0) {
      clean = "";
    }

    const row = document.createElement("div");
    row.className = "msg-user-row";

    let attachmentsHtml = "";
    if (displayAttachments.length > 0) {
      attachmentsHtml = `<div class="msg-attachment-grid">`;
      for (const att of displayAttachments) {
        if (att.is_image) {
          attachmentsHtml += `<img src="${this.escapeHtml(att.url)}" alt="${this.escapeHtml(att.filename || 'image')}" class="msg-attachment-img" onclick="window.avaApp.openImageModal(this.src)">`;
        } else {
          attachmentsHtml += `<a href="${this.escapeHtml(att.url)}" download="${this.escapeHtml(att.filename || 'document')}" class="msg-attachment-doc" target="_blank">📄 ${this.escapeHtml(att.filename || 'Download')}</a>`;
        }
      }
      attachmentsHtml += `</div>`;
    }

    const textHtml = clean ? `<div class="msg-user-bubble">${this.escapeHtml(clean)}</div>` : "";

    row.innerHTML = `<div class="msg-user-group">${attachmentsHtml}${textHtml}</div>`;
    this.feed.appendChild(row);
    this.scrollToBottom();
  }

  appendAssistantMessage(text) {
    if (!text || !text.trim()) return;

    const row = document.createElement("div");
    row.className = "msg-assistant-row";

    let renderedContent = text;
    if (window.marked) {
      try {
        renderedContent = marked.parse(text);
      } catch (e) {
        renderedContent = `<p>${this.escapeHtml(text)}</p>`;
      }
    }

    row.innerHTML = renderedContent;
    this.feed.appendChild(row);
    this.scrollToBottom();
  }

  formatToolCommand(name, args) {
    let cleanName = name || "tool";
    if (cleanName === "tool_call") cleanName = "tool";

    // Handle string or JSON object args
    let parsedArgs = args;
    if (typeof args === "string" && args.trim().startsWith("{")) {
      try { parsedArgs = JSON.parse(args); } catch (e) {}
    }

    if (typeof parsedArgs === "object" && parsedArgs !== null) {
      if (cleanName === "view_image") {
        const p = parsedArgs.image_path || parsedArgs.filename || parsedArgs.path || "";
        const fname = String(p).split(/[\\/]/).pop() || "image";
        return `view_image · ${fname}`;
      }
      if (cleanName === "bash" || cleanName === "execute_command" || cleanName === "run_command") {
        return `terminal · ${parsedArgs.command || ""}`;
      }
      if (cleanName === "web_search" || cleanName === "search") {
        return `search · "${parsedArgs.query || ""}"`;
      }
      if (cleanName === "get_time_date") {
        return `time · current_time()`;
      }
      if (parsedArgs.query) {
        return `${cleanName} · "${parsedArgs.query}"`;
      }
      if (parsedArgs.path || parsedArgs.file_path) {
        const fname = String(parsedArgs.path || parsedArgs.file_path).split(/[\\/]/).pop();
        return `${cleanName} · ${fname}`;
      }
      const keys = Object.keys(parsedArgs);
      if (keys.length === 0) return `${cleanName}()`;
      const firstVal = String(parsedArgs[keys[0]]);
      const cleanVal = firstVal.length > 25 ? firstVal.slice(0, 25) + "..." : firstVal;
      return `${cleanName} · ${keys[0]}=${cleanVal}`;
    }

    const strArgs = String(args || "");
    if (!strArgs || strArgs === "{}" || strArgs === "None" || strArgs === "") {
      return `${cleanName}()`;
    }
    const cleanStr = strArgs.replace(/\\\\/g, "/").replace(/"/g, "").slice(0, 40);
    return `${cleanName} · ${cleanStr}`;
  }

  appendToolStart(toolName, args, toolId) {
    const id = toolId || `tool_${Date.now()}`;
    this.toolStartTimeMap.set(id, performance.now());

    const pill = document.createElement("div");
    pill.className = "tool-pill";
    pill.id = `pill_${id}`;

    const cmdText = this.formatToolCommand(toolName, args);
    const argsFormatted = typeof args === "object" ? JSON.stringify(args, null, 2) : String(args || "");

    pill.innerHTML = `
      <div class="tool-pill-header" onclick="this.parentElement.classList.toggle('expanded')">
        <div class="tool-pill-left">
          <span class="term-prompt">&gt;_</span>
          <span class="tool-cmd">${this.escapeHtml(cmdText)}</span>
        </div>
        <div class="tool-pill-right">
          <span class="tool-status-tag running" id="tag_${id}">
            <div class="spinner-icon"></div>
          </span>
          <span class="chevron">&rsaquo;</span>
        </div>
      </div>
      <div class="tool-pill-body">
        <div class="tool-body-section">
          <div class="tool-body-label">Arguments:</div>
          <div class="tool-body-code">${this.escapeHtml(argsFormatted || '(none)')}</div>
        </div>
        <div class="tool-body-section" id="res_sec_${id}">
          <div class="tool-body-label">Output:</div>
          <div class="tool-body-code" id="res_${id}">running...</div>
        </div>
      </div>
    `;

    this.feed.appendChild(pill);
    this.scrollToBottom();
  }

  updateToolResult(toolName, result, toolId, isError) {
    let pill = toolId ? document.getElementById(`pill_${toolId}`) : null;
    if (!pill) {
      const pills = this.feed.querySelectorAll(".tool-pill");
      if (pills.length > 0) {
        pill = pills[pills.length - 1];
      }
    }

    if (pill) {
      const id = pill.id.replace("pill_", "");
      const startTime = this.toolStartTimeMap.get(id);
      const elapsedMs = startTime ? Math.round(performance.now() - startTime) : 45;

      const tag = pill.querySelector(".tool-status-tag");
      const resCode = pill.querySelector(`#res_${id}`) || pill.querySelector(".tool-body-code:last-of-type");

      if (tag) {
        tag.className = `tool-status-tag ${isError ? 'error' : 'done'}`;
        tag.innerHTML = isError ? `✕ failed` : `✓ ${elapsedMs}ms`;
      }

      if (resCode) {
        resCode.textContent = result || "done";
      }
    }
  }

  appendSystemMessage(text, priority) {
    const line = document.createElement("div");
    line.className = `msg-system-line ${priority === 'immediate' ? 'immediate' : ''}`;
    line.textContent = `· ${text}`;
    this.feed.appendChild(line);
    this.scrollToBottom();
  }

  showThinking(label = "Thinking...") {
    this.thinkingText.textContent = label;
    this.thinkingRow.classList.remove("hidden");
    this.scrollToBottom();
  }

  hideThinking() {
    this.thinkingRow.classList.add("hidden");
  }

  updateTasks(tasksList) {
    this.tasks = tasksList || [];
    const runningCount = this.tasks.filter((t) => t.is_running).length;

    if (runningCount > 0) {
      this.taskBadge.textContent = runningCount;
      this.taskBadge.classList.remove("hidden");
    } else {
      this.taskBadge.classList.add("hidden");
    }

    if (this.tasks.length === 0) {
      this.tasksPanelBody.innerHTML = `
        <div class="tasks-empty-state">
          <p>No active background tasks</p>
          <span>Spawn timers, deep research, or coding missions on AVA to monitor them live.</span>
        </div>
      `;
      return;
    }

    this.tasksPanelBody.innerHTML = "";
    for (const task of this.tasks) {
      const card = document.createElement("div");
      card.className = "task-item-card";
      card.dataset.taskId = task.id;

      let countdownText = "";
      if (task.remaining_seconds !== null && task.remaining_seconds !== undefined) {
        const mins = Math.floor(task.remaining_seconds / 60);
        const secs = task.remaining_seconds % 60;
        countdownText = `<div class="task-card-countdown">⏱ ${mins}m ${secs < 10 ? '0' : ''}${secs}s remaining</div>`;
      }

      const isRunning = task.status === "running" || task.is_running;
      const hintText = isRunning ? "👁 Click to watch live process" : "👁 Click to view process history";

      card.innerHTML = `
        <div class="task-card-top">
          <span class="task-card-name">${this.escapeHtml(task.name)}</span>
          <span class="task-card-tag ${task.status}">${task.status}</span>
        </div>
        ${countdownText}
        <div class="task-card-desc">${this.escapeHtml(task.summary || task.result || 'Task in progress...')}</div>
        <div class="task-card-action-hint">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
            <circle cx="12" cy="12" r="3"></circle>
          </svg>
          <span>${hintText}</span>
        </div>
      `;

      card.addEventListener("click", () => {
        this.openTaskWatcher(task.id);
      });

      this.tasksPanelBody.appendChild(card);
    }
  }

  openTaskWatcher(taskId) {
    this.activeWatchedTaskId = taskId;
    if (this.taskWatcherModal) {
      this.taskWatcherModal.classList.remove("hidden");
    }
    if (this.watcherFeed) {
      this.watcherFeed.innerHTML = `
        <div class="watcher-bubble system">
          <div class="watcher-bubble-header">Connecting...</div>
          <div>Loading agent process stream...</div>
        </div>
      `;
    }
    this.fetchTaskWatch(taskId);

    if (this.watcherPollTimer) clearInterval(this.watcherPollTimer);
    this.watcherPollTimer = setInterval(() => {
      if (this.activeWatchedTaskId) {
        this.fetchTaskWatch(this.activeWatchedTaskId, true);
      }
    }, 1500);
  }

  closeTaskWatcher() {
    this.activeWatchedTaskId = null;
    if (this.watcherPollTimer) {
      clearInterval(this.watcherPollTimer);
      this.watcherPollTimer = null;
    }
    if (this.taskWatcherModal) {
      this.taskWatcherModal.classList.add("hidden");
    }
  }

  async fetchTaskWatch(taskId, isPoll = false) {
    try {
      const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/watch`);
      if (!res.ok) return;
      const data = await res.json();
      this.renderTaskWatch(data);
      if (!data.is_running && this.watcherPollTimer) {
        clearInterval(this.watcherPollTimer);
        this.watcherPollTimer = null;
      }
    } catch (e) {
      if (!isPoll) console.error("Task watch fetch error:", e);
    }
  }

  renderTaskWatch(data) {
    if (!this.taskWatcherModal || this.taskWatcherModal.classList.contains("hidden")) return;
    if (this.watcherTitle) this.watcherTitle.textContent = data.name || "Task Process";
    if (this.watcherBadgeStatus) {
      this.watcherBadgeStatus.textContent = (data.status || "unknown").toUpperCase();
      this.watcherBadgeStatus.className = `watcher-badge status ${data.status || ''}`;
    }
    if (this.watcherBadgeSpec) {
      if (data.specialist) {
        this.watcherBadgeSpec.textContent = `Specialist: ${data.specialist}`;
        this.watcherBadgeSpec.classList.remove("hidden");
      } else {
        this.watcherBadgeSpec.classList.add("hidden");
      }
    }
    if (this.watcherBadgeSession) {
      if (data.session_id) {
        this.watcherBadgeSession.textContent = data.session_id;
        this.watcherBadgeSession.classList.remove("hidden");
      } else {
        this.watcherBadgeSession.classList.add("hidden");
      }
    }
    if (this.watcherPulseDot) {
      this.watcherPulseDot.className = `watcher-pulse-dot ${data.is_running ? 'running' : ''}`;
    }
    if (this.watcherActivityLabel) {
      this.watcherActivityLabel.textContent = data.current_activity || (data.is_running ? "Agent running..." : "Completed");
    }

    if (!this.watcherFeed) return;

    const messages = data.messages || [];
    if (messages.length === 0) {
      this.watcherFeed.innerHTML = `
        <div class="watcher-bubble system">
          <div class="watcher-bubble-header">System</div>
          <div>Waiting for agent to produce outputs...</div>
        </div>
      `;
      return;
    }

    let html = "";
    for (const msg of messages) {
      const role = msg.role || "unknown";
      const content = msg.content || "";
      const toolCalls = msg.tool_calls || [];
      const toolName = msg.name || "";

      if (role === "system") {
        html += `
          <div class="watcher-bubble system">
            <div class="watcher-bubble-header">⚙ System Specialist</div>
            <div>${this.escapeHtml(content.slice(0, 300))}${content.length > 300 ? '...' : ''}</div>
          </div>
        `;
      } else if (role === "user") {
        html += `
          <div class="watcher-bubble user">
            <div class="watcher-bubble-header">🎯 Delegated Objective</div>
            <div style="white-space: pre-wrap;">${this.escapeHtml(content)}</div>
          </div>
        `;
      } else if (role === "assistant") {
        let callBlocks = "";
        if (toolCalls && toolCalls.length > 0) {
          for (const tc of toolCalls) {
            const fName = tc.function ? tc.function.name : (tc.name || "tool");
            const fArgs = tc.function ? tc.function.arguments : "";
            callBlocks += `
              <div style="margin-top: 8px; font-family: var(--font-mono); font-size: 0.77rem; color: #fbbf24;">
                ▶ Invoking Tool: <strong>${this.escapeHtml(fName)}</strong>
                ${fArgs ? `<div class="watcher-tool-content">${this.escapeHtml(typeof fArgs === 'string' ? fArgs : JSON.stringify(fArgs, null, 2))}</div>` : ''}
              </div>
            `;
          }
        }
        html += `
          <div class="watcher-bubble assistant">
            <div class="watcher-bubble-header">AVA Delegate Agent</div>
            ${content ? `<div style="white-space: pre-wrap;">${this.escapeHtml(content)}</div>` : ''}
            ${callBlocks}
          </div>
        `;
      } else if (role === "tool") {
        html += `
          <div class="watcher-bubble tool">
            <div class="watcher-bubble-header">⚡ Tool Output${toolName ? ` (${this.escapeHtml(toolName)})` : ''}</div>
            <div class="watcher-tool-content">${this.escapeHtml(typeof content === 'string' ? content : JSON.stringify(content, null, 2))}</div>
          </div>
        `;
      }
    }

    this.watcherFeed.innerHTML = html;
  }

  startCountdownTicker() {
    setInterval(() => {
      let changed = false;
      for (const t of this.tasks) {
        if (t.is_running && t.remaining_seconds && t.remaining_seconds > 0) {
          t.remaining_seconds -= 1;
          changed = true;
        }
      }
      if (changed && this.tasksPanel.classList.contains("open")) {
        this.updateTasks(this.tasks);
      }
    }, 1000);
  }

  scrollToBottom() {
    this.viewport.scrollTop = this.viewport.scrollHeight;
  }

  escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
}

// Launch Codex App on page load
document.addEventListener("DOMContentLoaded", () => {
  window.avaApp = new AVACodexApp();
});
