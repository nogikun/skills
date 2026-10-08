// Scribe web UI: job list + flow (live over SSE) and the speaker editor. No build step, no dependencies.

const $ = (sel, root = document) => root.querySelector(sel);
const SVG = "http://www.w3.org/2000/svg";

function h(tag, props = {}, ...kids) {
  const svg = tag.startsWith("svg:");
  const el = svg ? document.createElementNS(SVG, tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (!svg && k in el && typeof v !== "string") el[k] = v;
    else if (!svg && (k === "value" || k === "textContent")) el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}

const STAGES = [["audio", "音声抽出"], ["diarize", "話者分離"], ["transcribe", "文字起こし"], ["fill", "抜け補完"],
                ["merge", "統合"], ["samples", "代表音声"]];
const STAGE_LABEL = Object.fromEntries(STAGES);
const WEIGHTS = { audio: 5, diarize: 35, transcribe: 50, fill: 5, merge: 2, samples: 3 }; // mirrors server.WEIGHTS
const STATUS = {
  created: "待機中", audio_extracted: "音声抽出済み", diarized: "話者分離済み", transcribed: "文字起こし済み",
  merged: "統合済み", speaker_identification_required: "話者名の入力待ち", ready: "完了", exported: "書き出し済み", failed: "失敗",
};
const KIND_MARK = { done: "✓", failed: "✕", action: "!", paused: "❚❚", queued: "…", stopped: "■", running: "" };
const FORMATS = [["markdown", "Markdown"], ["txt", "テキスト"], ["vtt", "WebVTT"], ["srt", "SRT"], ["json", "JSON"]];
const PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"];

const jobs = new Map();
let route = parseHash();
let editor = null;       // open speaker editor, if any
let selectedNode = null; // flow node shown in the detail panel
let exportFmt = "markdown";
let currentHash = location.hash;

// --- helpers -----------------------------------------------------------
const enc = encodeURIComponent;
const pad = (n) => String(n).padStart(2, "0");
function fmtTime(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const hh = Math.floor(sec / 3600), mm = Math.floor(sec / 60) % 60, ss = sec % 60;
  return hh ? `${hh}:${pad(mm)}:${pad(ss)}` : `${mm}:${pad(ss)}`;
}
function fmtDate(iso) {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
const since = (iso) => (iso ? (Date.now() - new Date(iso).getTime()) / 1000 : 0);

function kind(v) {
  if (v.running) return "running";
  if (v.queue) return v.queue.state; // queued | paused: its scribe process is alive and waiting
  if (v.status === "failed") return "failed";
  if (v.status === "speaker_identification_required") return "action";
  if (v.status === "ready" || v.status === "exported") return "done";
  return "stopped"; // no process: interrupted midway
}
function statusText(v) {
  if (v.running) return `処理中 · ${STAGE_LABEL[v.running] || v.running}`;
  if (v.queue?.state === "queued") return v.queue.position === 1 ? "待機中 · 次に実行" : `待機中 · ${v.queue.position} 番目`;
  if (v.queue?.state === "paused") return "一時停止中";
  if (kind(v) === "stopped") return `中断 (${STATUS[v.status] || v.status})`;
  return STATUS[v.status] || v.status;
}
const icon = (k) => h("span", { class: `icon ${k}`, "aria-hidden": "true" }, KIND_MARK[k]);
const canEdit = (v) => v.stages.audio?.status === "completed" && v.stages.diarize?.status === "completed";

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.message || r.statusText), { code: j.error, status: r.status });
  return j;
}

let toastTimer;
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast show${bad ? " bad" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), bad ? 6000 : 3000);
}

// --- routing -----------------------------------------------------------
function parseHash() {
  const m = location.hash.match(/^#\/job\/([^/]+)(\/speakers)?$/);
  return m ? { id: decodeURIComponent(m[1]), view: m[2] ? "speakers" : "detail" } : { id: null, view: null };
}

// In-page confirmation. window.confirm() is not used: app-embedded browsers (Claude's browser pane etc.)
// suppress native dialogs and answer "cancel" without showing anything.
function confirmDialog({ title, body, ok, danger = false }) {
  return new Promise((resolve) => {
    const dlg = h("dialog", { class: "confirm", "aria-labelledby": "confirm-title" },
      h("form", { method: "dialog" },
        h("h3", { id: "confirm-title" }, title),
        ...body.map((line) => h("p", {}, line)),
        h("div", { class: "actions" },
          h("button", { class: "btn", value: "cancel", autofocus: true }, "キャンセル"), // safe default for Enter
          h("button", { class: `btn ${danger ? "danger" : "primary"}`, value: "ok" }, ok))));
    dlg.addEventListener("close", () => { dlg.remove(); resolve(dlg.returnValue === "ok"); }); // Esc -> ""
    document.body.append(dlg);
    dlg.showModal();
  });
}

let leaving = false; // set while we re-apply a navigation the user confirmed
window.addEventListener("hashchange", async () => {
  if (editor?.dirty && !leaving) {
    const target = location.hash;
    history.replaceState(null, "", currentHash); // stay until the user decides
    const ok = await confirmDialog({ title: "保存していない変更があります",
      body: ["話者名や文字起こしの修正がまだ保存されていません。", "保存せずに移動すると、これらの変更は失われます。"],
      ok: "保存せずに移動", danger: true });
    if (ok) { leaving = true; location.hash = target; }
    return;
  }
  leaving = false;
  const prev = route;
  route = parseHash();
  currentHash = location.hash;
  if (prev.id !== route.id) selectedNode = null;
  renderList();
  renderMain();
});

window.addEventListener("beforeunload", (e) => {
  if (editor?.dirty) { e.preventDefault(); e.returnValue = ""; }
});

window.addEventListener("keydown", (e) => {
  if (!editor) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    editor.save();
  } else if (e.key === " " && !e.target.closest("input, button, select, textarea, a")) {
    e.preventDefault();
    editor.toggle();
  }
});

