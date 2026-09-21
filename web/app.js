/* StudyIA — interface. Estado simples + render direto no DOM.
   Todo texto que vem do usuário, de arquivo importado ou da IA passa por esc() antes de
   entrar em innerHTML. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  view: "home",
  decks: [],
  study: {
    deckId: null, deckName: "", mode: "due", sessionId: null, card: null, preview: null,
    answered: false, choice: null, startedAt: 0, ticker: null, enviando: false,
    stats: { respondidas: 0, acertos: 0, erros: 0, ms: 0 },
  },
  manageDeckId: null,
  manageFilter: "all",
  aiCards: [],
};

const GRADES = [
  { g: 0, label: "Errei", cls: "g0" },
  { g: 1, label: "Difícil", cls: "g1" },
  { g: 2, label: "Bom", cls: "g2" },
  { g: 3, label: "Fácil", cls: "g3" },
];
const STATE_LABEL = { new: "nova", learning: "aprendendo", relearning: "reaprendendo", review: "em revisão" };

/* ------------------------------------------------------------------ utilidades */

// Toda a interface fala com o Python por aqui. Não existe servidor: a chamada cai direto
// numa função do programa pela ponte da janela.
async function enviar(path, method, body) {
  if (!window.pywebview?.api) throw new Error("A ponte com o programa ainda não está pronta.");
  const res = await window.pywebview.api.request({ path, method, body: body ?? null });
  if (!res) throw new Error("Sem resposta do programa.");
  if (res.error) throw new Error(res.error);
  return res.data;
}

// A janela executa cada chamada numa thread própria, então duas gravações disparadas
// juntas (ex.: cliques rápidos no tema) podem terminar em ordem trocada e o valor que
// fica salvo não seria o último escolhido. Por isso gravações entram numa fila e saem
// na ordem em que foram pedidas. Leituras e a geração por IA (que demora) não esperam.
let filaDeGravacoes = Promise.resolve();
function api(path, { method = "GET", body } = {}) {
  const chamar = () => enviar(path, method, body);
  if (method === "GET" || path.startsWith("/api/ai/generate")) return chamar();
  const minha = filaDeGravacoes.then(chamar, chamar);
  filaDeGravacoes = minha.catch(() => {});   // uma falha não trava as próximas
  return minha;
}

const abrirLink = (url) => window.pywebview?.api?.abrir_link(url);
const ico = (nome, extra = "") => `<svg class="ico ${extra}" aria-hidden="true"><use href="#i-${nome}"/></svg>`;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const plural = (n, um, varios) => `${n} ${n === 1 ? um : varios}`;

let toastTimer;
function toast(msg, type = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = "toast"), 3000);
}

function untilText(iso) {
  const diff = new Date(iso) - new Date();
  if (diff <= 0) return "agora";
  const min = diff / 60000;
  if (min < 60) return `${Math.max(1, Math.round(min))} min`;
  if (min < 1440) return `${Math.round(min / 60)} h`;
  const dias = Math.round(min / 1440);
  return dias === 1 ? "1 dia" : `${dias} dias`;
}

