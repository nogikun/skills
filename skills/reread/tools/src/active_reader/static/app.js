'use strict';
// Reader UI。AI 機能は持たない（説明・評価・出題はチャットの reread スキルが行い、結果を書き戻す）
const KEYS = { u: 'unknown', e: 'explain', q: 'quiz' };
const LABEL = { unknown: '分からない', explain: '自分で説明', quiz: '問題にする' };
const NOTE_LABEL = {
  unknown: '何が分からないか',
  explain: '自分の言葉での説明',
  quiz: 'どの観点から問いたいか',
};
const STATUS_LABEL = {
  new: '未着手', learning: '学習中', understood: '理解した', review_required: '要復習', mastered: '定着',
};
const NAMES = {
  summary: '説明', prerequisites: '前提知識', meaning_in_context: '本文での意味',
  examples: '例', related: '関連概念', correct: '正しく説明できている点',
  missing: '不足している点', wrong: '誤っている点', vague: '曖昧な点',
  improved: '改善された説明例', score: 'スコア', score_reason: 'スコアの根拠',
  feedback: 'フィードバック', model_answer: '模範解答', raw: 'AI の回答',
};
const CTX = 32;

const $ = (s) => document.querySelector(s);
const reader = $('#reader');
let doc = null; // 表示中の文書
let anns = [];
let current = null;
const ranges = new Map(); // ann.id -> Range
const timers = new Map(); // ann.id -> [timeout, ann]  未保存のコメント
const saves = new Set(); // 保存の通信中に終了してコメントを失わないよう、完了まで待つ
let readingEnded = false;
// 切り替え後に遅れて届く保存が別の文書に入らないよう、文書を覚えておく
const docOf = new WeakMap();
const annPath = (a) => `/api/docs/${docOf.get(a)}/annotations/${a.id}`;
const refOf = (a) => `${doc.source_path}#${a.id}`;

function h(tag, props = {}, ...kids) {
  const el = Object.assign(document.createElement(tag), props);
  el.append(...kids.flat(Infinity).filter((k) => k != null && k !== false));
  return el;
}

// Tabler Icons v3.49.0（MIT, https://tabler.io/icons）から使う分だけ同梱（オフラインでも表示できるように）
/*
MIT License

Copyright (c) 2020-2026 Paweł Kuna

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
*/
const ICONS = {
  'arrow-back-up': '<path d="M9 14l-4 -4l4 -4"/><path d="M5 10h11a4 4 0 1 1 0 8h-1"/>',
  'book': '<path d="M3 19a9 9 0 0 1 9 0a9 9 0 0 1 9 0"/><path d="M3 6a9 9 0 0 1 9 0a9 9 0 0 1 9 0"/><path d="M3 6l0 13"/><path d="M12 6l0 13"/><path d="M21 6l0 13"/>',
  'checklist': '<path d="M9.615 20h-2.615a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2h8a2 2 0 0 1 2 2v8"/><path d="M14 19l2 2l4 -4"/><path d="M9 8h4"/><path d="M9 12h2"/>',
  'copy': '<path d="M7 9.667a2.667 2.667 0 0 1 2.667 -2.667h8.666a2.667 2.667 0 0 1 2.667 2.667v8.666a2.667 2.667 0 0 1 -2.667 2.667h-8.666a2.667 2.667 0 0 1 -2.667 -2.667l0 -8.666"/><path d="M4.012 16.737a2.005 2.005 0 0 1 -1.012 -1.737v-10c0 -1.1 .9 -2 2 -2h10c.75 0 1.158 .385 1.5 1"/>',
  'file-plus': '<path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M17 21h-10a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2h7l5 5v11a2 2 0 0 1 -2 2"/><path d="M12 11l0 6"/><path d="M9 14l6 0"/>',
  'file-text': '<path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M17 21h-10a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2h7l5 5v11a2 2 0 0 1 -2 2"/><path d="M9 9l1 0"/><path d="M9 13l6 0"/><path d="M9 17l6 0"/>',
  'help-circle': '<path d="M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0"/><path d="M12 16v.01"/><path d="M12 13a2 2 0 0 0 .914 -3.782a1.98 1.98 0 0 0 -2.414 .483"/>',
  'highlight': '<path d="M3 19h4l10.5 -10.5a2.828 2.828 0 1 0 -4 -4l-10.5 10.5v4"/><path d="M12.5 5.5l4 4"/><path d="M4.5 13.5l4 4"/><path d="M21 15v4h-8l4 -4l4 0"/>',
  'layout-sidebar-right': '<path d="M4 6a2 2 0 0 1 2 -2h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2l0 -12"/><path d="M15 4l0 16"/>',
  'layout-sidebar': '<path d="M4 6a2 2 0 0 1 2 -2h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2l0 -12"/><path d="M9 4l0 16"/>',
  'message-2': '<path d="M8 9h8"/><path d="M8 13h6"/><path d="M9 18h-3a3 3 0 0 1 -3 -3v-8a3 3 0 0 1 3 -3h12a3 3 0 0 1 3 3v8a3 3 0 0 1 -3 3h-3l-3 3l-3 -3"/>',
  'list': '<path d="M9 6l11 0"/><path d="M9 12l11 0"/><path d="M9 18l11 0"/><path d="M5 6l0 .01"/><path d="M5 12l0 .01"/><path d="M5 18l0 .01"/>',
  'arrows-sort': '<path d="M3 9l4 -4l4 4m-4 -4v14"/><path d="M21 15l-4 4l-4 -4m4 4v-14"/>',
  'send': '<path d="M10 14l11 -11"/><path d="M21 3l-6.5 18a.55 .55 0 0 1 -1 0l-3.5 -7l-7 -3.5a.55 .55 0 0 1 0 -1l18 -6.5"/>',
  'settings': '<path d="M10.325 4.317c.426 -1.756 2.924 -1.756 3.35 0a1.724 1.724 0 0 0 2.573 1.066c1.543 -.94 3.31 .826 2.37 2.37a1.724 1.724 0 0 0 1.065 2.572c1.756 .426 1.756 2.924 0 3.35a1.724 1.724 0 0 0 -1.066 2.573c.94 1.543 -.826 3.31 -2.37 2.37a1.724 1.724 0 0 0 -2.572 1.065c-.426 1.756 -2.924 1.756 -3.35 0a1.724 1.724 0 0 0 -2.573 -1.066c-1.543 .94 -3.31 -.826 -2.37 -2.37a1.724 1.724 0 0 0 -1.065 -2.572c-1.756 -.426 -1.756 -2.924 0 -3.35a1.724 1.724 0 0 0 1.066 -2.573c-.94 -1.543 .826 -3.31 2.37 -2.37c1 .608 2.296 .07 2.572 -1.065"/><path d="M9 12a3 3 0 1 0 6 0a3 3 0 0 0 -6 0"/>',
  'trash': '<path d="M4 7l16 0"/><path d="M10 11l0 6"/><path d="M14 11l0 6"/><path d="M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2 -2l1 -12"/><path d="M9 7v-3a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v3"/>',
};
const TYPE_ICON = { unknown: 'help-circle', explain: 'message-2', quiz: 'checklist' };

