// --- Helpers & state ---
const state = {
  account: "all",
  folder: "INBOX",
  baseTitle: document.title,
  selectedMessage: null, // currently opened / selected mail
  lastDeleted: null,     // info for "restore last deleted"
};

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function spinRefresh() {
  const icon = $("#refreshBtn .refresh-icon");
  if (!icon) return;
  icon.classList.remove("spin");
  void icon.offsetWidth;
  icon.classList.add("spin");
}

function setTitleUnread(total) {
  document.title = total > 0 ? `(${total}) ${state.baseTitle}` : state.baseTitle;
}

function escapeHtml(s) {
  return (s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatBytes(bytes) {
  const n = Number(bytes);
  if (!n || isNaN(n)) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = n;
  let u = 0;
  while (value >= 1024 && u < units.length - 1) {
    value /= 1024;
    u++;
  }
  const fixed = value >= 10 || u === 0 ? value.toFixed(0) : value.toFixed(1);
  return `${fixed} ${units[u]}`;
}

// --- Sidebar (accounts) ---
function accountIconSvg() {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="12" cy="8" r="4"></circle>
    <path d="M4 20a8 8 0 0 1 16 0"></path>
  </svg>`;
}

function renderAccounts(accounts) {
  const wrap = $("#accountList");
  wrap.innerHTML = accounts.map(a => `
    <a class="nav-item account" data-account="${a.key}">
      ${accountIconSvg()}${a.label}
      <span class="badge" data-count="${a.key}"></span>
    </a>
  `).join("");

  $$(".nav-item[data-account]").forEach(a => {
    a.addEventListener("click", () => setActiveAccount(a.dataset.account));
  });
}

// --- Sidebar (folders) ---
function folderIconSvg() {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M3 7h5l2 2h11v9a2 2 0 0 1-2 2H3z"></path>
    <path d="M3 7V5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2"></path>
  </svg>`;
}

function renderFolders(folders) {
  const wrap = $("#folderList");
  if (!wrap) return;

  wrap.innerHTML = folders.map(f => `
    <a class="nav-item folder-item" data-folder="${escapeHtml(f.key)}">
      ${folderIconSvg()}
      ${escapeHtml(f.label || f.key)}
      <span class="badge" data-folder-count="${escapeHtml(f.key)}">
        ${f.count ? String(f.count) : ""}
      </span>
    </a>
  `).join("");

  $$(".folder-item").forEach(a => {
    a.addEventListener("click", () => setActiveFolder(a.dataset.folder));
  });
}

async function loadFolders() {
  try {
    const res = await fetch("/api/folders");
    const data = await res.json();
    if (data && Array.isArray(data.folders)) {
      renderFolders(data.folders);
    }
  } catch (err) {
    console.error("Failed to load folders", err);
  }
}

// --- Data loading ---
async function loadInboxCounts() {
  const res = await fetch("/api/inbox");
  const data = await res.json();

  const totalUnread = data.all.unread || 0;
  setTitleUnread(totalUnread);

  const allBadge = $('.badge[data-count="all"]');
  if (allBadge) allBadge.textContent = data.all.count ? data.all.count : "";

  renderAccounts(data.accounts);
  data.accounts.forEach(a => {
    const b = $(`.badge[data-count="${CSS.escape(a.key)}"]`);
    if (b) b.textContent = a.count ? a.count : "";
  });
}

async function loadMessages(account) {
  const folder = state.folder || "INBOX";
  let res;
  try {
    res = await fetch(`/api/messages?folder=${encodeURIComponent(folder)}`);
  } catch (err) {
    $("#messageList").innerHTML = '<li class="empty-state">Failed to fetch.</li>';
    return;
  }

  let msgs;
  try {
    msgs = await res.json();
  } catch (err) {
    $("#messageList").innerHTML = '<li class="empty-state">No Mail</li>';
    return;
  }

  if (!Array.isArray(msgs)) {
    $("#messageList").innerHTML = '<li class="empty-state">No Mail</li>';
    return;
  }

  msgs = msgs.filter(m => (account === "all" || m.account === account));

  const list = $("#messageList");
  const count = $("#messageCount");

  list.classList.toggle("empty", msgs.length === 0);
  list.innerHTML = msgs.length
    ? msgs.map(m => `
        <li class="message-row ${m.unread ? "unread" : ""}"
            data-id="${m.id}" data-account="${m.account}">
          <div class="top">
            <span class="dot"></span>
            <span class="sender">${escapeHtml(m.sender || "")}</span>
            <span class="meta">${escapeHtml(m.date_str || "")}</span>
          </div>
          <div class="subject">${escapeHtml(m.subject || "")}</div>
          <div class="preview">${escapeHtml(m.preview || "")}</div>
        </li>
      `).join("")
    : '<li class="empty-state">No Mail</li>';

  count.textContent = `${msgs.length} ${msgs.length === 1 ? "Message" : "Messages"}`;
}

// --- Detail view / attachments ---
async function openMessage(account, id) {
  if (!id) return;

  const folder = state.folder || "INBOX";
  state.selectedMessage = { account, id };

  const res = await fetch(
    `/api/message/${encodeURIComponent(account)}/${encodeURIComponent(id)}?mark_read=1&folder=${encodeURIComponent(folder)}`
  );
  if (!res.ok) return;

  const msg = await res.json();
  const attachments = Array.isArray(msg.attachments) ? msg.attachments : [];

  const attachmentsHtml = attachments.length
    ? `
      <div class="attachment-bar" aria-label="Attachments">
        ${attachments.map(a => {
          const url =
            `/api/message/${encodeURIComponent(account)}/${encodeURIComponent(id)}/attachment/${encodeURIComponent(a.index)}?folder=${encodeURIComponent(folder)}`;
          return `
            <a class="attachment-pill"
               href="${url}"
               title="${escapeHtml(a.content_type || "")}">
              <span class="attachment-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24">
                  <path d="M7 13.5l5.3-5.3a3 3 0 1 1 4.2 4.2L10 18a4 4 0 0 1-5.7-5.7l6.4-6.4"></path>
                </svg>
              </span>
              <span class="attachment-name">${escapeHtml(a.filename || "attachment")}</span>
              ${a.size ? `<span class="attachment-size">${escapeHtml(formatBytes(a.size))}</span>` : ""}
            </a>
          `;
        }).join("")}
      </div>
    `
    : "";

  $("#detailPane").innerHTML = `
    <article class="detail-view">
      <header class="detail-head">
        <h1>${escapeHtml(msg.subject || "")}</h1>
        <div class="meta">
          From ${escapeHtml(msg.sender || "")}
          → ${escapeHtml(msg.to || "")}
          • ${escapeHtml(msg.date_str || "")}
          ${msg.account_label ? " • " + escapeHtml(msg.account_label) : ""}
        </div>
      </header>
      <div class="detail-body">
        ${attachmentsHtml}
        <div class="detail-body-content">
          ${msg.body || ""}
        </div>
      </div>
    </article>
  `;

  // mark row as read + selected
  $$(".message-row").forEach(row => row.classList.remove("selected"));
  const row = $(`.message-row[data-account="${CSS.escape(account)}"][data-id="${CSS.escape(String(id))}"]`);
  if (row) {
    row.classList.remove("unread");
    row.classList.add("selected");
  }

  await loadInboxCounts();
  await loadFolders();
}

// --- Delete / Restore ---
async function deleteSelectedMessage() {
  const sel = state.selectedMessage;
  if (!sel) return;

  const folder = state.folder || "INBOX";

  const res = await fetch(
    `/api/message/${encodeURIComponent(sel.account)}/${encodeURIComponent(sel.id)}/delete?folder=${encodeURIComponent(folder)}`,
    { method: "POST" }
  );

  let info = null;
  try {
    info = await res.json();
  } catch (e) {
    // ignore JSON parse errors
  }

  if (!res.ok) {
    console.error("Failed to delete message", info && info.error);
    return;
  }

  if (info && info.restorable !== false && info.message_id) {
    state.lastDeleted = {
      account: sel.account,
      id: sel.id,
      from_folder: info.from_folder || folder,
      trash_folder: info.trash_folder || "Gel&APY-scht",
      message_id: info.message_id,
    };
  } else {
    state.lastDeleted = null;
  }

  const row = $(`.message-row[data-account="${CSS.escape(sel.account)}"][data-id="${CSS.escape(String(sel.id))}"]`);
  if (row && row.parentElement) {
    row.parentElement.removeChild(row);
  }

  state.selectedMessage = null;
  $("#detailPane").innerHTML = `<div class="placeholder"><p>No Message Selected</p></div>`;

  await loadInboxCounts();
  await loadFolders();
  await loadMessages(state.account);
}

async function restoreLastDeleted() {
  const info = state.lastDeleted;
  if (!info || !info.message_id) return;

  const res = await fetch(
    `/api/message/${encodeURIComponent(info.account)}/${encodeURIComponent(info.id)}/restore`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        from_folder: info.from_folder,
        trash_folder: info.trash_folder,
        message_id: info.message_id,
      }),
    }
  );

  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    // ignore
  }

  if (!res.ok) {
    console.error("Failed to restore message", data && data.error);
    return;
  }

  const originalFolder = info.from_folder;
  state.lastDeleted = null;

  await loadInboxCounts();
  await loadFolders();
  if (state.folder === originalFolder) {
    await loadMessages(state.account);
  }
}