function fmtDur(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}min ${String(s % 60).padStart(2, "0")}s`;
}

const diaMes = (iso) => `${iso.slice(8)}/${iso.slice(5, 7)}`;
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

// Falha em qualquer chamada assíncrona aparece na tela em vez de morrer em silêncio.
window.addEventListener("unhandledrejection", (e) => {
  toast(e.reason?.message || "Algo deu errado.", "bad");
});

/* ---------------------------------------------------------------------- modal */

const dlg = $("#modal");

function abrirModal({ titulo, corpo = "", ok = "Confirmar", cancelar = "Cancelar", perigo = false }) {
  return new Promise((resolve) => {
    $("#modal-title").textContent = titulo;
    $("#modal-body").innerHTML = corpo;
    const okBtn = $("#modal-ok");
    const cancelBtn = $("#modal-cancel");
    okBtn.textContent = ok;
    okBtn.className = `btn ${perigo ? "danger-solid" : "primary"}`;
    cancelBtn.textContent = cancelar || "";
    cancelBtn.hidden = !cancelar;

    const fim = (valor) => { dlg.close(); resolve(valor); };
    okBtn.onclick = () => fim(true);
    cancelBtn.onclick = () => fim(false);
    dlg.oncancel = (e) => { e.preventDefault(); fim(false); };               // Esc
    dlg.onclick = (e) => { if (e.target === dlg) fim(false); };              // clique fora
    dlg.onkeydown = (e) => {
      if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); fim(true); }
    };
    dlg.showModal();
    ($("#modal-body input") || (perigo ? cancelBtn : okBtn)).focus();
  });
}

const confirmar = (titulo, texto, opts = {}) =>
  abrirModal({ titulo, corpo: `<p>${esc(texto)}</p>`, ...opts });

async function pedirTexto({ titulo, rotulo, valor = "", ok = "Salvar" }) {
  const aceitou = await abrirModal({
    titulo, ok,
    corpo: `<div class="field"><label for="modal-input" style="color:var(--text)">${esc(rotulo)}</label>
            <input id="modal-input" value="${esc(valor)}" maxlength="120" autocomplete="off"></div>`,
  });
  return aceitou ? $("#modal-input").value.trim() : null;
}

/* ----------------------------------------------------------------------- tema */

function aplicarTema(tema) {
  const raiz = document.documentElement;
  if (tema === "claro") raiz.dataset.theme = "light";
  else if (tema === "escuro") raiz.dataset.theme = "dark";
  else delete raiz.dataset.theme;
  $$("[data-theme-choice]").forEach((b) => b.classList.toggle("active", b.dataset.themeChoice === (tema || "auto")));
}

/* ------------------------------------------------------------------ navegação */

function switchView(name) {
  if (state.view === "study" && name !== "study") endSession();
  state.view = name;
  document.body.classList.toggle("studying", name === "study");   // esconde a lateral
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  $$(".nav-item").forEach((t) => {
    const ativo = t.dataset.view === name;
    t.classList.toggle("active", ativo);
    ativo ? t.setAttribute("aria-current", "page") : t.removeAttribute("aria-current");
  });
  if (name === "home") loadDecks();
  if (name === "manage") loadCards();
  if (name === "stats") loadStats();
  if (name === "ai") loadAiStatus();
  if (name === "settings") loadSettings();
  if (name === "import") ensureDecks().then(fillDeckSelects);
  $("#content").scrollTop = 0;
}

$$(".nav-item").forEach((t) => t.addEventListener("click", () => switchView(t.dataset.view)));
$$("[data-go]").forEach((el) => el.addEventListener("click", () => switchView(el.dataset.go)));

async function ensureDecks() {
  if (!state.decks.length) state.decks = await api("/api/decks");
  return state.decks;
}

/* ------------------------------------------------------------------- baralhos */

async function loadDecks() {
  state.decks = await api("/api/decks");
  renderDecks();
  renderSidebarDecks();
  fillDeckSelects();
  renderHomeSummary();
}

function renderSidebarDecks() {
  const el = $("#sidebar-decks");
  if (!state.decks.length) {
    el.innerHTML = `<p class="side-empty">Nenhum baralho ainda.</p>`;
    return;
  }
  el.innerHTML = state.decks.map((d) => `
    <button class="side-deck ${d.id === state.manageDeckId ? "active" : ""}" data-side-deck="${d.id}" title="${esc(d.name)}">
      <span class="name">${esc(d.name)}</span>
      <span class="count ${d.due ? "" : "zero"}">${d.due}</span>
    </button>`).join("");
  $$("[data-side-deck]", el).forEach((b) => b.onclick = () => {
    state.manageDeckId = +b.dataset.sideDeck;
    switchView("manage");
  });
}

function dataDeHoje() {
  const hoje = new Date().toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" });
  return hoje.charAt(0).toUpperCase() + hoje.slice(1);
}

async function renderHomeSummary() {
  const total = state.decks.reduce((a, d) => a + d.total, 0);
  const due = state.decks.reduce((a, d) => a + d.due, 0);
  const s = await api("/api/stats");
  $("#page-date").textContent = dataDeHoje();
  $("#home-summary").innerHTML = `
    <div class="stat ${due > 0 ? "hot" : ""}"><span class="stat-ico">${ico("inbox")}</span>
      <b>${due}</b><span>${due === 1 ? "questão para revisar agora" : "questões para revisar agora"}</span></div>
    <div class="stat"><span class="stat-ico">${ico("layers")}</span>
      <b>${total}</b><span>questões no total</span></div>
    <div class="stat"><span class="stat-ico">${ico("target")}</span>
      <b>${s.accuracy}%</b><span>de acerto geral</span></div>
    <div class="stat"><span class="stat-ico">${ico("flame")}</span>
      <b>${s.streak > 0 ? ico("flame", "flame") : ""}${s.streak}</b><span>${s.streak === 1 ? "dia seguido" : "dias seguidos"}</span></div>`;

  const todos = $("#btn-study-all");
  todos.hidden = state.decks.length < 2;
  $("span", todos).textContent = `Estudar tudo (${due})`;
}

function stackHTML(d) {
  const pct = (n) => (d.total ? (100 * n / d.total).toFixed(1) : 0);
  return `<div class="stack" aria-hidden="true">
      <span class="s-rev" style="width:${pct(d.maduros)}%"></span>
      <span class="s-learn" style="width:${pct(d.aprendendo)}%"></span>
      <span class="s-new" style="width:${pct(d.novos)}%"></span>
    </div>
    <div class="legend">
      <span><i class="s-rev"></i><b>${d.maduros}</b> em revisão</span>
      <span><i class="s-learn"></i><b>${d.aprendendo}</b> aprendendo</span>
      <span><i class="s-new"></i><b>${d.novos}</b> novas</span>
    </div>`;
}

function renderDecks() {
  const el = $("#deck-list");
  $("#decks-title").textContent = state.decks.length
    ? `Seus baralhos (${state.decks.length})` : "Primeiros passos";
  if (!state.decks.length) {
    el.innerHTML = `
      <div class="card onboarding" style="grid-column:1/-1">
        <h3>Vamos começar</h3>
        <p class="muted">Um baralho reúne as questões de uma matéria ou de uma prova. Três jeitos de enchê-lo:</p>
        <div class="steps">
          <div class="step"><span class="n">1</span><p>Crie um baralho e cadastre as questões à mão, com a explicação de cada uma.</p>
            <button class="btn small" id="ob-new">${ico("plus")}<span>Criar baralho</span></button></div>
          <div class="step"><span class="n">2</span><p>Importe um arquivo pronto (JSON, CSV ou texto) com várias questões de uma vez.</p>
            <button class="btn small" id="ob-import">${ico("upload")}<span>Importar</span></button></div>
          <div class="step"><span class="n">3</span><p>Cole seu material e deixe o Gemini montar questões com explicação.</p>
            <button class="btn small" id="ob-ai">${ico("sparkles")}<span>Gerar com IA</span></button></div>
        </div>
      </div>`;
    $("#ob-new").onclick = () => $("#btn-new-deck").click();
    $("#ob-import").onclick = () => switchView("import");
    $("#ob-ai").onclick = () => switchView("ai");
    return;
  }

  el.innerHTML = state.decks.map((d) => `
    <div class="deck">
      <div class="deck-top">
        <h3>${esc(d.name)}</h3>
        <span class="badge-due ${d.due ? "" : "zero"}" title="Questões na fila agora">${d.due} na fila</span>
      </div>
      ${d.description ? `<p class="desc">${esc(d.description)}</p>` : ""}
      ${stackHTML(d)}
      <div class="deck-actions">
        <button class="btn primary small" data-study="${d.id}" ${d.total ? "" : "disabled"}>${ico("play")}<span>Estudar</span></button>
        <button class="btn small" data-cram="${d.id}" ${d.total ? "" : "disabled"} title="Ignora o agendamento e sorteia questões">Revisão livre</button>
        <span class="deck-tools">
          <button class="btn ghost icon" data-manage="${d.id}" title="Ver questões" aria-label="Ver questões">${ico("list")}</button>
          <button class="btn ghost icon" data-rename="${d.id}" title="Renomear" aria-label="Renomear">${ico("edit")}</button>
          <button class="btn danger icon" data-del-deck="${d.id}" title="Excluir" aria-label="Excluir">${ico("trash")}</button>
        </span>
      </div>
    </div>`).join("");

  $$("[data-study]", el).forEach((b) => b.onclick = () => startStudy(+b.dataset.study, "due"));
  $$("[data-cram]", el).forEach((b) => b.onclick = () => startStudy(+b.dataset.cram, "cram"));
  $$("[data-manage]", el).forEach((b) => b.onclick = () => {
    state.manageDeckId = +b.dataset.manage;
    switchView("manage");
  });
  $$("[data-rename]", el).forEach((b) => b.onclick = async () => {
    const deck = state.decks.find((d) => d.id === +b.dataset.rename);
    const nome = await pedirTexto({ titulo: "Renomear baralho", rotulo: "Novo nome", valor: deck.name });
    if (!nome || nome === deck.name) return;
    await api(`/api/decks/${deck.id}`, { method: "PATCH", body: { name: nome, description: deck.description } });
    toast("Baralho renomeado", "ok");
    loadDecks();
  });
  $$("[data-del-deck]", el).forEach((b) => b.onclick = async () => {
    const deck = state.decks.find((d) => d.id === +b.dataset.delDeck);
    const sim = await confirmar("Excluir baralho?",
      `"${deck.name}" e ${plural(deck.total, "questão", "questões")} serão apagados. Isso não tem volta.`,
      { ok: "Excluir", perigo: true });
    if (!sim) return;
    await api(`/api/decks/${deck.id}`, { method: "DELETE" });
    toast("Baralho excluído");
    loadDecks();
  });
}

function fillDeckSelects() {
  const opts = state.decks.map((d) => `<option value="${d.id}">${esc(d.name)}</option>`).join("");
  $("#manage-deck").innerHTML = opts || `<option value="">— sem baralhos —</option>`;
  $("#import-deck").innerHTML = opts + `<option value="">— criar novo —</option>`;
  $("#stats-deck").innerHTML = `<option value="">Todos os baralhos</option>` + opts;
  if (!state.decks.some((d) => d.id === state.manageDeckId)) state.manageDeckId = state.decks[0]?.id ?? null;
  if (state.manageDeckId) $("#manage-deck").value = state.manageDeckId;
}

function abrirFormBaralho() {
  if (state.view !== "home") switchView("home");
  $("#form-deck").classList.remove("hidden");
  $("#deck-name").focus();
}
$("#btn-new-deck").onclick = abrirFormBaralho;
$("#btn-new-deck-side").onclick = abrirFormBaralho;
$("#cancel-deck").onclick = () => $("#form-deck").classList.add("hidden");
$("#btn-study-all").onclick = () => startStudy(null, "due");
$("#form-deck").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/api/decks", { method: "POST", body: {
      name: $("#deck-name").value, description: $("#deck-desc").value } });
    $("#deck-name").value = $("#deck-desc").value = "";
    $("#form-deck").classList.add("hidden");
    toast("Baralho criado", "ok");
    loadDecks();
  } catch (err) { toast(err.message, "bad"); }
};

/* --------------------------------------------------------------------- estudo */

async function startStudy(deckId, mode) {
  const deck = deckId ? state.decks.find((d) => d.id === deckId) : null;
  Object.assign(state.study, {
    deckId, mode, card: null, answered: false, choice: null, enviando: false,
    deckName: deck ? deck.name : "Todos os baralhos",
    stats: { respondidas: 0, acertos: 0, erros: 0, ms: 0 },
  });
  const { session_id } = await api(`/api/study/start?deck_id=${deckId ?? ""}`, { method: "POST" });
  state.study.sessionId = session_id;

  state.view = "study";
  document.body.classList.add("studying");
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-study"));
  $$(".nav-item").forEach((t) => t.classList.remove("active"));
  $("#study-deck-name").textContent = state.study.deckName + (mode === "cram" ? " · revisão livre" : "");
  $("#study-score").hidden = true;
  $("#study-timer").classList.remove("hidden");
  $("#study-progress-bar").style.width = "0%";
  $("#content").scrollTop = 0;
  nextCard();
}

function endSession() {
  stopTimer();
  if (state.study.sessionId) {
    api(`/api/study/end?session_id=${state.study.sessionId}`, { method: "POST" }).catch(() => {});
    state.study.sessionId = null;
  }
}

function startTimer() {
  state.study.startedAt = Date.now();
  stopTimer();
  state.study.ticker = setInterval(() => {
    const s = Math.floor((Date.now() - state.study.startedAt) / 1000);
    $("#study-timer-text").textContent = s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}`;
  }, 1000);
  $("#study-timer-text").textContent = "0s";
}
function stopTimer() { clearInterval(state.study.ticker); state.study.ticker = null; }

