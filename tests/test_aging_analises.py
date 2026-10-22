import datetime

from src.aging import analises

HOJE = datetime.date(2026, 10, 22)


def _op(status, aging, valor=100.0, cliente="A", recorrente=False, tipo="Painéis e elétrica", motivo=None,
        responsavel="Ana", criacao=datetime.date(2025, 1, 10), base="SharePoint", estagio=None):
    fechada = status in ("ganha", "perdida")
    return {"status": status, "aging_dias": aging, "valor": valor, "cliente": cliente, "cliente_recorrente": recorrente,
            "tipo_oferta": tipo, "motivo_perda": motivo, "responsavel": responsavel,
            "responsavel_chave": responsavel.lower(), "responsavel_unificado": responsavel,
            "data_criacao": criacao, "data_fechamento": criacao + datetime.timedelta(days=aging) if fechada else None,
            "base_origem": base, "estagio": estagio, "vertical_final": "P&P"}


def test_faixa_de_ticket_e_grupo_de_motivo():
    assert analises.faixa_ticket(9_999) == "< 10k"
    assert analises.faixa_ticket(10_000) == "10-50k"
    assert analises.faixa_ticket(2_000_000) == "> 1M"
    assert analises.faixa_ticket(None) == "< 10k"
    assert analises.grupo_motivo("Projeto cancelado ou suspenso pelo cliente") == "Cancelado ou suspenso"
    assert analises.grupo_motivo("Perdido para Concorrência") == "Concorrência"
    assert analises.grupo_motivo("Preço") == "Outros"
    assert analises.grupo_motivo(None) == "Não informado"


def test_curva_de_chance_considera_so_quem_passou_do_dia():
    # 10 ganhas em 5 dias, 10 perdidas em 100 dias, 10 ganhas em 200 dias
    linhas = ([_op("ganha", 5)] * 10) + ([_op("perdida", 100)] * 10) + ([_op("ganha", 200)] * 10)
    curva = {p["dia"]: p for p in analises.curva_chance(linhas, passo=5)}
    assert curva[0]["win_rate"] == round(20 / 30, 4)
    assert curva[10]["win_rate"] == 0.5          # passaram de 10 dias: 10 perdidas + 10 ganhas
    assert curva[10]["fechadas"] == 20
    assert 105 not in curva                       # so 10 fechadas passaram de 100 dias (< minimo 20)
    assert analises.ponto_de_corte([{"dia": 0, "win_rate": 0.5}, {"dia": 5, "win_rate": 0.19}]) == 5


def test_decisoes_acumuladas():
    linhas = [_op("ganha", d) for d in (0, 10, 20, 30, 40)] + [_op("perdida", 100)]
    dec = analises.decisoes_acumuladas(linhas, passo=10, max_dias=100)
    assert dec["ganhas"][:5] == [0.2, 0.4, 0.6, 0.8, 1.0]
    assert dec["perdidas"][-1] == 1.0 and dec["perdidas"][0] == 0.0
    assert dec["dia_80_ganhas"] == 30


def test_previsao_pelo_historico():
    # historico: 4 fechadas que passaram de 10 dias -> 1 ganha aos 20 dias (10 dias depois) = chance 25%
    fechadas = [_op("ganha", 20), _op("perdida", 30), _op("perdida", 40), _op("perdida", 50), _op("ganha", 5)]
    aberta = _op("aberta", 10, valor=1000.0)
    prev = analises.previsao_pelo_historico(fechadas + [aberta], HOJE)
    assert prev["ponderado"] == 250.0
    assert prev["valor_12_meses"] == 250.0 and prev["qtd_12_meses"] == 0.2  # 0,25 arredondado em 1 casa
    # ganha 10 dias depois de hoje (22/10) -> 01/11 -> mes seguinte
    assert prev["por_mes"][1] == {"mes": "2026-11", "valor": 250.0}
    assert prev["depois_12_meses"] == 0.0


def test_clientes_novo_recorrente_e_concentracao():
    linhas = ([_op("ganha", 10, valor=1000, cliente="Grande", recorrente=True)]
              + [_op("perdida", 10, cliente="Grande", recorrente=True)]
              + [_op("ganha", 10, valor=100, cliente="Pequeno")]
              + [_op("perdida", 10, cliente="Pequeno")] * 3
              + [_op("perdida", 10, cliente="Nunca")] * 5)
    cl = analises.clientes(linhas)
    assert cl["recorrente"]["win_rate"] == 0.5
    assert cl["novo"]["win_rate"] == round(1 / 9, 4)
    assert cl["concentracao"]["clientes_com_vitoria"] == 2
    assert cl["concentracao"]["top"][0]["cliente"] == "Grande"
    assert cl["concentracao"]["top"][0]["pct"] == round(1000 / 1100, 4)
    assert cl["concentracao"]["clientes_para_80"] == 1
    assert cl["sem_vitoria"] == {"clientes": 1, "fechadas": 5}


def test_ofertas_ordena_por_win_rate():
    linhas = ([_op("ganha", 10, tipo="Aditivos e complementos")] * 3 + [_op("perdida", 10, tipo="Aditivos e complementos")]
              + [_op("ganha", 30, tipo="Treinamento")] + [_op("perdida", 30, tipo="Treinamento")] * 3)
    tabela = analises.ofertas(linhas)["tabela"]
    assert [t["tipo"] for t in tabela] == ["Aditivos e complementos", "Treinamento"]
    assert tabela[0]["win_rate"] == 0.75 and tabela[1]["win_rate"] == 0.25


def test_time_mapa_por_trimestre():
    linhas = [_op("ganha", 10, criacao=datetime.date(2025, 1, 1))] * 15 + [_op("perdida", 10, criacao=datetime.date(2025, 4, 1))] * 10
    tm = analises.time(linhas, minimo_fechadas=20)
    assert tm["trimestres"] == ["2025-T1", "2025-T2"]
    celulas = tm["mapa"][0]["celulas"]
    assert celulas["2025-T1"] == {"ganhas": 15, "fechadas": 15, "win_rate": 1.0}
    assert celulas["2025-T2"]["win_rate"] == 0.0
    assert tm["bolhas"][0]["fechadas"] == 25


def test_sazonalidade_ultima_semana():
    linhas = [_op("ganha", 0, criacao=datetime.date(2025, 3, 31)), _op("ganha", 0, criacao=datetime.date(2025, 3, 10))]
    s = analises.sazonalidade(linhas)
    assert s["por_mes"][2]["ganhas"] == 2
    assert s["pct_ultima_semana"] == 0.5
