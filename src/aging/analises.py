"""Analises avancadas do dashboard de aging (abas Win rate, Aging, Pipeline, Clientes, Ofertas, Time e
os Destaques). Tudo puro: recebe as linhas ja filtradas da view aging_base e devolve dicts prontos.

Conceitos usados em mais de um lugar:
 * fechadas = ganhas + perdidas; win rate = ganhas / fechadas.
 * "chance de quem ainda esta aberta ha X dias" = entre as fechadas que passaram de X dias em aberto
   (aging > X), a fracao que foi ganha. E a base da previsao do pipeline."""
import bisect
import datetime
import statistics
from collections import Counter, defaultdict

FAIXAS_TICKET = [(10_000, "< 10k"), (50_000, "10-50k"), (100_000, "50-100k"), (250_000, "100-250k"),
                 (500_000, "250-500k"), (1_000_000, "500k-1M"), (None, "> 1M")]
GRUPOS_MOTIVO = ["Cancelado ou suspenso", "Concorrência", "Outros", "Não informado"]
FAIXAS_IDADE_ABERTA = [(90, "Até 90 dias"), (180, "91-180 dias"), (365, "181-365 dias"), (None, "Mais de 1 ano")]
MINIMO_CURVA = 20  # fechadas minimas pra mostrar um ponto de curva


# ── helpers ───────────────────────────────────────────────────
def _v(x) -> float:
    return float(x) if x is not None else 0.0


def _razao(a, b):
    return round(a / b, 4) if b else None


def _mediana(valores):
    valores = [v for v in valores if v is not None]
    return round(float(statistics.median(valores)), 1) if valores else None


def _fechadas(linhas):
    return [r for r in linhas if r["status"] in ("ganha", "perdida")]


def _wr(linhas):
    f = _fechadas(linhas)
    return _razao(sum(1 for r in f if r["status"] == "ganha"), len(f))


def faixa_ticket(valor) -> str:
    v = _v(valor)
    for limite, nome in FAIXAS_TICKET:
        if limite is None or v < limite:
            return nome
    return FAIXAS_TICKET[-1][1]


def grupo_motivo(motivo: str | None) -> str:
    texto = (motivo or "").strip().lower()
    if not texto:
        return "Não informado"
    if "cancel" in texto or "suspens" in texto:
        return "Cancelado ou suspenso"
    if "concorr" in texto or "competi" in texto:
        return "Concorrência"
    return "Outros"


def _idade_aberta(dias) -> str:
    for limite, nome in FAIXAS_IDADE_ABERTA:
        if limite is None or (dias or 0) <= limite:
            return nome
    return FAIXAS_IDADE_ABERTA[-1][1]


def _resumo(linhas) -> dict:
    f = _fechadas(linhas)
    ganhas = [r for r in f if r["status"] == "ganha"]
    valor_ganho = sum(_v(r["valor"]) for r in ganhas)
    valor_fechado = sum(_v(r["valor"]) for r in f)
    abertas = [r for r in linhas if r["status"] == "aberta"]
    return {
        "oportunidades": len(linhas), "fechadas": len(f), "ganhas": len(ganhas),
        "win_rate": _razao(len(ganhas), len(f)), "win_rate_valor": _razao(valor_ganho, valor_fechado),
        "ciclo_mediano": _mediana([r["aging_dias"] for r in ganhas]),
        "ticket_mediano": _mediana([_v(r["valor"]) for r in ganhas]),
        "valor_ganho": round(valor_ganho, 2),
        "abertas": len(abertas), "valor_aberto": round(sum(_v(r["valor"]) for r in abertas), 2),
    }


# ── Win rate ──────────────────────────────────────────────────
def win_rate_por_ticket(linhas) -> list[dict]:
    grupos = defaultdict(list)
    for r in _fechadas(linhas):
        grupos[faixa_ticket(r["valor"])].append(r)
    return [{"faixa": nome, "fechadas": len(grupos[nome]), "win_rate": _wr(grupos[nome])} for _, nome in FAIXAS_TICKET]


def _motivos_por(linhas, chave, minimo: int) -> list[dict]:
    grupos = defaultdict(list)
    for r in linhas:
        if r["status"] == "perdida":
            grupos[chave(r)].append(grupo_motivo(r.get("motivo_perda")))
    resultado = []
    for nome, motivos in grupos.items():
        if nome is None or len(motivos) < minimo:
            continue
        c = Counter(motivos)
        resultado.append({"grupo": nome, "perdidas": len(motivos),
                          **{m: _razao(c[m], len(motivos)) for m in GRUPOS_MOTIVO}})
    return resultado