function icon(name, cls = '') {
  const t = document.createElement('template');
  t.innerHTML = `<svg class="ti ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`; // 同梱の固定文字列のみ
  return t.content.firstChild;
}
const typeIcon = (type) => icon(TYPE_ICON[type], type);

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function toast(msg, action) {
  const t = h('div', { className: 'toast' }, msg, action
    ? h('button', { type: 'button', className: 'link', onclick: () => { t.remove(); action[1](); } }, icon('arrow-back-up'), action[0])
    : null);
  document.body.append(t);
  setTimeout(() => t.remove(), action ? 5000 : 1400);
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch { // 権限が無い環境向けの古い方法
    const t = h('textarea', { value: text });
    document.body.append(t);
    t.select();
    const ok = document.execCommand('copy');
    t.remove();
    if (!ok) return prompt('コピーしてください', text);
  }
  toast(`コピーしました: ${text.length > 60 ? `…${text.slice(-60)}` : text}`);
}

// 行にカーソルを合わせたときだけ出るコピー。
// click だと押している間に一覧が描き直されて取りこぼすので、押した瞬間（mousedown）に実行する
const copyAction = (text, title) => h('button', {
  type: 'button',
  className: 'row-action',
  title,
  onmousedown: (ev) => { ev.preventDefault(); ev.stopPropagation(); copy(text); },
  onclick: (ev) => ev.stopPropagation(),
}, icon('copy'));

// --- 本文 textContent 上の offset <-> Range -------------------------------

function offsetOf(node, offset) {
  const r = document.createRange();
  r.selectNodeContents(reader);
  r.setEnd(node, offset);
  return r.toString().length;
}

function toRange(start, end) {
  const w = document.createTreeWalker(reader, NodeFilter.SHOW_TEXT);
  const r = document.createRange();
  let pos = 0;
  let started = false;
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    const next = pos + n.length;
    if (!started && start <= next) { r.setStart(n, start - pos); started = true; }
    if (started && end <= next) { r.setEnd(n, end - pos); return r; }
    pos = next;
  }
  return null;
}

function common(a, b, fromEnd) {
  let n = 0;
  while (n < a.length && n < b.length
    && (fromEnd ? a[a.length - 1 - n] === b[b.length - 1 - n] : a[n] === b[n])) n++;
  return n;
}

// 位置復元: offset 一致 → 前後文脈が最も一致する出現箇所 → 見つからなければ null
function anchor(a, text) {
  const sel = a.selected_text;
  const s = a.locator.start_offset;
  if (text.slice(s, s + sel.length) === sel) return [s, s + sel.length];
  let best = null;
  let bestScore = -Infinity;
  for (let i = text.indexOf(sel); i !== -1; i = text.indexOf(sel, i + 1)) {
    const before = text.slice(Math.max(0, i - a.context_before.length), i);
    const after = text.slice(i + sel.length, i + sel.length + a.context_after.length);
    const score = common(before, a.context_before, true)
      + common(after, a.context_after, false) - Math.abs(i - s) / 1e7;
    if (score > bestScore) { bestScore = score; best = i; }
  }
  return best === null ? null : [best, best + sel.length];
}

function headingBefore(node) {
  let heading = '';
  for (const el of reader.querySelectorAll('h1, h2, h3, h4, h5, h6')) {
    if (el.contains(node) || el.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING) {
      heading = el.textContent;
    } else break;
  }
  return heading;
}

// 選択を文節の境界まで広げる。境界はサーバーが形態素解析（janome）で計算して渡す
let bounds = [];
// 専門用語: サーバーが検出（最初の出現位置）、解説は AI がチャット経由で書き戻す
let terms = [];
let glossary = {};
let currentTerm = null;
let tab = 'marks';
const termRanges = new Map(); // term -> Range

function snapToWords(start, end, text) {
  const atOrBelow = (x) => { // x 以下の境界の個数
    let lo = 0;
    let hi = bounds.length;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (bounds[m] <= x) lo = m + 1; else hi = m;
    }
    return lo;
  };
  if (bounds.length) {
    start = bounds[atOrBelow(start) - 1] ?? start;
    end = bounds[atOrBelow(end - 1)] ?? end;
  }
  while (start < end && /\s/.test(text[start])) start++;
  while (end > start && /\s/.test(text[end - 1])) end--;
  return [start, end];
}

// --- 描画 -------------------------------------------------------------------

function paint() {
  const text = reader.textContent;
  const groups = { unknown: [], explain: [], quiz: [] };
  ranges.clear();
  for (const a of anns) {
    const pos = anchor(a, text);
    const r = pos && toRange(...pos);
    if (r) { ranges.set(a.id, r); groups[a.type].push(r); }
  }
  for (const [type, rs] of Object.entries(groups)) CSS.highlights?.set(type, new Highlight(...rs));
  termRanges.clear();
  for (const t of terms) {
    const r = toRange(t.start, t.end);
    if (r) termRanges.set(t.term, r);
  }
  CSS.highlights?.set('term', new Highlight(...termRanges.values()));
  paintCurrent();
  renderList();
  renderTerms();
}

