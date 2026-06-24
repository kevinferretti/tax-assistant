"use strict";

const $ = (id) => document.getElementById(id);
const messages = $("messages");
const input = $("input");
const composer = $("composer");
const sendBtn = $("send");

let hasW2 = false;
let streaming = false;
// Start a fresh return on every page load so a refresh never reuses a prior session.
let sessionReady = fetch("/api/session/new", { method: "POST" }).catch(() => {});

// ---------- helpers ----------
function escapeHtml(s) {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function format(text) {
  const safe = escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    // [label](url) -> link, but only safe internal/absolute URLs
    .replace(/\[([^\]]+)\]\((\/[^)\s]+|https?:\/\/[^)\s]+)\)/g,
             '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return safe.split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
}
function money(v) {
  const n = Number(v);
  if (!isFinite(n)) return "—";
  return "$" + n.toLocaleString("en-US");
}
function scrollDown() { messages.scrollTop = messages.scrollHeight; }

function addMessage(role, html) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  el.innerHTML = html;
  messages.appendChild(el);
  scrollDown();
  return el;
}

// ---------- upload card ----------
function showUploadCard() {
  const card = document.createElement("div");
  card.className = "upload-card";
  card.id = "upload-card";
  card.innerHTML = `
    <h3>Let's start with your W-2</h3>
    <p>Drop a photo or PDF of your W-2 below — I'll read it for you. (No real documents; this is a demo.)</p>
    <div class="dropzone" id="dropzone">
      <strong>Click to upload</strong> or drag your W-2 here
    </div>
    <div class="upload-actions">
      <button class="btn-secondary" id="sample-btn" type="button">Try a sample W-2</button>
      <span class="upload-or">no W-2 handy? use a realistic fake one</span>
    </div>
    <input type="file" id="file-input" accept="image/*,application/pdf" hidden />`;
  messages.appendChild(card);

  const dz = $("dropzone"), fi = $("file-input");
  dz.addEventListener("click", () => fi.click());
  fi.addEventListener("change", () => fi.files[0] && uploadW2(fi.files[0]));
  ["dragover", "dragenter"].forEach((e) => dz.addEventListener(e, (ev) => { ev.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((e) => dz.addEventListener(e, () => dz.classList.remove("drag")));
  dz.addEventListener("drop", (ev) => { ev.preventDefault(); ev.dataTransfer.files[0] && uploadW2(ev.dataTransfer.files[0]); });
  $("sample-btn").addEventListener("click", useSample);
}
function removeUploadCard() { const c = $("upload-card"); if (c) c.remove(); }

async function uploadW2(file) {
  removeUploadCard();
  const working = addWorking("Reading your W-2…");
  await sessionReady;
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/upload", { method: "POST", body: fd });
  working.remove();
  if (!r.ok) { addMessage("assistant", "<p>Sorry, I couldn't read that file. Mind trying another?</p>"); showUploadCard(); return; }
  onW2Ready();
}
async function useSample() {
  removeUploadCard();
  const working = addWorking("Loading a sample W-2…");
  await sessionReady;
  await fetch("/api/use-sample", { method: "POST" });
  working.remove();
  onW2Ready();
}
function onW2Ready() {
  hasW2 = true;
  input.disabled = false; sendBtn.disabled = false;
  input.placeholder = "Type your answer…";
  startTurn({ just_uploaded: true });
}

function addWorking(label) {
  const el = document.createElement("div");
  el.className = "working";
  el.innerHTML = `<span class="spin"></span><span>${escapeHtml(label)}</span>`;
  messages.appendChild(el); scrollDown();
  return el;
}

// ---------- chat turn ----------
async function startTurn({ message = "", just_uploaded = false } = {}) {
  if (streaming) return;
  streaming = true; sendBtn.disabled = true; input.disabled = true;

  if (message) addMessage("user", format(message));
  let bubble = null, raw = "";
  const ensureBubble = () => {
    if (!bubble) bubble = addMessage("assistant", "");
    return bubble;
  };

  try {
    const resp = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, just_uploaded }),
    });
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const part of parts) {
        const line = part.replace(/^data: /, "").trim();
        if (!line) continue;
        handleEvent(JSON.parse(line), { ensureBubble, getRaw: () => raw, setRaw: (v) => (raw = v) });
      }
    }
  } catch (e) {
    addMessage("assistant", "<p>Something interrupted us. Please try again.</p>");
  } finally {
    if (bubble) bubble.querySelector(".cursor")?.remove();
    streaming = false; input.disabled = !hasW2; sendBtn.disabled = !hasW2;
    refreshTrace();
    input.focus();
  }
}