function renderMain() {
  editor?.destroy();
  editor = null;
  const main = $("#main");
  main.className = "";
  if (!route.id) return main.replaceChildren(h("p", { class: "placeholder" }, "左のジョブを選んでください。"));
  if (route.view === "speakers") return openEditor(route.id);
  const v = jobs.get(route.id);
  if (!v) return main.replaceChildren(h("p", { class: "placeholder" }, "読み込み中…"));
  renderDetail(v);
}

// --- live updates ------------------------------------------------------
function connect() {
  const es = new EventSource("/api/events");
  const conn = $("#conn");
  es.onopen = () => {
    conn.textContent = "ライブ";
    conn.classList.remove("off");
    setTimeout(renderList, 1500);
    // a job is only listed if `scribe process` used this same folder: show it so a mismatch is visible
    api("/api/jobs").then((j) => ($("#home").textContent = `ジョブの保存先: ${j.home}`)).catch(() => {});
  };
  es.onerror = () => { conn.textContent = "再接続中…"; conn.classList.add("off"); };
  es.addEventListener("job", (e) => {
    const v = JSON.parse(e.data);
    jobs.set(v.job_id, v);
    renderList();
    if (route.id !== v.job_id) return;
    if (route.view === "detail") renderDetail(v);
    else editor?.onJob(v);
  });
  es.addEventListener("gone", (e) => {
    const id = JSON.parse(e.data);
    jobs.delete(id);
    renderList();
    if (route.id === id && !editor?.dirty) location.hash = ""; // deleted elsewhere (another tab, by hand)
  });
}

