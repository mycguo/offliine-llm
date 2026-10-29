// Survival LLM front end: chat with a local model, show what it searched and read, open its sources.
const token = document.querySelector('meta[name="token"]').content;
const $ = (id) => document.getElementById(id);
const els = {
  status: $("status"), modelName: $("model-name"), messages: $("messages"), welcome: $("welcome"),
  using: $("using"), setup: $("setup"), composer: $("composer"), composerNote: $("composer-note"), input: $("input"), send: $("send"),
  reasoning: $("reasoning"), length: $("length"), layout: $("layout"), reader: $("reader"), readerBody: $("reader-body"),
  search: $("search"), searchForm: $("search-form"), dialog: $("models"), modelsList: $("models-list"),
  modelsIntro: $("models-intro"), announce: $("announce"), libraryBtn: $("library-btn"),
};
let state = null;          // last /api/status
let currentModel = null;   // Ollama name of the model answering
let conversation = null;
let busy = null;           // AbortController while an answer streams
let pollTimer = null;
let readerRequest = 0;     // ignore stale article loads
let lastFocus = null;      // where focus returns when the reader closes
let restarting = false;    // an update is installing; the app will restart
const narrow = () => window.matchMedia("(max-width: 820px)").matches;

class StaleTab extends Error {}

async function api(path, opts = {}) {
  const resp = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", "X-Token": token, ...opts.headers } });
  if (resp.status === 403) { showStale(); throw new StaleTab("stale"); }
  return resp;
}
function showStale() {
  if ($("stale")) return;
  document.body.insertAdjacentHTML("afterbegin", `<div id="stale" class="banner" role="alert">Survival LLM was restarted.
    <button type="button" class="primary" onclick="location.reload()">Reload this page</button></div>`);
}
function remember(key, value) { try { localStorage.setItem(key, value); } catch { /* private window */ } }
function recall(key) { try { return localStorage.getItem(key); } catch { return null; } }
function announce(text) { els.announce.textContent = ""; setTimeout(() => { els.announce.textContent = text; }, 50); }

// Server-sent events from a POST response body (": tick" heartbeat lines are ignored).
async function* events(resp) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      for (const line of block.split("\n")) if (line.startsWith("data: ")) yield JSON.parse(line.slice(6));
    }
  }
}

// ---------- Markdown: escape everything, then render a small safe subset ----------
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
function linkify(text) {  // escaped text with its web addresses as links (they open in the browser)
  return esc(text).replace(/https:\/\/[^\s<]+/g, (u) => `<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
}
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, "$1");
}
function md(src) {
  let html = "", list = null, para = [], code = null;
  const flush = () => { if (para.length) { html += "<p>" + inline(para.join("<br>")) + "</p>"; para = []; } };
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const line of esc(src).split("\n")) {
    if (line.trim().startsWith("```")) {
      if (code) { html += "<pre><code>" + code.join("\n") + "</code></pre>"; code = null; }
      else { flush(); close(); code = []; }
      continue;
    }
    if (code) { code.push(line); continue; }
    let m;
    if ((m = line.match(/^(#{1,6})\s+(.*)/))) {
      flush(); close();
      const level = Math.min(m[1].length + 2, 6);
      html += `<h${level}>${inline(m[2])}</h${level}>`;
    } else if ((m = line.match(/^\s*[-*•]\s+(.*)/))) {
      flush();
      if (list !== "ul") { close(); html += "<ul>"; list = "ul"; }
      html += "<li>" + inline(m[1]) + "</li>";
    } else if ((m = line.match(/^\s*(\d+)[.)]\s+(.*)/))) {
      flush();
      if (list !== "ol") { close(); html += `<ol start="${m[1]}">`; list = "ol"; }
      html += "<li>" + inline(m[2]) + "</li>";
    } else if (!line.trim()) {
      flush(); close();
    } else if (list && /^\s{2,}\S/.test(line)) {
      html = html.replace(/<\/li>$/, "<br>" + inline(line.trim()) + "</li>");
    } else {
      close(); para.push(line);
    }
  }
  if (code) html += "<pre><code>" + code.join("\n") + "</code></pre>";
  flush(); close();
  return html;
}

// ---------- status, models, first-run setup ----------
const nameOf = (model) => (state?.models.find((m) => m.model === model)?.name) || model || "none yet";
const jobFor = (key) => state?.downloads.find((d) => d.key === key);
const activeJobs = () => (state?.downloads || []).filter((d) => ["starting", "downloading", "unpacking"].includes(d.status));
const isReady = () => state && state.ollama && state.models.length && state.library;
const isLoaded = (model) => !!model && (state?.loaded || []).includes(model.replace(/:latest$/, ""));

async function loadStatus() {
  try {
    state = await (await api("/api/status")).json();
  } catch (err) {
    if (err instanceof StaleTab) return;
    if (restarting) {  // the new version starts in a few seconds (with a new page token, so this page then asks to reload)
      setStatus("warn", "Restarting into the new version…");
      pollTimer = setTimeout(loadStatus, 2000);
      return;
    }
    setStatus("off", "Survival LLM has stopped. Open the app again.");
    return;
  }
  const installed = state.models.map((m) => m.model);
  if (!installed.includes(currentModel)) {
    const saved = recall("model");
    const before = currentModel;
    currentModel = installed.includes(saved) ? saved : state.default_model;
    if (currentModel && currentModel !== before) warm(currentModel);
  }
  renderStatus();
  showModel();
  renderSetup();
  if (els.dialog.open) renderModels();
  els.libraryBtn.hidden = !state.library && !state.my_files?.ready;
  renderVersion();
  schedulePoll();
}

function schedulePoll() {
  clearTimeout(pollTimer);
  const quick = activeJobs().length || state?.warming.length || state?.ollama_starting || !state?.ollama;
  pollTimer = setTimeout(loadStatus, quick ? 1500 : isReady() ? 15000 : 6000);
}

function setStatus(kind, html) {
  els.status.innerHTML = `<span class="dot ${kind}"></span>${html}`;
}

function renderStatus() {
  const s = state;
  if (!s.ollama) {
    setStatus(s.ollama_installed ? "warn" : "off", s.ollama_installed
      ? (s.ollama_starting ? "Starting the AI engine…" : "The AI engine (Ollama) isn't running")
      : "Setup not finished: the AI engine is needed");
  } else if (!s.models.length || !s.library) {
    const missing = [!s.models.length && "an AI model", !s.library && "the library"].filter(Boolean).join(" and ");
    setStatus("warn", `<a href="#setup">Setup not finished: needs ${missing}</a>`);
  } else if (s.warming.includes(currentModel)) {
    setStatus("warn", `Getting ${esc(nameOf(currentModel))} ready…`);
  } else {
    const mine = s.my_files?.ready ? ` + ${s.my_files.ready.toLocaleString()} of your files` : "";
    setStatus("on", `Ready to use without internet · ${s.library.articles.toLocaleString()} articles${mine}`);
  }
}

function showModel() {
  const entry = state?.catalog.find((c) => c.model === currentModel && c.installed);
  els.modelName.textContent = nameOf(currentModel);
  els.using.hidden = !currentModel;
  if (currentModel) {
    els.using.innerHTML = `Answering with <strong>${esc(nameOf(currentModel))}</strong>`
      + (entry ? ` by ${esc(entry.maker)}. ${esc(entry.blurb)}` : ".")
      + ` <button class="ghost" type="button" id="change-model">Change model</button>`;
    $("change-model").onclick = openModels;
  }
}

function warm(model) {
  if (model) api("/api/warm", { method: "POST", body: JSON.stringify({ model }) }).then(loadStatus).catch(() => {});
}

// A second click confirms. (A browser confirm box looks out of place in the app window: "127.0.0.1 says".)
function confirmClick(btn, prompt) {
  if (!btn || btn.dataset.armed) return true;
  const original = btn.textContent;
  btn.dataset.armed = "1";
  btn.textContent = prompt;
  setTimeout(() => { delete btn.dataset.armed; btn.textContent = original; }, 4000);
  return false;
}

function selectModel(model, btn = null) {
  if (model === currentModel) { els.dialog.close(); return; }
  const chatting = els.messages.querySelector(".msg.user");
  if (chatting && !confirmClick(btn, "Starts a new chat: click again")) return;
  currentModel = model;
  remember("model", model);
  warm(model);
  if (chatting) newChat();
  if (els.dialog.open) els.dialog.close();
  showModel();
  renderStatus();
  announce(`Now using ${nameOf(model)}.`);
}

// One row per catalog model (download / use), plus any other installed models.
function modelRows(container, { onlyFitting = false } = {}) {
  const sig = JSON.stringify([state.catalog, state.downloads, state.models, state.machine, state.disk_free_gb,
    currentModel, onlyFitting]);
  if (container.dataset.sig === sig) return;  // unchanged: keep the buttons a click or keyboard focus is on
  container.dataset.sig = sig;
  const focusedKey = document.activeElement?.closest?.(".model-row")?.dataset.key;
  container.innerHTML = "";
  const rows = state.catalog.filter((c) => !onlyFitting || (c.fits && c.fits_disk) || c.installed);
  for (const c of rows) {
    const current = c.installed && c.model === currentModel;
    const job = jobFor(`model:${c.model}`);
    const row = document.createElement("div");
    row.className = "model-row" + (current ? " current" : "") + (c.fits ? "" : " unfit");
    row.dataset.key = c.key;
    const memory = c.fits ? `needs ${c.min_ram} GB of memory (RAM)`
      : `needs ${c.min_ram} GB of memory (RAM); this computer has ${state.machine.ram_gb} GB`;
    row.innerHTML = `<div class="mr-main">
        <div class="mr-title">${esc(c.name)} <span class="mr-maker">by ${esc(c.maker)}</span>
          ${c.recommended ? '<span class="badge">Recommended for this computer</span>' : ""}
          ${current ? '<span class="badge inuse">In use</span>' : ""}</div>
        <div class="mr-blurb">${esc(c.blurb)}</div>
        <div class="mr-meta">${c.installed ? "Downloaded" : `${c.gb} GB download`} · ${memory}</div>
      </div><div class="mr-action"></div>`;
    const action = row.querySelector(".mr-action");
    const button = (label, cls, onclick, disabled = false) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = cls; b.textContent = label; b.disabled = disabled; b.onclick = onclick;
      b.setAttribute("aria-label", `${label}: ${c.name}`);
      action.appendChild(b);
    };
    if (c.installed) {
      button(current ? "In use" : "Use this", current ? "ghost" : "primary", (e) => selectModel(c.model, e.currentTarget), current);
    } else if (job && ["starting", "downloading"].includes(job.status)) {
      const pct = job.total ? Math.round((job.completed / job.total) * 100) : 0;
      action.innerHTML = `<progress max="100" value="${pct}" aria-label="Downloading ${esc(c.name)}"></progress>
        <p class="meta">${job.total ? `${(job.completed / 1e9).toFixed(1)} of ${(job.total / 1e9).toFixed(1)} GB` : "Starting…"}</p>`;
    } else if (!state.ollama) {
      action.innerHTML = `<p class="meta">Start the AI engine first</p>`;
    } else if (!c.fits_disk) {
      action.innerHTML = `<p class="meta">Needs ${c.gb + 5} GB free disk; ${state.disk_free_gb} GB free</p>`;
    } else if (c.fits || onlyFitting) {
      if (job?.status === "error") action.insertAdjacentHTML("beforeend", `<p class="meta error">${esc(job.error)}</p>`);
      button(job?.status === "error" ? "Try again" : `Download ${c.gb} GB`, c.recommended ? "primary" : "ghost",
        () => startModelDownload(c));
    } else {
      action.innerHTML = `<p class="meta">Too big for this computer</p>`;
    }
    container.appendChild(row);
  }
  const others = state.models.filter((m) => !state.catalog.some((c) => c.installed && c.model === m.model));
  if (others.length && !onlyFitting) {
    container.insertAdjacentHTML("beforeend", `<div class="group-title">Other models on this computer</div>`);
    for (const m of others) {
      const current = m.model === currentModel;
      const row = document.createElement("div");
      row.className = "model-row" + (current ? " current" : "");
      row.dataset.key = m.model;
      row.innerHTML = `<div class="mr-main"><div class="mr-title">${esc(m.name)}
        ${current ? '<span class="badge inuse">In use</span>' : ""}</div>
        <div class="mr-meta">${m.tested ? "Another copy of a model listed above." : "Not tested with Survival LLM; answers may be worse."}</div></div>
        <div class="mr-action"><button type="button" class="ghost" ${current ? "disabled" : ""}>${current ? "In use" : "Use this"}</button></div>`;
      row.querySelector("button").onclick = (e) => selectModel(m.model, e.currentTarget);
      container.appendChild(row);
    }
  }
  if (focusedKey) container.querySelector(`.model-row[data-key="${CSS.escape(focusedKey)}"] button`)?.focus();
}