function paintCurrent() {
  const r = (tab === 'terms' && currentTerm) ? termRanges.get(currentTerm) : current && ranges.get(current.id);
  if (r) CSS.highlights?.set('current', new Highlight(r));
  else CSS.highlights?.delete('current');
}

function aiResult(a) {
  return a.type === 'unknown' ? aiView(a.ai.explanation)
    : a.type === 'explain' ? aiView(a.ai.evaluation)
      : (a.ai.questions.length ? a.ai.questions.map(quizView) : null);
}

function setTab(name) {
  tab = name;
  for (const b of document.querySelectorAll('#tabs button')) b.classList.toggle('on', b.dataset.tab === name);
  $('#list').hidden = name !== 'marks';
  $('#terms').hidden = name !== 'terms';
  paintCurrent();
  renderCaption();
}

// まだ AI が対応していないマーカー（種類ごと）
const UNDONE = {
  unknown: (a) => !a.ai.explanation,
  explain: (a) => !a.ai.evaluation,
  quiz: (a) => !a.ai.questions.length,
};
const SEND_LABEL = { unknown: '説明を頼む', explain: '評価を頼む', quiz: '出題を頼む' };

function renderCaption() {
  if (tab === 'terms') {
    const todo = terms.filter((t) => inSection(t) && !glossary[t.term]).length;
    return $('#caption').replaceChildren(todo ? `未解説 ${todo}` : '');
  }
  // 種類ごとに、未対応のマーカーをまとめて AI に送る
  $('#caption').replaceChildren(h('span', { className: 'bulk' }, Object.keys(UNDONE).map((type) => {
    const todo = anns.filter((a) => a.type === type && UNDONE[type](a) && !sent.has(a.id));
    return h('button', {
      type: 'button',
      className: 'bulk-btn',
      disabled: !todo.length,
      title: todo.length ? `未対応の「${LABEL[type]}」${todo.length} 件をまとめて AI に送る（${SEND_LABEL[type]}）`
        : `未対応の「${LABEL[type]}」はありません`,
      onclick: () => sendMarks(todo),
    }, typeIcon(type), String(todo.length), icon('send'));
  })), sortMenu());
}

// --- マーカー一覧の並べ替え（選んだ順はブラウザに記憶） -----------------------------

const SORTS = { pos: '本文の順', type: '種類ごと', new: '新しい順', todo: '未対応を先に' };
const TYPE_ORDER = Object.keys(KEYS).map((k) => KEYS[k]); // unknown, explain, quiz
let sortMode = 'pos';
try { sortMode = SORTS[localStorage.getItem('sort')] ? localStorage.getItem('sort') : 'pos'; } catch { /* noop */ }

function sortMenu() {
  const select = h('select', { className: 'sort', title: 'マーカーの並べ替え', ariaLabel: 'マーカーの並べ替え' },
    Object.entries(SORTS).map(([v, label]) => h('option', { value: v, selected: v === sortMode }, label)));
  select.onchange = () => {
    sortMode = select.value;
    try { localStorage.setItem('sort', sortMode); } catch { /* noop */ }
    renderList();
  };
  return h('label', { className: 'sort-wrap' }, icon('arrows-sort'), select);
}

function sortMarks(list) {
  const pos = (a, b) => a.locator.start_offset - b.locator.start_offset;
  const by = {
    pos,
    type: (a, b) => TYPE_ORDER.indexOf(a.type) - TYPE_ORDER.indexOf(b.type) || pos(a, b),
    new: (a, b) => (b.created_at || '').localeCompare(a.created_at || ''),
    todo: (a, b) => UNDONE[b.type](b) - UNDONE[a.type](a) || pos(a, b),
  }[sortMode];
  return [...list].sort(by);
}

// --- マーカーをエージェントに送る（用語と同じ待ち受けの仕組み） ---------------------

const sent = new Map(); // ann.id -> 送った時点の updated_at

async function sendMarks(list) {
  if (!list.length) return;
  const id = doc.id;
  const res = await api('POST', `/api/docs/${id}/ask`, { ann_ids: list.map((a) => a.id) })
    .catch((e) => alert(e.message));
  if (!res) return;
  for (const a of list) sent.set(a.id, a.updated_at);
  toast(res.agent_waiting ? `${list.length} 件を AI に送りました。説明はチャットに出ます`
    : '依頼を受け付けました。チャットで「送った依頼に対応して」と伝えてください');
  renderList();
  // 書き戻されたら（updated_at が変わったら）結果を反映する
  for (let i = 0; i < 200 && doc?.id === id && list.some((a) => sent.has(a.id)); i++) {
    await new Promise((r) => setTimeout(r, 3000));
    if (doc?.id !== id) break;
    const fresh = await api('GET', `/api/docs/${id}/annotations`).catch(() => null);
    let changed = false;
    for (const f of fresh || []) {
      const a = anns.find((x) => x.id === f.id);
      if (a && sent.has(a.id) && f.updated_at !== sent.get(a.id)) {
        Object.assign(a, { ai: f.ai, status: f.status, note: f.note, updated_at: f.updated_at });
        sent.delete(a.id);
        changed = true;
      }
    }
    if (changed) renderList();
  }
}

// 用語の状態: 一言解説あり / エージェントが解説中 / エージェントが待ち受けていない
const asking = new Map(); // term -> 'pending' | 'no-agent'

function termNote(term) {
  const g = glossary[term];
  const pending = asking.has(term);
  return h('div', {},
    g && h('p', { className: 'summary' }, g.summary || Object.values(g).find((v) => typeof v === 'string')),
    (!g || pending) && (asking.get(term) === 'no-agent'
      ? h('p', { className: 'muted' }, '依頼を受け付けました。チャットで「送った依頼に対応して」と伝えるか、用語の参照をコピーして貼ってください。')
      : h('p', { className: 'muted thinking' }, 'AI が解説しています。詳しい説明はチャットに出ます…')),
    g && h('button', { className: 'link term-retry', disabled: pending, onclick: () => askAgent(term) },
      pending ? '解説を依頼済み' : 'もう一度解説'));
}