function atualizarPlacar() {
  const st = state.study.stats;
  const pill = $("#study-score");
  pill.hidden = st.respondidas === 0;
  pill.innerHTML = `<span class="ok">✔ ${st.acertos}</span> · <span class="bad">✘ ${st.erros}</span>`;
}

function atualizarProgresso(dueLeft) {
  const barra = $("#study-progress");
  if (state.study.mode === "cram") { barra.classList.add("off"); return; }
  barra.classList.remove("off");
  const feitas = state.study.stats.respondidas;
  const total = feitas + dueLeft;
  const pct = total ? Math.round((100 * feitas) / total) : 0;
  $("#study-progress-bar").style.width = `${pct}%`;
  barra.setAttribute("aria-valuenow", pct);
}

async function nextCard(excludeId = 0) {
  const { deckId, mode } = state.study;
  const data = await api(`/api/study/next?deck_id=${deckId ?? ""}&mode=${mode}&exclude=${excludeId}`);
  Object.assign(state.study, { answered: false, choice: null, enviando: false });

  if (!data.card) { fimDaSessao(data); return; }

  $("#study-timer").classList.remove("hidden");
  state.study.card = data.card;
  state.study.preview = data.grade_preview;
  $("#study-left").textContent = mode === "cram" ? "revisão livre" : `${data.due_left} na fila`;
  atualizarProgresso(data.due_left);
  renderCard();
  startTimer();
}

function fimDaSessao(data) {
  stopTimer();
  state.study.card = null;
  $("#study-left").textContent = "";
  $("#study-timer").classList.add("hidden");
  atualizarProgresso(0);
  if (state.study.mode !== "cram") $("#study-progress-bar").style.width = state.study.stats.respondidas ? "100%" : "0%";

  const st = state.study.stats;
  const proxima = data.next_due
    ? `A próxima questão volta em <b>${untilText(data.next_due)}</b>.`
    : "Esse baralho ainda não tem questões.";

  const resumo = st.respondidas ? `
    <div class="session-stats">
      <div class="stat"><b>${st.respondidas}</b><span>respondidas</span></div>
      <div class="stat"><b>${Math.round((100 * st.acertos) / st.respondidas)}%</b><span>de acerto</span></div>
      <div class="stat"><b>${fmtDur(st.ms)}</b><span>de estudo</span></div>
      <div class="stat"><b>${fmtDur(st.ms / st.respondidas)}</b><span>por questão</span></div>
    </div>` : "";

  $("#study-body").innerHTML = `
    <div class="empty-study card">
      <div class="badge-big">${ico("check")}</div>
      <h2>${st.respondidas ? "Sessão concluída" : "Tudo em dia por aqui"}</h2>
      <p class="muted" style="margin-top:6px">${proxima}</p>
      ${resumo}
      <div class="actions" style="justify-content:center;margin-top:18px">
        <button class="btn" id="btn-cram-now">Revisar mesmo assim</button>
        <button class="btn primary" id="btn-back-home">Voltar aos baralhos</button>
      </div>
    </div>`;
  $("#btn-cram-now").onclick = () => { state.study.mode = "cram"; nextCard(); };
  $("#btn-back-home").onclick = () => switchView("home");
}

function renderCard() {
  const c = state.study.card;
  const sched = c.schedule || {};
  const tags = [
    ...(c.tags ? c.tags.split(",").map((t) => t.trim()).filter(Boolean).map((t) => `<span class="tag">${esc(t)}</span>`) : []),
    sched.state ? `<span class="tag">${STATE_LABEL[sched.state]}</span>` : "",
    sched.lapses ? `<span class="tag warn">errada ${sched.lapses}×</span>` : "",
  ].join("");
  const cabecalho = `<div class="question">${esc(c.question)}</div>${tags ? `<div class="tagline">${tags}</div>` : ""}`;

  if (c.kind === "quiz") {
    $("#study-body").innerHTML = `${cabecalho}
      <div class="options">
        ${c.options.map((o, i) => `
          <button class="option" data-opt="${i}">
            <span class="key">${String.fromCharCode(65 + i)}</span>
            <span>${esc(o)}</span>
          </button>`).join("")}
      </div>
      <div id="feedback-slot"></div>`;
    $$("[data-opt]").forEach((b) => b.onclick = () => answerQuiz(+b.dataset.opt));
  } else {
    $("#study-body").innerHTML = `${cabecalho}
      <div class="actions"><button class="btn primary" id="btn-reveal">Mostrar resposta <kbd>espaço</kbd></button></div>
      <div id="feedback-slot"></div>`;
    $("#btn-reveal").onclick = revealFlash;
  }
}