function renderModels() {
  els.modelsIntro.textContent = `This ${state.machine.os} computer has ${state.machine.ram_gb} GB of memory (RAM) and `
    + `${state.disk_free_gb} GB of free disk space. Bigger models know more but need more memory and are slower. `
    + "Download while you have internet, and leave Survival LLM open until it finishes; an interrupted download picks up where it left off.";
  modelRows(els.modelsList);
}
function openModels() {
  if (!state) return;
  renderModels();
  els.dialog.showModal();
}

async function startModelDownload(c) {
  try {
    const resp = await api("/api/pull", { method: "POST", body: JSON.stringify({ model: c.model }) });
    if (resp.status === 409) announce(`${c.name} is already downloading.`);
  } catch { /* status polling shows any problem */ }
  loadStatus();
}

async function startLibraryDownload(pack) {
  try { await api("/api/library", { method: "POST", body: JSON.stringify({ pack }) }); } catch { /* shown via status */ }
  loadStatus();
}

let doneJobs = new Set(), setupSig = "";
function progressHTML(job, doing, label) {
  const pct = job.total ? Math.round((job.completed / job.total) * 100) : 0;
  return `<p>${job.status === "unpacking" ? "Unpacking… (a few minutes)" : doing}</p>
    <progress max="100" value="${pct}" aria-label="${label}"></progress>
    <p class="meta">${job.total ? `${(job.completed / 1e9).toFixed(1)} of ${(job.total / 1e9).toFixed(1)} GB · ` : ""}Leave Survival LLM open until it finishes. If it gets interrupted, start it again and it picks up where it left off.</p>`;
}
function renderSetup() {
  const s = state;
  // A model that just finished downloading becomes the one in use if nothing is selected yet.
  for (const job of s.downloads.filter((d) => d.status === "done" && d.key.startsWith("model:"))) {
    if (doneJobs.has(job.key)) continue;
    doneJobs.add(job.key);
    const model = job.key.slice(6).replace(/:latest$/, "");
    if (!currentModel && s.models.some((m) => m.model === model)) selectModel(model);
  }
  const hasModel = s.models.length > 0;
  const steps = [];
  let step1;
  const engineJob = jobFor("engine");
  const engineBusy = engineJob && ["starting", "downloading", "unpacking"].includes(engineJob.status);
  if (s.ollama) step1 = "<p>Running.</p>";
  else if (engineBusy) step1 = progressHTML(engineJob, "Downloading the AI engine", "AI engine download");
  else if (s.ollama_installed) step1 = `<p>${s.ollama_starting ? "Starting the AI engine… (this takes a few seconds)" : "The AI engine isn't running."}</p>
    <button class="ghost" type="button" id="start-ollama">Start it</button>`;
  else if (s.engine?.downloadable) step1 = `<p>The AI engine (Ollama, free and open source) runs the model on this computer. It's a
    one-time ${s.engine.gb} GB download, and nothing else to install.</p>
    ${engineJob?.status === "error" ? `<p class="error">${esc(engineJob.error)}</p>` : ""}
    <button class="primary" type="button" id="get-engine">Download the AI engine (${s.engine.gb} GB)</button>`;
  else step1 = `<p>Survival LLM uses the free Ollama app to run the AI on this computer. Install it while you have internet:
    <a href="https://ollama.com/download" target="_blank" rel="noopener">ollama.com/download</a>. You don't need Ollama's own
    window; it just has to be running. This page notices on its own when it is.</p>`;
  steps.push(`<div class="step ${s.ollama ? "done" : ""}"><h3><span class="num">${s.ollama ? "✓" : "1"}</span>AI engine</h3>${step1}</div>`);
  if (s.ollama) {
    const fitting = s.catalog.filter((c) => c.fits && c.fits_disk);
    let modelStep;
    if (hasModel) modelStep = `<p>Using ${esc(nameOf(currentModel))}. You can change it any time with Model at the top.</p>`;
    else if (fitting.length) modelStep = `<p>Pick one for this ${esc(s.machine.os)} computer (${s.machine.ram_gb} GB of memory,
      ${s.disk_free_gb} GB free disk). The recommended one is the smartest that fits.</p><div id="setup-models"></div>`;
    else {
      const small = s.catalog.reduce((a, b) => (b.gb < a.gb ? b : a));
      modelStep = `<p>This computer has ${s.machine.ram_gb} GB of memory and ${s.disk_free_gb} GB free disk; even the smallest model
        (${esc(small.name)}) wants ${small.min_ram} GB of memory and ${small.gb + 5} GB of disk. You can try it anyway (answers will be
        slow), or use Search library without the AI.</p>
        <button class="ghost" type="button" id="try-anyway">Try ${esc(small.name)} anyway (${small.gb} GB)</button>`;
    }
    steps.push(`<div class="step ${hasModel ? "done" : ""}"><h3><span class="num">${hasModel ? "✓" : "2"}</span>AI model</h3>${modelStep}</div>`);
    let libStep;
    if (s.library) libStep = `<p>Installed: ${s.library.articles.toLocaleString()} articles.</p>`;
    else {
      const jobs = (s.packs || []).map((p) => jobFor(`library:${p.id}`)).filter(Boolean);
      const active = jobs.find((j) => ["starting", "downloading", "unpacking"].includes(j.status));
      const failed = jobs.find((j) => j.status === "error");
      if (active) libStep = progressHTML(active, `Downloading ${esc(active.label)}`, "Library download");
      else if (s.packs?.length) {
        libStep = `<p>The articles the AI looks things up in. Without it, answers come only from the model's memory, with no sources
          to check. Pick one:</p>${failed ? `<p class="error">${esc(failed.error)}</p>` : ""}
          <div class="pack-row">${s.packs.map((p) => `<button class="ghost pack" type="button" data-pack="${esc(p.id)}">${esc(p.label)}</button>`).join("")}</div>`;
      } else {
        libStep = s.packs_error === "unavailable"
          ? `<p>The list of library downloads isn't available right now (the download site answered, but without it). Try again
            later; it checks again every minute.</p>`
          : `<p>Couldn't reach the list of library downloads (no internet?). Connect, and it will appear here; or put a library
            folder at ${esc(s.library_dir)}.</p>`;
      }
    }
    steps.push(`<div class="step ${s.library ? "done" : ""}"><h3><span class="num">${s.library ? "✓" : "3"}</span>Offline library</h3>${libStep}</div>`);
  }
  const needsSetup = !isReady();
  els.setup.hidden = !needsSetup;
  if (!needsSetup) { els.setup.innerHTML = ""; setupSig = ""; return; }
  const html = `<h2 class="setup-title">Set up Survival LLM</h2>
    <p class="meta">Do this while you have internet. Afterwards everything works without it.</p>` + steps.join("");
  const sig = html + JSON.stringify([s.catalog, s.downloads, s.models, currentModel]);
  if (sig === setupSig) return;  // rebuilding an unchanged card would knock keyboard focus off its buttons
  setupSig = sig;
  const focusedId = document.activeElement?.id, focusedPack = document.activeElement?.dataset?.pack;
  els.setup.innerHTML = html;
  if ($("start-ollama")) $("start-ollama").onclick = () => api("/api/start-ollama", { method: "POST", body: "{}" }).then(loadStatus);
  if ($("get-engine")) $("get-engine").onclick = () => api("/api/engine", { method: "POST", body: "{}" }).then(loadStatus);
  if ($("try-anyway")) $("try-anyway").onclick = () => startModelDownload(s.catalog.reduce((a, b) => (b.gb < a.gb ? b : a)));
  const box = $("setup-models");
  if (box) modelRows(box, { onlyFitting: true });
  for (const b of els.setup.querySelectorAll(".pack")) b.onclick = () => startLibraryDownload(b.dataset.pack);
  if (focusedId && $(focusedId)) $(focusedId).focus();
  if (focusedPack) els.setup.querySelector(`.pack[data-pack="${CSS.escape(focusedPack)}"]`)?.focus();
}