function renderTerms() {
  const shown = terms.filter(inSection); // 読んでいる節に初出する用語だけ
  $('#tab-terms').replaceChildren(icon('book'), `用語${shown.length ? ` ${shown.length}` : ''}`);
  renderCaption();
  if (!shown.length) {
    return $('#terms').replaceChildren(h('li', { className: 'empty-row muted' },
      !doc ? '' : terms.length ? 'このセクションで初めて出てくる専門用語はありません。' : '専門用語は見つかりませんでした。'));
  }
  $('#terms').replaceChildren(...shown.flatMap((t) => {
    const row = h('li', {
      className: `term${t.term === currentTerm ? ' active' : ''}${glossary[t.term] ? '' : ' undefined'}`,
      onclick: () => showTerm(t.term, true),
    }, h('span', { className: 'item-text' }, t.term));
    return t.term === currentTerm ? [row, h('li', { className: 'expand' }, termNote(t.term))] : [row];
  }));
}

// 破線の用語をタップ → 「用語」タブを開き、解説が無ければエージェントに依頼する
function showTerm(term, scroll = false) {
  currentTerm = term;
  if (!layout.panel) togglePanel(true); // 閉じていたら開く
  if (!glossary[term] && !asking.has(term)) askAgent(term);
  setTab('terms');
  renderTerms();
  const r = termRanges.get(term);
  if (scroll && r) {
    const b = r.getBoundingClientRect();
    if (b.top < 60 || b.bottom > innerHeight - 60) r.startContainer.parentElement.scrollIntoView({ block: 'center' });
  }
  $('#terms > li.active')?.scrollIntoView({ block: 'nearest' });
  $('#terms > li.expand')?.scrollIntoView({ block: 'nearest' }); // 解説まで見えるように
}

async function askAgent(term) {
  if (asking.has(term)) return;
  const id = doc.id;
  const previous = glossary[term]?.updated_at;
  asking.set(term, 'pending');
  renderTerms();
  try {
    const res = await api('POST', `/api/docs/${id}/ask`, { term });
    asking.set(term, res.agent_waiting ? 'pending' : 'no-agent');
  } catch (e) {
    asking.delete(term);
    if (doc?.id === id) renderTerms();
    return alert(e.message);
  }
  if (doc?.id === id) renderTerms();
  // エージェントが一言解説を書き戻すまで待つ（待ち受けが後から始まっても拾えるよう、no-agent でも続ける）
  for (let i = 0; i < 200 && doc?.id === id && asking.has(term); i++) {
    await new Promise((r) => setTimeout(r, 3000));
    if (doc?.id !== id) break;
    const g = await api('GET', `/api/docs/${id}/glossary`).catch(() => null);
    if (g?.[term] && g[term].updated_at !== previous) {
      glossary = g;
      asking.delete(term);
      renderTerms();
    }
  }
  asking.delete(term);
  if (doc?.id === id) renderTerms();
}

function renderList() {
  $('#tab-marks').replaceChildren(icon('highlight'), `マーカー${anns.length ? ` ${anns.length}` : ''}`);
  renderCaption();
  if (!anns.length) {
    return $('#list').replaceChildren(h('li', { className: 'empty-row muted' },
      doc ? '本文を選択するとマーカーを付けられます。' : ''));
  }
  const sorted = sortMarks(anns);
  $('#list').replaceChildren(...sorted.flatMap((a, i) => {
    const lost = !ranges.has(a.id);
    // 「種類ごと」では種類が変わるところに小見出し
    const head = sortMode === 'type' && sorted[i - 1]?.type !== a.type
      ? h('li', { className: 'group-head' }, typeIcon(a.type), LABEL[a.type],
        h('span', { className: 'muted' }, String(anns.filter((x) => x.type === a.type).length)))
      : null;
    const row = h('li', {
      className: `${a.type}${lost ? ' lost' : ''}${a === current ? ' active' : ''}`,
      title: LABEL[a.type],
      onclick: () => { show(a); if (lost) return; editAt(a); }, // 該当箇所へ移動してコメントを開く
    }, typeIcon(a.type), h('span', { className: 'item-text' }, a.selected_text.slice(0, 40), lost ? '（位置不明）' : ''),
    sent.has(a.id) ? h('span', { className: 'pending', title: 'AI が対応中' }, icon('send')) : null,
    h('button', {
      type: 'button',
      className: 'row-action',
      title: `AI に送る（${SEND_LABEL[a.type]}）`,
      onclick: (ev) => { ev.stopPropagation(); sendMarks([a]); },
    }, icon('send')),
    copyAction(refOf(a), 'ID をコピーしてチャットに貼る'));
    // 選んだ行の下に、AI が書き戻した結果があるときだけ展開する
    const result = a === current && aiResult(a);
    return [head, row, result ? h('li', { className: 'expand' }, result) : null].filter(Boolean);
  }));
}

const fmt = (v) => (typeof v === 'object' ? JSON.stringify(v) : String(v));

function aiView(obj) {
  if (!obj) return null;
  return h('dl', { className: 'ai' }, Object.entries(obj).map(([k, v]) => [
    h('dt', {}, NAMES[k] || k),
    h('dd', {}, Array.isArray(v)
      ? (v.length ? h('ul', {}, v.map((x) => h('li', {}, fmt(x)))) : '—')
      : fmt(v)),
  ]));
}

function quizView(q, i) {
  return h('div', { className: 'quiz-item' },
    h('p', {}, `Q${i + 1}. ${q.question}`),
    q.answer ? h('p', { className: 'muted' }, `回答: ${q.answer}`) : null,
    aiView(q.grading));
}

function show(a) {
  current = a;
  if (tab !== 'marks') setTab('marks');
  paintCurrent();
  renderList();
}

// --- 保存 -------------------------------------------------------------------

async function put(a) {
  clearTimeout(timers.get(a.id)?.[0]);
  timers.delete(a.id);
  const request = api('PUT', annPath(a), a);
  saves.add(request);
  try {
    const saved = await request;
    Object.assign(a, { ai: saved.ai, created_at: saved.created_at, updated_at: saved.updated_at });
  } catch (e) {
    timers.set(a.id, [null, a]); // 終了前の保存に失敗したら、再試行できるよう残す
    throw e;
  } finally {
    saves.delete(request);
  }
}

function putLater(a) {
  clearTimeout(timers.get(a.id)?.[0]);
  timers.set(a.id, [setTimeout(() => put(a).catch((e) => alert(e.message)), 500), a]);
}

