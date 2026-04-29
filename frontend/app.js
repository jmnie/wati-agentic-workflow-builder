// Tiny vanilla-JS chat UI for the WATI agent.
// Holds session_id in localStorage; renders messages, plan previews, and execution reports.

const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("chat-input");
const sessionIdEl = document.getElementById("session-id");
const confirmBar = document.getElementById("confirm-bar");
const runBtn = document.getElementById("run-btn");
const dryRunBtn = document.getElementById("dryrun-btn");
const cancelBtn = document.getElementById("cancel-btn");
const resetBtn = document.getElementById("reset-btn");

let sessionId = localStorage.getItem("wati_session_id") || null;
updateSessionDisplay();

function updateSessionDisplay() {
  sessionIdEl.textContent = sessionId ? sessionId.slice(0, 8) + "…" : "—";
}

function setSession(id) {
  sessionId = id;
  if (id) localStorage.setItem("wati_session_id", id);
  else localStorage.removeItem("wati_session_id");
  updateSessionDisplay();
}

function addMessage(role, text, { kind, plan, report } = {}) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  if (kind === "plan_preview") div.classList.add("preview");
  if (kind === "execution") {
    if (report?.dry_run) div.classList.add("dryrun");
    else if (report?.success) div.classList.add("success");
    else div.classList.add("failure");
  }
  div.textContent = text;
  if (plan && plan.steps?.length) {
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(plan, null, 2);
    pre.style.display = "none";
    const toggle = document.createElement("button");
    toggle.textContent = "▸ raw plan JSON";
    toggle.style.marginTop = "8px";
    toggle.style.fontSize = "11px";
    toggle.onclick = () => {
      pre.style.display = pre.style.display === "none" ? "block" : "none";
      toggle.textContent = pre.style.display === "none" ? "▸ raw plan JSON" : "▾ raw plan JSON";
    };
    div.appendChild(toggle);
    div.appendChild(pre);
  }
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function postJSON(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const errText = await r.text();
    throw new Error(`HTTP ${r.status}: ${errText}`);
  }
  return r.json();
}

function showConfirmBar(show) {
  confirmBar.classList.toggle("hidden", !show);
}

async function send(message) {
  addMessage("user", message);
  inputEl.value = "";
  inputEl.disabled = true;
  try {
    const data = await postJSON("/api/chat", { message, session_id: sessionId });
    setSession(data.session_id);
    addMessage("assistant", data.text, { kind: data.kind, plan: data.plan, report: data.report });
    showConfirmBar(!!data.awaiting_confirmation);
  } catch (e) {
    addMessage("assistant", "Error: " + e.message);
  } finally {
    inputEl.disabled = false;
    inputEl.focus();
  }
}

async function confirm(action) {
  if (!sessionId) return;
  showConfirmBar(false);
  try {
    const data = await postJSON("/api/confirm", { session_id: sessionId, action });
    addMessage("assistant", data.text, { kind: data.kind, plan: data.plan, report: data.report });
    showConfirmBar(!!data.awaiting_confirmation);
  } catch (e) {
    addMessage("assistant", "Error: " + e.message);
  }
}

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = inputEl.value.trim();
  if (text) send(text);
});

runBtn.addEventListener("click", () => confirm("run"));
dryRunBtn.addEventListener("click", () => confirm("dry_run"));
cancelBtn.addEventListener("click", () => confirm("cancel"));

resetBtn.addEventListener("click", async () => {
  if (sessionId) {
    try {
      await fetch(`/api/reset?session_id=${encodeURIComponent(sessionId)}`, { method: "POST" });
    } catch {}
  }
  setSession(null);
  messagesEl.innerHTML = "";
  showConfirmBar(false);
});

document.querySelectorAll("button.example").forEach((b) =>
  b.addEventListener("click", () => {
    inputEl.value = b.textContent.trim();
    inputEl.focus();
  })
);

// Welcome banner.
addMessage(
  "assistant",
  "Hi — describe what you'd like to do in plain English. I'll show you a plan before running anything destructive."
);