function gradeButton(g, previews, sugerida) {
  const rotulo = GRADES[g];
  const texto = g === 0 && state.study.card.kind === "quiz" ? "Entendi, revisar" : rotulo.label;
  return `<button class="grade ${rotulo.cls} ${sugerida ? "sugerida" : ""}" data-grade="${g}">
      <b>${texto}</b><span>volta em ${esc(previews[g])}</span><kbd>${g + 1}</kbd>
    </button>`;
}

async function answerQuiz(choice) {
  if (state.study.answered) return;
  state.study.answered = true;
  state.study.choice = choice;
  const c = state.study.card;
  const res = await api("/api/study/answer", { method: "POST", body: { card_id: c.id, choice } });

  $$(".option").forEach((b, i) => {
    b.disabled = true;
    if (i === res.correct_index) b.classList.add("correct");
    if (i === choice && !res.correct) b.classList.add("wrong");
  });

  const previews = res.grade_preview;
  const lista = res.correct ? [1, 2, 3] : [0];
  const sugerida = res.correct ? 2 : 0;

  $("#feedback-slot").innerHTML = `
    <div class="feedback ${res.correct ? "ok" : "bad"}">
      <div class="verdict">${res.correct ? ico("check") + " Você acertou" : ico("x") + " Resposta errada"}</div>
      ${res.correct ? "" : `
        <div><span class="label">Você marcou</span>${esc(res.chosen_text)}</div>
        <div style="margin-top:8px"><span class="label">Resposta certa</span><b>${esc(res.correct_text)}</b></div>`}
      ${res.explanation ? `<div class="why"><span class="label">Por quê</span>${esc(res.explanation)}</div>`
        : (res.correct ? "" : `<div class="why muted">Essa questão não tem explicação cadastrada.</div>`)}
    </div>
    <div class="grades n${lista.length}">${lista.map((g) => gradeButton(g, previews, g === sugerida)).join("")}</div>
    <p class="muted small-text" style="margin:10px 2px 0">Enter confirma a opção destacada.</p>`;
  $$("[data-grade]").forEach((b) => b.onclick = () => sendGrade(+b.dataset.grade));
  stopTimer();
}

function revealFlash() {
  if (state.study.answered) return;
  state.study.answered = true;
  const c = state.study.card;
  const previews = state.study.preview;
  $("#btn-reveal").closest(".actions").remove();
  $("#feedback-slot").innerHTML = `
    <div class="flash-back">${esc(c.answer)}</div>
    ${c.explanation ? `<div class="feedback"><span class="label">Por quê</span>${esc(c.explanation)}</div>` : ""}
    <div class="grades">${[0, 1, 2, 3].map((g) => gradeButton(g, previews, g === 2)).join("")}</div>
    <p class="muted small-text" style="margin:10px 2px 0">Seja honesto: quanto mais fiel a nota, melhor o agendamento.</p>`;
  $$("[data-grade]").forEach((b) => b.onclick = () => sendGrade(+b.dataset.grade));
  stopTimer();
}

async function sendGrade(grade) {
  if (state.study.enviando) return;      // Enter/clique repetido não pode gravar duas notas
  state.study.enviando = true;
  const c = state.study.card;
  const ms = Date.now() - state.study.startedAt;
  const res = await api("/api/study/grade", { method: "POST", body: {
    card_id: c.id, grade, choice: state.study.choice, session_id: state.study.sessionId, ms } });

  const st = state.study.stats;
  st.respondidas += 1; st.ms += ms;
  res.correct ? (st.acertos += 1) : (st.erros += 1);
  atualizarPlacar();
  toast(res.message, res.correct ? "ok" : "");
  nextCard(c.id);
}

document.addEventListener("keydown", (e) => {
  if (dlg.open || state.view !== "study" || !state.study.card) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

  if (e.key === "Escape") { switchView("home"); return; }

  const { card, answered } = state.study;
  if (!answered) {
    if (card.kind === "quiz") {
      const indice = /^[1-9]$/.test(e.key) ? +e.key - 1
        : /^[a-z]$/i.test(e.key) ? e.key.toLowerCase().charCodeAt(0) - 97 : -1;
      const botao = indice >= 0 ? $(`[data-opt="${indice}"]`) : null;
      if (botao) { e.preventDefault(); botao.click(); }
    } else if (e.key === " " || e.key === "Enter") {
      e.preventDefault(); revealFlash();
    }
  } else if (/^[1-4]$/.test(e.key)) {
    const botao = $(`[data-grade="${+e.key - 1}"]`);
    if (botao) { e.preventDefault(); botao.click(); }
  } else if (e.key === "Enter" || e.key === " ") {
    e.preventDefault(); $(".grade.sugerida")?.click();
  }
});

/* ------------------------------------------------------------------ questões */

$("#manage-deck").onchange = (e) => { state.manageDeckId = +e.target.value || null; loadCards(); };
$("#btn-example").onclick = () => { switchView("import"); carregarExemplo(); };
$("#manage-search").oninput = debounce(() => loadCards(), 250);
$$("#manage-filters .chip").forEach((chip) => chip.onclick = () => {
  state.manageFilter = chip.dataset.filter;
  $$("#manage-filters .chip").forEach((c) => c.classList.toggle("active", c === chip));
  loadCards();
});

const FILTROS = {
  all: () => true,
  new: (c) => c.schedule?.state === "new",
  learning: (c) => ["learning", "relearning"].includes(c.schedule?.state),
  review: (c) => c.schedule?.state === "review",
  lapsed: (c) => (c.schedule?.lapses || 0) > 0,
};

function renderDeckHeader(deck) {
  const el = $("#deck-header");
  if (!deck) { el.innerHTML = ""; return; }
  el.innerHTML = `
    <div class="deck-page-head">
      <div>
        <h2>${esc(deck.name)}</h2>
        ${deck.description ? `<p class="muted small-text" style="margin:0">${esc(deck.description)}</p>` : ""}
        <div class="legend">
          <span><i class="s-rev"></i><b>${deck.maduros}</b> em revisão</span>
          <span><i class="s-learn"></i><b>${deck.aprendendo}</b> aprendendo</span>
          <span><i class="s-new"></i><b>${deck.novos}</b> novas</span>
        </div>
      </div>
      <div class="inline">
        <button class="btn primary" data-study="${deck.id}" ${deck.total ? "" : "disabled"}>${ico("play")}<span>Estudar${deck.due ? ` (${deck.due})` : ""}</span></button>
        <button class="btn" data-cram="${deck.id}" ${deck.total ? "" : "disabled"}>Revisão livre</button>
      </div>
    </div>`;
  $("[data-study]", el).onclick = () => startStudy(deck.id, "due");
  $("[data-cram]", el).onclick = () => startStudy(deck.id, "cram");
}