def perdas(linhas) -> dict:
    perdidas = [r for r in linhas if r["status"] == "perdida"]
    c = Counter(grupo_motivo(r.get("motivo_perda")) for r in perdidas)
    # estagio em que estava ao ser perdida: so quem fechou no Odoo (o SharePoint so registra "Perdida")
    por_estagio = Counter(r.get("estagio") or "(sem estágio)" for r in perdidas
                          if r.get("base_origem") == "Odoo" or r.get("fechada_no_odoo"))
    total_odoo = sum(por_estagio.values())
    return {
        "total": len(perdidas),
        "motivos": {m: _razao(c[m], len(perdidas)) for m in GRUPOS_MOTIVO},
        "por_vertical": sorted(_motivos_por(linhas, lambda r: r["vertical_final"], 10), key=lambda x: -x["perdidas"]),
        "por_ano": sorted(_motivos_por(linhas, lambda r: r["data_criacao"].year if r["data_criacao"] else None, 1),
                          key=lambda x: x["grupo"]),
        # so o Odoo registra o estagio em que estava ao ser perdida
        "por_estagio": [{"estagio": e, "qtd": n, "pct": _razao(n, total_odoo)} for e, n in por_estagio.most_common()],
    }


def sazonalidade(linhas) -> dict:
    ganhas = [r for r in linhas if r["status"] == "ganha" and r.get("data_fechamento")]
    por_mes = Counter(r["data_fechamento"].month for r in ganhas)
    ultima_semana = sum(1 for r in ganhas if _ultimos_7_dias_do_mes(r["data_fechamento"]))
    return {"por_mes": [{"mes": m, "ganhas": por_mes.get(m, 0)} for m in range(1, 13)],
            "pct_ultima_semana": _razao(ultima_semana, len(ganhas)), "pct_esperado": round(7 / 30.44, 4)}


def _ultimos_7_dias_do_mes(d: datetime.date) -> bool:
    proximo = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return (proximo - d).days <= 7


# ── Aging ─────────────────────────────────────────────────────
def curva_chance(linhas, passo: int = 5, max_dias: int = 900) -> list[dict]:
    """Pra cada dia X: entre as fechadas que ficaram abertas mais de X dias, quantas foram ganhas."""
    f = _fechadas(linhas)
    todas = sorted(r["aging_dias"] for r in f if r["aging_dias"] is not None)
    ganhas = sorted(r["aging_dias"] for r in f if r["status"] == "ganha" and r["aging_dias"] is not None)
    curva = []
    for x in range(0, max_dias + 1, passo):
        n = len(todas) - bisect.bisect_left(todas, x)
        if n < MINIMO_CURVA:
            break
        g = len(ganhas) - bisect.bisect_left(ganhas, x)
        curva.append({"dia": x, "win_rate": _razao(g, n), "fechadas": n})
    return curva


def ponto_de_corte(curva: list[dict], limite: float = 0.2) -> int | None:
    return next((p["dia"] for p in curva if p["win_rate"] is not None and p["win_rate"] < limite), None)


def decisoes_acumuladas(linhas, passo: int = 5, max_dias: int = 720) -> dict:
    """% acumulado das ganhas e das perdidas ja encerradas ate cada dia."""
    def acumulado(status):
        dias = sorted(r["aging_dias"] for r in linhas if r["status"] == status and r["aging_dias"] is not None)
        return dias, [_razao(bisect.bisect_right(dias, x), len(dias)) for x in range(0, max_dias + 1, passo)]
    dias_ganhas, ganhas = acumulado("ganha")
    _, perdidas = acumulado("perdida")
    dia_80 = dias_ganhas[int(0.8 * (len(dias_ganhas) - 1))] if dias_ganhas else None
    return {"dias": list(range(0, max_dias + 1, passo)), "ganhas": ganhas, "perdidas": perdidas,
            "dia_80_ganhas": dia_80}