// --- sidebar -----------------------------------------------------------
function renderList() {
  const list = [...jobs.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
  $("#empty").hidden = list.length > 0;
  $("#jobs").replaceChildren(...list.map((v) => {
    const k = kind(v);
    return h("li", {},
      h("a", { class: "job", href: `#/job/${enc(v.job_id)}`, "aria-current": route.id === v.job_id ? "page" : null,
               title: `${v.input}\n右クリックで一時停止・優先などの操作`, "aria-haspopup": "menu",
               oncontextmenu: (e) => openMenu(e, v.job_id) },
        icon(k),
        h("span", { class: "name" }, v.name),
        h("span", { class: "sub" }, h("span", {}, statusText(v)), h("span", {}, fmtDate(v.created_at))),
        k === "running" ? h("span", { class: "mini" }, h("i", { style: `width:${v.pct}%` })) : null));
  }));
}

// --- job actions: pause / resume / run first (right-click menu + Workflow header) ----------
async function control(id, body, done) {
  try {
    const v = await api(`/api/jobs/${enc(id)}/control`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    jobs.set(id, v);
    renderList();
    if (route.id === id && route.view === "detail") renderDetail(v);
    toast(done);
  } catch (err) {
    toast(err.code === "not_running" ? "このジョブの処理は動いていません (scribe process --job で再開できます)" : `操作できませんでした: ${err.message}`, true);
  }
}

function jobActions(v) {
  const k = kind(v), id = v.job_id, out = [];
  if (k === "running" || k === "queued") {
    out.push({ label: "一時停止", hint: k === "running" ? "実行中の段階は破棄し、再開時にやり直します" : null,
               run: () => control(id, { paused: true }, "一時停止しました") });
  }
  if (k === "paused") out.push({ label: "再開", run: () => control(id, { paused: false }, "再開しました") });
  if (k === "queued" || k === "paused") {
    // also when first in line: "next" only means after the running job's remaining stages
    out.push({ label: "最優先にする", hint: "実行中のジョブの今の段階が終わったら、こちらを先に処理します",
               run: () => control(id, { first: true }, "最優先にしました") });
  }
  if (k === "stopped" || k === "failed") {
    out.push({ label: "再開コマンドをコピー", hint: `scribe process --job ${id}`,
               run: () => navigator.clipboard.writeText(`scribe process --job ${id}`).then(() => toast("コピーしました"), () => toast("コピーできませんでした", true)) });
  }
  return out;
}

async function deleteJob(v) {
  const ok = await confirmDialog({ title: "本当に削除しますか？", danger: true, ok: "削除する", body: [
    `「${v.name}」(${v.job_id}) のジョブを削除します。`,
    "音声のコピー・文字起こし・話者名・手で直した内容が完全に消え、元に戻せません。",
    "書き出したファイルと、元の音声・動画ファイルは残ります。"] });
  if (!ok) return;
  try {
    await api(`/api/jobs/${enc(v.job_id)}`, { method: "DELETE" });
    jobs.delete(v.job_id);
    if (route.id === v.job_id) {
      editor?.destroy(); // releases the audio file, and there is nothing left to save
      editor = null;
      location.hash = "";
    }
    renderList();
    toast("削除しました");
  } catch (err) {
    toast(err.code === "job_busy" ? `削除できません: ${err.message}` : `削除できませんでした: ${err.message}`, true);
  }
}

let menu = null;
function closeMenu(refocus) {
  if (!menu) return;
  menu.el.remove();
  if (refocus) menu.from?.focus();
  menu = null;
}
function openMenu(e, id) {
  e.preventDefault();
  closeMenu();
  const v = jobs.get(id);
  if (!v) return;
  const items = [...jobActions(v),
    { label: "Workflow を開く", run: () => (location.hash = `#/job/${enc(id)}`) },
    { label: "Preview を開く", disabled: !canEdit(v), run: () => (location.hash = `#/job/${enc(id)}/speakers`) },
    { label: "削除…", danger: true, disabled: ["running", "queued", "paused"].includes(kind(v)),
      hint: ["running", "queued", "paused"].includes(kind(v)) ? "処理中・待機中・一時停止中は削除できません" : null,
      run: () => deleteJob(v) }];
  const buttons = items.map((it) => h("button", { role: "menuitem", disabled: !!it.disabled, class: it.danger ? "danger" : null,
                                                  onclick: () => { closeMenu(true); it.run(); } },
    it.label, it.hint ? h("small", {}, it.hint) : null));
  const el = h("div", { class: "menu", role: "menu", "aria-label": `${v.name} の操作`,
                        onkeydown: (ev) => {
                          const live = buttons.filter((b) => !b.disabled), i = live.indexOf(document.activeElement);
                          if (ev.key === "Escape" || ev.key === "Tab") { ev.preventDefault(); closeMenu(true); }
                          else if (ev.key === "ArrowDown") { ev.preventDefault(); live[(i + 1) % live.length]?.focus(); }
                          else if (ev.key === "ArrowUp") { ev.preventDefault(); live[(i - 1 + live.length) % live.length]?.focus(); }
                        } }, buttons);
  document.body.append(el);
  const r = e.currentTarget.getBoundingClientRect();
  const x = e.clientX || r.left + 24, y = e.clientY || r.bottom; // keyboard-opened menus have no pointer position
  el.style.left = `${Math.min(x, innerWidth - el.offsetWidth - 8)}px`;
  el.style.top = `${Math.min(y, innerHeight - el.offsetHeight - 8)}px`;
  menu = { el, from: e.currentTarget };
  buttons.find((b) => !b.disabled)?.focus();
}
document.addEventListener("pointerdown", (e) => { if (menu && !menu.el.contains(e.target)) closeMenu(); });
window.addEventListener("blur", () => closeMenu());
window.addEventListener("resize", () => closeMenu());

// --- job detail --------------------------------------------------------
function exportMenu(v, disabled = false) {
  if (v.stages.merge?.status !== "completed") return null;
  const link = h("a", { class: "btn", download: "", href: `/api/jobs/${enc(v.job_id)}/export?format=${exportFmt}`,
                        "aria-disabled": disabled ? "true" : null,
                        title: disabled ? "未保存の話者名があります。保存してから書き出してください" : "現在の話者名で書き出します" },
    "ダウンロード");
  const sel = h("select", { class: "btn", "aria-label": "書き出し形式",
                            onchange: (e) => { exportFmt = e.target.value; link.href = `/api/jobs/${enc(v.job_id)}/export?format=${exportFmt}`; } },
    FORMATS.map(([f, label]) => h("option", { value: f, selected: f === exportFmt }, label)));
  return h("span", { class: "actions" }, sel, link);
}

function progressBlock(v) {
  const p = v.progress;
  const segs = STAGES.map(([s, label]) => {
    const st = v.stages[s]?.status;
    let width = st === "completed" ? 100 : 0, cls = st === "completed" ? "done" : "";
    if (v.running === s) {
      if (p?.total) width = Math.min(100, (100 * (p.n || 0)) / p.total);
      else cls = "indet";
    }
    return h("div", { class: `seg ${cls}`, style: `flex:${WEIGHTS[s]}`, title: label }, h("i", { style: `width:${width}%` }));
  });
  let now = "";
  if (v.running) {
    const label = STAGE_LABEL[v.running];
    const amount = p?.total ? ` ${Math.round(p.n || 0)}/${Math.round(p.total)}${p.unit === "s" ? "秒" : ` ${p.unit}`}` : "";
    now = `${label}${p?.attempt ? ` (${p.attempt})` : ""}${amount} · 経過 ${fmtTime(since(p?.started_at))}`;
    if (p?.text) now += ` · 「${p.text.replace(/^,\s*/, "")}」`;
  } else if (kind(v) === "done") now = "処理はすべて完了しています";
  return h("section", { class: "progress", "aria-label": "全体の進捗" },
    h("div", { class: "row1" }, h("span", {}, statusText(v)), h("span", { class: "pct" }, `${Math.round(v.pct)}%`)),
    h("div", { class: "pbar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100",
               "aria-valuenow": String(Math.round(v.pct)), "aria-label": "全体の進捗" }, segs),
    now ? h("div", { class: "now", title: now }, now) : null);
}

const NODES = [
  { id: "input", label: "入力", col: 0, row: 0.5 },
  { id: "audio", col: 1, row: 0.5 },
  { id: "diarize", col: 2, row: 0 },
  { id: "transcribe", col: 2, row: 1 },
  { id: "samples", col: 3, row: 0 },
  { id: "fill", col: 3, row: 1 },
  { id: "merge", col: 4, row: 1 },
  { id: "naming", label: "話者名入力", col: 5, row: 0.5 },
  { id: "export", label: "書き出し", col: 6, row: 0.5 },
];
const EDGES = [["input", "audio"], ["audio", "diarize"], ["audio", "transcribe"], ["diarize", "samples"], ["diarize", "fill"],
               ["transcribe", "fill"], ["fill", "merge"], ["samples", "naming"], ["merge", "naming"], ["naming", "export"]];

function nodeState(v, id) {
  if (id === "input") return "done";
  if (id === "naming") return v.status === "speaker_identification_required" ? "action"
    : (v.status === "ready" || v.status === "exported") ? "done" : "pending";
  if (id === "export") return v.status === "exported" ? "done" : "pending";
  const st = v.stages[id]?.status;
  if (st === "completed") return "done";
  if (st === "failed") return "failed";
  if (st === "running") return v.running === id ? "running" : "failed"; // running but the process died
  return "pending";
}

function nodeSub(v, id, state) {
  const st = v.stages[id];
  if (id === "input") return v.duration ? fmtTime(v.duration) : "";
  if (id === "naming") return state === "action" ? "要対応" : state === "done" ? "完了" : "";
  if (id === "export") return state === "done" ? `${Object.keys(v.exports || {}).length} 形式` : "";
  if (state === "done" && id === "fill") return st.clips == null ? "未実施" : st.clips ? `${st.clips} 区間を補完` : "抜けなし";
  if (state === "done") return `${st.secs ?? 0}秒${st.backend ? ` · ${st.backend.split("/")[0]}` : ""}`;
  if (state === "running") {
    const p = v.progress;
    return p?.total ? `${Math.round((100 * (p.n || 0)) / p.total)}%` : fmtTime(since(p?.started_at));
  }
  if (state === "failed") return "失敗";
  return "";
}

function flowSvg(v) {
  // top-to-bottom so it stays legible in a narrow side-panel browser
  const W = 150, H = 50, GX = 24, GY = 26, P = 8;
  const pos = Object.fromEntries(NODES.map((n) => [n.id, { x: P + n.row * (W + GX), y: P + n.col * (H + GY) }]));
  const levels = Math.max(...NODES.map((n) => n.col)) + 1;
  const width = P * 2 + 2 * W + GX, height = P * 2 + levels * H + (levels - 1) * GY;
  const svg = h("svg:svg", { viewBox: `0 0 ${width} ${height}`, role: "group", "aria-label": "処理の流れ" });
  for (const [a, b] of EDGES) {
    const A = pos[a], B = pos[b], x1 = A.x + W / 2, y1 = A.y + H, x2 = B.x + W / 2, y2 = B.y, my = (y1 + y2) / 2;
    svg.append(h("svg:path", { class: `edge ${nodeState(v, a) === "done" ? "done" : ""}`,
                               d: `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}` }));
  }
  for (const n of NODES) {
    const { x, y } = pos[n.id], state = nodeState(v, n.id), label = n.label || STAGE_LABEL[n.id];
    const select = () => { selectedNode = n.id; renderDetail(jobs.get(v.job_id) || v); $(`[data-node="${n.id}"]`)?.focus(); };
    const g = h("svg:g", { class: `node ${state}${selectedNode === n.id ? " selected" : ""}`, tabindex: "0", role: "button",
                           "data-node": n.id, "aria-label": `${label}: ${{ done: "完了", running: "実行中", failed: "失敗", action: "要対応", pending: "未着手" }[state]}`,
                           onclick: select, onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(); } } },
      h("svg:rect", { class: "box", x, y, width: W, height: H, rx: 10 }),
      h("svg:text", { class: "label", x: x + 12, y: y + 21 }, label),
      h("svg:text", { class: "subl", x: x + 12, y: y + 39 }, nodeSub(v, n.id, state)));
    const cx = x + W - 15, cy = y + 15;
    if (state === "running") {
      g.append(h("svg:circle", { cx, cy, r: 7, fill: "none", stroke: "var(--track)", "stroke-width": 2.5 }),
               h("svg:circle", { class: "spin", cx, cy, r: 7, fill: "none", stroke: "var(--accent)", "stroke-width": 2.5,
                                 "stroke-dasharray": "11 33", "stroke-linecap": "round" }));
    } else if (state !== "pending") {
      const color = { done: "var(--ok)", failed: "var(--bad)", action: "var(--warn)" }[state];
      g.append(h("svg:circle", { cx, cy, r: 8, fill: color }),
               h("svg:text", { class: "mark", x: cx, y: cy + 4, "text-anchor": "middle" }, KIND_MARK[state]));
    }
    svg.append(g);
  }
  return svg;
}