// ---------- chat ----------
function addMessage(role, html) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.innerHTML = html;
  els.messages.appendChild(div);
  return div;
}
function scrollDown() {
  const m = els.messages;
  if (m.scrollHeight - m.scrollTop - m.clientHeight < 160) m.scrollTop = m.scrollHeight;
}
function setBusy(controller) {
  busy = controller;
  els.send.textContent = controller ? "Stop" : "Send";
  els.send.classList.toggle("stop", !!controller);
  els.send.setAttribute("aria-label", controller ? "Stop the answer" : "Send question");
}
function newChat() {
  if (busy) busy.abort();
  conversation = null;
  for (const m of [...els.messages.querySelectorAll(".msg")]) m.remove();
  els.welcome.hidden = false;
  if (!els.reader.hidden) hideReader();  // the article belonged to the old chat
}

function sourceButton(source, how, strong) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "chip" + (strong ? " read" : "");
  b.innerHTML = `${esc(source.title)} <span class="src">· ${esc(source.source)}</span>${source.official ? ' <span class="official" title="Published by a government or health agency. They do not endorse this app.">Official</span>' : ""}`;
  b.onclick = () => openArticle(source.id, how, b);
  return b;
}

function needModel(text) {  // keep the question in the box so it can be sent once a model is ready
  els.input.value = text;
  autosize();
  els.composerNote.textContent = "Download an AI model first (step 2 of the setup above). Your question will wait here.";
  els.composerNote.hidden = false;
  announce(els.composerNote.textContent);
  els.setup.scrollIntoView({ behavior: "smooth" });
}