function handleEvent(ev, ctx) {
  switch (ev.type) {
    case "token": {
      const b = ctx.ensureBubble();
      ctx.setRaw(ctx.getRaw() + ev.text);
      b.innerHTML = format(ctx.getRaw()) + '<span class="cursor"></span>';
      scrollDown();
      break;
    }
    case "question": {
      const b = ctx.ensureBubble();
      const sep = ctx.getRaw() ? "\n\n" : "";
      ctx.setRaw(ctx.getRaw() + sep + ev.text);
      b.innerHTML = format(ctx.getRaw()) + '<span class="cursor"></span>';
      scrollDown();
      break;
    }
    case "notice":
      addMessage("assistant", `<p><em>${escapeHtml(ev.message)}</em></p>`);
      break;
    case "state":
      updateLedger(ev.result);
      break;
    case "pdf":
      showDownload(ev.url, ev.filename);
      break;
    case "error":
      addMessage("assistant", `<p>${escapeHtml(ev.message)}</p>`);
      break;
  }
}

// ---------- ledger ----------
function updateLedger(r) {
  const headline = $("headline"), label = $("headline-label"), amount = $("headline-amount");
  headline.classList.remove("refund", "owe");
  if (Number(r.refund) > 0) {
    headline.classList.add("refund"); label.textContent = "Your refund"; amount.textContent = money(r.refund);
  } else if (Number(r.amount_owed) > 0) {
    headline.classList.add("owe"); label.textContent = "You owe"; amount.textContent = money(r.amount_owed);
  } else {
    label.textContent = "All settled"; amount.textContent = "$0";
  }
  const lines = [
    ["Taxable income", r.taxable_income],
    ["Total tax", r.total_tax],
    ["Total payments", r.total_payments],
  ];
  if (Number(r.child_tax_credit) > 0) lines.push(["Child tax credit", r.child_tax_credit]);
  if (Number(r.additional_child_tax_credit) > 0) lines.push(["Additional CTC (refundable)", r.additional_child_tax_credit]);
  if (Number(r.eitc) > 0) lines.push(["Earned income credit", r.eitc]);
  $("ledger-lines").innerHTML = lines
    .map(([k, v]) => `<li><span>${k}</span><span>${money(v)}</span></li>`).join("");
  $("verified").hidden = !r.verified;
}
function showDownload(url, filename) {
  const d = $("download");
  d.href = url; d.setAttribute("download", filename || "Form1040.pdf"); d.hidden = false;
}

// ---------- activity trace + budget ----------
async function refreshTrace() {
  try {
    const r = await fetch("/api/trace");
    const data = await r.json();
    updateBudget(data.questions_asked);
    renderActivity(data.events);
  } catch (_) {}
}
function updateBudget(asked) {
  const left = Math.max(0, 5 - asked);
  $("budget-label").textContent = left === 1 ? "1 question left" : `${left} questions left`;
  $("budget-dots").innerHTML = Array.from({ length: 5 }, (_, i) =>
    `<span class="dot ${i < asked ? "spent" : ""}"></span>`).join("");
}
function renderActivity(events) {
  const box = $("activity");
  box.innerHTML = events.map((e) =>
    `<div class="evt ${e.category}">
       <span class="ico">${e.icon || "•"}</span>
       <div class="body"><div class="kind">${e.kind.replace(/_/g, " ")}</div>
       <div class="summary">${escapeHtml(e.summary)}</div></div>
     </div>`).join("");
}
$("activity-toggle").addEventListener("click", () => {
  const a = $("activity"), t = $("activity-toggle");
  const open = a.hidden;
  a.hidden = !open; t.setAttribute("aria-expanded", String(open));
  if (open) refreshTrace();
});

// ---------- composer ----------
composer.addEventListener("submit", (e) => {
  e.preventDefault();
  const msg = input.value.trim();
  if (!msg || streaming) return;
  input.value = "";
  startTurn({ message: msg });
});

// ---------- boot ----------
addMessage("assistant",
  format("Hi — I'm **Form**. I'll turn your W-2 into a finished 2025 Form 1040 in a few friendly questions. Whenever you're ready 👇"));
showUploadCard();
updateBudget(0);
