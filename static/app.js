// --- Helpers & state ---
const state = { account: "all", baseTitle: document.title };
const $ = (sel, root = document) => root.querySelector(sel);
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
  return (s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// --- Sidebar (accounts) ---
function accountIconSvg() {
  // simple user-circle-ish icon
  return `<svg viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="12" cy="8" r="4"></circle><path d="M4 20a8 8 0 0 1 16 0"></path>
  </svg>`;
}

function renderAccounts(accounts) {
  const wrap = $("#accountList");
  wrap.innerHTML = accounts.map(a => `
    <a class="nav-item account" data-account="${a.key}">
      ${accountIconSvg()}${a.label} <span class="badge" data-count="${a.key}"></span>
    </a>
  `).join("");
  // click handlers
  $$(".nav-item[data-account]").forEach(a => {
    a.addEventListener("click", () => setActiveAccount(a.dataset.account));
  });
}

// --- Data loading ---
async function loadInboxCounts() {
  const res = await fetch("/api/inbox");
  const data = await res.json();

  // badges
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
  let res;
  try {
    res = await fetch(`/api/messages`);
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

  // FILTER IF USING MULTIPLE ACCOUNTS
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

async function openMessage(account, id) {
  if (!id) return;
  const res = await fetch(`/api/message/${encodeURIComponent(account)}/${encodeURIComponent(id)}?mark_read=1`);
  if (!res.ok) return;
  const msg = await res.json();

  $("#detailPane").innerHTML = `
    <article class="detail-view">
      <header class="detail-head">
        <h1>${escapeHtml(msg.subject || "")}</h1>
        <div class="meta">From ${escapeHtml(msg.sender || "")} → ${escapeHtml(msg.to || "")} • ${escapeHtml(msg.date_str || "")}${msg.account_label ? " • " + escapeHtml(msg.account_label) : ""}</div>
      </header>
      <div class="detail-body">${msg.body || ""}</div>
    </article>
  `;

  // Mark row as read
  const row = $(`.message-row[data-account="${CSS.escape(account)}"][data-id="${CSS.escape(String(id))}"]`);
  if (row) row.classList.remove("unread");

  await loadInboxCounts();
}

// --- UI behaviors ---
function setActiveAccount(account) {
  state.account = account;
  $("#mailboxTitle").textContent = `Inbox — ${account === "all" ? "All" : accountLabel(account)}`;
  $$(".nav-item[data-account]").forEach(a => a.classList.toggle("active", a.dataset.account === account));
  loadMessages(account);
}

function accountLabel(key) {
  const el = $(`.nav-item[data-account="${CSS.escape(key)}"]`);
  return el ? el.textContent.replace(/\s+\d*$/, "").trim() : key;
}

document.addEventListener("DOMContentLoaded", () => {
  // base ALL item
  $$('.nav-item[data-account="all"]').forEach(a => {
    a.addEventListener("click", () => setActiveAccount("all"));
  });

  // refresh
  const r = $("#refreshBtn");
  if (r) r.addEventListener("click", () => { spinRefresh(); loadMessages(state.account); });

  // message row (delegated)
  $("#messageList").addEventListener("click", (e) => {
    const row = e.target.closest(".message-row");
    if (!row) return;
    openMessage(row.dataset.account, row.dataset.id);  // Correctly passes the message ID
  });

  // init
  loadInboxCounts().then(() => setActiveAccount("all"));
});

// Example frontend function to send an email:
async function sendEmail(subject, body, to_email) {
    const response = await fetch('/api/send_email', {
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
    const response = await fetch('http://127.0.0.1:5000/api/messages');
    const data = await response.json();
    
    if (response.ok) {
        console.log("Fetched emails:", data);
    } else {
        console.log("Failed to fetch emails:", data.error);
    }
}