async function ask(text) {
  text = text.trim();
  if (busy || !text) return;
  if (!currentModel) { await loadStatus(); if (!currentModel) { needModel(text); return; } }
  els.composerNote.hidden = true;
  const model = currentModel;
  const firstLoad = !isLoaded(model);
  els.welcome.hidden = true;
  addMessage("user", `<div class="bubble">${esc(text)}</div>`);
  const msg = addMessage("assistant", `<details class="how" open><summary>Working on it…</summary><div class="activity"></div></details>
    <div class="answer cursor"></div><div class="remembered"></div>
    <div class="answer-foot"><div class="meta"></div><button type="button" class="link reasoning-btn" aria-expanded="false">Show reasoning</button></div>
    <div class="reasoning" hidden><div class="reasoning-head"></div><div class="reasoning-note"></div><div class="reasoning-body"></div></div>
    <div class="sources"></div>`);
  const how = msg.querySelector(".how"), summary = how.querySelector("summary"), activity = how.querySelector(".activity");
  const answerEl = msg.querySelector(".answer"), meta = msg.querySelector(".meta"), sourcesEl = msg.querySelector(".sources");
  const started = Date.now();
  let answer = "", queued = false, steps = 0, failed = false, answered = false, stopped = false;
  let thought = null, thoughts = 0, thoughtMs = 0, thinkSetting;  // thought: the stretch of reasoning streaming in now
  const remembered = msg.querySelector(".remembered"), changes = [];  // memory changes, announced together at the end
  const tick = setInterval(() => {
    summary.textContent = `${nameOf(model)} is working on it… ${Math.round((Date.now() - started) / 1000)} s`;
  }, 1000);
  const render = () => { queued = false; answerEl.innerHTML = md(answer); scrollDown(); };
  const note = (label, id, howText) => {
    const el = document.createElement(id ? "button" : "div");
    el.textContent = label;
    if (id) { el.type = "button"; el.className = "linklike"; el.onclick = () => openArticle(id, howText, el); }
    activity.appendChild(el);
    steps += 1;
    scrollDown();
  };
  // Its reasoning, hidden until the person presses Show reasoning: a timeline of each stretch of thinking and the
  // lookups in between, in the order they happened. It streams in while hidden, so it can be opened at any point.
  const reasonBtn = msg.querySelector(".reasoning-btn"), reasoning = msg.querySelector(".reasoning");
  const reasonHead = reasoning.querySelector(".reasoning-head"), reasonBody = reasoning.querySelector(".reasoning-body");
  const duration = (ms) => `${Math.max(1, Math.round(ms / 1000))} s`;
  const reasonStatus = (final) => {
    const who = nameOf(model);  // one title while it works (switching with each thought read as jumpy)
    const until = answered ? "before it answered" : stopped ? "before you stopped it" : "so far";
    reasonHead.textContent = !final ? (thoughts ? `${who}'s reasoning so far` : "No reasoning yet.")
      : thoughts ? `${who}'s reasoning ${until} · ${duration(thoughtMs)} of thinking`
      : thinkSetting === false ? `${who} answered without reasoning first, because Reasoning is set to Off. Set it to Short or more before you ask, and it thinks first.`
      : thinkSetting === null ? `${who} doesn't reason before it answers, so there's nothing to show.`
      : "No reasoning to show for this answer.";
    reasoning.querySelector(".reasoning-note").textContent = thoughts
      ? "Before it answers, the AI thinks by writing notes to itself, and looks things up in between. In its notes, “the user” "
        + "means you. They can include ideas it dropped along the way, so go by the answer." : "";
    reasonHead.classList.toggle("plain", !thoughts);  // a sentence saying why there is none, not a heading
    reasonBody.hidden = !thoughts;
  };
  const row = (kind, cls) => {  // one entry in the timeline: what kind of step, then what it was; returns the second
    const el = document.createElement("div");
    el.className = `r-row ${cls}`;
    el.innerHTML = `<div class="r-kind"></div><div class="r-what"></div>`;
    el.firstChild.textContent = kind;
    reasonBody.appendChild(el);
    return el.lastChild;
  };
  reasonBtn.onclick = () => {
    reasoning.hidden = !reasoning.hidden;
    reasonBtn.textContent = reasoning.hidden ? "Show reasoning" : "Hide reasoning";
    reasonBtn.setAttribute("aria-expanded", String(!reasoning.hidden));
  };
  const paintThought = (t) => {
    t.queued = false;
    t.el.innerHTML = md(t.text.trim());  // models write their reasoning in Markdown too
    if (!reasoning.hidden) scrollDown();
  };
  const think = (text) => {
    if (!thought) {
      if (!text.trim()) return;  // blank lines before it starts
      thought = { el: row("Thought", "r-thought"), text: "", started: Date.now(), queued: false };
      thoughts += 1;
      reasonStatus(false);
    }
    thought.text += text;
    if (!thought.queued) { thought.queued = true; const t = thought; requestAnimationFrame(() => paintThought(t)); }
  };
  const endThought = () => {
    if (!thought) return;
    thoughtMs += Date.now() - thought.started;
    paintThought(thought);
    thought = null;
    reasonStatus(false);
  };
  const reasonStep = (ev) => {  // a lookup between its thoughts; an article it read opens on a click, as above
    const what = row({ search: "Searched", read: "Read", show: "Opened" }[ev.type], "r-step");
    if (ev.type === "search") { what.textContent = `the library for “${ev.query}”`; return; }
    const b = document.createElement("button");
    b.type = "button";
    b.className = "linklike";
    b.textContent = `${ev.title} · ${ev.source}`;
    b.onclick = () => openArticle(ev.id, ev.type === "read" ? "The answer used this part" : "The answer pointed here", b);
    what.appendChild(b);
  };
  const lookupLabel = (ev) => (ev.type === "search" ? `Searched the library: “${ev.query}”`
    : ev.type === "read" ? `Read: ${ev.title} · ${ev.source}` : `Opened for you: ${ev.title}`);
  reasonStatus(false);
  if (firstLoad) note("Loading the AI into memory. The first answer can take a minute, and the fans may spin up; that's normal.");
  setBusy(new AbortController());
  scrollDown();
  try {
    if (!conversation) {
      conversation = (await (await api("/api/new", { method: "POST", body: "{}" })).json()).conversation;
    }
    const resp = await api("/api/chat", {
      method: "POST", signal: busy.signal,
      body: JSON.stringify({ conversation, model, message: text, reasoning: els.reasoning.value, length: els.length.value }),
    });
    if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).error || `error ${resp.status}`);
    for await (const ev of events(resp)) {
      if (ev.type !== "thinking") endThought();
      if (["search", "read", "show", "retry"].includes(ev.type)) {
        if (answer) { answer = ""; render(); }  // text before a lookup, or before it was asked again, is not the answer
        if (ev.type !== "retry") reasonStep(ev);
      }
      if (ev.type === "thinking") think(ev.text);
      else if (ev.type === "think_limit") {  // its reasoning reached the Reasoning limit; told to answer from what it had
        const said = `Its thinking reached the limit (${Number(ev.limit).toLocaleString()} tokens), so it answered from what it had.`;
        note(said);
        row("Limit", "r-step").textContent = said;
      } else if (ev.type === "retry") {
        note(ev.why === "search" ? "It answered from memory, so it was asked to search the library, as you asked."
          : "It hadn't written an answer, so it was given the best articles it found and asked again.");
      } else if (ev.type === "memory_check") memoryChecking(msg, true);
      else if (ev.type === "memory") changes.push(showRemembered(remembered, ev));
      else if (ev.type === "memory_failed") memoryNote(remembered, "Couldn't update memory this time.");
      else if (ev.type === "memory_unchanged") memoryNote(remembered, "Memory didn't change. You can edit it yourself:", true);
      else if (ev.type === "search") note(lookupLabel(ev));
      else if (ev.type === "read") note(lookupLabel(ev), ev.id, "The answer used this part");
      else if (ev.type === "show") {
        note(lookupLabel(ev), ev.id, "The answer pointed here");
        if (!narrow()) openArticle(ev.id, "The answer pointed here");
      } else if (ev.type === "token") {
        answer += ev.text;
        if (!queued) { queued = true; requestAnimationFrame(render); }
      } else if (ev.type === "error" && answered) {
        memoryNote(remembered, "Couldn't update memory this time.");  // the answer itself is fine
      } else if (ev.type === "error") {
        failed = true;
        meta.textContent = ev.message;
        meta.classList.add("error");
      } else if (ev.type === "done") {
        answered = true;  // a memory step may still follow
        thinkSetting = ev.think;  // whether it was asked to reason first, for when it didn't
        reasonStatus(true);  // its reasoning for the answer is complete (the memory step's isn't shown)
        answer = ev.unfinished ? ev.answer || "" : ev.answer || answer;  // unfinished: what streamed isn't an answer
        render();
        showSources(sourcesEl, ev);
        const secs = Math.round((Date.now() - started) / 1000);
        meta.textContent = ev.cut_off
          ? `${nameOf(model)} was stopped after ${secs} s because the answer reached the length limit. Keep going, or raise the limit below.`
          : ev.unfinished
            ? (ev.sources?.length ? `${nameOf(model)} didn't finish an answer. Try asking again, or open the articles it read below.`
              : ev.seen?.length ? `${nameOf(model)} didn't finish an answer. Try asking again, or open one of the search results below.`
              : `${nameOf(model)} didn't write an answer. Try asking again.`)
            : `Answered by ${nameOf(model)} in ${secs} s`;
        meta.title = ev.seconds ? `${ev.tokens} tokens at ${Math.round(ev.tokens / ev.seconds)} tokens per second` : "";
        if (ev.cut_off) {
          const more = document.createElement("button");
          more.type = "button";
          more.className = "ghost keep-going";
          more.textContent = "Keep going";
          more.onclick = () => { if (busy) return; more.remove(); ask("Please continue exactly where you stopped."); };
          reasoning.after(more);
        }
        announce(`Answer ready from ${nameOf(model)}.`);
      }
    }
  } catch (err) {
    failed = true;
    if (err instanceof StaleTab) { /* banner shown */ }
    // Notes go in the meta line: a pending render() would wipe anything added to the answer itself.
    else if (err.name === "AbortError" && answered) {
      failed = false;
      if (remembered.querySelector(".memory-checking")) memoryNote(remembered, "Stopped updating memory. Anything it already saved is in Memory.", true);
    } else if (err.name === "AbortError") { meta.textContent = "Stopped."; stopped = true; }
    else { meta.textContent = `Couldn't get an answer: ${err.message || err}`; meta.classList.add("error"); }
  } finally {
    clearInterval(tick);
    endThought();
    reasonStatus(true);
    memoryChecking(msg, false);
    if (changes.length) announce(changes.join(". "));
    summary.textContent = steps ? `How it found this (${steps} step${steps === 1 ? "" : "s"})` : "Details";
    how.open = false;
    if (!steps) how.hidden = true;
    answerEl.classList.remove("cursor");
    setBusy(null);
    if (failed) loadStatus();
    else if (!isLoaded(model)) state.loaded = [...(state.loaded || []), model.replace(/:latest$/, "")];
  }
}