const flush = () => Promise.all([...timers.values()].map(([, a]) => put(a)));

async function removeAnn(a, ask = true) {
  if (ask && !confirm('このマーカーを削除しますか？')) return;
  clearTimeout(timers.get(a.id)?.[0]);
  timers.delete(a.id);
  if (editing === a) { editing = null; bar.hidden = true; } // 閉じるときの保存で復活させない
  await api('DELETE', annPath(a));
  anns = anns.filter((x) => x !== a);
  if (current === a) current = null;
  paint();
  loadDocs();
}

// --- 選択とマーカー -------------------------------------------------------------

// 選択範囲に重なっている既存のマーカー
function marksIn(r) {
  const s = offsetOf(r.startContainer, r.startOffset);
  const e = offsetOf(r.endContainer, r.endOffset);
  return anns.filter((a) => {
    const x = ranges.get(a.id);
    return x && offsetOf(x.startContainer, x.startOffset) < e && s < offsetOf(x.endContainer, x.endOffset);
  });
}

// 既存のマーカーを選び直して Esc → 削除。AI の結果が付いているものだけは確認する（書き戻しは復元できないため）
async function removeMarks(list) {
  const hasAi = list.some((a) => a.ai.explanation || a.ai.evaluation || a.ai.questions.length);
  if (hasAi && !confirm(`AI の説明・評価が付いたマーカーを含みます。${list.length} 件を削除しますか？`)) return;
  for (const a of list) await removeAnn(a, false);
  // Esc は「閉じる」と取り違えやすいので、すぐ元に戻せるようにする（AI の結果が無いものは完全に戻る）
  toast(`マーカーを ${list.length} 件削除しました`, ['元に戻す', async () => {
    for (const a of list) {
      await put(a);
      anns.push(a);
    }
    paint();
    loadDocs();
  }]);
}

function selectedRange() {
  const sel = getSelection();
  if (!doc || sel.isCollapsed || !sel.rangeCount) return null;
  const r = sel.getRangeAt(0);
  // 改行や空白だけの選択（行末の余白を少しドラッグしたときなど）は、見た目では何も選ばれていないので選択なしとする
  return reader.contains(r.commonAncestorContainer) && r.toString().trim() ? r : null;
}

// ドラッグを離したら文節単位に揃える（Option / Alt を押しながらなら文字単位のまま）
// 選択中に本文を押したら、まず選択を外す。選択の中を押すと文字のドラッグになったり、
// 下の mouseup で選択が戻ったりして、すぐに引き直せないため（ダブル・トリプルクリックと Shift は除く）
reader.addEventListener('mousedown', (ev) => {
  if (ev.button === 0 && ev.detail === 1 && !ev.shiftKey && !getSelection().isCollapsed) getSelection().removeAllRanges();
});

reader.addEventListener('mouseup', (ev) => {
  const r = selectedRange();
  if (!r || ev.altKey) return;
  const [start, end] = snapToWords(offsetOf(r.startContainer, r.startOffset),
    offsetOf(r.endContainer, r.endOffset), reader.textContent);
  const snapped = start < end && toRange(start, end);
  if (!snapped) return;
  getSelection().removeAllRanges();
  getSelection().addRange(snapped);
});

// 物理キーで判定する（日本語入力 ON だと ev.key が 'Process' や 'う' になるため）
const CODES = { KeyU: 'unknown', KeyE: 'explain', KeyQ: 'quiz' };
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape') {
    // クリックで選んだ既存のマーカー（入力欄にカーソルが無い状態）→ そのマーカーを削除
    if (editing && !bar.contains(document.activeElement)) {
      ev.preventDefault();
      removeMarks([editing]).catch((e) => alert(e.message));
      return;
    }
    if (editing) return; // 入力中の Esc は小窓を閉じるだけ（入力欄側で処理）
    const r = selectedRange();
    const hits = r ? marksIn(r) : [];
    bar.hidden = true;
    getSelection().removeAllRanges(); // Esc で選択も外す
    if (hits.length) {
      ev.preventDefault();
      removeMarks(hits).catch((e) => alert(e.message));
    }
    return;
  }
  const type = CODES[ev.code];
  if (!type || ev.metaKey || ev.ctrlKey || ev.altKey || ev.target.closest?.('textarea, input, select, dialog')) return;
  if (!selectedRange()) return;
  ev.preventDefault();
  mark(type);
});

async function mark(type) {
  const r = selectedRange();
  if (!r) return;
  const text = reader.textContent;
  const start = offsetOf(r.startContainer, r.startOffset);
  const end = offsetOf(r.endContainer, r.endOffset);
  if (!text.slice(start, end).trim()) return;
  const a = {
    id: `ann_${crypto.randomUUID()}`,
    type,
    selected_text: text.slice(start, end),
    context_before: text.slice(Math.max(0, start - CTX), start),
    context_after: text.slice(end, end + CTX),
    locator: { heading: headingBefore(r.startContainer), start_offset: start, end_offset: end },
    note: '',
    status: 'new',
  };
  const box = r.getBoundingClientRect();
  docOf.set(a, doc.id);
  getSelection().removeAllRanges();
  try {
    await put(a); // この時点で記録される（コメントは任意）
  } catch (e) {
    return alert(e.message);
  }
  anns.push(a);
  paint();
  show(a);
  showNoteEditor(a, box);
  loadDocs();
}

function caretAt(x, y) {
  if (document.caretPositionFromPoint) {
    const p = document.caretPositionFromPoint(x, y);
    return p && [p.offsetNode, p.offset];
  }
  const r = document.caretRangeFromPoint?.(x, y); // Safari
  return r && [r.startContainer, r.startOffset];
}

// マーカーをクリック → その場でコメント編集
reader.addEventListener('click', (ev) => {
  if (!getSelection().isCollapsed) return;
  const [node, off] = caretAt(ev.clientX, ev.clientY) || [];
  if (!node) return;
  // 破線の用語を優先（マーカーと重なっていても用語を開く。マーカーは右の一覧からも開ける）
  for (const [term, r] of termRanges) {
    if (r.isPointInRange(node, off)) return showTerm(term);
  }
  for (const [id, r] of ranges) {
    if (r.isPointInRange(node, off)) {
      const a = anns.find((x) => x.id === id);
      show(a);
      return showNoteEditor(a, r.getBoundingClientRect(), false);
    }
  }
});