function dl(rows) {
  return h("dl", {}, rows.filter(([, v]) => v != null && v !== "" && !(Array.isArray(v) && !v.length))
    .flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

function nodeDetail(v) {
  const id = selectedNode || v.running || (kind(v) === "action" ? "naming" : null)
    || STAGES.map(([s]) => s).find((s) => nodeState(v, s) === "failed");
  if (!id) return null;
  const label = NODES.find((n) => n.id === id)?.label || STAGE_LABEL[id];
  let body;
  if (id === "input") {
    body = dl([["ファイル", v.input], ["長さ", v.duration ? fmtTime(v.duration) : null], ["ジョブ ID", v.job_id]]);
  } else if (id === "naming") {
    const unnamed = v.speakers || [];
    body = h("div", {},
      unnamed.length ? h("p", {}, `名前が付いていない話者が ${unnamed.length} 人います。`) : h("p", {}, kind(v) === "done" ? "全員に名前が付いています。" : "話者分離の完了後に入力できます。"),
      canEdit(v) ? h("a", { class: "btn primary", href: `#/job/${enc(v.job_id)}/speakers` }, "Preview を開く") : null);
  } else if (id === "export") {
    const entries = Object.entries(v.exports || {}).flatMap(([fmt, es]) => es.map((e) => `${fmt}: ${e.path || "(標準出力)"}`));
    body = entries.length ? h("ul", {}, entries.map((e) => h("li", {}, e)))
      : h("p", {}, "まだファイルに書き出していません。エージェントの scribe export か、上のダウンロードで書き出せます。");
  } else {
    const st = v.stages[id] || {}, p = v.running === id ? v.progress : null, state = nodeState(v, id);
    body = dl([
      ["状態", { done: "完了", running: "実行中", failed: "失敗", pending: "未着手" }[state]],
      ["バックエンド", st.backend || p?.attempt],
      ["所要時間", st.secs != null ? `${st.secs}秒` : p ? `${fmtTime(since(p.started_at))} 経過` : null],
      ["進捗", p ? (p.total ? `${Math.round(p.n || 0)} / ${Math.round(p.total)} ${p.unit}` : p.desc || "計測できない処理中") : null],
      ["補完", id !== "fill" || state !== "done" ? null
        : st.clips == null ? h("span", {}, "この機能の追加前に処理されたジョブです。", h("code", {}, `scribe process --job ${v.job_id}`), " で補完できます")
        : st.clips ? `文字が付かなかった発話 ${st.gap_seconds}秒 (${st.clips} 区間) を個別に文字起こし` : "抜けはありませんでした"],
      ["試した順", st.attempts?.length ? h("ul", {}, [...st.attempts.map((a) => h("li", {}, `✕ ${a}`)), st.backend ? h("li", {}, `✓ ${st.backend}`) : null]) : null],
      ["エラー", st.error ? h("span", { class: "err" }, st.error) : state === "failed" && !st.error ? "処理プロセスが途中で終了しました" : null],
    ]);
  }
  return h("section", { class: "detail", "aria-live": "polite" }, h("h3", {}, label), body);
}

function warningBanner(v) {
  const w = v.warnings.find((x) => x.code === "untranscribed_speech");
  if (!w) return null;
  const who = [...new Set(w.spans.map((g) => g.speaker_id))].join(", ");
  return h("div", { class: "banner warn", role: "status" },
    h("span", {}, `文字起こしされていない発話が ${w.seconds}秒 (${w.spans.length} 区間、${who}) あります。`),
    canEdit(v) ? h("a", { class: "btn", href: `#/job/${enc(v.job_id)}/speakers` }, "Preview で確認") : null);
}

// Title, status, actions and the Workflow / Preview tabs (plain links: routing and unsaved-edit guards stay in hashchange)
function jobHead(v, id, view, actions) {
  const k = v && kind(v);
  const tab = (target, label, href, enabled, extra) =>
    h("a", { class: "tab", href, "aria-current": view === target ? "page" : null,
             "aria-disabled": enabled ? null : "true", title: enabled ? null : "話者分離が終わると使えます" }, label, extra);
  return [
    h("header", { class: "top" },
      h("div", { class: "title" }, h("h2", { title: v?.input }, v?.name || id),
        v ? h("span", { class: `badge ${k}` }, statusText(v)) : null),
      h("div", { class: "actions" }, actions)),
    v ? h("p", { class: "meta" }, `${v.job_id} · ${fmtDate(v.created_at)}${v.duration ? ` · 音声 ${fmtTime(v.duration)}` : ""}`) : null,
    h("nav", { class: "tabs", "aria-label": "表示の切り替え" },
      tab("detail", "Workflow", `#/job/${enc(id)}`, true, v?.running ? h("span", { class: "icon running", "aria-hidden": "true" }) : null),
      tab("speakers", "Preview", `#/job/${enc(id)}/speakers`, !v || canEdit(v),
        k === "action" ? h("span", { class: "dot", title: "名前の入力待ち" }) : null)),
  ].filter(Boolean);
}

function renderDetail(v) {
  if (editor) return;
  const main = $("#main");
  const k = kind(v);
  const focused = document.activeElement?.dataset?.node;
  main.replaceChildren(...[
    ...jobHead(v, v.job_id, "detail", [
      ...jobActions(v).filter((a) => a.label !== "再開コマンドをコピー")
        .map((a) => h("button", { class: "btn", disabled: !!a.disabled, title: a.hint, onclick: a.run }, a.label)),
      exportMenu(v)]),
    k === "paused"
      ? h("div", { class: "banner warn" }, "一時停止中です。再開すると、止めた段階の最初から処理します。",
          h("button", { class: "btn primary", onclick: () => control(v.job_id, { paused: false }, "再開しました") }, "再開"))
      : null,
    k === "stopped" || k === "failed"
      ? h("div", { class: `banner ${k === "failed" ? "bad" : "warn"}` },
          k === "failed" ? "処理に失敗しました。" : "処理が途中で止まっています。",
          h("span", {}, "続きから再開するには ", h("code", {}, `scribe process --job ${v.job_id}`), " を実行してください。"))
      : null,
    v.warnings?.length ? warningBanner(v) : null,
    progressBlock(v),
    h("div", { class: "flowgrid" }, h("section", { class: "flow" }, flowSvg(v)), nodeDetail(v)),
  ].filter(Boolean)); // replaceChildren would print null as text
  if (focused) $(`[data-node="${focused}"]`)?.focus();
}

// --- speaker editor ----------------------------------------------------
async function openEditor(id) {
  const main = $("#main");
  main.className = "editor";
  main.replaceChildren(h("p", { class: "placeholder" }, "読み込み中…"));
  const base = `/api/jobs/${enc(id)}`;
  let sp, tl, pk;
  try {
    [sp, tl, pk] = await Promise.all([api(`${base}/speakers`), api(`${base}/timeline`), api(`${base}/peaks?n=4000`)]);
  } catch (err) {
    if (route.id === id) main.replaceChildren(h("div", { class: "banner bad" }, `読み込めませんでした: ${err.message}`,
      h("a", { class: "btn", href: `#/job/${enc(id)}` }, "戻る")));
    return;
  }
  if (route.id !== id || route.view !== "speakers" || editor) return; // navigated away meanwhile
  editor = buildEditor(id, base, sp, tl, pk);
  const v = jobs.get(id);
  if (v) editor.onJob(v);
}

function buildEditor(id, base, sp, tl, pk) {
  const main = $("#main");
  const speakers = sp.speakers.slice().sort((a, b) => b.speech_seconds - a.speech_seconds);
  const ids = speakers.map((s) => s.speaker_id);
  const saved = Object.fromEntries(speakers.map((s) => [s.speaker_id, s.name]));
  const draft = {};
  const tag = sp.diarize_tag;
  const duration = tl.duration || Math.max(1, ...tl.turns.map((t) => t.end), ...tl.segments.map((s) => s.end));
  const norm = (s) => (s ?? "").trim() || null;
  const nameOf = (sid) => (sid in draft ? norm(draft[sid]) : saved[sid]);
  const dirtyIds = () => ids.filter((sid) => sid in draft && norm(draft[sid]) !== saved[sid]);
  // transcript corrections: what the ASR said (origText), saved corrections (savedTexts), typed here (textDraft)
  const segKey = (s) => `${Math.round(s.start * 1000)}-${Math.round(s.end * 1000)}`; // mirrors job.seg_key
  const origText = Object.fromEntries(tl.segments.map((s) => [segKey(s), s.text]));
  const savedTexts = { ...tl.edits };
  const textDraft = {};
  const savedText = (k) => (k in savedTexts ? savedTexts[k] : origText[k]);
  const textOf = (k) => (k in textDraft ? textDraft[k].trim() : savedText(k));
  const dirtyTexts = () => Object.keys(textDraft).filter((k) => textDraft[k].trim() !== savedText(k));

  // audio + transport
  const audio = h("audio", { preload: "metadata", src: `${base}/audio` });
  let stopAt = null, raf = 0, zoom = 1, laneW = 800;
  const playBtn = h("button", { class: "btn", "aria-label": "再生", onclick: () => toggle() }, "▶ 再生");
  const timeEl = h("span", { class: "time" }, `0:00 / ${fmtTime(duration)}`);
  const zoomEl = h("input", { type: "range", min: "0", max: "5", step: "1", value: "0", "aria-label": "拡大",
                              oninput: (e) => setZoom(2 ** +e.target.value) });
  const saveBtn = h("button", { class: "btn primary", disabled: true, onclick: () => save(), title: "Ctrl+S" }, "保存 (Ctrl+S)");
  const dirtyNote = h("span", { class: "dirty-note", "aria-live": "polite" });
  const exportSlot = h("span");
  const banner = h("div", { class: "banner warn", hidden: true });

  // tracks
  const tracks = h("div", { class: "tracks" });
  const inner = h("div", { class: "tracks-inner" });
  const playhead = h("div", { class: "playhead" });
  const timeAt = (e, el) => { const r = el.getBoundingClientRect(); return ((e.clientX - r.left) / r.width) * duration; };

  const amp = Math.max(1e-3, ...pk.max.map(Math.abs), ...pk.min.map(Math.abs));
  const top = pk.max.map((y, i) => `${i},${(-y).toFixed(3)}`), bottom = pk.min.map((y, i) => `${i},${(-y).toFixed(3)}`).reverse();
  const wave = h("svg:svg", { viewBox: `0 ${-amp} ${pk.n} ${2 * amp}`, preserveAspectRatio: "none" },
    h("svg:path", { d: `M${top.join("L")}L${bottom.join("L")}Z` }));
  const waveLane = h("div", { class: "lane", onclick: (e) => seek(timeAt(e, waveLane), true) }, wave);
  inner.append(h("div", { class: "trow wave" }, h("div", { class: "thead" }, "波形 (クリックで再生)"), waveLane));

  const lanes = new Map();
  const inputs = [];
  for (const s of speakers) {
    const sid = s.speaker_id;
    const turns = tl.turns.filter((t) => t.speaker_id === sid);
    const svg = h("svg:svg", { viewBox: `0 0 ${duration} 1`, preserveAspectRatio: "none" },
      turns.map((t) => h("svg:rect", { x: t.start, y: 0.2, width: Math.max(t.end - t.start, duration / 4000), height: 0.6,
                                        "data-start": t.start })),
      tl.gaps.filter((g) => g.speaker_id === sid).map((g) =>  // speech that has no text: marked over the turn
        h("svg:rect", { class: "gap", x: g.start, y: 0, width: g.end - g.start, height: 1, "data-start": g.start })));
    const lane = h("div", { class: "lane", title: `${sid} の発話区間 (クリックで再生)`,
                            onclick: (e) => seek(e.target.dataset?.start != null ? +e.target.dataset.start : timeAt(e, lane), true) }, svg);
    const input = h("input", { type: "text", value: s.name || "", placeholder: `${sid} の名前`, "aria-label": `${sid} の名前`,
                               oninput: (e) => { draft[sid] = e.target.value; applyNames(); },
                               onkeydown: (e) => { if (e.key === "Enter") inputs[inputs.indexOf(input) + 1]?.focus(); } });
    inputs.push(input);
    const span = s.sample_span;
    const sample = h("button", { class: "play", disabled: !span, title: "代表音声を再生", "aria-label": `${sid} の代表音声を再生`,
                                 onclick: () => play(span[0], span[1]) }, "▶");
    const unnamedTag = h("span", { class: "tag" }, "未命名");
    const row = h("div", { class: "trow" },
      h("div", { class: "thead" },
        h("div", { class: "top-line" }, sample, input),
        h("div", { class: "m" }, h("span", {}, `${sid} · 発話 ${fmtTime(s.speech_seconds)}`), unnamedTag,
          s.transcribed_ratio != null && s.transcribed_ratio < 0.8
            ? h("span", { class: "tag bad", title: "この話者の発話のうち、文字が付いている割合" },
                `文字起こし ${Math.round(s.transcribed_ratio * 100)}%`) : null),
        s.sample_text ? h("div", { class: "sample", title: s.sample_text }, `「${s.sample_text}」`) : null),
      lane);
    lanes.set(sid, { row, input, unnamedTag });
    inner.append(row);
  }
  inner.append(playhead);
  tracks.append(inner);

  // transcript
  // rows = transcript + untranscribed speech (so a missing passage is visible where it happened)
  const segs = [...tl.segments, ...tl.gaps.map((g) => ({ ...g, gap: true }))].sort((a, b) => a.start - b.start);
  const chips = new Map();
  const texts = new Map(); // seg key -> text element
  const items = segs.map((s) => {
    const chip = h("span", { class: "chip" });
    if (!chips.has(s.speaker_id)) chips.set(s.speaker_id, []);
    chips.get(s.speaker_id).push(chip);
    const k = s.gap ? null : segKey(s);
    const text = s.gap
      ? h("span", { class: "text" }, `(文字起こしされていない発話 ${Math.round(s.end - s.start)}秒 · クリックで再生)`)
      : h("span", { class: "text", tabindex: "0", role: "button", "aria-label": `${fmtTime(s.start)} の文字起こし。Enter で修正`,
                    ondblclick: () => editText(k),
                    onkeydown: (e) => { if (e.key === "Enter" || e.key === "F2") { e.preventDefault(); editText(k); } } });
    if (k) texts.set(k, text);
    return h("li", { class: s.gap ? "gap" : null },
      h("div", { class: "seg", onclick: (e) => { if (!e.target.closest("textarea")) seek(s.start, true); } },
        h("button", { class: "t", "aria-label": `${fmtTime(s.start)} から再生`,
                      onclick: (e) => { e.stopPropagation(); seek(s.start, true); } }, fmtTime(s.start)),
        chip, text));
  });
  for (const k of texts.keys()) renderText(k);
  const transcript = segs.length ? h("ol", { class: "transcript", "aria-label": "文字起こし" }, items)
    : h("p", { class: "placeholder" }, "文字起こしはまだありません (処理が終わると表示されます)。");

  const actions = h("span", { class: "actions" }, dirtyNote, saveBtn, exportSlot);
  const head = h("div", { class: "head-slot" }, jobHead(jobs.get(id), id, "speakers", actions));
  main.replaceChildren(
    head,
    banner,
    h("div", { class: "toolbar" }, playBtn, timeEl, h("label", {}, "拡大", zoomEl),
      h("span", { class: "time" }, "同じ名前の話者は 1 人として書き出されます · 文字起こしの行はダブルクリックで修正")),
    tracks, transcript, audio);

  // --- behaviour ---
  function layout() {
    const head = parseFloat(getComputedStyle(tracks).getPropertyValue("--head")) || 260;
    const center = (tracks.scrollLeft + (tracks.clientWidth - head) / 2) / laneW;
    laneW = Math.max(200, tracks.clientWidth - head - 2) * zoom;
    inner.style.setProperty("--lane-w", `${laneW}px`);
    tracks.scrollLeft = center * laneW - (tracks.clientWidth - head) / 2;
    tick(true);
  }
  function setZoom(z) { zoom = z; layout(); }
  const onResize = () => layout();
  window.addEventListener("resize", onResize);

  let cur = -1;
  function highlight(t) {
    let lo = 0, hi = segs.length - 1, i = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (segs[m].start <= t) { i = m; lo = m + 1; } else hi = m - 1; }
    if (i === cur) return;
    items[cur]?.classList.remove("current");
    cur = i;
    if (i >= 0) {
      items[i].classList.add("current");
      if (!audio.paused) items[i].scrollIntoView({ block: "nearest" });
    }
  }
  function tick(once = false) {
    const t = audio.currentTime;
    if (stopAt != null && t >= stopAt) { audio.pause(); stopAt = null; }
    const head = parseFloat(getComputedStyle(tracks).getPropertyValue("--head")) || 260;
    const x = head + (t / duration) * laneW;
    playhead.style.left = `${x}px`;
    if (!audio.paused && (x < tracks.scrollLeft + head || x > tracks.scrollLeft + tracks.clientWidth - 20)) {
      tracks.scrollLeft = x - head - 40;
    }
    timeEl.textContent = `${fmtTime(t)} / ${fmtTime(duration)}`;
    highlight(t);
    raf = !once && !audio.paused ? requestAnimationFrame(() => tick()) : 0;
  }
  audio.addEventListener("play", () => { playBtn.textContent = "❚❚ 停止"; playBtn.setAttribute("aria-label", "停止"); if (!raf) tick(); });
  audio.addEventListener("pause", () => { playBtn.textContent = "▶ 再生"; playBtn.setAttribute("aria-label", "再生"); tick(true); });
  audio.addEventListener("seeked", () => tick(true));
  audio.onerror = () => toast("音声を読み込めませんでした。scribe serve が起動しているか確認してください", true);

  function seek(t, andPlay) {
    t = Math.max(0, Math.min(duration, t));
    if (andPlay) return play(t);
    stopAt = null;
    audio.currentTime = t;
  }
  function play(from = null, until = null) {
    if (audio.error) audio.load(); // retry, e.g. after `scribe serve` was restarted
    if (from != null) audio.currentTime = from;
    stopAt = until;
    audio.play().catch((e) => { if (e.name !== "AbortError") toast(`再生できません: ${e.message}`, true); });
  }
  function toggle() {
    if (audio.paused) play(); else audio.pause();
  }

  function renderText(k) {
    const el = texts.get(k);
    if (el.querySelector("textarea")) return; // being edited
    const t = textOf(k), dirty = k in textDraft && textDraft[k].trim() !== savedText(k);
    el.textContent = (t || origText[k]) + (dirty ? " *" : "");
    el.classList.toggle("edited", t !== origText[k]);
    el.classList.toggle("removed", !t);
    el.classList.toggle("dirty", dirty);
    el.title = !t ? "削除 (書き出しに含めません)。ダブルクリックで戻せます"
      : t !== origText[k] ? `修正済み。元の文字起こし: ${origText[k]}` : "ダブルクリックで修正";
  }

  function editText(k) {
    const el = texts.get(k);
    if (el.querySelector("textarea")) return;
    const ta = h("textarea", { rows: "1", value: textOf(k),
                               "aria-label": "文字起こしを修正。Enter で確定、Esc で取り消し、空にすると行を削除" });
    const grow = () => { ta.style.height = "auto"; ta.style.height = `${ta.scrollHeight}px`; };
    let done = false;
    const finish = (keep) => {
      if (done) return;
      done = true;
      if (keep) textDraft[k] = ta.value;
      el.replaceChildren();
      renderText(k);
      applyNames();
      if (keep !== "blur") el.focus();
    };
    ta.addEventListener("input", grow);
    ta.addEventListener("keydown", (e) => {
      if (e.isComposing || e.keyCode === 229) return; // Enter confirming a Japanese IME conversion
      // stopPropagation: the line itself opens the editor on Enter, which would reopen it at once
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.stopPropagation(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") finish(true); // then the global Ctrl+S saves
    });
    ta.addEventListener("blur", () => finish("blur"));
    el.replaceChildren(ta);
    grow();
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  }

  function applyNames() {
    const colors = new Map();
    const keyOf = (sid) => (nameOf(sid) ? `n:${nameOf(sid)}` : `i:${sid}`);
    for (const sid of ids) if (!colors.has(keyOf(sid))) colors.set(keyOf(sid), PALETTE[colors.size % PALETTE.length]);
    const dirty = new Set(dirtyIds());
    for (const sid of ids) {
      const name = nameOf(sid), c = colors.get(keyOf(sid)), L = lanes.get(sid);
      L.row.style.setProperty("--c", c);
      L.row.classList.toggle("unnamed", !name);
      L.row.classList.toggle("dirty", dirty.has(sid));
      L.unnamedTag.hidden = !!name;
      for (const chip of chips.get(sid) || []) {
        chip.textContent = (name || sid) + (dirty.has(sid) ? " *" : "");
        chip.style.setProperty("--c", c);
      }
    }
    for (const chip of chips.get(null) || []) chip.textContent = "UNKNOWN";
    const n = dirty.size + dirtyTexts().length;
    saveBtn.disabled = !n;
    dirtyNote.textContent = n ? `未保存 ${n} 件` : "";
    const v = jobs.get(id);
    exportSlot.replaceChildren(v ? exportMenu(v, n > 0) || "" : "");
  }

  let saving = false;
  async function save() {
    const changed = dirtyIds(), changedTexts = dirtyTexts();
    if (saving) return;
    if (!changed.length && !changedTexts.length) return toast("変更はありません");
    saving = true;
    saveBtn.disabled = true;
    try {
      const r = await api(`${base}/speakers`, {
        method: "PUT", headers: { "Content-Type": "application/json", "If-Match": tag },
        body: JSON.stringify({ names: Object.fromEntries(changed.map((sid) => [sid, norm(draft[sid])])),
                               texts: Object.fromEntries(changedTexts.map((k) => [k, textDraft[k].trim()])) }),
      });
      for (const sid of ids) {
        saved[sid] = r.names[sid] ?? null;
        if (sid in draft && norm(draft[sid]) === saved[sid]) delete draft[sid]; // keeps edits typed during the save
      }
      for (const k of Object.keys(savedTexts)) delete savedTexts[k];
      Object.assign(savedTexts, r.texts);
      for (const k of changedTexts) {
        if (k in textDraft && textDraft[k].trim() === savedText(k)) delete textDraft[k];
        renderText(k);
      }
      const n = r.refreshed.length;
      toast(n ? `保存しました。書き出し済みのファイル ${n} 件も更新しました` : "保存しました");
    } catch (err) {
      if (err.code === "stale") showStale(err.message);
      else if (err.code === "job_busy") toast("処理中のため保存できません。処理が終わってからもう一度 Ctrl+S を押してください", true);
      else toast(`保存できませんでした: ${err.message}`, true);
    } finally {
      saving = false;
      applyNames();
    }
  }

  function showStale(detail) {
    banner.replaceChildren("処理がやり直されたため、この画面の内容は古くなっています。変更は保存されていません。",
      h("button", { class: "btn", onclick: () => { for (const k in draft) delete draft[k]; renderMain(); } }, "再読み込み"));
    banner.hidden = false;
  }

  function onJob(v) {
    head.replaceChildren(...jobHead(v, id, "speakers", actions));
    if (v.diarize_tag !== tag) showStale();
    else if (v.running || v.queue) {
      banner.replaceChildren(v.running ? `処理中です (${STAGE_LABEL[v.running]})。処理が終わるまで保存できません。`
        : `${statusText(v)}です。処理が終わるまで保存できません。`);
      banner.hidden = false;
    } else banner.hidden = true;
    if (!dirtyIds().length) applyNames(); // keeps export menu in sync without touching edits
  }

  function destroy() {
    audio.onerror = null;
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    cancelAnimationFrame(raf);
    window.removeEventListener("resize", onResize);
  }

  applyNames();
  requestAnimationFrame(layout);
  return { get dirty() { return dirtyIds().length + dirtyTexts().length > 0; }, save, toggle, onJob, destroy };
}

connect();
renderList();
renderMain();