function showSources(el, ev) {
  el.innerHTML = "";
  const read = ev.sources || [], seen = ev.seen || [];
  if (read.length) {
    el.insertAdjacentHTML("beforeend", `<div class="label">Sources it read · click to open</div>`);
    for (const s of read) el.appendChild(sourceButton(s, "The answer used this part", true));
  } else if (seen.length) {
    el.insertAdjacentHTML("beforeend", `<div class="label">It searched, but didn't open any of these · click to read them</div>`);
    for (const s of seen.slice(0, 5)) el.appendChild(sourceButton(s, "This matched the search", true));
  } else if (state?.library) {
    el.insertAdjacentHTML("beforeend", `<div class="memory-note">This answer came from what the AI already knows, not the library,
      so there's no source to check. Ask “look that up” to get one.</div>`);
  } else {
    el.insertAdjacentHTML("beforeend", `<div class="memory-note">No library installed yet, so this answer came from what the AI
      already knows, with no source to check.</div>`);
  }
  const rest = read.length ? seen : seen.slice(5);
  if (rest.length) {
    const more = document.createElement("details");
    more.className = "more";
    more.innerHTML = `<summary>Other search results (${rest.length})</summary>`;
    const wrap = document.createElement("div");
    wrap.className = "sources";
    for (const s of rest.slice(0, 15)) wrap.appendChild(sourceButton(s, "This matched the search", false));
    more.appendChild(wrap);
    el.appendChild(more);
  }
}

// After the answer, the AI takes a moment to update its notes about the person; Send waits for it.
function memoryChecking(msg, on) {
  msg.querySelector(".memory-checking")?.remove();
  if (on) msg.querySelector(".remembered").insertAdjacentHTML("beforeend", `<div class="memory-checking">Updating memory…</div>`);
}

function seeMemoryButton() {
  const see = document.createElement("button");
  see.type = "button";
  see.className = "link";
  see.textContent = "See memory";
  see.onclick = () => openMemory();
  return see;
}

// A short line about the memory step under the answer (it failed, or was stopped), optionally with See memory.
function memoryNote(el, text, withLink = false) {
  const row = document.createElement("div");
  row.className = "memory-saved";
  row.append(text);
  if (withLink) row.append(" ", seeMemoryButton());
  el.appendChild(row);
}

// A note the AI saved, changed or deleted after answering, with Undo and a way to see all of them. Returns its text.
function showRemembered(el, ev) {
  if (memoryDialog.open) memoryChangedWhileOpen();
  const row = document.createElement("div");
  row.className = "memory-saved";
  const text = document.createElement("span");
  const said = { add: `Remembered: “${ev.note}”`, change: `Updated a memory: “${ev.note}”`, delete: `Forgot: “${ev.old}”`,
                 full: `Memory is full, so it didn't save “${ev.note}”. Delete notes you don't need in Memory.` };
  text.textContent = said[ev.action] || "Changed its memory";
  if (ev.action === "full") {
    row.append(text, " ", seeMemoryButton());
    el.appendChild(row);
    return text.textContent;
  }
  const sep = document.createTextNode(" · ");
  const undo = document.createElement("button");
  undo.type = "button";
  undo.className = "link";
  undo.textContent = "Undo";
  undo.onclick = async () => {
    undo.disabled = true;
    try {
      const resp = await api("/api/memory/undo", { method: "POST", body: JSON.stringify(ev) });
      if (!resp.ok) throw new Error();
      text.textContent = { add: `Undone: it won't remember “${ev.note}”`, change: `Undone: back to “${ev.old}”`,
                           delete: `Undone: it remembers “${ev.old}” again` }[ev.action];
      undo.remove();
      sep.remove();
    } catch (err) {
      if (!(err instanceof StaleTab)) {
        text.textContent = `${said[ev.action]}. Couldn't undo it here, maybe because the note was edited since; change it in Memory.`;
        undo.remove();
        sep.remove();
      } else undo.disabled = false;
    }
  };
  row.append(text, sep, undo, " · ", seeMemoryButton());
  el.appendChild(row);
  return text.textContent;
}

// ---------- reader ----------
function showReader(opener) {
  if (opener) lastFocus = opener;
  els.reader.hidden = false;
  els.layout.classList.add("with-reader");
}
function hideReader() {
  els.reader.hidden = true;
  els.layout.classList.remove("with-reader");
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
}