async function loadCards() {
  await ensureDecks();
  fillDeckSelects();
  renderSidebarDecks();
  const deckId = state.manageDeckId;
  renderDeckHeader(state.decks.find((d) => d.id === deckId));
  if (!deckId) {
    $("#card-count").textContent = "";
    $("#card-list").innerHTML = `<div class="card empty-card">${ico("layers")}
      <p style="margin:0">Crie um baralho primeiro, no Início.</p></div>`;
    return;
  }
  const q = encodeURIComponent($("#manage-search").value.trim());
  const todas = await api(`/api/decks/${deckId}/cards?q=${q}`);
  const cards = todas.filter(FILTROS[state.manageFilter]);
  $("#card-count").textContent = cards.length === todas.length
    ? plural(todas.length, "questão", "questões")
    : `${cards.length} de ${plural(todas.length, "questão", "questões")}`;

  $("#card-list").innerHTML = cards.length ? cards.map((c) => {
    const s = c.schedule || {};
    const correta = c.kind === "quiz" ? c.options[+c.answer] : c.answer;
    const cls = s.state === "new" ? "new" : s.state === "review" ? "review" : "learning";
    return `<div class="card-item ${c.suspended ? "paused" : ""}">
      <div>
        <div class="q">${esc(c.question)}</div>
        <div class="meta">
          <span class="badge ${cls}">${STATE_LABEL[s.state] || "—"}</span>
          ${c.suspended ? `<span class="badge">pausada</span>` : ""}
          <span>${c.kind === "quiz" ? "múltipla escolha" : "flashcard"}</span>
          <span>✔ ${esc((correta || "").slice(0, 60))}${(correta || "").length > 60 ? "…" : ""}</span>
          ${s.due_at && !c.suspended ? `<span>volta em ${untilText(s.due_at)}</span>` : ""}
          ${s.lapses ? `<span>${plural(s.lapses, "erro", "erros")}</span>` : ""}
          ${c.tags ? `<span>${esc(c.tags)}</span>` : ""}
        </div>
      </div>
      <div class="item-actions">
        <button class="btn ghost icon" data-edit="${c.id}" title="Editar" aria-label="Editar">${ico("edit")}</button>
        <button class="btn ghost icon" data-susp="${c.id}" title="${c.suspended ? "Retomar" : "Pausar (não aparece no estudo)"}" aria-label="${c.suspended ? "Retomar" : "Pausar"}">${ico(c.suspended ? "play" : "pause")}</button>
        <button class="btn ghost icon" data-reset="${c.id}" title="Zerar progresso" aria-label="Zerar progresso">${ico("rotate")}</button>
        <button class="btn danger icon" data-del="${c.id}" title="Excluir" aria-label="Excluir">${ico("trash")}</button>
      </div>
    </div>`;
  }).join("") : `<div class="card empty-card">${ico(todas.length ? "search" : "inbox")}
    <p style="margin:0">${todas.length ? "Nenhuma questão nesse filtro." : "Nenhuma questão nesse baralho ainda."}</p></div>`;

  $$("[data-edit]").forEach((b) => b.onclick = () => openCardForm(cards.find((c) => c.id === +b.dataset.edit)));
  $$("[data-susp]").forEach((b) => b.onclick = async () => {
    const c = cards.find((x) => x.id === +b.dataset.susp);
    await api(`/api/cards/${c.id}`, { method: "PATCH", body: { suspended: !c.suspended } });
    toast(c.suspended ? "Questão retomada" : "Questão pausada");
    loadCards();
  });
  $$("[data-reset]").forEach((b) => b.onclick = async () => {
    const sim = await confirmar("Zerar o progresso?",
      "A questão volta a ser tratada como nova e perde o histórico de agendamento.", { ok: "Zerar" });
    if (!sim) return;
    await api(`/api/cards/${b.dataset.reset}/reset`, { method: "POST" });
    toast("Progresso zerado");
    loadCards();
  });
  $$("[data-del]").forEach((b) => b.onclick = async () => {
    const sim = await confirmar("Excluir esta questão?", "Isso não tem volta.", { ok: "Excluir", perigo: true });
    if (!sim) return;
    await api(`/api/cards/${b.dataset.del}`, { method: "DELETE" });
    toast("Questão excluída");
    loadCards();
  });
}

function optionRow(text = "", checked = false) {
  const row = document.createElement("div");
  row.className = "option-row";
  row.innerHTML = `<input type="radio" name="correct" ${checked ? "checked" : ""} aria-label="Esta é a correta">
    <input type="text" class="opt-text" placeholder="Texto da alternativa" autocomplete="off">
    <button type="button" class="btn ghost icon" aria-label="Remover alternativa">${ico("x")}</button>`;
  $(".opt-text", row).value = text;
  $("button", row).onclick = () => row.remove();
  return row;
}

