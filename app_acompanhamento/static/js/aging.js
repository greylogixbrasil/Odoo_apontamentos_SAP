(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const graficos = {};
  let dados = null;
  let abaAtiva = "geral";
  const base = { pagina: 1, tamanho: 50, ordem: "data_criacao", desc: true };

  // ── Tema (este dashboard abre escuro por padrao) ──────────
  function aplicarBotaoTema() {
    $("btnThemeToggle").textContent = document.documentElement.getAttribute("data-theme") === "dark" ? "🌙" : "🌕";
  }
  $("btnThemeToggle").addEventListener("click", () => {
    const novo = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", novo);
    try { localStorage.setItem("aging_theme_mode", novo); } catch (e) { /* sem storage: so nesta visita */ }
    aplicarBotaoTema();
    if (dados) renderGraficos();
  });
  aplicarBotaoTema();

  // ── Formatação ────────────────────────────────────────────
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (v, casas = 1) => (v === null || v === undefined ? "—" : (v * 100).toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas }) + "%");
  const num = (v, casas = 0) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("pt-BR", { maximumFractionDigits: casas }));
  const brl = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 }));
  function brlCurto(v) {
    if (v === null || v === undefined) return "—";
    const a = Math.abs(v);
    if (a >= 1e9) return "R$ " + (v / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " bi";
    if (a >= 1e6) return "R$ " + (v / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " mi";
    if (a >= 1e3) return "R$ " + (v / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " mil";
    return brl(v);
  }
  const dataBR = (iso) => (iso ? iso.slice(0, 10).split("-").reverse().join("/") : "");
  const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
  const mesCurto = (ym) => { const [a, m] = ym.split("-"); return `${MESES[+m - 1]}/${a.slice(2)}`; };

  function mostrarErro(msg) { $("avisoErro").textContent = msg; $("avisoErro").classList.remove("hidden"); }

  // ── Filtros (tambem lidos da URL, pra outra aplicacao poder linkar ja filtrado) ──
  const CAMPOS = { criada_de: "fCriadaDe", criada_ate: "fCriadaAte", vertical: "fVertical", tipo_oferta: "fTipo",
                   responsavel: "fResponsavel", cliente: "fCliente", base_origem: "fBase", busca: "fBusca" };

  function lerFiltros() {
    const p = new URLSearchParams();
    Object.entries(CAMPOS).forEach(([k, id]) => { const v = $(id).value.trim(); if (v) p.set(k, v); });
    p.set("so_validas", $("fSoValidas").checked ? "true" : "false");
    return p;
  }

  function aplicarFiltrosDaUrl() {
    const p = new URLSearchParams(location.search);
    Object.entries(CAMPOS).forEach(([k, id]) => { if (p.has(k)) $(id).value = p.get(k); });
    if (p.has("so_validas")) $("fSoValidas").checked = p.get("so_validas") !== "false";
  }

  function preencherSelect(id, valores) {
    const sel = $(id);
    valores.forEach((v) => { const o = document.createElement("option"); o.value = v; o.textContent = v; sel.appendChild(o); });
  }

  async function carregarOpcoes() {
    const op = await (await fetch("/api/aging/filtros")).json();
    preencherSelect("fVertical", op.verticais);
    preencherSelect("fTipo", op.tipos_oferta);
    preencherSelect("fResponsavel", op.responsaveis);
    preencherSelect("fBase", op.bases);
    $("listaClientes").innerHTML = op.clientes.map((c) => `<option value="${esc(c)}">`).join("");
  }

  /** Clique num grafico/tabela -> filtra o dashboard inteiro por aquele valor. */
  function filtrarPor(campo, valor) {
    const sel = $(CAMPOS[campo]);
    if (sel.tagName === "SELECT" && ![...sel.options].some((o) => o.value === valor)) return;
    sel.value = valor;
    base.pagina = 1;
    atualizar();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  let timerFiltro = null;
  function aoMudarFiltro() {
    clearTimeout(timerFiltro);
    timerFiltro = setTimeout(() => { base.pagina = 1; atualizar(); }, 350);
  }
  Object.values(CAMPOS).forEach((id) => $(id).addEventListener(id === "fBusca" || id === "fCliente" ? "input" : "change", aoMudarFiltro));
  $("fSoValidas").addEventListener("change", aoMudarFiltro);
  $("btnLimpar").addEventListener("click", () => {
    Object.values(CAMPOS).forEach((id) => { $(id).value = ""; });
    $("fSoValidas").checked = true;
    aoMudarFiltro();
  });

  // ── Abas ──────────────────────────────────────────────────
  function urlAtual() {
    const p = lerFiltros();
    if (abaAtiva !== "geral") p.set("aba", abaAtiva);
    return "?" + p.toString();
  }

  $("abas").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-aba]");
    if (!btn) return;
    abaAtiva = btn.dataset.aba;
    history.replaceState(null, "", urlAtual());
    document.querySelectorAll("#abas button").forEach((b) => b.classList.toggle("ativa", b === btn));
    document.querySelectorAll("section.ag-aba").forEach((s) => s.classList.toggle("hidden", s.dataset.aba !== abaAtiva));
    if (abaAtiva === "base") { carregarBase(); carregarQualidade(); }
    else if (dados) renderGraficos();  // Chart.js precisa do canvas visivel pra medir
  });

  // ── "Atualizado há X min" ─────────────────────────────────
  let ultimaSync = null;
  function renderAtualizado() {
    if (!ultimaSync) { $("atualizado").textContent = "Ainda não sincronizado com o Odoo"; return; }
    const min = Math.max(0, Math.round((Date.now() - new Date(ultimaSync).getTime()) / 60000));
    const txt = min < 1 ? "agora" : min < 60 ? `há ${min} min` : min < 1440 ? `há ${Math.floor(min / 60)} h ${min % 60} min` : `há ${Math.floor(min / 1440)} dia(s)`;
    $("atualizado").textContent = `Atualizado ${txt}`;
    $("atualizado").classList.toggle("ag-atrasado", min > 120);
  }
  async function carregarStatus() {
    try { ultimaSync = (await (await fetch("/api/aging/status")).json()).ultima_sync_ok; } catch (e) { /* mantem o anterior */ }
    renderAtualizado();
  }
  setInterval(renderAtualizado, 60000);
  setInterval(carregarStatus, 5 * 60000);

  // ── Indicadores ───────────────────────────────────────────
  async function atualizar() {
    $("avisoErro").classList.add("hidden");
    history.replaceState(null, "", urlAtual());
    try {
      const resp = await fetch("/api/aging/indicadores?" + lerFiltros().toString());
      if (!resp.ok) throw new Error((await resp.json()).detail || "Erro ao carregar indicadores");
      dados = await resp.json();
      renderTudo();
      if (abaAtiva === "base") carregarBase();
    } catch (e) { mostrarErro(e.message); }
  }

  function card(rotulo, valor, sub, classe = "") {
    return `<div class="kpi ${classe}"><div class="rail"></div><div class="lab">${rotulo}</div><div class="val">${valor}</div><div class="sub">${sub}</div></div>`;
  }

  function tabela(id, colunas, linhas, aoClicar) {
    const head = "<thead><tr>" + colunas.map((c) => `<th class="${c.num ? "col-valor" : ""}">${c.t}</th>`).join("") + "</tr></thead>";
    const body = linhas.length
      ? linhas.map((r, i) => `<tr data-i="${i}" class="${aoClicar ? "ag-clicavel" : ""}">` + colunas.map((c) => `<td class="${c.num ? "col-valor" : ""}">${c.f ? c.f(r) : esc(r[c.k])}</td>`).join("") + "</tr>").join("")
      : `<tr><td colspan="${colunas.length}" class="empty-state">Sem dados para esses filtros.</td></tr>`;
    $(id).innerHTML = head + "<tbody>" + body + "</tbody>";
    if (aoClicar) $(id).querySelectorAll("tbody tr[data-i]").forEach((tr) => tr.addEventListener("click", () => aoClicar(linhas[+tr.dataset.i])));
  }

  const COLS_GRUPO = [
    { t: "Total", num: true, f: (r) => num(r.total) },
    { t: "Ganhas", num: true, f: (r) => num(r.ganhas) },
    { t: "Perdidas", num: true, f: (r) => num(r.perdidas) },
    { t: "Abertas", num: true, f: (r) => num(r.abertas) },
    { t: "Win rate", num: true, f: (r) => pct(r.win_rate_qtd) },
    { t: "Win rate (valor)", num: true, f: (r) => pct(r.win_rate_valor) },
    { t: "Valor ganho", num: true, f: (r) => brl(r.valor_ganho) },
    { t: "Pipeline aberto", num: true, f: (r) => brl(r.pipeline_valor) },
    { t: "Ciclo mediano ganhas (dias)", num: true, f: (r) => num(r.ciclo_mediano_ganhas) },
  ];

  function renderTudo() {
    const c = dados.cards;
    const ab = dados.abertas;
    const prev = ab.previsao;
    $("frase").textContent = dados.frase;

    // Visao geral
    $("cards").innerHTML = [
      card("Oportunidades", num(c.total), `${num(c.ganhas)} ganhas · ${num(c.perdidas)} perdidas · ${num(c.abertas)} abertas`),
      card("Win rate (quantidade)", pct(c.win_rate_qtd), "ganhas ÷ (ganhas + perdidas)", "ok"),
      card("Win rate (valor)", pct(c.win_rate_valor), "valor ganho ÷ valor fechado", "ok"),
      card("Valor ganho", brlCurto(c.valor_ganho), `ticket mediano ${brlCurto(c.ticket_mediano)}`),
      card("Pipeline aberto", brlCurto(c.pipeline_valor), `${num(c.pipeline_qtd)} abertas`, "amber"),
      card("Ciclo mediano das ganhas", `${num(c.ciclo_mediano_ganhas)} dias`, `perdidas: ${num(c.ciclo_mediano_perdidas)} dias`),
    ].join("");
    $("destaques").innerHTML = dados.destaques.length
      ? dados.destaques.map((d) => `<li>${esc(d)}</li>`).join("")
      : "<li>Poucos dados com esses filtros para gerar destaques.</li>";

    // Win rate
    tabela("tbWinVertical", [{ t: "Vertical", k: "vertical" }, ...COLS_GRUPO], dados.por_vertical, (r) => filtrarPor("vertical", r.vertical));
    const saz = dados.sazonalidade;
    $("subSazonalidade").textContent = `Ganhas por mês da data de ganho, somando os anos do filtro. ${pct(saz.pct_ultima_semana)} das vitórias são registradas na última semana do mês (o esperado sem efeito de fechamento seria ${pct(saz.pct_esperado)}).`;

    // Aging
    const corte = dados.ponto_de_corte;
    $("subChance").textContent = "Entre as oportunidades que chegaram vivas ao dia X, quantas foram ganhas. Mostra a partir de quando insistir deixa de valer a pena. Linha tracejada: win rate geral." +
      (corte !== null ? ` Depois de ${corte} dias, a chance cai abaixo de 20%.` : "");
    if (dados.decisoes.dia_80_ganhas !== null) $("subDecisoes").textContent = `% acumulado das ganhas e das perdidas já encerradas até cada dia. 80% das ganhas fecham em até ${dados.decisoes.dia_80_ganhas} dias.`;
    tabela("tbBases", [
      { t: "Base", k: "base" }, { t: "Fechadas", num: true, f: (r) => num(r.fechadas) }, { t: "Win rate", num: true, f: (r) => pct(r.win_rate) },
      { t: "Mediana ganhas", num: true, f: (r) => `${num(r.mediana_ganhas)} d` }, { t: "Mediana perdidas", num: true, f: (r) => `${num(r.mediana_perdidas)} d` },
    ], dados.bases);
    tabela("tbAgingStatus", [
      { t: "Status", f: (r) => ({ ganha: "Ganha", perdida: "Perdida", aberta: "Em aberto (dias até hoje)" }[r.status]) },
      { t: "Qtde", num: true, f: (r) => num(r.qtd) }, { t: "Médio", num: true, f: (r) => num(r.medio, 1) },
      { t: "Mediano", num: true, f: (r) => num(r.mediano, 1) }, { t: "Mínimo", num: true, f: (r) => num(r.minimo) },
      { t: "Máximo", num: true, f: (r) => num(r.maximo) },
    ], dados.aging_por_status);

    // Pipeline
    $("cardsPipeline").innerHTML = [
      card("Oportunidades abertas", num(ab.qtd), `mediana de ${num(ab.mediana_dias)} dias em aberto`, "amber"),
      card("Valor aberto", brlCurto(ab.valor), `valor mediano ${brlCurto(ab.valor_mediano)}`),
      card("Valor ponderado pela chance", brlCurto(prev.ponderado), `${pct(ab.valor ? prev.ponderado / ab.valor : null)} do valor aberto (histórico)`),
      card("Vitórias esperadas em 12 meses", brlCurto(prev.valor_12_meses), `~${num(prev.qtd_12_meses)} oportunidades · ${brlCurto(prev.depois_12_meses)} depois disso`, "ok"),
      card("Abertas há mais de 1 ano", num(ab.mais_de_1_ano), `${pct(ab.pct_mais_de_1_ano)} das abertas · ${brlCurto(ab.valor_mais_de_1_ano)}`, "crit"),
    ].join("");
    tabela("tbPipelineVertical", [
      { t: "Vertical", k: "vertical" }, { t: "Abertas", num: true, f: (r) => num(r.abertas) },
      { t: "Pipeline aberto", num: true, f: (r) => brl(r.pipeline_valor) }, { t: "Win rate", num: true, f: (r) => pct(r.win_rate_qtd) },
    ], dados.por_vertical.filter((r) => r.abertas > 0).sort((a, b) => b.pipeline_valor - a.pipeline_valor), (r) => filtrarPor("vertical", r.vertical));

    // Clientes
    const cl = dados.clientes;
    const linhasNR = [
      ["Win rate", (g) => pct(g.win_rate)], ["Win rate em valor", (g) => pct(g.win_rate_valor)],
      ["Fechadas", (g) => num(g.fechadas)], ["Ganhas", (g) => num(g.ganhas)],
      ["Ciclo mediano das ganhas", (g) => `${num(g.ciclo_mediano)} d`], ["Ticket mediano ganho", (g) => brlCurto(g.ticket_mediano)],
      ["Valor ganho", (g) => brlCurto(g.valor_ganho)],
    ];
    $("tbNovoRecorrente").innerHTML = `<thead><tr><th></th><th class="col-valor">Cliente novo</th><th class="col-valor">Cliente recorrente</th></tr></thead><tbody>` +
      linhasNR.map(([t, f]) => `<tr><td>${t}</td><td class="col-valor">${f(cl.novo)}</td><td class="col-valor">${f(cl.recorrente)}</td></tr>`).join("") + "</tbody>";
    const conc = cl.concentracao;
    $("subConcentracao").textContent = `Os 15 maiores clientes por valor ganho. Top 10 = ${pct(conc.pct_top_10)} do total` +
      (conc.clientes_para_80 ? `; ${conc.clientes_para_80} de ${conc.clientes_com_vitoria} clientes (${pct(conc.clientes_para_80 / conc.clientes_com_vitoria)}) somam 80% do valor ganho.` : ".") + " Clique para filtrar.";
    tabela("tbClientes", [{ t: "Cliente", k: "cliente" }, ...COLS_GRUPO], dados.clientes_top, (r) => filtrarPor("cliente", r.cliente));

    // Ofertas
    tabela("tbOfertas", [
      { t: "Tipo de oferta", k: "tipo" }, { t: "Oportunidades", num: true, f: (r) => num(r.oportunidades) },
      { t: "Fechadas", num: true, f: (r) => num(r.fechadas) },
      { t: "Win rate ↓", num: true, f: (r) => `<span class="ag-barrinha" style="--w:${(r.win_rate || 0) * 100}%"></span>${pct(r.win_rate)}` },
      { t: "Win rate (valor)", num: true, f: (r) => pct(r.win_rate_valor) }, { t: "Ciclo mediano", num: true, f: (r) => `${num(r.ciclo_mediano)} d` },
      { t: "Ticket mediano", num: true, f: (r) => brlCurto(r.ticket_mediano) }, { t: "Valor ganho", num: true, f: (r) => brlCurto(r.valor_ganho) },
      { t: "Abertas", num: true, f: (r) => num(r.abertas) }, { t: "Valor aberto", num: true, f: (r) => brlCurto(r.valor_aberto) },
    ], dados.ofertas.tabela, (r) => filtrarPor("tipo_oferta", r.tipo));

    // Time
    const tm = dados.time;
    $("subVolume").textContent = `Cada bolha é um responsável com 20+ oportunidades fechadas: volume de fechadas × win rate; o tamanho é o valor ganho. Linhas tracejadas: mediana de volume (${num(tm.mediana_volume)}) e win rate geral (${pct(tm.win_rate_geral)}). Clique para filtrar.`;
    renderMapa(tm);
    tabela("tbTime", [{ t: "Responsável", k: "responsavel" }, ...COLS_GRUPO,
      { t: "Ciclo mediano perdidas (dias)", num: true, f: (r) => num(r.ciclo_mediano_perdidas) }], dados.time_tabela, (r) => filtrarPor("responsavel", r.responsavel));

    renderGraficos();
  }

  // Mapa de calor vendedor x trimestre (HTML, nao Chart.js)
  function renderMapa(tm) {
    if (!tm.mapa.length) { $("tbMapa").innerHTML = '<tbody><tr><td class="empty-state">Nenhum responsável com 20+ fechadas nesse filtro.</td></tr></tbody>'; return; }
    const geral = tm.win_rate_geral || 0;
    const head = "<thead><tr><th></th>" + tm.trimestres.map((t) => `<th>${t.replace("-", " ")}</th>`).join("") + '<th class="col-valor">Geral</th></tr></thead>';
    const body = tm.mapa.map((p) => `<tr><td class="ag-mapa-nome" data-resp="${esc(p.responsavel)}">${esc(p.responsavel)}</td>` +
      tm.trimestres.map((t) => {
        const c = p.celulas[t];
        if (!c) return "<td></td>";
        const titulo = `${p.responsavel} · ${t}\nGanhas ${c.ganhas} de ${c.fechadas} fechadas`;
        if (c.fechadas < 3) return `<td class="ag-mapa-poucas" title="${esc(titulo)}">${c.ganhas}/${c.fechadas}</td>`;
        const dif = c.win_rate - geral;
        const forca = Math.min(1, Math.abs(dif) / 0.3) * 0.75 + 0.2;
        const corBase = dif >= 0 ? "59,130,246" : "234,88,12";
        return `<td style="background:rgba(${corBase},${forca.toFixed(2)})" title="${esc(titulo)}">${Math.round(c.win_rate * 100)}%</td>`;
      }).join("") + `<td class="col-valor">${pct(p.geral, 0)}</td></tr>`).join("");
    $("tbMapa").innerHTML = head + "<tbody>" + body + "</tbody>";
    $("tbMapa").querySelectorAll(".ag-mapa-nome").forEach((td) => td.addEventListener("click", () => filtrarPor("responsavel", td.dataset.resp)));
  }

  // ── Gráficos (Chart.js) ───────────────────────────────────
  const CORES = { azul: "#3B82F6", laranja: "#EA580C", verde: "#16A34A", cinza: "#6B7280", vermelho: "#DC2626", roxo: "#8B5CF6", amarelo: "#F59E0B" };
  const PALETA = ["#3B82F6", "#EA580C", "#16A34A", "#F59E0B", "#EC4899", "#8B5CF6", "#14B8A6", "#6B7280"];
  const COR_MOTIVO = { "Cancelado ou suspenso": CORES.laranja, "Concorrência": CORES.verde, "Outros": CORES.roxo, "Não informado": CORES.cinza };

  // Plugin: rotulo de valor em cima/ao lado de cada barra (options.plugins.rotulos.formato)
  const pluginRotulos = {
    id: "rotulos",
    afterDatasetsDraw(chart, _args, opts) {
      if (!opts || !opts.formato) return;
      const { ctx } = chart;
      const horizontal = chart.options.indexAxis === "y";
      ctx.save();
      ctx.font = "11px 'Segoe UI', system-ui, sans-serif";
      ctx.fillStyle = Chart.defaults.color;
      chart.data.datasets.forEach((ds, di) => {
        if (opts.datasets && !opts.datasets.includes(di)) return;
        chart.getDatasetMeta(di).data.forEach((el, i) => {
          const v = ds.data[i];
          if (v === null || v === undefined) return;
          const texto = opts.formato(v, i, di);
          if (!texto) return;
          // bolha: texto logo acima do circulo
          const acima = chart.config.type === "bubble" ? (el.options.radius || 0) + 3 : 4;
          if (horizontal) { ctx.textAlign = "left"; ctx.textBaseline = "middle"; ctx.fillText(texto, el.x + 6, el.y); }
          else { ctx.textAlign = "center"; ctx.textBaseline = "bottom"; ctx.fillText(texto, el.x, el.y - acima); }
        });
      });
      ctx.restore();
    },
  };
  // Plugin: linha tracejada de referencia (options.plugins.linhaRef = {valor, eixo: "y"|"x", rotulo})
  const pluginLinhaRef = {
    id: "linhaRef",
    afterDraw(chart, _args, opts) {
      if (!opts || opts.valor === null || opts.valor === undefined) return;
      const refs = Array.isArray(opts.valor) ? opts.valor.map((v, i) => ({ valor: v, eixo: (opts.eixos || [])[i] || "y" })) : [{ valor: opts.valor, eixo: opts.eixo || "y" }];
      const { ctx, chartArea: a } = chart;
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = opts.cor || "rgba(160,170,180,.75)";
      ctx.lineWidth = 1;
      refs.forEach((r) => {
        const escala = chart.scales[r.eixo];
        if (!escala) return;
        const p = escala.getPixelForValue(r.valor);
        ctx.beginPath();
        if (r.eixo === "x") { ctx.moveTo(p, a.top); ctx.lineTo(p, a.bottom); } else { ctx.moveTo(a.left, p); ctx.lineTo(a.right, p); }
        ctx.stroke();
      });
      ctx.restore();
    },
  };

  function corTexto(nome, padrao) { return getComputedStyle(document.documentElement).getPropertyValue(nome).trim() || padrao; }

  function grafico(id, config) {
    if (graficos[id]) graficos[id].destroy();
    const escuro = document.documentElement.getAttribute("data-theme") === "dark";
    Chart.defaults.color = corTexto("--mut", "#9CA3AF");
    Chart.defaults.font.family = '"Segoe UI", system-ui, sans-serif';
    Chart.defaults.font.size = 11;
    config.options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: false,
      plugins: {} }, config.options || {});
    config.options.plugins = Object.assign({ legend: { position: "top", align: "start", labels: { boxWidth: 10, boxHeight: 10 } } }, config.options.plugins);
    const grade = escuro ? "rgba(255,255,255,.07)" : "rgba(30,36,40,.08)";
    Object.values(config.options.scales || {}).forEach((s) => { s.grid = Object.assign({ color: grade }, s.grid || {}); });
    // barra horizontal com rotulo ao lado: reserva espaco a direita pro texto nao ser cortado
    if (config.options.indexAxis === "y" && config.options.plugins.rotulos) {
      config.options.layout = Object.assign({ padding: { right: 120 } }, config.options.layout || {});
    }
    config.plugins = [pluginRotulos, pluginLinhaRef];
    graficos[id] = new Chart($(id), config);
  }

  const eixoPct = (max = 1) => ({ beginAtZero: true, max, ticks: { callback: (v) => Math.round(v * 100) + "%" } });
  const tooltipPct = { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${pct(ctx.raw && ctx.raw.y !== undefined ? ctx.raw.y : ctx.raw)}` } };
  const semLegenda = { display: false };
  const wrGeral = () => dados.cards.win_rate_qtd;

  function renderGraficos() {
    if (!dados) return;
    ({ geral: graficosGeral, winrate: graficosWinRate, aging: graficosAging, pipeline: graficosPipeline,
       clientes: graficosClientes, ofertas: graficosOfertas, time: graficosTime }[abaAtiva] || (() => {}))();
  }

  function graficosGeral() {
    const anos = dados.por_ano.filter((r) => r.ano !== null);
    grafico("chAno", { type: "bar", data: { labels: anos.map((r) => r.ano), datasets: [
      { label: "Ganhas", data: anos.map((r) => r.ganhas), backgroundColor: CORES.azul },
      { label: "Perdidas", data: anos.map((r) => r.perdidas), backgroundColor: CORES.laranja },
      { label: "Abertas", data: anos.map((r) => r.abertas), backgroundColor: CORES.cinza },
    ] }, options: { scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } } });
    grafico("chWinAno", { type: "line", data: { labels: anos.map((r) => r.ano), datasets: [
      { label: "Em quantidade", data: anos.map((r) => r.win_rate_qtd), borderColor: CORES.azul, backgroundColor: CORES.azul, tension: 0.2 },
      { label: "Em valor", data: anos.map((r) => r.win_rate_valor), borderColor: "#EC4899", backgroundColor: "#EC4899", tension: 0.2 },
    ] }, options: { scales: { y: eixoPct(1) }, plugins: { tooltip: tooltipPct } } });
  }

  function graficosWinRate() {
    const tk = dados.win_rate_ticket;
    grafico("chTicket", { type: "bar", data: { labels: tk.map((r) => r.faixa), datasets: [
      { label: "Win rate", data: tk.map((r) => r.win_rate), backgroundColor: CORES.azul },
    ] }, options: { scales: { y: eixoPct(1) }, plugins: { legend: semLegenda, tooltip: { callbacks: { label: (c) => `Win rate ${pct(c.raw)} · ${tk[c.dataIndex].fechadas} fechadas` } },
      rotulos: { formato: (v) => pct(v, 0) }, linhaRef: { valor: wrGeral() } } } });

    const verts = dados.por_vertical.filter((r) => r.ganhas + r.perdidas > 0).sort((a, b) => (b.win_rate_qtd || 0) - (a.win_rate_qtd || 0));
    grafico("chWinVertical", { type: "bar", data: { labels: verts.map((r) => r.vertical), datasets: [
      { label: "Win rate", data: verts.map((r) => r.win_rate_qtd), backgroundColor: CORES.azul },
    ] }, options: { indexAxis: "y", scales: { x: eixoPct(1) }, onClick: (_e, el) => el.length && filtrarPor("vertical", verts[el[0].index].vertical),
      plugins: { legend: semLegenda, tooltip: tooltipPct, linhaRef: { valor: wrGeral(), eixo: "x" },
        rotulos: { formato: (v, i) => `${pct(v, 0)} · ${num(verts[i].ganhas + verts[i].perdidas)} fechadas` } } } });

    const est = dados.perdas.por_estagio;
    grafico("chEstagioPerda", { type: "bar", data: { labels: est.map((r) => r.estagio), datasets: [
      { label: "Perdidas", data: est.map((r) => r.qtd), backgroundColor: CORES.laranja },
    ] }, options: { indexAxis: "y", scales: { x: { beginAtZero: true, grace: "25%" } }, plugins: { legend: semLegenda,
      rotulos: { formato: (v, i) => `${v} · ${pct(est[i].pct, 0)}` } } } });

    const motivos = ["Cancelado ou suspenso", "Concorrência", "Outros", "Não informado"];
    const empilhado = (id, linhas) => grafico(id, { type: "bar", data: { labels: linhas.map((r) => r.grupo), datasets:
      motivos.map((m) => ({ label: m, data: linhas.map((r) => r[m]), backgroundColor: COR_MOTIVO[m] })) },
      options: { scales: { x: { stacked: true }, y: Object.assign(eixoPct(1), { stacked: true }) },
        plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${pct(c.raw)} (${linhas[c.dataIndex].perdidas} perdidas)` } } } } });
    empilhado("chMotivoVertical", dados.perdas.por_vertical);
    empilhado("chMotivoAno", dados.perdas.por_ano);

    const saz = dados.sazonalidade.por_mes;
    const media = saz.reduce((s, r) => s + r.ganhas, 0) / 12;
    grafico("chSazonalidade", { type: "bar", data: { labels: MESES, datasets: [
      { label: "Ganhas", data: saz.map((r) => r.ganhas), backgroundColor: CORES.azul },
    ] }, options: { scales: { y: { beginAtZero: true, grace: "10%" } }, plugins: { legend: semLegenda, rotulos: { formato: (v) => num(v) }, linhaRef: { valor: media } } } });
  }

  function graficosAging() {
    const curva = dados.curva_chance;
    grafico("chChance", { type: "line", data: { labels: curva.map((p) => `${p.dia} d`), datasets: [
      { label: "Chance de ganho", data: curva.map((p) => p.win_rate), borderColor: CORES.azul, backgroundColor: CORES.azul, pointRadius: 0, tension: 0.2 },
    ] }, options: { scales: { y: eixoPct(1), x: { ticks: { maxTicksLimit: 30 } } }, plugins: { legend: semLegenda,
      tooltip: { callbacks: { label: (c) => `Win rate ${pct(c.raw)} · ${num(curva[c.dataIndex].fechadas)} fechadas passaram desse dia` } },
      linhaRef: { valor: wrGeral() } } } });

    const safras = dados.safras;
    const dias = [...new Set(safras.flatMap((s) => s.pontos.map((p) => p.dia)))].sort((a, b) => a - b);
    grafico("chSafra", { type: "line", data: { labels: dias.map((d) => `${d} d`), datasets: safras.map((s, i) => {
      const porDia = Object.fromEntries(s.pontos.map((p) => [p.dia, p.pct]));
      return { label: String(s.ano), data: dias.map((d) => (d in porDia ? porDia[d] : null)), borderColor: PALETA[i % PALETA.length],
        backgroundColor: PALETA[i % PALETA.length], pointRadius: 0, tension: 0.2, spanGaps: false };
    }) }, options: { scales: { y: eixoPct(0.6), x: { ticks: { maxTicksLimit: 30 } } }, plugins: { tooltip: tooltipPct } } });

    const faixas = dados.por_faixa;
    grafico("chFaixa", { type: "bar", data: { labels: faixas.map((r) => r.faixa.replace(" dias", "")), datasets: [
      { label: "Ganhas", data: faixas.map((r) => r.pct_ganhas), backgroundColor: CORES.azul },
      { label: "Perdidas", data: faixas.map((r) => r.pct_perdidas), backgroundColor: CORES.laranja },
    ] }, options: { scales: { y: eixoPct(undefined) }, plugins: { tooltip: tooltipPct } } });

    const dec = dados.decisoes;
    grafico("chDecisoes", { type: "line", data: { labels: dec.dias.map((d) => `${d} d`), datasets: [
      { label: "Ganhas", data: dec.ganhas, borderColor: CORES.azul, backgroundColor: CORES.azul, pointRadius: 0 },
      { label: "Perdidas", data: dec.perdidas, borderColor: CORES.laranja, backgroundColor: CORES.laranja, pointRadius: 0 },
    ] }, options: { scales: { y: eixoPct(1), x: { ticks: { maxTicksLimit: 8 } } }, plugins: { tooltip: tooltipPct, linhaRef: { valor: 0.8 } } } });

    const am = dados.aging_mediano_ano;
    grafico("chAgingAno", { type: "line", data: { labels: am.map((r) => r.ano), datasets: [
      { label: "Ganhas", data: am.map((r) => r.ganhas), borderColor: CORES.azul, backgroundColor: CORES.azul },
      { label: "Perdidas", data: am.map((r) => r.perdidas), borderColor: CORES.laranja, backgroundColor: CORES.laranja },
    ] }, options: { scales: { y: { beginAtZero: true, ticks: { callback: (v) => `${v} d` } } } } });
  }

  function graficosPipeline() {
    const prev = dados.abertas.previsao;
    const rot = [...prev.por_mes.map((r) => mesCurto(r.mes)), "Depois"];
    grafico("chPrevisao", { type: "bar", data: { labels: rot, datasets: [
      { label: "Vitórias esperadas", data: [...prev.por_mes.map((r) => r.valor), prev.depois_12_meses],
        backgroundColor: [...prev.por_mes.map(() => CORES.azul), CORES.cinza] },
    ] }, options: { scales: { y: { beginAtZero: true, grace: "12%", ticks: { callback: (v) => brlCurto(v) } } },
      plugins: { legend: semLegenda, tooltip: { callbacks: { label: (c) => brl(c.raw) } }, rotulos: { formato: (v) => brlCurto(v) } } } });

    const idade = dados.abertas_por_idade;
    grafico("chIdade", { type: "bar", data: { labels: idade.map((r) => r.faixa.replace(" dias", "")), datasets: [
      { label: "Abertas", data: idade.map((r) => r.qtd), backgroundColor: idade.map((r) => (r.faixa === "Acima de 365 dias" ? CORES.vermelho : CORES.verde)) },
    ] }, options: { scales: { y: { beginAtZero: true, grace: "10%" } }, plugins: { legend: semLegenda, rotulos: { formato: (v) => num(v) },
      tooltip: { callbacks: { label: (c) => `${num(c.raw)} abertas · ${brl(idade[c.dataIndex].valor)}` } } } } });

    const est = dados.abertas.por_estagio;
    grafico("chEstagioAberta", { type: "bar", data: { labels: est.map((r) => r.estagio), datasets: [
      { label: "Abertas", data: est.map((r) => r.qtd), backgroundColor: CORES.verde },
    ] }, options: { indexAxis: "y", scales: { x: { beginAtZero: true, grace: "15%" } }, plugins: { legend: semLegenda, rotulos: { formato: (v) => num(v) },
      tooltip: { callbacks: { label: (c) => `${num(c.raw)} abertas · ${brl(est[c.dataIndex].valor)} · idade mediana ${num(est[c.dataIndex].mediana_dias)} d` } } } } });
  }

  function graficosClientes() {
    const cl = dados.clientes;
    const ev = cl.por_ano;
    grafico("chNovoRecorrente", { type: "line", data: { labels: ev.map((r) => r.ano), datasets: [
      { label: "Win rate recorrente", data: ev.map((r) => r.win_rate_recorrente), borderColor: CORES.azul, backgroundColor: CORES.azul },
      { label: "Win rate novo", data: ev.map((r) => r.win_rate_novo), borderColor: CORES.amarelo, backgroundColor: CORES.amarelo },
      { label: "% das fechadas de clientes novos", data: ev.map((r) => r.pct_fechadas_novos), borderColor: CORES.cinza, backgroundColor: CORES.cinza, borderDash: [5, 4] },
    ] }, options: { scales: { y: eixoPct(1) }, plugins: { tooltip: tooltipPct } } });

    const top = cl.concentracao.top;
    grafico("chConcentracao", { type: "bar", data: { labels: top.map((r) => r.cliente), datasets: [
      { label: "Valor ganho", data: top.map((r) => r.valor_ganho), backgroundColor: CORES.azul },
    ] }, options: { indexAxis: "y", scales: { x: { beginAtZero: true, grace: "18%", ticks: { callback: (v) => brlCurto(v) } } },
      onClick: (_e, el) => el.length && filtrarPor("cliente", top[el[0].index].cliente),
      plugins: { legend: semLegenda, rotulos: { formato: (v, i) => `${brlCurto(v)} · ${pct(top[i].pct_acumulado, 0)} acum.` },
        tooltip: { callbacks: { label: (c) => { const r = top[c.dataIndex]; return [`Valor ganho ${brl(r.valor_ganho)}`, `% do total ${pct(r.pct)}`, `% acumulado ${pct(r.pct_acumulado)}`, `Win rate ${pct(r.win_rate)}`, `Ganhas / fechadas ${r.ganhas} / ${r.fechadas}`]; } } } } } });
  }

  function graficosOfertas() {
    const tipos = dados.ofertas.tabela.filter((r) => r.fechadas >= 20 && r.ciclo_mediano !== null);
    const maxValor = Math.max(1, ...tipos.map((r) => r.valor_ganho));
    grafico("chOfertasBolhas", { type: "bubble", data: { datasets: [{ label: "Tipos de oferta", data: tipos.map((r) => ({
      x: r.ciclo_mediano, y: r.win_rate, r: 5 + 22 * Math.sqrt(r.valor_ganho / maxValor), tipo: r.tipo })),
      backgroundColor: tipos.map((r) => (r.tipo.startsWith("Outros") ? "rgba(107,114,128,.55)" : "rgba(59,130,246,.55)")),
      borderColor: tipos.map((r) => (r.tipo.startsWith("Outros") ? CORES.cinza : CORES.azul)) }] },
      options: { scales: { y: eixoPct(1), x: { beginAtZero: true, title: { display: true, text: "ciclo mediano das ganhas (dias)" } } },
        onClick: (_e, el) => el.length && filtrarPor("tipo_oferta", tipos[el[0].index].tipo),
        plugins: { legend: semLegenda, linhaRef: { valor: [wrGeral(), 20], eixos: ["y", "x"] },
          rotulos: { formato: (_v, i) => tipos[i].tipo },
          tooltip: { callbacks: { label: (c) => { const r = tipos[c.dataIndex]; return [r.tipo, `Win rate ${pct(r.win_rate)} · ciclo ${num(r.ciclo_mediano)} d`, `Valor ganho ${brlCurto(r.valor_ganho)} · ${r.fechadas} fechadas`]; } } } } } });

    const mix = dados.ofertas.mix;
    grafico("chMix", { type: "bar", data: { labels: mix.map((r) => r.ano), datasets: dados.ofertas.tipos_mix.map((t, i) => ({
      label: t, data: mix.map((r) => r[t]), backgroundColor: t === "Demais tipos" ? CORES.cinza : PALETA[i % PALETA.length] })) },
      options: { scales: { x: { stacked: true }, y: Object.assign(eixoPct(1), { stacked: true }) }, plugins: { tooltip: tooltipPct } } });
  }

  function graficosTime() {
    const tm = dados.time;
    const b = tm.bolhas;
    const maxValor = Math.max(1, ...b.map((r) => r.valor_ganho));
    // nome so nas 12 bolhas de maior valor ganho (todas juntas ficariam ilegiveis)
    const destacados = new Set([...b].sort((x, y) => y.valor_ganho - x.valor_ganho).slice(0, 12).map((r) => r.responsavel));
    const nomeCurto = (n) => { const p = n.split(/\s+/); return p.length > 2 ? `${p[0]} ${p[p.length - 1]}` : n; };
    grafico("chTimeBolhas", { type: "bubble", data: { datasets: [{ label: "Responsáveis", data: b.map((r) => ({
      x: r.fechadas, y: r.win_rate, r: 4 + 20 * Math.sqrt(r.valor_ganho / maxValor) })),
      backgroundColor: "rgba(59,130,246,.55)", borderColor: CORES.azul }] },
      options: { scales: { y: eixoPct(1), x: { beginAtZero: true, title: { display: true, text: "oportunidades fechadas" } } },
        onClick: (_e, el) => el.length && filtrarPor("responsavel", b[el[0].index].responsavel),
        plugins: { legend: semLegenda, linhaRef: { valor: [tm.win_rate_geral, tm.mediana_volume], eixos: ["y", "x"] },
          rotulos: { formato: (_v, i) => (destacados.has(b[i].responsavel) ? nomeCurto(b[i].responsavel) : "") },
          tooltip: { callbacks: { label: (c) => { const r = b[c.dataIndex]; return [r.responsavel, `Win rate ${pct(r.win_rate)} · ${r.fechadas} fechadas`, `Valor ganho ${brlCurto(r.valor_ganho)}`]; } } } } } });

    const h = tm.higiene;
    const coresIdade = [CORES.azul, "#14B8A6", CORES.verde, CORES.vermelho];
    grafico("chHigiene", { type: "bar", data: { labels: h.map((r) => r.responsavel), datasets: tm.faixas_idade.map((f, i) => ({
      label: f, data: h.map((r) => r[f]), backgroundColor: coresIdade[i] })) },
      options: { indexAxis: "y", scales: { x: { stacked: true, beginAtZero: true }, y: { stacked: true } },
        onClick: (_e, el) => el.length && filtrarPor("responsavel", h[el[0].index].responsavel) } });
  }

  // ── Base de dados ─────────────────────────────────────────
  ["Negativo", "0-30 dias", "31-60 dias", "61-90 dias", "91-180 dias", "181-365 dias", "Acima de 365 dias"].forEach((f) => {
    const o = document.createElement("option"); o.value = f; o.textContent = f; $("fFaixa").appendChild(o);
  });
  ["fStatus", "fFaixa"].forEach((id) => $(id).addEventListener("change", () => { base.pagina = 1; carregarBase(); }));

  function paramsBase() {
    const p = lerFiltros();
    if ($("fStatus").value) p.set("status", $("fStatus").value);
    if ($("fFaixa").value) p.set("faixa", $("fFaixa").value);
    return p;
  }

  async function carregarBase() {
    const p = paramsBase();
    Object.entries(base).forEach(([k, v]) => p.set(k, v));
    try {
      const resp = await fetch("/api/aging/base?" + p.toString());
      const r = await resp.json();
      if (!resp.ok) throw new Error(r.detail || "Erro ao carregar a base");
      renderBase(r);
    } catch (e) { mostrarErro(e.message); }
  }

  function celula(col, v) {
    if (v === null || v === undefined) return "";
    if (col === "valor") return brl(v);
    if (col.startsWith("data_")) return dataBR(v);
    if (col === "usar_na_analise" || col === "cliente_recorrente") return v ? "Sim" : "Não";
    if (col === "status") return `<span class="status-chip ag-status-${esc(v)}">${esc(v)}</span>`;
    return esc(v);
  }

  function renderBase(r) {
    const paginas = Math.max(1, Math.ceil(r.total / r.tamanho));
    $("baseTotal").textContent = `Base completa de oportunidades — ${num(r.total)} linhas`;
    $("pagInfo").textContent = `Página ${r.pagina} de ${paginas}`;
    $("btnPagAnt").disabled = r.pagina <= 1;
    $("btnPagProx").disabled = r.pagina >= paginas;
    const head = "<thead><tr>" + r.colunas.map(([c, t]) =>
      `<th data-ordem="${c}" class="ag-ordenavel">${esc(t)}${base.ordem === c ? (base.desc ? " ▼" : " ▲") : ""}</th>`).join("") + "</tr></thead>";
    const body = r.linhas.length
      ? r.linhas.map((l) => "<tr>" + r.colunas.map(([c]) => `<td>${celula(c, l[c])}</td>`).join("") + "</tr>").join("")
      : `<tr><td colspan="${r.colunas.length}" class="empty-state">Nenhuma oportunidade com esses filtros.</td></tr>`;
    $("tbBaseDados").innerHTML = head + "<tbody>" + body + "</tbody>";
  }

  let qualidadeCarregada = false;
  async function carregarQualidade() {
    if (qualidadeCarregada) return;
    try {
      const q = await (await fetch("/api/aging/qualidade")).json();
      qualidadeCarregada = true;
      const itens = [
        `${num(q.fora_analise)} linhas estão fora da análise ("Usar na análise" = Não): ${num(q.fora_carga_inicial)} com data de criação da carga inicial do SharePoint (16/12/2019), ${num(q.fora_aging_negativo)} com aging negativo e ${num(q.fora_arquivadas)} arquivadas no Odoo sem ganho/perda. O filtro "Só linhas válidas" as exclui.`,
        `${num(q.sharepoint_sem_fechamento_real)} fechadas no SharePoint não têm FechamentoReal; nelas o aging usa a data da última modificação.`,
        `${num(q.sharepoint_andamento_odoo)} oportunidades estavam abertas no SharePoint e seguiram no Odoo: o estágio atual vem do Odoo, e ${num(q.sharepoint_fechada_odoo)} delas fecharam em 2026 (status, data de fechamento e motivo da perda do Odoo; criação, vertical e valor do SharePoint).`,
        `Vertical: ${num(q.odoo_vertical_da_equipe)} oportunidades do Odoo usam a equipe de vendas; ${num(q.odoo_vertical_inferida)} estão na equipe genérica "Sales" e tiveram a vertical inferida pelo vendedor.`,
        `${num(q.grafias_responsavel)} grafias de responsável foram unificadas em ${num(q.pessoas)} pessoas (${num(q.linhas_responsavel_ajustado)} linhas ajustadas, ex.: "Marcos Liebl" → "Marcos Vinicius Henrique Liebl").`,
        `Tipo de oferta: classificado por palavras-chave no título; ${num(q.tipo_outros)} oportunidades (${pct(q.tipo_outros / q.total)}) não tinham pista no título e ficaram em "Outros".`,
        `Cliente recorrente: ${num(q.cliente_recorrente)} oportunidades foram criadas quando o cliente já tinha uma vitória registrada. Como a base começa em 2019, clientes antigos aparecem como "novos" nas primeiras oportunidades.`,
        `${num(q.responsavel_administrator)} oportunidades estão com o responsável "Administrator" (usuário do sistema).`,
        `${num(q.sem_codigo)} linhas sem código e ${num(q.codigo_repetido)} com código repetido.`,
        `${num(q.valor_zerado)} oportunidades com valor zerado ou vazio, e ${num(q.valor_acima_50mi)} acima de R$ 50 mi que somam ${pct(q.soma_total ? q.soma_acima_50mi / q.soma_total : null)} de todo o valor da base — elas distorcem os indicadores em R$.`,
      ];
      $("qualidade").innerHTML = itens.map((t) => `<li>${esc(t)}</li>`).join("");
    } catch (e) { $("qualidade").innerHTML = "<li>Não foi possível carregar.</li>"; }
  }

  $("tbBaseDados").addEventListener("click", (ev) => {
    const th = ev.target.closest("th[data-ordem]");
    if (!th) return;
    base.desc = base.ordem === th.dataset.ordem ? !base.desc : true;
    base.ordem = th.dataset.ordem;
    base.pagina = 1;
    carregarBase();
  });
  $("btnPagAnt").addEventListener("click", () => { base.pagina -= 1; carregarBase(); });
  $("btnPagProx").addEventListener("click", () => { base.pagina += 1; carregarBase(); });
  $("btnCsv").addEventListener("click", () => window.open("/api/aging/base.csv?" + paramsBase().toString(), "_blank"));

  // ── Bootstrap ─────────────────────────────────────────────
  // filtros da URL so depois das opcoes carregadas - senao o select nao tem a opcao ainda
  carregarStatus();
  carregarOpcoes().catch(() => { /* filtros ficam so com "Todos" */ })
    .finally(() => {
      aplicarFiltrosDaUrl();
      // ?aba=pipeline abre direto numa aba (link vindo de outra aplicacao)
      const aba = new URLSearchParams(location.search).get("aba");
      const botao = aba && document.querySelector(`#abas button[data-aba="${CSS.escape(aba)}"]`);
      if (botao) botao.click();
      atualizar();
    });
})();