def conversao_por_safra(linhas, hoje: datetime.date, passo: int = 5, max_dias: int = 360, minimo: int = 30) -> list[dict]:
    """% das oportunidades criadas em cada ano que ja estavam ganhas X dias depois da criacao. So conta
    quem ja tem X dias de vida - safras recentes param onde ainda nao amadureceram."""
    por_ano = defaultdict(list)
    for r in linhas:
        if r["data_criacao"]:
            por_ano[r["data_criacao"].year].append(r)
    safras = []
    for ano, grupo in sorted(por_ano.items()):
        pontos = []
        for x in range(0, max_dias + 1, passo):
            elegiveis = [r for r in grupo if (hoje - r["data_criacao"]).days >= x]
            if len(elegiveis) < minimo:
                break
            ganhas = sum(1 for r in elegiveis if r["status"] == "ganha" and r["aging_dias"] is not None
                         and r["aging_dias"] <= x)
            pontos.append({"dia": x, "pct": _razao(ganhas, len(elegiveis))})
        if pontos:
            safras.append({"ano": ano, "pontos": pontos})
    return safras


def aging_mediano_por_ano(linhas) -> list[dict]:
    por_ano = defaultdict(list)
    for r in linhas:
        if r["data_criacao"]:
            por_ano[r["data_criacao"].year].append(r)
    return [{"ano": ano,
             "ganhas": _mediana([r["aging_dias"] for r in g if r["status"] == "ganha"]),
             "perdidas": _mediana([r["aging_dias"] for r in g if r["status"] == "perdida"])}
            for ano, g in sorted(por_ano.items())]


def por_base(linhas) -> list[dict]:
    grupos = defaultdict(list)
    for r in linhas:
        grupos[r["base_origem"]].append(r)
    return [{"base": b, "fechadas": len(_fechadas(g)), "win_rate": _wr(g),
             "mediana_ganhas": _mediana([r["aging_dias"] for r in g if r["status"] == "ganha"]),
             "mediana_perdidas": _mediana([r["aging_dias"] for r in g if r["status"] == "perdida"])}
            for b, g in sorted(grupos.items())]


# ── Pipeline e previsao (pelo historico) ──────────────────────
def _inicio_meses(hoje: datetime.date, n: int) -> list[datetime.date]:
    meses, ano, mes = [], hoje.year, hoje.month
    for _ in range(n + 1):
        meses.append(datetime.date(ano, mes, 1))
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return meses


def previsao_pelo_historico(linhas, hoje: datetime.date, meses: int = 12) -> dict:
    """Pra cada aberta ha A dias: olha as fechadas que passaram de A dias. A fracao ganha e a chance; quando
    elas foram ganhas (aging - A dias depois) da o mes provavel. Valor esperado = valor x chance."""
    f = _fechadas(linhas)
    todas = sorted(r["aging_dias"] for r in f if r["aging_dias"] is not None)
    ganhas = sorted(r["aging_dias"] for r in f if r["status"] == "ganha" and r["aging_dias"] is not None)
    inicios = _inicio_meses(hoje, meses)
    # fim de cada mes em "dias a partir de hoje"
    limites = [(inicios[k + 1] - hoje).days - 1 for k in range(meses)]

    por_mes = [0.0] * meses
    qtd_12m = valor_12m = depois = ponderado = 0.0
    for r in linhas:
        if r["status"] != "aberta" or r["aging_dias"] is None:
            continue
        a, valor = r["aging_dias"], _v(r["valor"])
        n = len(todas) - bisect.bisect_right(todas, a)
        if n == 0:
            continue
        base = bisect.bisect_right(ganhas, a)
        total_ganhas = len(ganhas) - base
        ponderado += valor * total_ganhas / n
        anterior = base
        for k, limite in enumerate(limites):
            ate = bisect.bisect_right(ganhas, a + limite)
            chance = (ate - anterior) / n
            por_mes[k] += valor * chance
            qtd_12m += chance
            valor_12m += valor * chance
            anterior = ate
        depois += valor * (len(ganhas) - anterior) / n

    return {
        "ponderado": round(ponderado, 2),
        "valor_12_meses": round(valor_12m, 2),
        "qtd_12_meses": round(qtd_12m, 1),
        "depois_12_meses": round(depois, 2),
        "por_mes": [{"mes": inicios[k].strftime("%Y-%m"), "valor": round(v, 2)} for k, v in enumerate(por_mes)],
    }