// --- ポップアップ: 選択 → 種別を選ぶ → そのままコメント（任意） -------------------

const bar = h('div', { id: 'bar', hidden: true });
document.body.append(bar);
let editing = null; // ポップアップでコメント編集中のアノテーション

function placeBar(box) {
  bar.hidden = false;
  const below = box.bottom + 8;
  const top = below + bar.offsetHeight > innerHeight ? box.top - bar.offsetHeight - 8 : below;
  const maxLeft = document.documentElement.clientWidth - bar.offsetWidth - 8;
  bar.style.top = `${Math.max(8, top) + scrollY}px`;
  bar.style.left = `${Math.max(8, Math.min(box.left, maxLeft)) + scrollX}px`;
  return top === below;
}

function showTypeButtons(r, overlaps = 0) {
  bar.className = 'menu';
  bar.replaceChildren(...Object.entries(KEYS).map(([key, type]) => h('button', {
    type: 'button',
    className: type,
    title: `${key} キーでも付けられます`,
    onmousedown: (ev) => ev.preventDefault(), // 選択を消さない
    onclick: () => mark(type),
  }, typeIcon(type), LABEL[type])), ...(overlaps ? [h('span', { className: 'menu-hint' }, `Esc で ${overlaps} 件削除`)] : []));
  // ドラッグ中は本文を動かさない（動かすと選択がずれる）。離してから空ける
  if (placeBar(r.getBoundingClientRect()) && !pointerDown) openSpace(r);
}

// 選択メニューの分だけ、選択した行の次から本文を下にずらす（メニューで文章が隠れないように）。
// 空の要素なので textContent（マーカー位置の基準）は変わらない
const spacer = h('span', { className: 'bar-spacer' });
let spaceKey = '';
let pointerDown = false;

function openSpace(r) {
  const key = `${offsetOf(r.startContainer, r.startOffset)}-${offsetOf(r.endContainer, r.endOffset)}`;
  if (key === spaceKey && spacer.isConnected) return; // 差し込みで selectionchange が来ても繰り返さない
  closeSpace();
  const line = [...r.getClientRects()].filter((b) => b.width > 0).pop();
  if (!line) return;
  // 選択の最後の行の右端 = 行末に差し込む
  const right = reader.getBoundingClientRect().right - parseFloat(getComputedStyle(reader).paddingRight) - 1;
  const p = caretAt(right, line.top + line.height / 2);
  const at = document.createRange();
  if (p && reader.contains(p[0]) && r.comparePoint(p[0], p[1]) > 0) at.setStart(p[0], p[1]);
  else at.setStart(r.endContainer, r.endOffset);
  spacer.style.height = `${bar.offsetHeight + 8}px`;
  at.insertNode(spacer);
  spaceKey = key;
}

function closeSpace() {
  const parent = spacer.parentNode;
  if (!parent) return;
  spacer.remove();
  parent.normalize(); // 差し込みで分かれたテキストを戻す（マーカーの Range は追従する）
  spaceKey = '';
}

// メニューを閉じたら本文を戻す。押している間は戻さない（押した位置の文章が動いてしまうため）
new MutationObserver(() => { if (bar.hidden && !pointerDown) closeSpace(); })
  .observe(bar, { attributes: true, attributeFilter: ['hidden'] });
document.addEventListener('mousedown', () => { pointerDown = true; }, true);
// 離した時点の選択でメニューを出し直す。選択が無ければメニューを消して本文を戻す。
// 選択した文字をドラッグすると mouseup が来ないので、dragend でも同じことをする
const released = () => {
  pointerDown = false;
  setTimeout(() => {
    const r = selectedRange();
    if (r && !editing) showTypeButtons(r, marksIn(r).length);
    else if (!r && bar.className === 'menu') bar.hidden = true;
    if (bar.hidden || bar.className !== 'menu') closeSpace();
  });
};
document.addEventListener('mouseup', released, true);
document.addEventListener('dragend', released, true);

function showNoteEditor(a, box, focus = true) {
  closeSpace();
  editing = a;
  bar.className = 'editor';
  const note = h('textarea', {
    value: a.note,
    rows: 3,
    placeholder: `${NOTE_LABEL[a.type]}（空欄でも記録されます）`,
  });
  note.oninput = () => { a.note = note.value; putLater(a); };
  note.onkeydown = (ev) => {
    if (ev.key === 'Escape' || (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey))) {
      ev.preventDefault();
      closeEditor();
    }
  };
  const status = h('select', { className: 'status', title: '理解の状態' },
    Object.entries(STATUS_LABEL).map(([v, label]) => h('option', { value: v, selected: v === a.status }, label)));
  status.onchange = () => { a.status = status.value; put(a); };
  bar.replaceChildren(
    h('div', { className: 'editor-head' },
      h('span', { className: 'tag' }, typeIcon(a.type), LABEL[a.type]),
      h('span', { className: 'editor-tools' }, status,
        h('button', { type: 'button', className: 'icon-btn danger', title: 'マーカーを削除', onclick: () => removeAnn(a) }, icon('trash')))),
    note,
    h('div', { className: 'hint' }, focus ? '自動保存 · Esc で閉じる' : 'クリックして書く · Esc でこのマーカーを削除'),
  );
  placeBar(box);
  if (focus) note.focus();
}

function closeEditor() {
  const a = editing;
  editing = null;
  bar.hidden = true;
  if (!a) return;
  put(a).catch((e) => alert(e.message));
  if (current === a) show(a);
}

function editAt(a) {
  const r = ranges.get(a.id);
  if (!r) return alert('本文中の位置が見つからないため編集できません');
  const b = r.getBoundingClientRect();
  if (b.top < 60 || b.bottom > innerHeight - 160) r.startContainer.parentElement.scrollIntoView({ block: 'center' });
  showNoteEditor(a, r.getBoundingClientRect(), false);
}

document.addEventListener('selectionchange', () => {
  if (editing) return;
  const r = selectedRange();
  if (r) showTypeButtons(r, marksIn(r).length);
  else bar.hidden = true;
});

