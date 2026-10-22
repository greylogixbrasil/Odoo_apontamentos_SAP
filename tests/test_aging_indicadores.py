import datetime

from src.aging.indicadores import Filtros, calcular_indicadores, montar_where

HOJE = datetime.date(2026, 10, 22)


def _op(status, valor, aging, vertical="P&P", responsavel="Ana", cliente="Cliente A", ano=2025,
        prob=None, prevista=None, base="SharePoint", tipo="Painéis e elétrica", recorrente=False,
        motivo=None, estagio=None):
    criacao = datetime.date(ano, 1, 10)
    fechada = status in ("ganha", "perdida")
    return {"status": status, "valor": valor, "probabilidade": prob, "aging_dias": aging, "faixa_aging": None,
            "data_criacao": criacao, "data_fechamento": criacao + datetime.timedelta(days=aging) if fechada else None,
            "data_prevista_fechamento": prevista, "vertical_final": vertical, "responsavel": responsavel,
            "responsavel_chave": responsavel.split()[0].lower(), "responsavel_unificado": responsavel,
            "cliente": cliente, "cliente_recorrente": recorrente, "base_origem": base, "tipo_oferta": tipo,
            "motivo_perda": motivo, "estagio": estagio}


# 3 ganhas, 2 perdidas, 2 abertas - numeros escolhidos pra conferir de cabeca
FIXTURE = [
    _op("ganha", 100.0, 10),
    _op("ganha", 300.0, 20, vertical="F&B", responsavel="Bruno"),
    _op("ganha", 200.0, 400, cliente="Cliente B"),
    _op("perdida", 400.0, 25),
    _op("perdida", 100.0, 500, vertical="F&B", responsavel="Bruno", cliente="Cliente B"),
    _op("aberta", 1000.0, 400, prob=50.0, prevista=datetime.date(2026, 11, 5), base="Odoo", ano=2025),
    _op("aberta", 500.0, 30, prob=None, prevista=None, base="Odoo", ano=2026),
]


def test_cards():
    c = calcular_indicadores(FIXTURE, HOJE)["cards"]
    assert (c["total"], c["ganhas"], c["perdidas"], c["abertas"]) == (7, 3, 2, 2)
    assert c["win_rate_qtd"] == 0.6                 # 3 / (3 + 2)
    assert c["win_rate_valor"] == round(600 / 1100, 4)  # ganho 600 / fechado 600 + 500
    assert c["valor_ganho"] == 600.0
    assert c["ticket_mediano"] == 200.0
    assert (c["pipeline_valor"], c["pipeline_qtd"]) == (1500.0, 2)
    assert c["ciclo_mediano_ganhas"] == 20.0        # 10, 20, 400
    assert c["ciclo_mediano_perdidas"] == 262.5     # 25, 500
    assert c["abertas_paradas_1_ano"] == 1 and c["pct_abertas_paradas_1_ano"] == 0.5


def test_win_rate_por_faixa_de_aging():
    faixas = {f["faixa"]: f for f in calcular_indicadores(FIXTURE, HOJE)["por_faixa"]}
    assert (faixas["0-30 dias"]["ganhas"], faixas["0-30 dias"]["perdidas"]) == (2, 1)
    assert faixas["0-30 dias"]["win_rate"] == round(2 / 3, 4)
    assert (faixas["Acima de 365 dias"]["ganhas"], faixas["Acima de 365 dias"]["perdidas"]) == (1, 1)
    assert faixas["Acima de 365 dias"]["win_rate"] == 0.5
    assert faixas["61-90 dias"]["win_rate"] is None  # sem fechadas -> sem win rate


def test_frase_resumo():
    frase = calcular_indicadores(FIXTURE, HOJE)["frase"]
    assert frase.startswith("Win rate de 60% (3 ganhas de 5 fechadas).")
    assert "até 30 dias ganham 67%; acima de 1 ano, 50%" in frase
    assert "50% delas paradas há mais de 1 ano" in frase


def test_aging_por_status():
    status = {s["status"]: s for s in calcular_indicadores(FIXTURE, HOJE)["aging_por_status"]}
    assert status["ganha"] == {"status": "ganha", "qtd": 3, "medio": 143.3, "mediano": 20.0, "minimo": 10, "maximo": 400}
    assert status["aberta"]["qtd"] == 2


def test_quebras_por_vertical_e_responsavel():
    ind = calcular_indicadores(FIXTURE, HOJE)
    vert = {v["vertical"]: v for v in ind["por_vertical"]}
    assert (vert["P&P"]["ganhas"], vert["P&P"]["perdidas"], vert["P&P"]["abertas"]) == (2, 1, 2)
    assert vert["F&B"]["win_rate_qtd"] == 0.5
    time = {t["responsavel"]: t for t in ind["time_tabela"]}
    assert time["Bruno"]["valor_ganho"] == 300.0 and time["Ana"]["valor_ganho"] == 300.0


def test_resultado_tem_todas_as_abas_e_destaques():
    ind = calcular_indicadores(FIXTURE, HOJE)
    for chave in ("win_rate_ticket", "perdas", "sazonalidade", "curva_chance", "decisoes", "safras",
                  "aging_mediano_ano", "bases", "abertas", "clientes", "ofertas", "time", "destaques"):
        assert chave in ind
    assert ind["destaques"][0].startswith("Velocidade decide: fechadas em até 30 dias têm win rate de 66,7%")


def test_base_vazia_nao_quebra():
    ind = calcular_indicadores([], HOJE)
    assert ind["cards"]["total"] == 0 and ind["cards"]["win_rate_qtd"] is None
    assert ind["frase"] == "Nenhuma oportunidade com esses filtros."


def test_montar_where_parametriza_todos_os_filtros():
    where, params = montar_where(Filtros(criada_de=datetime.date(2025, 1, 1), verticais=["P&P"],
                                         busca="C24.0474", so_validas=True))
    assert "usar_na_analise" in where and "vertical_final = ANY(%s)" in where
    assert params == [datetime.date(2025, 1, 1), ["P&P"], "%C24.0474%", "%C24.0474%", "%C24.0474%"]
    # nada do texto do usuario entra no SQL, so em params
    assert "C24" not in where


def test_frase_formata_bilhoes():
    from src.aging.indicadores import _brl
    assert _brl(2_092_863_911.09) == "R$ 2,1 bi"
    assert _brl(581_375_626.4) == "R$ 581,4 mi"
    assert _brl(33_780.5) == "R$ 34 mil"