// --- UI behaviors ---
function setActiveAccount(account) {
  state.account = account;

  $$(".nav-item[data-account]").forEach(a => {
    a.classList.toggle("active", a.dataset.account === account);
  });

  if (typeof setActiveFolder === "function") {
    setActiveFolder("INBOX");
  } else {
    $("#mailboxTitle").textContent =
      `Inbox — ${account === "all" ? "All" : accountLabel(account)}`;
    loadMessages(account);
  }
}

function folderLabel(key) {
  const el = $(`.folder-item[data-folder="${CSS.escape(key)}"]`);
  return el ? el.textContent.replace(/\s+\d*$/, "").trim() : key;
}

function setActiveFolder(folderKey) {
  state.folder = folderKey || "INBOX";

  const accountName = state.account === "all"
    ? "All accounts"
    : accountLabel(state.account);

  $("#mailboxTitle").textContent =
    `${folderLabel(state.folder)} — ${accountName}`;

  $$(".folder-item").forEach(a => {
    a.classList.toggle("active", a.dataset.folder === state.folder);
  });

  loadMessages(state.account);
}

function accountLabel(key) {
  const el = $(`.nav-item[data-account="${CSS.escape(key)}"]`);
  return el ? el.textContent.replace(/\s+\d*$/, "").trim() : key;
}