def abertas(linhas, hoje: datetime.date) -> dict:
    ab = [r for r in linhas if r["status"] == "aberta"]
    valor = sum(_v(r["valor"]) for r in ab)
    velhas = [r for r in ab if (r["aging_dias"] or 0) > 365]
    estagios = defaultdict(list)
    for r in ab:
        rotulo = r.get("estagio") or "(sem estágio)"
        if r.get("base_origem") == "SharePoint" and not r.get("andamento_do_odoo"):
            rotulo += " (SharePoint)"
        estagios[rotulo].append(r)
    return {
        "qtd": len(ab), "valor": round(valor, 2),
        "mediana_dias": _mediana([r["aging_dias"] for r in ab]),
        "valor_mediano": _mediana([_v(r["valor"]) for r in ab]),
        "mais_de_1_ano": len(velhas), "pct_mais_de_1_ano": _razao(len(velhas), len(ab)),
        "valor_mais_de_1_ano": round(sum(_v(r["valor"]) for r in velhas), 2),
        "pct_valor_mais_de_1_ano": _razao(sum(_v(r["valor"]) for r in velhas), valor),
        "por_estagio": sorted(({"estagio": e, "qtd": len(g), "valor": round(sum(_v(r["valor"]) for r in g), 2),
                                "mediana_dias": _mediana([r["aging_dias"] for r in g])} for e, g in estagios.items()),
                              key=lambda x: -x["qtd"]),
        "previsao": previsao_pelo_historico(linhas, hoje),
    }


# ── Clientes ──────────────────────────────────────────────────
def clientes(linhas) -> dict:
    novos = [r for r in linhas if not r.get("cliente_recorrente")]
    recorrentes = [r for r in linhas if r.get("cliente_recorrente")]

    por_ano = defaultdict(lambda: {"novo": [], "recorrente": []})
    for r in linhas:
        if r["data_criacao"]:
            por_ano[r["data_criacao"].year]["recorrente" if r.get("cliente_recorrente") else "novo"].append(r)
    evolucao = []
    for ano, g in sorted(por_ano.items()):
        fn, fr = len(_fechadas(g["novo"])), len(_fechadas(g["recorrente"]))
        evolucao.append({"ano": ano, "win_rate_novo": _wr(g["novo"]) if fn >= 10 else None,
                         "win_rate_recorrente": _wr(g["recorrente"]) if fr >= 10 else None,
                         "pct_fechadas_novos": _razao(fn, fn + fr)})

    por_cliente = defaultdict(list)
    for r in linhas:
        if r.get("cliente"):
            por_cliente[r["cliente"]].append(r)
    ganho = {c: sum(_v(r["valor"]) for r in g if r["status"] == "ganha") for c, g in por_cliente.items()}
    com_vitoria = sorted(((c, v) for c, v in ganho.items() if v > 0), key=lambda kv: -kv[1])
    total = sum(v for _, v in com_vitoria)
    acumulado, top, ate_80 = 0.0, [], None
    for i, (c, v) in enumerate(com_vitoria, 1):
        acumulado += v
        if i <= 15:
            g = por_cliente[c]
            top.append({"cliente": c, "valor_ganho": round(v, 2), "pct": _razao(v, total),
                        "pct_acumulado": _razao(acumulado, total), "win_rate": _wr(g),
                        "ganhas": sum(1 for r in g if r["status"] == "ganha"), "fechadas": len(_fechadas(g))})
        if ate_80 is None and total and acumulado / total >= 0.8:
            ate_80 = i
    sem_vitoria = [(c, len(_fechadas(g))) for c, g in por_cliente.items()
                   if len(_fechadas(g)) >= 5 and not any(r["status"] == "ganha" for r in g)]
    return {
        "novo": _resumo(novos), "recorrente": _resumo(recorrentes), "por_ano": evolucao,
        "concentracao": {"top": top, "clientes_com_vitoria": len(com_vitoria), "clientes_para_80": ate_80,
                         "pct_top_10": _razao(sum(v for _, v in com_vitoria[:10]), total)},
        "sem_vitoria": {"clientes": len(sem_vitoria), "fechadas": sum(n for _, n in sem_vitoria)},
    }


