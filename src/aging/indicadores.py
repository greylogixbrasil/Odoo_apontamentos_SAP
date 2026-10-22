"""Indicadores do dashboard de aging. Os filtros viram SQL (montar_where) sobre a view aging_base;
as agregacoes sao feitas aqui no backend (calcular_indicadores) - o navegador so recebe os
numeros prontos. calcular_indicadores e puro (lista de linhas -> dict), testavel sem banco."""
import datetime
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from src.aging import analises

FAIXAS = ["Negativo", "0-30 dias", "31-60 dias", "61-90 dias", "91-180 dias", "181-365 dias", "Acima de 365 dias"]

# Colunas que a agregacao precisa (o resto da view so vai pra aba Base de dados)
COLUNAS_INDICADORES = ("status, valor, probabilidade, aging_dias, faixa_aging, data_criacao, data_fechamento, "
                       "data_prevista_fechamento, vertical_final, responsavel, responsavel_chave, "
                       "responsavel_unificado, cliente, cliente_recorrente, base_origem, tipo_oferta, "
                       "motivo_perda, estagio, fechada_no_odoo, andamento_do_odoo")


@dataclass
class Filtros:
    criada_de: datetime.date | None = None
    criada_ate: datetime.date | None = None
    verticais: list[str] = field(default_factory=list)
    cliente: str | None = None
    responsaveis: list[str] = field(default_factory=list)
    base_origem: str | None = None
    busca: str | None = None
    so_validas: bool = True
    tipos_oferta: list[str] = field(default_factory=list)


def montar_where(f: Filtros) -> tuple[str, list]:
    condicoes, params = ["TRUE"], []
    if f.so_validas:
        condicoes.append("usar_na_analise")
    if f.criada_de:
        condicoes.append("data_criacao >= %s")
        params.append(f.criada_de)
    if f.criada_ate:
        condicoes.append("data_criacao <= %s")
        params.append(f.criada_ate)
    if f.verticais:
        condicoes.append("vertical_final = ANY(%s)")
        params.append(f.verticais)
    if f.cliente:
        condicoes.append("cliente ILIKE %s")
        params.append(f"%{f.cliente.strip()}%")
    if f.responsaveis:
        # pelo nome unificado da pessoa (ou qualquer grafia dela) - o nome muda entre SharePoint e Odoo
        condicoes.append("responsavel_chave IN (SELECT responsavel_chave FROM aging_base "
                         "WHERE responsavel = ANY(%s) OR responsavel_unificado = ANY(%s))")
        params += [f.responsaveis, f.responsaveis]
    if f.base_origem:
        condicoes.append("base_origem = %s")
        params.append(f.base_origem)
    if f.tipos_oferta:
        condicoes.append("tipo_oferta = ANY(%s)")
        params.append(f.tipos_oferta)
    if f.busca:
        termo = f"%{f.busca.strip()}%"
        condicoes.append("(cliente ILIKE %s OR nome ILIKE %s OR codigo ILIKE %s)")
        params += [termo, termo, termo]
    return " AND ".join(condicoes), params


# ── agregacao ─────────────────────────────────────────────────
def _num(v) -> float:
    return float(v) if v is not None else 0.0


def _razao(a, b):
    return round(a / b, 4) if b else None


def _mediana(valores):
    valores = [v for v in valores if v is not None]
    return round(float(statistics.median(valores)), 1) if valores else None


def _media(valores):
    valores = [v for v in valores if v is not None]
    return round(float(statistics.fmean(valores)), 1) if valores else None


def _faixa(dias) -> str | None:
    if dias is None:
        return None
    if dias < 0:
        return "Negativo"
    for limite, nome in ((30, "0-30 dias"), (60, "31-60 dias"), (90, "61-90 dias"),
                         (180, "91-180 dias"), (365, "181-365 dias")):
        if dias <= limite:
            return nome
    return "Acima de 365 dias"


