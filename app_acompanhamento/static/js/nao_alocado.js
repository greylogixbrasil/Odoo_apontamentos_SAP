(function () {
  "use strict";

  let pollTimer = null;
  let linhasAtuais = [];

  const $ = (id) => document.getElementById(id);

  // ── Tema / logout (mesma lógica da tela de envio diário) ───
  function aplicarBotaoTema() {
    const isDark = document.documentElement.getAttribute("data-theme") === "dark";
    $("btnThemeToggle").textContent = isDark ? "🌙" : "🌕";
  }
  $("btnThemeToggle").addEventListener("click", () => {
    const novo = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", novo);
    localStorage.setItem("app_theme_mode", novo);
    aplicarBotaoTema();
  });
  aplicarBotaoTema();

  $("btnLogout").addEventListener("click", () => {
    const xhr = new XMLHttpRequest();
    xhr.open("GET", "/", true, "logout", "logout");
    xhr.onloadend = () => { window.location.reload(); };
    xhr.send();
  });

  // ── Helpers ───────────────────────────────────────────────
  function escapeHtml(str) {
    return String(str ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  const fmtHoras = (v) => (v ?? 0).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function mostrarErro(msg) {
    $("avisoErro").textContent = msg;
    $("avisoErro").classList.remove("hidden");
  }

  // ── Período padrão: 1º dia do mês até ontem ────────────────
  (function preencherPeriodo() {
    const ontem = new Date();
    ontem.setDate(ontem.getDate() - 1);
    const inicio = new Date(ontem.getFullYear(), ontem.getMonth(), 1);
    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    $("dataInicio").value = iso(inicio);
    $("dataFim").value = iso(ontem);
    $("dataFim").max = iso(ontem);
  })();

  // ── Config (IdSAP / envio liberado) ────────────────────────
  async function carregarConfig() {
    try {
      const resp = await fetch("/api/nao-alocado/config");
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Erro ao carregar configuração");
      $("idsapLabel").textContent = data.idsap ? `IdSAP: ${data.idsap}` : "IdSAP: não definido";
      $("btnEnviar").disabled = !data.envio_habilitado;
      $("btnEnviar").title = data.envio_habilitado ? "" : "Envio bloqueado até definir o IdSAP e liberar no .env";
      $("avisoBloqueado").classList.toggle("hidden", !!data.envio_habilitado);
    } catch (e) {
      mostrarErro(e.message);
    }
  }

  // ── Preview ───────────────────────────────────────────────
  function kpiClasse(status) {
    if (status === "pronto_para_envio") return "ok";
    if (status.startsWith("erro_")) return "crit";
    if (status === "ja_existe_sap") return "";
    return "amber";
  }

  function renderKpis(contagem, horas) {
    const row = $("kpiRow");
    row.innerHTML = "";
    Object.entries(contagem).forEach(([status, qtd]) => {
      const div = document.createElement("div");
      div.className = "kpi " + kpiClasse(status);
      div.innerHTML = `
        <div class="rail"></div>
        <div class="lab">${escapeHtml(status)}</div>
        <div class="val">${fmtHoras(horas[status])} h</div>
        <div class="sub">${qtd} dia(s)</div>
      `;
      row.appendChild(div);
    });
  }

  function renderTabela() {
    const filtro = $("filtroTexto").value.trim().toLowerCase();
    const linhas = linhasAtuais.filter((r) => !filtro ||
      [r.colaborador, r.projeto, r.status, r.motivo, r.data].some((v) => String(v ?? "").toLowerCase().includes(filtro)));

    const body = $("gridBody");
    if (!linhas.length) {
      body.innerHTML = `<tr><td colspan="11" class="empty-state">${linhasAtuais.length ? "Nenhuma linha para esse filtro." : "Nenhum tempo não alocado no período."}</td></tr>`;
      return;
    }
    body.innerHTML = linhas.map((r) => `
      <tr>
        <td>${escapeHtml(r.colaborador)}</td>
        <td>${escapeHtml(r.data)}</td>
        <td>${escapeHtml(r.projeto_sap ?? r.projeto)}</td>
        <td class="col-valor">${fmtHoras(r.previsto)}</td>
        <td class="col-valor">${fmtHoras(r.apontado)}</td>
        <td class="col-valor">${fmtHoras(r.ja_lancado_sap)}</td>
        <td class="col-valor"><strong>${fmtHoras(r.horas)}</strong></td>
        <td>${r.start_time ? `${escapeHtml(r.start_time)}–${escapeHtml(r.end_time)}` : ""}</td>
        <td>${escapeHtml(r.employee_id_sap)}</td>
        <td><span class="status-chip status-${escapeHtml(r.status)}">${escapeHtml(r.status)}</span></td>
        <td>${escapeHtml(r.motivo)}</td>
      </tr>
    `).join("");
  }

  $("filtroTexto").addEventListener("input", renderTabela);

  $("btnPreview").addEventListener("click", async () => {
    $("avisoErro").classList.add("hidden");
    $("avisoCarregando").classList.remove("hidden");
    $("btnPreview").disabled = true;
    try {
      const resp = await fetch("/api/nao-alocado/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data_inicio: $("dataInicio").value || null, data_fim: $("dataFim").value || null }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Erro ao calcular o tempo não alocado");

      linhasAtuais = data.linhas;
      renderKpis(data.contagem, data.horas_por_status);
      renderTabela();
      $("kpiRow").classList.remove("hidden");
      $("previewSection").classList.remove("hidden");
    } catch (e) {
      mostrarErro(e.message);
    } finally {
      $("avisoCarregando").classList.add("hidden");
      $("btnPreview").disabled = false;
    }
  });

  // ── Envio (bloqueado no servidor enquanto o IdSAP não for definido) ──
  $("btnEnviar").addEventListener("click", async () => {
    const periodo = `${$("dataInicio").value} a ${$("dataFim").value}`;
    if (!confirm(`Enviar o tempo não alocado de ${periodo} para o SAP de PRODUÇÃO?`)) return;

    $("avisoErro").classList.add("hidden");
    $("btnEnviar").disabled = true;
    try {
      const resp = await fetch("/api/nao-alocado/enviar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data_inicio: $("dataInicio").value || null, data_fim: $("dataFim").value || null }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Erro ao iniciar o envio");

      $("progressoWrap").classList.remove("hidden");
      $("resumoEnvio").classList.add("hidden");
      $("logBox").textContent = "";
      $("progressBar").style.width = "0%";
      iniciarPolling();
    } catch (e) {
      mostrarErro(e.message);
      $("btnEnviar").disabled = false;
    }
  });

  function iniciarPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      const p = await (await fetch("/api/nao-alocado/progresso")).json();
      $("progressBar").style.width = (p.total > 0 ? (p.pos / p.total) * 100 : 0) + "%";
      $("logBox").textContent = p.logs.join("\n");
      $("logBox").scrollTop = $("logBox").scrollHeight;

      if (!p.em_andamento) {
        clearInterval(pollTimer);
        pollTimer = null;
        $("btnEnviar").disabled = false;
        if (p.erro) {
          mostrarErro(p.erro);
        } else if (p.resumo) {
          $("resumoEnvio").textContent = `Execução ${p.execucao_id} concluída: ${p.resumo.total_enviadas} enviadas, ` +
            `${p.resumo.total_ja_existentes} já existentes, ${p.resumo.total_erros} erros.`;
          $("resumoEnvio").classList.remove("hidden");
        }
      }
    }, 1200);
  }

  carregarConfig();
})();