# ── Ofertas ───────────────────────────────────────────────────
def ofertas(linhas) -> dict:
    grupos = defaultdict(list)
    for r in linhas:
        grupos[r.get("tipo_oferta") or "Outros / não identificado"].append(r)
    tabela = sorted(({"tipo": t, **_resumo(g)} for t, g in grupos.items()),
                    key=lambda x: (x["win_rate"] is None, -(x["win_rate"] or 0)))

    principais = [t for t, _ in Counter(r.get("tipo_oferta") for r in linhas).most_common(7)]
    por_ano = defaultdict(Counter)
    for r in linhas:
        if r["data_criacao"]:
            tipo = r.get("tipo_oferta") if r.get("tipo_oferta") in principais else "Demais tipos"
            por_ano[r["data_criacao"].year][tipo] += 1
    mix = [{"ano": ano, **{t: _razao(c[t], sum(c.values())) for t in principais + ["Demais tipos"]}}
           for ano, c in sorted(por_ano.items())]
    return {"tabela": tabela, "mix": mix, "tipos_mix": principais + ["Demais tipos"]}


# ── Time ──────────────────────────────────────────────────────
def _trimestre(d: datetime.date) -> str:
    return f"{d.year}-T{(d.month - 1) // 3 + 1}"


def time(linhas, minimo_fechadas: int = 20) -> dict:
    por_pessoa = defaultdict(list)
    for r in linhas:
        por_pessoa[r.get("responsavel_chave")].append(r)
    nome = {k: max((r.get("responsavel_unificado") or r.get("responsavel") or "" for r in g), key=len) or "(sem responsável)"
            for k, g in por_pessoa.items()}

    relevantes = {k: g for k, g in por_pessoa.items() if k and len(_fechadas(g)) >= minimo_fechadas}
    bolhas = sorted(({"responsavel": nome[k], **_resumo(g)} for k, g in relevantes.items()),
                    key=lambda x: -x["fechadas"])
    volumes = [b["fechadas"] for b in bolhas]

    trimestres = sorted({_trimestre(r["data_fechamento"]) for g in relevantes.values() for r in _fechadas(g)
                         if r.get("data_fechamento")})
    mapa = []
    for k, g in sorted(relevantes.items(), key=lambda kv: -len(_fechadas(kv[1])))[:25]:
        celulas = defaultdict(lambda: [0, 0])
        for r in _fechadas(g):
            if r.get("data_fechamento"):
                c = celulas[_trimestre(r["data_fechamento"])]
                c[1] += 1
                c[0] += r["status"] == "ganha"
        mapa.append({"responsavel": nome[k], "geral": _wr(g),
                     "celulas": {t: {"ganhas": c[0], "fechadas": c[1], "win_rate": _razao(c[0], c[1])}
                                 for t, c in celulas.items()}})

    higiene = []
    for k, g in por_pessoa.items():
        ab = [r for r in g if r["status"] == "aberta"]
        if ab:
            c = Counter(_idade_aberta(r["aging_dias"]) for r in ab)
            higiene.append({"responsavel": nome[k], "abertas": len(ab),
                            **{f: c.get(f, 0) for _, f in FAIXAS_IDADE_ABERTA}})
    return {
        "bolhas": bolhas, "mediana_volume": _mediana(volumes), "win_rate_geral": _wr(linhas),
        "trimestres": trimestres, "mapa": mapa, "faixas_idade": [f for _, f in FAIXAS_IDADE_ABERTA],
        "higiene": sorted(higiene, key=lambda x: -x["abertas"])[:15],
    }