def _resumo_grupo(linhas: list[dict]) -> dict:
    """Contagens, win rate, valores e ciclo de um grupo de oportunidades."""
    ganhas = [r for r in linhas if r["status"] == "ganha"]
    perdidas = [r for r in linhas if r["status"] == "perdida"]
    abertas = [r for r in linhas if r["status"] == "aberta"]
    valor_ganho = sum(_num(r["valor"]) for r in ganhas)
    valor_perdido = sum(_num(r["valor"]) for r in perdidas)
    return {
        "total": len(linhas),
        "ganhas": len(ganhas),
        "perdidas": len(perdidas),
        "abertas": len(abertas),
        "win_rate_qtd": _razao(len(ganhas), len(ganhas) + len(perdidas)),
        "win_rate_valor": _razao(valor_ganho, valor_ganho + valor_perdido),
        "valor_ganho": round(valor_ganho, 2),
        "ticket_mediano": _mediana([_num(r["valor"]) for r in ganhas]),
        "pipeline_valor": round(sum(_num(r["valor"]) for r in abertas), 2),
        "ciclo_mediano_ganhas": _mediana([r["aging_dias"] for r in ganhas]),
        "ciclo_mediano_perdidas": _mediana([r["aging_dias"] for r in perdidas]),
        "aging_medio_ganhas": _media([r["aging_dias"] for r in ganhas]),
        "aging_medio_perdidas": _media([r["aging_dias"] for r in perdidas]),
    }


def _por(linhas: list[dict], chave) -> dict:
    grupos = defaultdict(list)
    for r in linhas:
        grupos[chave(r)].append(r)
    return grupos


def _aging_status(linhas: list[dict], status: str) -> dict:
    dias = [r["aging_dias"] for r in linhas if r["status"] == status and r["aging_dias"] is not None]
    return {"status": status, "qtd": len(dias), "medio": _media(dias), "mediano": _mediana(dias),
            "minimo": min(dias) if dias else None, "maximo": max(dias) if dias else None}