// --- Init ---
document.addEventListener("DOMContentLoaded", () => {
  // "All" account
  $$('.nav-item[data-account="all"]').forEach(a => {
    a.addEventListener("click", () => setActiveAccount("all"));
  });

  // refresh
  const r = $("#refreshBtn");
  if (r) {
    r.addEventListener("click", () => {
      spinRefresh();
      loadMessages(state.account);
      loadFolders();
    });
  }

  // delete button
  const del = $("#deleteBtn");
  if (del) {
    del.addEventListener("click", () => {
      deleteSelectedMessage();
    });
  }

  // restore button
  const restore = $("#restoreBtn");
  if (restore) {
    restore.addEventListener("click", () => {
      restoreLastDeleted();
    });
  }

  // keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    const isMac = navigator.platform.toUpperCase().includes("MAC");

    // DELETE / BACKSPACE -> delete selected message
    if (state.selectedMessage && (e.key === "Delete" || e.key === "Backspace")) {
      e.preventDefault();
      deleteSelectedMessage();
      return;
    }

    // UNDO: Cmd+Z on macOS, Ctrl+Z on others
    const isZ = e.key === "z" || e.key === "Z";
    const undoCombo =
      (isMac && e.metaKey && !e.ctrlKey && isZ) ||  // ⌘+Z
      (!isMac && e.ctrlKey && isZ);                 // Ctrl+Z

    if (undoCombo) {
      e.preventDefault();
      restoreLastDeleted();
    }
  });

  // click to open message
  $("#messageList").addEventListener("click", (e) => {
    const row = e.target.closest(".message-row");
    if (!row) return;
    openMessage(row.dataset.account, row.dataset.id);
  });

  // initial load
  (async () => {
    await Promise.all([loadInboxCounts(), loadFolders()]);

    setActiveAccount("all");
    const inboxEl =
      document.querySelector('.folder-item[data-folder="INBOX"]') ||
      document.querySelector(".folder-item");
    if (inboxEl) {
      setActiveFolder(inboxEl.dataset.folder);
    } else {
      loadMessages(state.account);
    }
  })();
});

// Example frontend function to send an email:
async function sendEmail(subject, body, to_email) {
  const response = await fetch('/api/send', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ subject, body, to_email })
  });

  const data = await response.json();

  if (response.ok) {
    alert("Email sent successfully!");
  } else {
    alert("Failed to send email: " + data.error);
  }
}

async function fetchEmails() {
  const response = await fetch('/api/messages');
  const data = await response.json();

  if (response.ok) {
    console.log("Fetched emails:", data);
  } else {
    console.log("Failed to fetch emails:", data.error);
  }
}