// ポップアップの外を押したら閉じる（書いた内容は保存される）。狭い画面の文書一覧（引き出し）も外を押したら閉じる
document.addEventListener('mousedown', (ev) => {
  if (!bar.hidden && !bar.contains(ev.target)) closeEditor();
  if (document.body.classList.contains('nav-open') && !ev.target.closest('#nav, #nav-btn')) {
    document.body.classList.remove('nav-open');
  }
  if (document.body.classList.contains('toc-open') && !ev.target.closest('#toc, #toc-btn')) {
    document.body.classList.remove('toc-open');
  }
});

// --- 文書一覧（左サイドバー）と切り替え ---------------------------------------------

async function loadDocs() {
  const docs = await api('GET', '/api/docs');
  docs.sort((a, b) => b.modified.localeCompare(a.modified)); // 最近使った順
  $('#docs').replaceChildren(...docs.map((d) => h('li', {
    className: (d.doc_id === doc?.id ? 'active' : '') + (d.missing ? ' missing' : ''),
    title: d.source_path,
    onclick: () => { location.hash = d.doc_id; document.body.classList.remove('nav-open'); },
  }, icon('file-text', 'muted'), h('span', { className: 'item-text' },
    h('span', { className: 'doc-title' }, d.title),
    h('span', { className: 'doc-meta' }, d.missing ? 'ファイルが見つかりません' : `マーカー ${d.annotations}`)),
  copyAction(d.source_path, '文書の参照をコピーしてチャットに貼る'))));
}

async function openDoc(id) {
  await flush(); // 前の文書の未保存コメントを先に保存
  closeEditor();
  current = null;
  currentTerm = null;
  bounds = [];
  terms = [];
  glossary = {};
  if (!id) {
    doc = null;
    anns = [];
    document.title = 'Active Reader';
    $('#title').textContent = '';
    reader.replaceChildren(h('p', { className: 'muted empty' },
      '左の一覧から文書を選ぶか、ファイルのパスを入力してください（.md / .txt）。'));
  } else {
    const d = await api('GET', `/api/docs/${id}`);
    doc = d.document;
    anns = d.annotations;
    bounds = d.boundaries;
    terms = d.terms;
    glossary = d.glossary;
    anns.forEach((a) => docOf.set(a, doc.id));
    document.title = doc.title;
    $('#title').textContent = doc.title;
    reader.innerHTML = d.html; // サーバー側で生 HTML 無効化済み
    for (const link of reader.querySelectorAll('a[href^="http"]')) { // 外部リンクは別タブ（読書位置を失わない）
      Object.assign(link, { target: '_blank', rel: 'noopener noreferrer' });
    }
    try { scrollTo(0, Number(localStorage.getItem(`scroll:${doc.id}`)) || 0); } catch { /* storage 不可 */ }
  }
  paint();
  renderToc();
  await loadDocs();
}

// --- 目次（Zenn 風）: 本文の見出しを横に並べ、読んでいる節を示す ------------------------

let tocItems = []; // [見出し要素, 目次のボタン, 本文 textContent 上の offset]
let section = null; // 読んでいる節の範囲 [start, end)。目次が無い文書は null（全体）
const inSection = (t) => !section || (section[0] <= t.start && t.start < section[1]);

function renderToc() {
  const hs = [...reader.querySelectorAll('h1, h2, h3')];
  const top = Math.min(...hs.map((el) => Number(el.tagName[1])));
  document.body.classList.toggle('has-toc', hs.length > 1);
  tocItems = hs.map((el) => [el, h('button', {
    type: 'button', // a[href="#…"] だと文書の切り替え（hash）と衝突する
    style: `padding-left: ${0.75 + (Number(el.tagName[1]) - top) * 0.9}rem`,
    onclick: () => {
      document.body.classList.remove('toc-open'); // 小窓で選んだら閉じる
      el.scrollIntoView({ behavior: 'smooth' });
    },
  }, el.textContent), offsetOf(el, 0)]);
  $('#toc').replaceChildren(h('div', { className: 'toc-head muted' }, '目次'),
    h('ol', {}, tocItems.map(([, b]) => h('li', {}, b))));
  markToc();
}

// 画面上端を過ぎた最後の見出しを「読んでいる節」とする
function markToc() {
  let i = 0;
  while (i + 1 < tocItems.length && tocItems[i + 1][0].getBoundingClientRect().top < 80) i++;
  // 末尾の短い節は上端まで届かないので、一番下までスクロールしたら最後の節にする
  if (tocItems.length && scrollY > 0 && innerHeight + scrollY >= document.documentElement.scrollHeight - 2) i = tocItems.length - 1;
  tocItems.forEach((it, j) => it[1].classList.toggle('active', j === i));
  // 最初の見出しより前の本文は最初の節に含める
  const next = tocItems.length > 1 ? [i ? tocItems[i][2] : 0, tocItems[i + 1]?.[2] ?? Infinity] : null;
  if (String(next) !== String(section)) {
    section = next;
    renderTerms(); // 節が変わったら用語一覧を絞り直す
  }
}

const route = () => openDoc(location.hash.slice(1) || null).catch((e) => {
  alert(e.message);
  location.hash = '';
});
addEventListener('hashchange', route);

// Enter で開く（ボタンは置かない）
$('#add').onsubmit = async (ev) => {
  ev.preventDefault();
  const path = $('#add-path').value.trim();
  if (!path) return;
  try {
    const row = await api('POST', '/api/docs', { path });
    $('#add-path').value = '';
    location.hash = row.doc_id;
    if (doc?.id === row.doc_id) await loadDocs();
  } catch (e) {
    alert(e.message);
  }
};
// --- レイアウト（VS Code 風）: 左右パネルの開閉と幅。ブラウザに記憶する ----------------------

const narrow = matchMedia('(max-width: 900px)');
const layout = { nav: true, panel: true, navW: 240, panelW: 320 };
try { Object.assign(layout, JSON.parse(localStorage.getItem('layout') || '{}')); } catch { /* storage 不可 */ }