async function openArticle(id, how = "This matched the search", opener = null) {
  const request = ++readerRequest;
  showReader(opener);
  els.readerBody.innerHTML = `<p class="meta">Opening…</p>`;
  let doc;
  try {
    const resp = await api(`/api/article?id=${encodeURIComponent(id)}`);
    if (!resp.ok) throw new Error();
    doc = await resp.json();
  } catch {
    if (request === readerRequest) els.readerBody.innerHTML = `<p class="error">Couldn't open that source.</p>`;
    return;
  }
  if (request !== readerRequest) return;  // a newer source was opened meanwhile
  let html = `<div class="doc-head"><h2 id="doc-title" tabindex="-1">${esc(doc.title)}</h2>
    <div class="src-line">${esc(doc.source)}${doc.official ? ' <span class="official" title="Published by a government or health agency. They do not endorse this app.">Official</span>' : ""}${doc.url ? " · " + esc(doc.url) : ""}</div>
    ${doc.license ? `<div class="src-line license">License: ${linkify(doc.license)}</div>` : ""}</div>`;
  for (const part of doc.parts) {
    const cited = part.id === doc.focus;
    html += `<section class="${cited ? "cited" : ""}">${cited ? `<div class="cited-label">${esc(how)}</div>` : ""}${md(part.text)}</section>`;
  }
  els.readerBody.innerHTML = html;
  const target = els.readerBody.querySelector("section.cited");
  if (target && doc.parts[0].id !== doc.focus) {
    target.style.scrollMarginTop = `${els.readerBody.querySelector(".doc-head").offsetHeight + 8}px`;  // clear of the sticky title
    target.scrollIntoView({ block: "start" });
  }
  else els.readerBody.scrollTop = 0;
  if (opener) $("doc-title").focus({ preventScroll: true });
}

async function searchLibrary(q) {
  const request = ++readerRequest;
  showReader(els.search);
  els.readerBody.innerHTML = `<p class="meta">Searching…</p>`;
  let results = [];
  try { results = (await (await api(`/api/search?q=${encodeURIComponent(q)}`)).json()).results; } catch { /* below */ }
  if (request !== readerRequest) return;
  els.readerBody.innerHTML = results.length
    ? `<p class="meta">${results.length} results for “${esc(q)}”</p>` + results.map((r) => `<button type="button" class="result" data-id="${r.id}">
        <span class="r-title">${esc(r.title)}</span><span class="r-src">${esc(r.source)}${r.official ? ' <span class="official" title="Published by a government or health agency. They do not endorse this app.">Official</span>' : ""}</span>
        <span class="r-snip">${esc(r.snippet)}</span></button>`).join("")
    : `<p class="meta">Nothing found for “${esc(q)}”. Try fewer or different words.</p>`;
  for (const el of els.readerBody.querySelectorAll(".result")) el.onclick = () => openArticle(+el.dataset.id, "This matched your search", el);
}

// ---------- wiring ----------
function autosize() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 200) + "px";
}
els.composer.onsubmit = (e) => {
  e.preventDefault();
  if (busy) { busy.abort(); return; }  // the button reads "Stop" while an answer streams
  const text = els.input.value;
  els.input.value = "";
  autosize();
  ask(text);
};
els.input.onkeydown = (e) => {
  if (e.key !== "Enter" || e.shiftKey || e.isComposing || e.keyCode === 229) return;
  e.preventDefault();
  if (!busy) els.composer.requestSubmit();  // Enter never stops an answer; only the Stop button does
};
els.input.oninput = autosize;
for (const b of document.querySelectorAll(".example")) b.onclick = () => ask(b.textContent);
els.length.value = recall("length") || "normal";
els.reasoning.value = recall("reasoning") || "off";
if (!els.reasoning.value) els.reasoning.value = "off";  // a value saved by a version with other choices
const lengthNote = () => {  // warns when a limit is off: a stuck model then runs until Stop
  const lines = [];
  if (els.reasoning.value === "none") lines.push("With no reasoning limit, a model can get stuck reasoning in circles and keep going until you press Stop.");
  if (els.length.value === "none") lines.push("With no length limit, a model stuck repeating itself keeps going until you press Stop.");
  $("length-note").textContent = lines.join(" ");
  $("length-note").hidden = !lines.length;
};
els.length.onchange = () => { remember("length", els.length.value); lengthNote(); };
els.reasoning.onchange = () => { remember("reasoning", els.reasoning.value); lengthNote(); };
lengthNote();
els.searchForm.onsubmit = (e) => { e.preventDefault(); if (els.search.value.trim()) searchLibrary(els.search.value.trim()); };
els.libraryBtn.onclick = () => { showReader(els.libraryBtn); els.search.focus(); };
$("close-reader").onclick = hideReader;
els.reader.onkeydown = (e) => { if (e.key === "Escape") hideReader(); };
$("new-chat").onclick = () => { newChat(); els.input.focus(); };
$("model-btn").onclick = openModels;
$("close-models").onclick = () => els.dialog.close();

// ---------- the person's own files ----------
const FILE_TYPES = [".pdf", ".docx", ".odt", ".epub", ".html", ".htm", ".txt", ".md", ".markdown", ".csv"];
const CANT_READ = "Survival LLM can't read this kind of file. It reads PDF, Word, EPUB, web pages and text files.";
const filesDialog = $("files-dialog");
let myFiles = null;        // last /api/myfiles
let filesTimer = null, filesSig = "", filesChecks = 0, adding = 0;
let fileNotes = [];        // what happened to the files just added or removed: {text, error}
let addedNames = [];       // files added since the window opened, reported in one line
let fileAsks = [];         // files whose name is taken, waiting for Replace it or Keep both: {file, name}

const readable = (name) => FILE_TYPES.some((t) => name.toLowerCase().endsWith(t));
const trashName = () => (state?.machine.os === "Windows" ? "Recycle Bin" : "Trash");

function openFiles() {
  fileNotes = [];
  addedNames = [];
  filesSig = "";
  if (!filesDialog.open) filesDialog.showModal();
  refreshFiles(true);
}

async function refreshFiles(check = false) {
  clearTimeout(filesTimer);
  try { myFiles = await (await api(`/api/myfiles${check ? "?check=1" : ""}`)).json(); } catch { return; }
  if (check) filesChecks = 3;  // the check runs in the background, so look again a few times
  renderFiles();
  if (filesDialog.open && (myFiles.busy || adding || filesChecks-- > 0)) filesTimer = setTimeout(refreshFiles, 1000);
  else loadStatus();  // the count at the top
}

function renderFiles() {
  const f = myFiles || { items: [] };
  $("files-status").textContent = adding ? `Adding ${adding === 1 ? "a file" : `${adding} files`}…`
    : f.busy && f.doing ? `Reading ${f.doing} (${f.done + 1} of ${f.total})… Big files take a minute or two.` : "";
  const added = addedNames.length > 3 ? `${addedNames.slice(0, 3).join(", ")} and ${addedNames.length - 3} more` : addedNames.join(", ");
  $("files-notes").innerHTML = (added ? `<p class="meta">Added ${esc(added)}.</p>` : "")
    + fileNotes.map((n) => `<p class="${n.error ? "error" : "meta"}">${esc(n.text)}</p>`).join("");
  $("files-ask").hidden = !fileAsks.length;
  if (fileAsks.length) $("files-ask-text").textContent = `You already have a file called “${fileAsks[0].name}”. Replace it with this one?`;
  $("files-folder").textContent = f.folder
    ? `They're kept in ${f.folder}. Files you put in that folder, or change there, are picked up on their own.` : "";
  const sig = JSON.stringify(f.items);
  if (sig === filesSig) return;  // rebuilding the list would undo a half-confirmed Remove
  filesSig = sig;
  $("files-list").innerHTML = f.items.length ? f.items.map((it) => `<li>
      <div class="f-main"><div class="f-title">${esc(it.title || it.name)}</div>
        <div class="meta">${it.title && it.title !== it.name ? `${esc(it.name)} · ` : ""}${it.status === "ready" ? "Ready: the AI searches it" : "The AI can't search this one"}</div>
        ${it.error ? `<div class="error">${esc(it.error)}</div>` : ""}</div>
      <button class="ghost" type="button" data-name="${esc(it.name)}">Remove</button></li>`).join("")
    : `<li class="empty meta">No files yet.</li>`;
  for (const b of $("files-list").querySelectorAll("button[data-name]")) b.onclick = () => removeFile(b.dataset.name, b);
}