def calcular_indicadores(linhas: list[dict], hoje: datetime.date | None = None) -> dict:
    hoje = hoje or datetime.date.today()
    geral = _resumo_grupo(linhas)
    fechadas = [r for r in linhas if r["status"] in ("ganha", "perdida")]
    abertas = [r for r in linhas if r["status"] == "aberta"]

    # Win rate por faixa de aging (so fechadas) e abertas por idade (dias em aberto)
    fechadas_faixa = _por(fechadas, lambda r: r["faixa_aging"] or _faixa(r["aging_dias"]))
    por_faixa = []
    for faixa in FAIXAS:
        grupo = fechadas_faixa.get(faixa, [])
        g = sum(1 for r in grupo if r["status"] == "ganha")
        p = len(grupo) - g
        por_faixa.append({"faixa": faixa, "ganhas": g, "perdidas": p, "win_rate": _razao(g, g + p),
                          "pct_ganhas": _razao(g, geral["ganhas"]), "pct_perdidas": _razao(p, geral["perdidas"])})
    abertas_idade = _por(abertas, lambda r: _faixa(r["aging_dias"]))
    abertas_por_idade = [{"faixa": f, "qtd": len(abertas_idade.get(f, [])),
                          "valor": round(sum(_num(r["valor"]) for r in abertas_idade.get(f, [])), 2)}
                         for f in FAIXAS if f != "Negativo"]

    paradas_1_ano = sum(1 for r in abertas if (r["aging_dias"] or 0) > 365)
    wr_ate_30 = next(f["win_rate"] for f in por_faixa if f["faixa"] == "0-30 dias")
    wr_acima_1_ano = next(f["win_rate"] for f in por_faixa if f["faixa"] == "Acima de 365 dias")

    por_ano = [{"ano": ano, **_resumo_grupo(grupo)}
               for ano, grupo in sorted(_por(linhas, lambda r: r["data_criacao"].year if r["data_criacao"] else None).items(),
                                        key=lambda kv: (kv[0] is None, kv[0]))]
    por_vertical = sorted(({"vertical": v or "(sem vertical)", **_resumo_grupo(g)}
                           for v, g in _por(linhas, lambda r: r["vertical_final"]).items()),
                          key=lambda x: -x["total"])
    por_base = [{"base_origem": b, **_resumo_grupo(g)} for b, g in sorted(_por(linhas, lambda r: r["base_origem"]).items())]
    clientes = sorted(({"cliente": c or "(sem cliente)", **_resumo_grupo(g)}
                       for c, g in _por(linhas, lambda r: r["cliente"]).items()),
                      key=lambda x: (-x["valor_ganho"], -x["total"]))[:20]
    # mesma pessoa com nomes diferentes nas duas bases -> agrupa pela chave e mostra o nome mais completo
    time = sorted(({"responsavel": max((r["responsavel"] or "" for r in g), key=len) or "(sem responsável)",
                    **_resumo_grupo(g)}
                   for _, g in _por(linhas, lambda r: r.get("responsavel_chave")).items()),
                  key=lambda x: (-x["valor_ganho"], -x["total"]))

    resultado = {
        "cards": {**geral, "pipeline_qtd": len(abertas), "abertas_paradas_1_ano": paradas_1_ano,
                  "pct_abertas_paradas_1_ano": _razao(paradas_1_ano, len(abertas)),
                  "win_rate_ate_30_dias": wr_ate_30, "win_rate_acima_1_ano": wr_acima_1_ano},
        "frase": _frase(geral, len(abertas), paradas_1_ano, wr_ate_30, wr_acima_1_ano),
        "aging_por_status": [_aging_status(linhas, s) for s in ("ganha", "perdida", "aberta")],
        "por_faixa": por_faixa,
        "abertas_por_idade": abertas_por_idade,
        "por_ano": por_ano,
        "por_vertical": por_vertical,
        "por_base": por_base,
        "clientes_top": clientes,
        "time_tabela": time,
        # abas avancadas (src/aging/analises.py)
        "win_rate_ticket": analises.win_rate_por_ticket(linhas),
        "perdas": analises.perdas(linhas),
        "sazonalidade": analises.sazonalidade(linhas),
        "curva_chance": analises.curva_chance(linhas),
        "decisoes": analises.decisoes_acumuladas(linhas),
        "safras": analises.conversao_por_safra(linhas, hoje),
        "aging_mediano_ano": analises.aging_mediano_por_ano(linhas),
        "bases": analises.por_base(linhas),
        "abertas": analises.abertas(linhas, hoje),
        "clientes": analises.clientes(linhas),
        "ofertas": analises.ofertas(linhas),
        "time": analises.time(linhas),
    }
    resultado["ponto_de_corte"] = analises.ponto_de_corte(resultado["curva_chance"])
    resultado["destaques"] = analises.destaques(linhas, resultado)
    return resultado


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.0f}%".replace(".", ",")


def _brl(v: float) -> str:
    if v >= 1_000_000_000:
        return f"R$ {v / 1_000_000_000:.1f} bi".replace(".", ",")
    if v >= 1_000_000:
        return f"R$ {v / 1_000_000:.1f} mi".replace(".", ",")
    if v >= 1_000:
        return f"R$ {v / 1_000:.0f} mil"
    return f"R$ {v:.0f}"


def _frase(geral: dict, abertas: int, paradas: int, wr_30, wr_365) -> str:
    fechadas = geral["ganhas"] + geral["perdidas"]
    if not geral["total"]:
        return "Nenhuma oportunidade com esses filtros."
    return (f"Win rate de {_pct(geral['win_rate_qtd'])} ({geral['ganhas']} ganhas de {fechadas} fechadas). "
            f"Fechadas em até 30 dias ganham {_pct(wr_30)}; acima de 1 ano, {_pct(wr_365)}. "
            f"Pipeline aberto de {_brl(geral['pipeline_valor'])} em {abertas} oportunidades, "
            f"{_pct(paradas / abertas if abertas else None)} delas paradas há mais de 1 ano.")