# ── Destaques ─────────────────────────────────────────────────
def _p(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%".replace(".", ",")


def _rs(v: float) -> str:
    if abs(v) >= 1e9:
        return f"R$ {v / 1e9:.1f} bi".replace(".", ",")
    if abs(v) >= 1e6:
        return f"R$ {v / 1e6:.1f} mi".replace(".", ",")
    if abs(v) >= 1e3:
        return f"R$ {v / 1e3:.0f} mil"
    return f"R$ {v:.0f}"


def destaques(linhas, a: dict) -> list[str]:
    """Frases geradas a partir dos filtros. 'a' = resultado das outras analises (evita recalcular)."""
    frases = []
    faixas = {f["faixa"]: f["win_rate"] for f in a["por_faixa"]}
    if faixas.get("0-30 dias") is not None and faixas.get("Acima de 365 dias") is not None:
        frases.append(f"Velocidade decide: fechadas em até 30 dias têm win rate de {_p(faixas['0-30 dias'])}; "
                      f"acima de 1 ano, só {_p(faixas['Acima de 365 dias'])}.")
    corte = ponto_de_corte(a["curva_chance"])
    if corte is not None:
        frases.append(f"Ponto de corte: depois de {corte} dias em aberto, a chance de ganho fica abaixo de 20%.")
    mg, mp = a["cards"]["ciclo_mediano_ganhas"], a["cards"]["ciclo_mediano_perdidas"]
    if mg and mp:
        vezes = f"{mp / mg:.1f}".replace(".", ",")
        frases.append(f"Perdemos devagar: perdidas levam {mp:.0f} dias (mediana) para serem encerradas, "
                      f"{vezes}× o tempo das ganhas ({mg:.0f} dias).")
    cl = a["clientes"]
    if cl["recorrente"]["win_rate"] is not None and cl["novo"]["win_rate"] is not None:
        frases.append(f"Relacionamento pesa: clientes que já compraram convertem {_p(cl['recorrente']['win_rate'])}; "
                      f"clientes novos, {_p(cl['novo']['win_rate'])}.")
    tipos = [t for t in a["ofertas"]["tabela"] if t["fechadas"] >= 20 and t["win_rate"] is not None
             and not t["tipo"].startswith("Outros")]
    if len(tipos) >= 2:
        melhor, pior = tipos[0], tipos[-1]
        frases.append(f"Por tipo de oferta: {melhor['tipo'].lower()} ganha {_p(melhor['win_rate'])} em "
                      f"{melhor['ciclo_mediano'] or 0:.0f} dias (mediana); {pior['tipo'].lower()} ganha "
                      f"{_p(pior['win_rate'])} e leva {pior['ciclo_mediano'] or 0:.0f} dias.")
    ticket = {t["faixa"]: t["win_rate"] for t in a["win_rate_ticket"]}
    c = a["cards"]
    if ticket.get("< 10k") is not None and ticket.get("> 1M") is not None:
        frase = (f"Ticket pesa: até R$ 10 mil ganhamos {_p(ticket['< 10k'])}; acima de R$ 1 mi, {_p(ticket['> 1M'])}.")
        if c["win_rate_valor"] is not None and c["win_rate_qtd"] is not None and c["win_rate_valor"] < c["win_rate_qtd"]:
            frase += (f" Por isso o win rate em valor ({_p(c['win_rate_valor'])}) fica abaixo do em quantidade "
                      f"({_p(c['win_rate_qtd'])}).")
        frases.append(frase)
    m = a["perdas"]["motivos"]
    if a["perdas"]["total"]:
        frases.append(f"{_p(m['Cancelado ou suspenso'])} das perdas são projetos cancelados ou suspensos e "
                      f"{_p(m['Concorrência'])} são para a concorrência; {_p(m['Não informado'])} não têm motivo informado.")
    verticais = [v for v in a["por_vertical"] if v["ganhas"] + v["perdidas"] >= 30 and v["win_rate_qtd"] is not None]
    if len(verticais) >= 2:
        verticais.sort(key=lambda v: -v["win_rate_qtd"])
        frases.append(f"Entre as verticais com 30+ fechadas, {verticais[0]['vertical']} lidera com "
                      f"{_p(verticais[0]['win_rate_qtd'])} e {verticais[-1]['vertical']} fica com {_p(verticais[-1]['win_rate_qtd'])}.")
    conc = cl["concentracao"]
    if conc["clientes_com_vitoria"] >= 10:
        frases.append(f"Os 10 maiores clientes concentram {_p(conc['pct_top_10'])} do valor ganho "
                      f"(de {conc['clientes_com_vitoria']} clientes com vitórias).")
    sv = cl["sem_vitoria"]
    if sv["clientes"]:
        frases.append(f"{sv['clientes']} clientes somam {sv['fechadas']} propostas fechadas sem nenhuma vitória "
                      f"(5+ cada) — candidatos a requalificação.")
    ab = a["abertas"]
    if ab["qtd"]:
        frases.append(f"{ab['mais_de_1_ano']} das {ab['qtd']} abertas ({_p(ab['pct_mais_de_1_ano'])}) estão há mais de "
                      f"1 ano no pipeline, somando {_rs(ab['valor_mais_de_1_ano'])} ({_p(ab['pct_valor_mais_de_1_ano'])} do valor aberto).")
        prev = ab["previsao"]
        if prev["valor_12_meses"]:
            frases.append(f"Pelo histórico, o pipeline aberto deve gerar cerca de {_rs(prev['valor_12_meses'])} em vitórias "
                          f"nos próximos 12 meses ({prev['qtd_12_meses']:.0f} oportunidades), "
                          f"{_p(_razao(prev['valor_12_meses'], ab['valor']))} do valor nominal.")
    return frases