function openCardForm(card = null) {
  $("#form-card").classList.remove("hidden");
  $("#card-id").value = card?.id || "";
  $("#card-kind").value = card?.kind || "quiz";
  $("#card-kind").disabled = !!card;
  $("#card-question").value = card?.question || "";
  $("#card-explanation").value = card?.explanation || "";
  $("#card-tags").value = card?.tags || "";
  $("#card-back").value = card && card.kind === "flash" ? card.answer : "";

  const list = $("#options-list");
  list.innerHTML = "";
  const opts = card?.kind === "quiz" ? card.options : ["", "", "", ""];
  opts.forEach((o, i) => list.append(optionRow(o, card ? +card.answer === i : i === 0)));
  syncKindFields();
  $("#card-question").focus();
  $("#form-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function syncKindFields() {
  const quiz = $("#card-kind").value === "quiz";
  $("#quiz-fields").classList.toggle("hidden", !quiz);
  $("#flash-fields").classList.toggle("hidden", quiz);
  $("#label-question").textContent = quiz ? "Pergunta" : "Frente do card";
}

$("#card-kind").onchange = syncKindFields;
$("#btn-add-option").onclick = () => $("#options-list").append(optionRow());
$("#btn-new-card").onclick = () => openCardForm(null);
$("#cancel-card").onclick = () => $("#form-card").classList.add("hidden");

$("#form-card").onsubmit = async (e) => {
  e.preventDefault();
  const id = $("#card-id").value;
  const kind = $("#card-kind").value;
  const rows = $$("#options-list .option-row");
  const options = rows.map((r) => $(".opt-text", r).value.trim());
  const answerIdx = rows.findIndex((r) => $("input[type=radio]", r).checked);

  const body = {
    question: $("#card-question").value.trim(),
    explanation: $("#card-explanation").value.trim(),
    tags: $("#card-tags").value.trim(),
  };
  if (kind === "quiz") {
    body.options = options;
    body.answer = String(answerIdx);
    if (options.filter(Boolean).length < 2) return toast("Preencha ao menos 2 alternativas", "bad");
    if (answerIdx < 0 || !options[answerIdx]) return toast("Marque qual alternativa é a correta", "bad");
  } else {
    body.answer = $("#card-back").value.trim();
    body.options = [];
    if (!body.answer) return toast("Preencha o verso do card", "bad");
  }

  try {
    if (id) await api(`/api/cards/${id}`, { method: "PATCH", body });
    else await api("/api/cards", { method: "POST", body: { ...body, kind, deck_id: state.manageDeckId } });
    toast(id ? "Questão atualizada" : "Questão criada", "ok");
    $("#form-card").classList.add("hidden");
    loadCards();
  } catch (err) { toast(err.message, "bad"); }
};

/* ------------------------------------------------------------------ importar */

const EXEMPLO = `P: Qual prazo o servidor tem para recorrer de penalidade disciplinar?
a) 10 dias
b) 30 dias *
c) 60 dias
d) 5 dias
E: A Lei 8.112/90 fixa 30 dias contados da ciência da decisão. O erro comum é confundir com o prazo de defesa no PAD.
T: Lei 8.112, processo disciplinar
---
P: O que é vacância?
R: A situação em que um cargo público fica sem titular.
E: Não confunda vacância (cargo sem ocupante) com vagância; as hipóteses estão no art. 33.
`;

function carregarExemplo() {
  $("#import-content").value = EXEMPLO;
  $("#btn-preview-import").click();
}

// Soltar um arquivo fora da área certa faria a janela navegar até ele e sumir com o app.
["dragover", "drop"].forEach((ev) => window.addEventListener(ev, (e) => e.preventDefault()));

const zona = $("#drop-zone");
["dragenter", "dragover"].forEach((ev) => zona.addEventListener(ev, () => zona.classList.add("over")));
["dragleave", "drop"].forEach((ev) => zona.addEventListener(ev, () => zona.classList.remove("over")));
zona.addEventListener("drop", (e) => { const f = e.dataTransfer?.files?.[0]; if (f) lerArquivo(f); });
$("#import-file").onchange = (e) => { const f = e.target.files[0]; if (f) lerArquivo(f); e.target.value = ""; };

async function lerArquivo(file) {
  if (file.size > 5 * 1024 * 1024) return toast("Arquivo grande demais (limite de 5 MB).", "bad");
  const buf = await file.arrayBuffer();
  let texto;
  try { texto = new TextDecoder("utf-8", { fatal: true }).decode(buf); }
  catch { texto = new TextDecoder("windows-1252").decode(buf); }   // CSV salvo pelo Excel
  $("#import-content").value = texto.replace(/^﻿/, "");

  const ext = file.name.split(".").pop().toLowerCase();
  $("#import-format").value = ext === "json" ? "json" : ext === "csv" ? "csv" : "auto";
  if (!state.decks.length && !$("#import-deck-new").value) {
    $("#import-deck-new").value = file.name.replace(/\.[^.]+$/, "");
  }
  toast(`Arquivo "${file.name}" carregado`, "ok");
  $("#btn-preview-import").click();
}

function cardPreviewHTML(c, opcoes = {}) {
  const correta = c.kind === "quiz" ? c.options[+c.answer] : c.answer;
  const seletor = opcoes.indice === undefined ? "" : `
    <label class="select-mark"><input type="checkbox" data-pick="${opcoes.indice}" ${opcoes.marcada ? "checked" : ""} aria-label="Incluir esta questão"></label>`;
  return `<div class="card-item">
    ${seletor}
    <div style="flex:1;min-width:0">
      <div class="q">${esc(c.question)}</div>
      <div class="meta">
        <span class="badge">${c.kind === "quiz" ? "múltipla escolha" : "flashcard"}</span>
        <span>✔ ${esc(correta || "")}</span>
      </div>
      ${c.explanation ? `<div class="why-line">${esc(c.explanation)}</div>` : ""}
    </div>
  </div>`;
}

$("#btn-preview-import").onclick = async () => {
  const content = $("#import-content").value;
  if (!content.trim()) return toast("Cole algum conteúdo ou escolha um arquivo primeiro", "bad");
  try {
    const res = await api("/api/import/preview", { method: "POST", body: {
      content, format: $("#import-format").value } });
    renderImportPreview(res);
  } catch (err) { toast(err.message, "bad"); }
};

function renderImportPreview(res) {
  const el = $("#import-preview");
  if (!res.cards.length) {
    el.innerHTML = `<div class="card notice warn" style="margin-top:16px">${ico("x")}
      <span><b>Nada reconhecido.</b> ${res.errors.map(esc).join("<br>") || "Confira o formato e tente de novo."}</span></div>`;
    return;
  }
  el.innerHTML = `
    <div class="row-between" style="margin-top:22px">
      <h3 style="margin:0">${plural(res.cards.length, "questão reconhecida", "questões reconhecidas")} <span class="muted">· formato ${esc(res.format)}</span></h3>
      <button class="btn primary" id="btn-do-import">Importar tudo</button>
    </div>
    ${res.errors.length ? `<p class="muted">Ignoradas: ${res.errors.map(esc).join("; ")}</p>` : ""}
    <div class="card-list">${res.cards.map((c) => cardPreviewHTML(c)).join("")}</div>`;
  $("#btn-do-import").onclick = async () => {
    try {
      const out = await api("/api/import", { method: "POST", body: {
        deck_id: +$("#import-deck").value || null,
        deck_name: $("#import-deck-new").value.trim(),
        content: $("#import-content").value,
        format: $("#import-format").value } });
      toast(`${plural(out.imported, "questão importada", "questões importadas")}`, "ok");
      $("#import-content").value = "";
      $("#import-deck-new").value = "";
      el.innerHTML = "";
      loadDecks();
    } catch (err) { toast(err.message, "bad"); }
  };
}

/* ------------------------------------------------------------------------- IA */

async function loadAiStatus() {
  await ensureDecks();
  const st = await api("/api/ai/status");
  const box = $("#ai-key-box");

  const formChave = (aviso = "") => {
    box.innerHTML = `<div class="card form">
      ${aviso}
      <div class="field">
        <label for="ai-key">Chave da API do ${esc(st.provider)}</label>
        <input type="password" id="ai-key" placeholder="AIza…" autocomplete="off" spellcheck="false">
        <p class="muted small-text" style="margin:8px 0 0">Crie a sua em
          <a href="#" id="link-aistudio">aistudio.google.com/apikey</a>. Depois de salva, ela fica
          <b>criptografada com o Windows</b> e não volta a aparecer na tela.</p>
      </div>
      <div class="actions">
        <button class="btn primary" id="btn-save-key">${ico("lock")}<span>Salvar chave</span></button>
        ${st.configured ? `<button class="btn ghost" id="btn-cancel-key">Cancelar</button>` : ""}
      </div>
    </div>`;
    $("#link-aistudio").onclick = (e) => { e.preventDefault(); abrirLink("https://aistudio.google.com/apikey"); };
    $("#btn-save-key").onclick = async () => {
      const valor = $("#ai-key").value.trim();
      if (!valor) return toast("Cole a chave primeiro", "bad");
      await api("/api/ai/key", { method: "POST", body: { api_key: valor } });
      $("#ai-key").value = "";
      toast("Chave salva e protegida", "ok");
      loadAiStatus();
    };
    if ($("#btn-cancel-key")) $("#btn-cancel-key").onclick = () => loadAiStatus();
  };

  if (!st.configured) {
    formChave(st.key_unreadable ? `<div class="notice warn" style="margin:0 0 14px">${ico("lock")}
      <span><b>A chave salva não pôde ser lida neste computador ou usuário.</b> Isso acontece quando o arquivo de dados vem de outra máquina — por segurança a chave não viaja junto. Cole-a de novo.</span></div>` : "");
    return;
  }

  box.innerHTML = `<div class="card form key-box">
      <span class="lock-badge">${ico("lock")}${st.from_env ? "Chave da variável de ambiente" : "Chave protegida pelo Windows"}</span>
      <div class="inline" style="margin-left:auto">
        <label for="ai-model" class="muted small-text">Modelo</label>
        <select id="ai-model"><option value="${esc(st.model)}">${esc(st.model)}</option></select>
        ${st.from_env ? "" : `<button class="btn ghost small" id="btn-change-key">Trocar chave</button>
                               <button class="btn danger small" id="btn-remove-key">Remover</button>`}
      </div>
    </div>`;
  if ($("#btn-change-key")) {
    $("#btn-change-key").onclick = () => formChave();
    $("#btn-remove-key").onclick = async () => {
      const sim = await confirmar("Remover a chave?", "Você precisará colar a chave de novo para gerar questões.", { ok: "Remover", perigo: true });
      if (!sim) return;
      await api("/api/ai/key", { method: "POST", body: { api_key: "" } });
      toast("Chave removida");
      loadAiStatus();
    };
  }
  $("#ai-model").onchange = async (e) => {
    await api("/api/ai/model", { method: "POST", body: { model: e.target.value } });
    toast(`Modelo: ${e.target.value}`, "ok");
  };
  // A lista vem da própria conta, então modelos novos aparecem sem mexer no código.
  api("/api/ai/models").then(({ models }) => {
    const sel = $("#ai-model");
    if (!sel || !models?.length) return;
    sel.innerHTML = models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
    sel.value = models.includes(st.model) ? st.model : models[0];
  }).catch(() => {});
}

const LIMITE_MATERIAL = 40000;   // igual ao do Python (app/ai.py)
let gerando = false;

function atualizarContador() {
  const n = $("#ai-material").value.length;
  const cont = $("#ai-count");
  const acima = n > LIMITE_MATERIAL;
  cont.textContent = n
    ? `${n.toLocaleString("pt-BR")} de ${LIMITE_MATERIAL.toLocaleString("pt-BR")} caracteres${acima ? " — divida o material em partes" : ""}`
    : "";
  cont.classList.toggle("over", acima);
  $("#btn-generate").disabled = gerando || acima;
}
$("#ai-material").addEventListener("input", atualizarContador);

// O erro fica na tela até o próximo clique; um aviso de 3 segundos passava batido.
function mostrarAvisoIA(texto, tipo) {
  $("#ai-error").innerHTML = texto
    ? `<div class="notice ${tipo}">${ico(tipo === "error" ? "x" : "shield")}<span>${esc(texto)}</span></div>` : "";
}

$("#btn-generate").onclick = async () => {
  if (gerando) return;                      // um clique = um pedido
  gerando = true;
  atualizarContador();
  mostrarAvisoIA("", "");
  const status = $("#ai-status");
  const inicio = Date.now();
  const tick = () => {
    status.innerHTML = `<span class="spinner"></span> Gerando… ${Math.round((Date.now() - inicio) / 1000)}s <span class="small-text">(um único pedido, até 2 min)</span>`;
  };
  tick();
  const relogio = setInterval(tick, 1000);
  try {
    const res = await api("/api/ai/generate", { method: "POST", body: {
      material: $("#ai-material").value,
      n: Math.min(20, Math.max(1, +$("#ai-n").value || 10)),
      kind: $("#ai-kind").value,
      difficulty: $("#ai-difficulty").value,
      notes: $("#ai-notes").value } });
    state.aiCards = res.cards.map((c) => ({ ...c, marcada: true }));
    renderAiPreview();
    if (res.aviso) mostrarAvisoIA(res.aviso, "warn");
  } catch (err) {
    toast(err.message, "bad");
    mostrarAvisoIA(err.message, "error");
  } finally {
    clearInterval(relogio);
    status.textContent = "";
    gerando = false;
    atualizarContador();
  }
};

function renderAiPreview() {
  const el = $("#ai-preview");
  if (!state.aiCards.length) { el.innerHTML = ""; return; }
  const opts = state.decks.map((d) => `<option value="${d.id}">${esc(d.name)}</option>`).join("");
  const marcadas = state.aiCards.filter((c) => c.marcada).length;

  el.innerHTML = `
    <div class="row-between" style="margin-top:22px">
      <h3 style="margin:0">${plural(state.aiCards.length, "questão gerada", "questões geradas")}
        <span class="muted">· ${marcadas} selecionada${marcadas === 1 ? "" : "s"}</span></h3>
      <button class="btn ghost small" id="btn-pick-all">${marcadas === state.aiCards.length ? "Desmarcar todas" : "Marcar todas"}</button>
    </div>
    <div class="card-list">${state.aiCards.map((c, i) => cardPreviewHTML(c, { indice: i, marcada: c.marcada })).join("")}</div>
    <div class="card form" style="margin-top:14px">
      <div class="inline">
        <select id="ai-deck">${opts}<option value="">— criar novo —</option></select>
        <input id="ai-deck-new" placeholder="Nome do novo baralho" style="flex:1;min-width:180px" autocomplete="off">
        <button class="btn primary" id="btn-save-ai" ${marcadas ? "" : "disabled"}>Adicionar ${marcadas} ao baralho</button>
      </div>
    </div>`;

  $$("[data-pick]", el).forEach((cb) => cb.onchange = () => {
    state.aiCards[+cb.dataset.pick].marcada = cb.checked;
    renderAiPreview();
  });
  $("#btn-pick-all").onclick = () => {
    const alvo = marcadas !== state.aiCards.length;
    state.aiCards.forEach((c) => (c.marcada = alvo));
    renderAiPreview();
  };
  $("#btn-save-ai").onclick = async () => {
    try {
      const escolhidas = state.aiCards.filter((c) => c.marcada).map(({ marcada, ...c }) => c);
      const out = await api("/api/import", { method: "POST", body: {
        deck_id: +$("#ai-deck").value || null,
        deck_name: $("#ai-deck-new").value.trim(),
        cards: escolhidas } });
      toast(`${plural(out.imported, "questão adicionada", "questões adicionadas")}`, "ok");
      state.aiCards = [];
      renderAiPreview();
      loadDecks();
    } catch (err) { toast(err.message, "bad"); }
  };
}

/* ------------------------------------------------------------------ progresso */

$("#stats-deck").onchange = loadStats;

const isoLocal = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function heatmapHTML(mapa) {
  const hoje = new Date(); hoje.setHours(0, 0, 0, 0);
  const inicio = new Date(hoje);
  inicio.setDate(hoje.getDate() - ((hoje.getDay() + 6) % 7) - 77);   // segunda-feira, 11 semanas atrás
  let celulas = "", ativos = 0, total = 0, melhor = { iso: "", n: 0 };
  for (let i = 0; i < 84; i++) {
    const d = new Date(inicio); d.setDate(inicio.getDate() + i);
    const iso = isoLocal(d);
    const n = mapa[iso] || 0;
    const futuro = d > hoje;
    if (!futuro && n > 0) { ativos += 1; total += n; if (n > melhor.n) melhor = { iso, n }; }
    const nivel = n === 0 ? 0 : n < 5 ? 1 : n < 15 ? 2 : n < 30 ? 3 : 4;
    const rot = futuro ? "" : `${diaMes(iso)}: ${plural(n, "revisão", "revisões")}`;
    celulas += `<i class="${nivel ? "l" + nivel : ""} ${futuro ? "f" : ""}" title="${rot}"></i>`;
  }
  return `<div class="heat-wrap">
      <div>
        <div class="heat" role="img" aria-label="Revisões por dia nas últimas 12 semanas">${celulas}</div>
        <div class="heat-legend">menos <i></i><i class="l1"></i><i class="l2"></i><i class="l3"></i><i class="l4"></i> mais</div>
      </div>
      <div class="heat-side">
        <div><b>${ativos}</b><span>${ativos === 1 ? "dia estudado" : "dias estudados"} nas 12 semanas</span></div>
        <div><b>${total}</b><span>${total === 1 ? "revisão" : "revisões"} no período</span></div>
        <div><b>${melhor.n ? melhor.n : "—"}</b><span>${melhor.n ? `no melhor dia (${diaMes(melhor.iso)})` : "nenhum dia ainda"}</span></div>
      </div>
    </div>`;
}

// Os últimos 14 dias completos: dia sem estudo aparece como barra vazia, não some do gráfico.
function ultimos14(daily) {
  const porDia = Object.fromEntries(daily.map((d) => [d.dia, d]));
  return Array.from({ length: 14 }, (_, i) => {
    const d = new Date(); d.setDate(d.getDate() - 13 + i);
    const iso = isoLocal(d);
    return porDia[iso] || { dia: iso, revisoes: 0, acertos: 0 };
  });
}

async function loadStats() {
  await ensureDecks();
  fillDeckSelects();
  const deckId = $("#stats-deck").value;
  const s = await api(`/api/stats${deckId ? `?deck_id=${deckId}` : ""}`);
  const t = s.totals;
  const dias14 = ultimos14(s.daily);
  const max = Math.max(1, ...dias14.map((d) => d.revisoes));
  const maxPrev = Math.max(1, ...s.forecast.map((f) => f.n));
  const pct = (n) => (t.total ? (100 * n / t.total).toFixed(1) : 0);

  $("#stats-body").innerHTML = `
    <div class="summary">
      <div class="stat"><b>${s.reviews}</b><span>revisões feitas</span></div>
      <div class="stat"><b>${s.accuracy}%</b><span>de acerto</span></div>
      <div class="stat"><b>${s.streak > 0 ? ico("flame", "flame") : ""}${s.streak}</b><span>${s.streak === 1 ? "dia seguido" : "dias seguidos"}</span></div>
      <div class="stat ${t.devidos ? "hot" : ""}"><b>${t.devidos}</b><span>na fila agora</span></div>
    </div>

    <div class="card panel" style="margin-bottom:14px">
      <h3>Atividade nas últimas 12 semanas</h3>
      ${heatmapHTML(s.heatmap || {})}
    </div>

    <div class="stats-grid">
      <div class="card panel">
        <h3>Últimos 14 dias</h3>
        ${s.daily.length ? `
          <div class="bars">
            ${dias14.map((d) => d.revisoes ? `
              <div class="bar" style="height:${(d.revisoes / max) * 100}%" title="${diaMes(d.dia)}: ${d.revisoes} revisões, ${d.acertos} acertos">
                <div class="hit" style="height:${(d.acertos / d.revisoes) * 100}%"></div>
              </div>` : `<div class="bar zero" title="${diaMes(d.dia)}: sem revisões"></div>`).join("")}
          </div>
          <div class="bar-labels">${dias14.map((d) => `<span>${d.dia.slice(8)}</span>`).join("")}</div>
          <p class="muted small-text" style="margin:10px 0 0">Barra inteira = revisões · parte verde = acertos</p>`
          : `<p class="muted">Sem revisões ainda. Estude um pouco e volte aqui.</p>`}
      </div>

      <div class="card panel">
        <h3>Suas questões</h3>
        <div class="stack" style="margin:10px 0 8px">
          <span class="s-rev" style="width:${pct(t.maduros)}%"></span>
          <span class="s-learn" style="width:${pct(t.aprendendo)}%"></span>
          <span class="s-new" style="width:${pct(t.novos)}%"></span>
        </div>
        <div class="legend">
          <span><i class="s-rev"></i><b>${t.maduros}</b> em revisão</span>
          <span><i class="s-learn"></i><b>${t.aprendendo}</b> aprendendo</span>
          <span><i class="s-new"></i><b>${t.novos}</b> novas</span>
        </div>
        <h3 style="margin-top:20px">Próximos 7 dias</h3>
        ${s.forecast.length ? s.forecast.map((f) => `
          <div class="hbar-row"><span>${diaMes(f.dia)}</span>
            <div class="track"><span style="width:${(f.n / maxPrev) * 100}%"></span></div><span class="n">${f.n}</span></div>`).join("")
          : `<p class="muted" style="margin:0">Nada agendado.</p>`}
      </div>
    </div>

    <div class="card panel" style="margin-top:14px">
      <h3>Onde você mais erra</h3>
      ${s.hardest.length ? s.hardest.map((h) => `
        <div class="hard-item"><div class="q">${esc(h.question.slice(0, 110))}</div>
          <div class="meta">${plural(h.lapses, "erro", "erros")} · ${h.acertos}/${h.tentativas} acertos</div></div>`).join("")
        : `<p class="muted" style="margin:0">Nenhuma questão problemática ainda.</p>`}
    </div>`;
}

/* -------------------------------------------------------------------- ajustes */

async function loadSettings() {
  const s = await api("/api/settings");
  aplicarTema(s.theme);
  $("#about-version").textContent = `StudyIA ${s.version.replace(/\.0$/, "")}`;
  $("#link-author").onclick = (e) => { e.preventDefault(); abrirLink("https://github.com/4TyllaL/"); };
}

$$("[data-theme-choice]").forEach((b) => b.onclick = async () => {
  aplicarTema(b.dataset.themeChoice);
  await api("/api/settings", { method: "POST", body: { theme: b.dataset.themeChoice } });
});

$("#btn-backup").onclick = async () => {
  const r = await window.pywebview.api.exportar_backup();
  if (r?.cancelado) return;
  if (r?.error) return toast(r.error, "bad");
  toast(`Backup salvo (${r.data.tamanho_kb} KB)`, "ok");
};
$("#btn-open-data").onclick = () => window.pywebview.api.abrir_pasta_dados();

/* ---------------------------------------------------------------------- início */

window.addEventListener("beforeunload", endSession);

async function iniciar() {
  try { aplicarTema((await api("/api/settings")).theme); } catch { /* segue no tema automático */ }
  loadDecks();
}

// `pywebviewready` dispara quando a ponte com o Python fica disponível; se já estiver
// pronta quando este script rodar, o evento pode ter passado — daí o teste direto.
if (window.pywebview?.api) iniciar();
else window.addEventListener("pywebviewready", iniciar);