async function sendFile(file, how = "") {  // how: "" (ask if the name is taken), "&keep=1" or "&replace=<name>"
  adding++;
  renderFiles();
  try {
    const resp = await api(`/api/myfiles/add?name=${encodeURIComponent(file.name)}${how}`,
      { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } });
    const out = await resp.json();
    if (resp.status === 409) fileAsks.push({ file, name: out.name || file.name });
    else if (!resp.ok) fileNotes.push({ error: true, text: `${file.name}: ${out.error || "couldn't add it."}` });
    else if (out.duplicate || out.unchanged) fileNotes.push({ text: `${out.name} is already in your files.` });
    else if (out.replaced) fileNotes.push({ text: `Replaced ${out.replaced} with the new version. The old one is in the ${trashName()}.` });
    else addedNames.push(out.name);
  } catch (err) {
    if (!(err instanceof StaleTab)) fileNotes.push({ error: true, text: `${file.name}: couldn't add it. Is Survival LLM still running?` });
  } finally {
    adding--;
    renderFiles();
  }
}

async function addFiles(list, notes = []) {
  const chosen = [...list];
  if (!filesDialog.open) openFiles();
  fileNotes.push(...notes);
  const taken = new Set((myFiles?.items || []).map((it) => it.name));
  for (const file of chosen) {
    if (!readable(file.name)) fileNotes.push({ error: true, text: `${file.name}: ${CANT_READ}` });
    else if (taken.has(file.name)) fileAsks.push({ file, name: file.name });
    else await sendFile(file);
  }
  renderFiles();
  refreshFiles(true);
}

async function answerAsk(how) {
  const ask = fileAsks.shift();
  renderFiles();
  if (ask && how) await sendFile(ask.file, how === "replace" ? `&replace=${encodeURIComponent(ask.name)}` : "&keep=1");
  refreshFiles(true);
}

async function removeFile(name, btn) {
  if (!confirmClick(btn, `Click again to move it to the ${trashName()}`)) return;
  try {
    const resp = await api("/api/myfiles/remove", { method: "POST", body: JSON.stringify({ name }) });
    fileNotes.push(resp.ok ? { text: `Removed ${name}. It's in the ${trashName()} if you want it back.` }
      : { error: true, text: (await resp.json()).error });
  } catch { /* a stale page shows its own banner */ }
  refreshFiles(true);
}

$("files-btn").onclick = openFiles;
$("close-files").onclick = () => filesDialog.close();
filesDialog.onclick = (e) => { if (e.target === filesDialog) filesDialog.close(); };
filesDialog.addEventListener("close", () => clearTimeout(filesTimer));
$("choose-files").onclick = () => $("file-input").click();
$("file-input").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
$("open-folder").onclick = () => api("/api/myfiles/open", { method: "POST", body: "{}" }).catch(() => {});
$("ask-replace").onclick = () => answerAsk("replace");
$("ask-keep").onclick = () => answerAsk("keep");
$("ask-skip").onclick = () => answerAsk(null);

// Files dropped anywhere on the window are added to Your files (instead of the window opening them).
const dragsFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
let dragDepth = 0;
function showDrop(on) {
  $("drop-overlay").hidden = !on || filesDialog.open;
  $("dropzone").classList.toggle("over", on && filesDialog.open);
}
document.addEventListener("dragenter", (e) => { if (dragsFiles(e)) { e.preventDefault(); dragDepth++; showDrop(true); } });
document.addEventListener("dragover", (e) => { if (dragsFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } });
document.addEventListener("dragleave", (e) => { if (dragsFiles(e) && --dragDepth <= 0) { dragDepth = 0; showDrop(false); } });
document.addEventListener("drop", (e) => {
  if (!dragsFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  showDrop(false);
  const folders = new Set([...e.dataTransfer.items].map((i) => i.webkitGetAsEntry?.()).filter((en) => en?.isDirectory).map((en) => en.name));
  addFiles([...e.dataTransfer.files].filter((f) => !folders.has(f.name)), [...folders].map((name) =>
    ({ error: true, text: `${name} is a folder. To add a whole folder, click Open the folder and put it there.` })));
});

// ---------- updates ----------
const updatesDialog = $("updates-dialog");
let updateReport = null, updateChecking = false, updatesSig = "";
const day = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { dateStyle: "medium" });
const size = (gb) => (Number(gb) < 1 ? `${Math.round(Number(gb) * 1000)} MB` : `${Number(gb)} GB`);
let updateError = "";

function renderVersion() {
  $("version-line").hidden = !state.version;
  $("version-text").textContent = `Survival LLM ${state.version}` + (state.dev ? ` · dev copy: ${state.dev}` : "");
  $("dev-badge").hidden = !state.dev;  // a copy for trying changes before they ship (bin/dev)
  $("dev-badge").title = state.dev ? `Dev copy: ${state.dev}` : "";
  const app = state.update?.app;
  $("updates-btn").textContent = app || state.update?.library ? "Update available" : "Check for updates";
  $("update-banner").hidden = !app || recall("update-dismissed") === app;
  if (app) $("update-banner").querySelector(".update-text").textContent = `Survival LLM ${app} is available.`;
  if (updatesDialog.open) renderUpdates();
}

async function openUpdates(check) {
  updatesSig = "";
  if (!updatesDialog.open) updatesDialog.showModal();
  updateChecking = true;
  renderUpdates();
  try { updateReport = await (await api(`/api/updates${check ? "?check=1" : ""}`)).json(); } catch { /* shown below */ }
  updateChecking = false;
  renderUpdates();
  loadStatus();
}

function renderUpdates() {
  const r = updateReport;
  const busy = (job) => job && ["starting", "downloading", "unpacking"].includes(job.status);
  let html;
  if (updateChecking) html = `<p class="meta">Checking for updates…</p>`;
  else if (!r) html = `<p class="error">Couldn't check for updates. Is Survival LLM still running?</p>`;
  else {
    html = r.failed ? `<p class="error">The last update couldn't be installed (${esc(r.failed)}). You still have version ${esc(r.version)}.</p>` : "";
    const job = jobFor("app-update");
    if (r.app) {
      const a = r.app;
      html += `<h3>Survival LLM ${esc(a.version)} is available</h3>
        <p class="meta">You have ${esc(r.version)}.${a.date ? ` Released ${esc(day(a.date))}.` : ""} ${size(a.gb)} download.</p>`;
      if (Array.isArray(a.notes) && a.notes.length) html += `<ul class="notes">${a.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`;
      if (busy(job)) html += progressHTML(job, `Downloading Survival LLM ${esc(a.version)}`, "Update download");
      else if (job?.status === "done") html += `<p>Installed. Survival LLM is restarting into the new version…</p>`;
      else if (a.blocker) html += `<p>${esc(a.blocker)}</p>
        <p><a href="${esc(r.download_page)}" target="_blank" rel="noopener">Get the new version from the website</a></p>`;
      else html += `${job?.status === "error" || updateError ? `<p class="error">${esc(updateError || job.error)}</p>` : ""}
        <button class="primary" type="button" id="install-update">Update now</button>
        <p class="meta">Survival LLM downloads the new version, closes, and opens again a few seconds later. Your AI models,
          library, files and settings stay as they are.</p>`;
    } else if (r.error) {
      html += `<p>${r.error === "offline" ? "Couldn't check for updates (no internet?)." : "Couldn't check for updates right now."}
        Survival LLM works fine without them. You have version ${esc(r.version)}.</p>`;
    } else {
      html += `<p>You have the latest version of Survival LLM (${esc(r.version)}).</p>`;
    }
    if (r.library) {
      const lib = jobFor(`library:${r.library.id}`);
      html += `<h3>A newer library is available</h3><p class="meta">${esc(r.library.label)}, from ${esc(day(r.library.version))}.</p>`;
      if (busy(lib)) html += progressHTML(lib, "Downloading the new library", "Library update");
      else if (lib?.status === "done") html += `<p>Your library is up to date.</p>`;
      else html += `${lib?.status === "error" ? `<p class="error">${esc(lib.error)}</p>` : ""}
        <button class="ghost" type="button" id="update-library">Update the library (${esc(size(r.library.gb))})</button>
        <p class="meta">Your current library keeps working until the new one is ready, and your own files stay.</p>`;
    }
    if (r.checked) html += `<p class="meta">Last checked ${esc(new Date(r.checked * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }))}.</p>`;
  }
  if (html === updatesSig) return;  // rebuilding an unchanged window would knock keyboard focus off its buttons
  updatesSig = html;
  $("updates-body").innerHTML = html;
  if ($("install-update")) $("install-update").onclick = installUpdate;
  if ($("update-library")) $("update-library").onclick = () => startLibraryDownload(r.library.id);
}