function applyLayout() {
  document.body.classList.toggle('hide-nav', !layout.nav);
  document.body.classList.toggle('hide-panel', !layout.panel);
  $('main').style.setProperty('--nav-size', `${layout.navW}px`);
  $('main').style.setProperty('--panel-size', `${layout.panelW}px`);
  try { localStorage.setItem('layout', JSON.stringify(layout)); } catch { /* noop */ }
}

function toggleNav() {
  if (narrow.matches) return document.body.classList.toggle('nav-open'); // 狭い画面は引き出し
  layout.nav = !layout.nav;
  applyLayout();
}

function togglePanel(show = !layout.panel) {
  layout.panel = show;
  applyLayout();
}

function resizer(el, side, key, min, max) {
  const handle = h('div', { className: 'resizer', title: 'ドラッグで幅を変更' });
  handle.onmousedown = (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    document.body.classList.add('resizing');
    const move = (e) => {
      const box = el.getBoundingClientRect();
      const w = side === 'left' ? e.clientX - box.left : box.right - e.clientX;
      layout[key] = Math.round(Math.min(max, Math.max(min, w)));
      applyLayout();
    };
    const up = () => {
      document.body.classList.remove('resizing');
      removeEventListener('mousemove', move);
      removeEventListener('mouseup', up);
    };
    addEventListener('mousemove', move);
    addEventListener('mouseup', up);
  };
  el.append(handle);
}

resizer($('#nav'), 'left', 'navW', 160, 480);
resizer($('#panel'), 'right', 'panelW', 220, 640);
$('#nav-btn').onclick = toggleNav;
$('#panel-btn').onclick = () => togglePanel();
$('#toc-btn').onclick = () => {
  document.body.classList.toggle('toc-open');
  $('#toc button.active')?.scrollIntoView({ block: 'nearest' }); // 長い目次でも読んでいる節が見えるように
};
document.addEventListener('keydown', (ev) => { // ⌘B: 文書一覧 / ⌥⌘B: サイドパネル（VS Code と同じ）
  if (ev.code !== 'KeyB' || !(ev.metaKey || ev.ctrlKey)) return;
  ev.preventDefault();
  if (ev.altKey) togglePanel(); else toggleNav();
});
applyLayout();

let scrollTimer;
addEventListener('scroll', () => {
  markToc();
  clearTimeout(scrollTimer);
  const id = doc?.id;
  scrollTimer = setTimeout(() => {
    try { if (id) localStorage.setItem(`scroll:${id}`, String(scrollY)); } catch { /* noop */ }
  }, 300);
});

// --- 記録管理 -----------------------------------------------------------------

async function openRecords() {
  const rows = await api('GET', '/api/docs');
  $('#rec-rows').replaceChildren(...rows.map((r) => h('tr', { className: r.missing ? 'missing' : '' },
    h('td', {}, r.title), h('td', {}, r.source_path, r.missing ? '（見つかりません）' : ''),
    h('td', {}, String(r.annotations)), h('td', {}, r.updated_at || '—'),
    h('td', {}, h('button', {
      type: 'button',
      className: 'link danger',
      onclick: () => deleteRecords('/api/records/delete', { doc_ids: [r.doc_id] }),
    }, '削除')))));
  if (!$('#records').open) $('#records').showModal();
}

async function deleteRecords(url, body) {
  try {
    const rows = await api('POST', url, { ...body, dry_run: true });
    if (!rows.length) return alert('対象はありません');
    const list = rows.map((r) => `・${r.title}（${r.annotations}件）${r.source_path}`).join('\n');
    if (!confirm(`以下 ${rows.length} 件の読書記録を削除します（元文書は削除しません）:\n\n${list}`)) return;
    const deleted = await api('POST', url, body);
    if (doc && deleted.includes(doc.id)) location.hash = '';
    await loadDocs();
    await openRecords();
  } catch (e) {
    alert(e.message);
  }
}

$('#records-btn').onclick = () => openRecords().catch((e) => alert(e.message));
$('#del-dir').onclick = () => deleteRecords('/api/records/delete', { path: $('#dir').value.trim() });
$('#clean').onclick = () => deleteRecords('/api/records/clean', {});

// チャット（スキル）から書き込まれた結果を、タブに戻ったときに反映する
async function refresh() {
  if (readingEnded) return;
  await loadDocs();
  if (timers.size || editing || !doc) return; // 入力中は触らない
  const d = await api('GET', `/api/docs/${doc.id}`);
  const before = current && JSON.stringify(current);
  anns = d.annotations;
  glossary = d.glossary;
  anns.forEach((a) => docOf.set(a, doc.id));
  current = current && anns.find((a) => a.id === current.id);
  paint();
  if (current && JSON.stringify(current) !== before) show(current);
}
addEventListener('focus', () => refresh().catch(() => {}));

$('#stop-btn').onclick = async () => {
  const button = $('#stop-btn');
  button.disabled = true;
  button.textContent = '保存して終了中…';
  document.body.inert = true;
  try {
    await flush();
    await Promise.all(saves);
    await api('POST', '/api/shutdown', {});
    readingEnded = true;
    doc = null; // 用語・マーカーの結果待ちループも終了させる
    editing = null;
    bar.hidden = true;
    closeSpace();
    document.body.inert = false;
    button.textContent = '読書を終了しました';
    $('#reading-ended').showModal();
  } catch (e) {
    document.body.inert = false;
    button.disabled = false;
    button.textContent = '読書をやめる';
    alert(`読書を終了できませんでした: ${e.message}`);
  }
};
$('#reading-ended').oncancel = (ev) => ev.preventDefault();

// --- 起動 -------------------------------------------------------------------

if (!window.CSS?.highlights) {
  alert('このブラウザはマーカー表示 (CSS Custom Highlight API) に未対応です。Chrome 105+ / Safari 17.2+ / Firefox 140+ を使ってください。');
}
for (const b of document.querySelectorAll('#tabs button')) b.onclick = () => setTab(b.dataset.tab);
$('#nav-btn').replaceChildren(icon('layout-sidebar'));
$('#panel-btn').replaceChildren(icon('layout-sidebar-right'));
$('#toc-btn').replaceChildren(icon('list'));
$('#add').prepend(icon('file-plus', 'muted'));
$('#records-btn').prepend(icon('settings'));
route();