async function installUpdate() {
  updateError = "";
  try {
    const resp = await api("/api/update/app", { method: "POST", body: "{}" });
    if (resp.ok) restarting = true;
    else updateError = (await resp.json()).error || "The update couldn't start. Try again.";
  } catch { return; }
  renderUpdates();
  loadStatus();
}

$("updates-btn").onclick = () => openUpdates(true);
$("close-updates").onclick = () => updatesDialog.close();
updatesDialog.onclick = (e) => { if (e.target === updatesDialog) updatesDialog.close(); };
$("banner-update").onclick = () => openUpdates(false);
$("banner-dismiss").onclick = () => { remember("update-dismissed", state?.update?.app || ""); $("update-banner").hidden = true; };

// ---------- memory: the AI's notes about the person, viewable and editable ----------
const memoryDialog = $("memory-dialog");
let memoryBase = "", memoryMax = 6000;  // memoryBase: the notes as last loaded or saved
const noteLines = (text) => text.split("\n").map((l) => l.trim()).filter(Boolean);

function memoryCount() {
  const lines = noteLines($("memory-text").value);
  const chars = lines.reduce((n, l) => n + l.length + 1, 0);
  const over = chars > memoryMax;
  $("memory-count").textContent = lines.length
    ? `${lines.length} note${lines.length === 1 ? "" : "s"} · ${chars.toLocaleString()} of ${memoryMax.toLocaleString()} characters`
    : "";
  $("memory-count").classList.toggle("error", over);
  $("memory-save").disabled = over;
}

// Edits not yet saved are kept when the window closes, and are there again when it reopens.
const memoryEdited = () => $("memory-text").value.trim() !== memoryBase.trim();

async function openMemory() {
  $("memory-status").textContent = "";
  try {
    const m = await (await api("/api/memory")).json();
    memoryMax = m.max_chars;
    $("memory-writes").checked = m.ai_writes;
    if (memoryEdited()) {
      $("memory-status").textContent = "These are your unsaved changes. Save keeps them, along with anything the AI changed since.";
    } else {
      memoryBase = m.text;
      $("memory-text").value = m.text;
    }
  } catch (err) {
    if (!(err instanceof StaleTab)) $("memory-status").textContent = "Couldn't load the memory.";
  }
  memoryCount();
  if (!memoryDialog.open) memoryDialog.showModal();
}

// The AI changed its notes while the window is open: show them if nothing was edited, else say Save will merge.
async function memoryChangedWhileOpen() {
  if (!memoryEdited()) return openMemory();
  $("memory-status").textContent = "The AI just changed its notes. Save keeps your edits and its changes.";
}

async function saveMemory(text, done) {
  $("memory-status").textContent = "Saving…";
  try {
    const resp = await api("/api/memory", { method: "POST", body: JSON.stringify({ text, base: memoryBase }) });
    const m = await resp.json();
    if (!resp.ok) { $("memory-status").textContent = m.error || "Couldn't save."; return; }
    memoryBase = m.text;
    $("memory-text").value = m.text;
    const n = (k) => (k === 1 ? "a note" : `${k} notes`);
    const kept = m.kept.length ? ` Also kept ${n(m.kept.length)} the AI saved while you were editing.` : "";
    const dropped = m.dropped.length ? ` Left out ${n(m.dropped.length)} the AI deleted or changed meanwhile.` : "";
    $("memory-status").textContent = done + kept + dropped;
  } catch (err) {
    $("memory-status").textContent = err instanceof StaleTab
      ? "Not saved: Survival LLM restarted. Copy your edits, then reload the page."
      : "Couldn't save. Is Survival LLM still running?";
  }
  memoryCount();
}

$("memory-btn").onclick = openMemory;
$("close-memory").onclick = () => memoryDialog.close();
$("memory-text").oninput = memoryCount;
$("memory-save").onclick = () => saveMemory($("memory-text").value, "Saved. Used from your next question.");
$("memory-clear").onclick = (e) => {
  if (!confirmClick(e.currentTarget, "Click again to delete every note")) return;
  saveMemory("", "Deleted all notes.");
};
$("memory-writes").onchange = async (e) => {
  const on = e.target.checked;
  try {
    const resp = await api("/api/memory/ai-writes", { method: "POST", body: JSON.stringify({ on }) });
    const m = await resp.json();
    if (!resp.ok) { e.target.checked = !on; $("memory-status").textContent = m.error || "Couldn't change that."; return; }
    e.target.checked = m.ai_writes;
    $("memory-status").textContent = m.ai_writes ? "The AI can add and change notes again."
      : "The AI won't add or change notes now. It still reads the ones here.";
  } catch (err) {
    e.target.checked = !on;
    if (!(err instanceof StaleTab)) $("memory-status").textContent = "Couldn't change that. Is Survival LLM still running?";
  }
};

// ---------- the AI's instructions, viewable and editable ----------
let promptDefault = "";
$("prompt-btn").onclick = async () => {
  $("prompt-status").textContent = "";
  try {
    const p = await (await api("/api/prompt")).json();
    promptDefault = p.default;
    $("prompt-text").value = p.custom || p.default;
    $("prompt-status").textContent = p.custom ? "Using your edited instructions." : "Using the original instructions.";
  } catch {
    $("prompt-status").textContent = "Couldn't load the instructions.";
  }
  $("prompt-dialog").showModal();
};
async function savePrompt(body, done) {
  try {
    const p = await (await api("/api/prompt", { method: "POST", body: JSON.stringify(body) })).json();
    $("prompt-text").value = p.custom || promptDefault;
    $("prompt-status").textContent = done;
  } catch {
    $("prompt-status").textContent = "Couldn't save. Is Survival LLM still running?";
  }
}
$("prompt-save").onclick = () => {
  const text = $("prompt-text").value.trim();
  savePrompt(text === promptDefault.trim() ? { reset: true } : { text }, "Saved. Used from your next question.");
};
$("prompt-reset").onclick = () => savePrompt({ reset: true }, "Back to the original instructions.");
$("close-prompt").onclick = () => $("prompt-dialog").close();
els.dialog.onclick = (e) => { if (e.target === els.dialog) els.dialog.close(); };  // click outside closes
$("quit").onclick = async (e) => {
  if (!confirmClick(e.currentTarget, "Click again to quit")) return;
  try { await api("/api/quit", { method: "POST", body: "{}" }); } catch { /* already stopped */ }
  clearTimeout(pollTimer);
  document.body.innerHTML = `<div class="welcome" style="max-width:640px;margin:10vh auto;padding:0 16px">
    <h1>Survival LLM has stopped.</h1><p>You can close this window. Open Survival LLM again to start it.</p></div>`;
};
loadStatus();
els.input.focus();
