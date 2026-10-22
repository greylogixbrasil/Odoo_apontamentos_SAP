"""Testes com Postgres de verdade (upsert, importacao historica e regras da view aging_base).
Rodam so com AGING_TEST_DSN apontando pra um banco DESCARTAVEL - as tabelas do aging sao
recriadas a cada teste. Ex.: AGING_TEST_DSN=postgresql://sap_import:sap_import@localhost:55432/aging_test"""
import csv
import datetime
import os
from pathlib import Path

import psycopg2
import pytest

from src.aging.historico import importar_historico
from src.aging.mapeamento import mapear_odoo
from src.aging.repositorio import AgingRepositorio

DSN = os.environ.get("AGING_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="AGING_TEST_DSN nao definido (banco de teste descartavel)")
MIGRACAO = Path(__file__).resolve().parent.parent / "db" / "migrations" / "0003_aging_oportunidades.sql"


@pytest.fixture()
def repo():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP VIEW IF EXISTS aging_base; DROP TABLE IF EXISTS aging_oportunidade, aging_sync_log, aging_ajuste;")
        cur.execute(MIGRACAO.read_text(encoding="utf-8"))
    conn.close()
    r = AgingRepositorio(DSN)
    yield r
    r.close()


def _lead(id_, codigo, nome, criado="2026-02-10 12:00:00", equipe="Comercial P&P", **extra):
    base = {"id": id_, "name": nome, "partner_id": [1, "Cliente X"], "user_id": [2, "Ana"], "team_id": [3, equipe],
            "stage_id": [4, "Novo"], "lost_reason_id": False, "tag_ids": [], "create_date": criado,
            "date_closed": False, "date_deadline": False, "date_last_stage_update": False,
            "write_date": "2026-10-01 12:00:00", "x_studio_id": codigo, "won_status": "pending", "active": True,
            "expected_revenue": 1000.0, "probability": 20.0}
    return mapear_odoo({**base, **extra})


def _escrever_csv(caminho, linhas):
    colunas = ["ID", "Código", "Título", "Cliente", "Responsavel", "Vertical", "Estagio", "Criado", "Modificado",
               "FechamentoReal", "FechamentoEstimado", "ValEstimado", "ValFechado", "Perdidas", "Chance"]
    with open(caminho, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=colunas)
        w.writeheader()
        for linha in linhas:
            w.writerow({c: linha.get(c, "") for c in colunas})


SHAREPOINT = [
    {"ID": "1", "Código": "C24.0474", "Título": "Retrofit Linha 3", "Cliente": "Cliente X", "Responsavel": "Ana",
     "Vertical": "F&B", "Estagio": "Ganha", "Criado": "3/1/2024 10:00 AM", "Modificado": "4/1/2024 10:00 AM",
     "FechamentoReal": "3/31/2024", "ValEstimado": "R$ 5,000.00", "ValFechado": "R$ 4,500.00"},
    {"ID": "2", "Código": "C19.371", "Título": "Hazop Reator I", "Cliente": "Arauco", "Responsavel": "Ana",
     "Vertical": "F&B", "Estagio": "Perdida", "Criado": "12/16/2019 9:00 AM", "Modificado": "5/13/2020 9:00 AM",
     "FechamentoReal": "5/13/2020", "ValEstimado": "R$ 42,000.00"},
    {"ID": "3", "Código": "0", "Título": "Sem codigo", "Cliente": "Cliente Y", "Responsavel": "Ana",
     "Vertical": "F&B", "Estagio": "Budget", "Criado": "6/1/2025 9:00 AM", "Modificado": "6/1/2025 9:00 AM",
     "ValEstimado": "R$ 1,000.00"},
]


def test_upsert_e_idempotente(repo):
    registros = [_lead(10, "26.0001", "A"), _lead(11, "26.0002", "B")]
    assert repo.upsert(registros) == (2, 0)
    assert repo.upsert(registros) == (0, 2)
    assert repo.contar_base("TRUE", []) == 2


def test_importacao_historica_rodando_duas_vezes_nao_duplica(repo, tmp_path):
    arquivo = tmp_path / "sharepoint.csv"
    _escrever_csv(arquivo, SHAREPOINT)
    assert importar_historico(DSN, arquivo) == {"lidos": 3, "inseridos": 3, "atualizados": 0}
    assert importar_historico(DSN, arquivo) == {"lidos": 3, "inseridos": 0, "atualizados": 3}
    assert repo.contar_base("base_origem = 'SharePoint'", []) == 3


def test_oportunidade_que_existe_no_sharepoint_vale_so_a_do_sharepoint(repo, tmp_path):
    arquivo = tmp_path / "sharepoint.csv"
    _escrever_csv(arquivo, SHAREPOINT)
    importar_historico(DSN, arquivo)
    repo.upsert([
        _lead(20, "C24.474", "Retrofit Linha 3", criado="2025-12-15 21:24:06"),       # copia da migracao
        _lead(21, "C24.0474", "Aditivo Projeto 82.24.0329", criado="2025-12-19 09:49:48"),  # aditivo novo, codigo reaproveitado
        _lead(22, "0", "Sem codigo", partner_id=[9, "Cliente Y"], criado="2025-12-15 21:24:06"),  # migrada sem codigo
        _lead(23, "26.0916", "Nova em 2026"),                                          # so existe no Odoo
    ])
    ids_odoo = {r["id_origem"] for r in repo.consultar_base("base_origem = 'Odoo'", [], colunas="id_origem")}
    assert ids_odoo == {"21", "23"}
    sp = repo.consultar_base("codigo = 'C24.0474' AND base_origem = 'SharePoint'", [])[0]
    assert sp["status"] == "ganha" and sp["vertical_final"] == "F&B" and sp["aging_dias"] == 30


def test_regras_de_linha_valida_e_vertical_inferida(repo, tmp_path):
    arquivo = tmp_path / "sharepoint.csv"
    _escrever_csv(arquivo, SHAREPOINT)
    importar_historico(DSN, arquivo)
    repo.upsert([
        _lead(30, "26.0100", "Equipe generica", equipe="Sales"),
        _lead(31, "26.0101", "Do Joao", equipe="Sales", user_id=[5, "João Battisti"]),
        _lead(18525, "26.0102", "Oportunidade da ANDRITZ PULP & PAPER"),
        _lead(32, "26.0103", "Arquivada", won_status="pending", active=False),
        _lead(33, "26.0104", "Fechou antes de criar", won_status="won", create_date="2026-05-10 12:00:00",
              date_closed="2026-05-01 12:00:00"),
    ])
    linhas = {r["nome"]: r for r in repo.consultar_base("TRUE", [])}
    assert "Oportunidade da ANDRITZ PULP & PAPER" not in linhas            # teste excluido (aging_ajuste)
    assert linhas["Equipe generica"]["vertical_final"] == "F&B"           # predominante da Ana no SharePoint
    assert linhas["Do Joao"]["vertical_final"] == "ENG"                   # ajuste manual
    assert linhas["Hazop Reator I"]["usar_na_analise"] is False           # criada em 16/12/2019
    assert linhas["Arquivada"]["usar_na_analise"] is False
    assert linhas["Fechou antes de criar"]["faixa_aging"] == "Negativo"
    assert linhas["Fechou antes de criar"]["usar_na_analise"] is False
    aberta = linhas["Sem codigo"]
    assert aberta["status"] == "aberta"
    assert aberta["aging_dias"] == (datetime.date.today() - datetime.date(2025, 6, 1)).days


def test_sync_completa_marca_excluidos_no_odoo(repo):
    repo.upsert([_lead(40, "26.0200", "Fica"), _lead(41, "26.0201", "Some")])
    assert repo.marcar_excluidos("Odoo", ["40"]) == 1
    assert {r["id_origem"] for r in repo.consultar_base("TRUE", [], colunas="id_origem")} == {"40"}
    repo.upsert([_lead(41, "26.0201", "Some")])   # voltou no Odoo -> volta pra base
    assert repo.contar_base("TRUE", []) == 2


def test_mesma_pessoa_com_nome_diferente_nas_bases(repo, tmp_path):
    arquivo = tmp_path / "sharepoint.csv"
    _escrever_csv(arquivo, [{**SHAREPOINT[0], "ID": "9", "Código": "C24.0900", "Título": "Outra",
                             "Responsavel": "Marcos Liebl", "Vertical": "P&P"}])
    importar_historico(DSN, arquivo)
    repo.upsert([_lead(50, "26.0300", "Nova do Marcos", equipe="Sales",
                       user_id=[7, "Marcos Vinicius Henrique Liebl"])])
    nova = repo.consultar_base("nome = 'Nova do Marcos'", [])[0]
    assert nova["vertical_final"] == "P&P"
    assert nova["responsavel_unificado"] == "Marcos Vinicius Henrique Liebl"
    # filtro por qualquer uma das grafias traz as duas bases
    from src.aging.indicadores import Filtros, montar_where
    where, params = montar_where(Filtros(responsaveis=["Marcos Liebl"], so_validas=False))
    assert repo.contar_base(where, params) == 2
    assert repo.opcoes_filtros()["responsaveis"] == ["Marcos Vinicius Henrique Liebl"]


def test_copia_da_migracao_sem_codigo_e_sem_cliente_fica_so_no_sharepoint(repo, tmp_path):
    arquivo = tmp_path / "sharepoint.csv"
    _escrever_csv(arquivo, SHAREPOINT)
    importar_historico(DSN, arquivo)
    repo.upsert([
        _lead(60, "0", "Sem codigo", partner_id=False, criado="2025-12-15 21:24:06"),   # copia da migracao
        _lead(61, "0", "Sem codigo", partner_id=[8, "Outro Cliente"]),                   # nova em 2026, outro cliente
    ])
    assert {r["id_origem"] for r in repo.consultar_base("base_origem = 'Odoo'", [], colunas="id_origem")} == {"61"}


def test_tipo_de_oferta_e_cliente_recorrente(repo):
    repo.upsert([
        _lead(70, "26.0400", "Aditivo Projeto 82.24.0329", won_status="won", create_date="2026-01-05 12:00:00",
              date_closed="2026-02-01 12:00:00"),
        _lead(71, "26.0401", "Retrofit PCS7 Caldeira", create_date="2026-03-01 12:00:00"),
        _lead(72, "26.0402", "Painel CCM Linha 2", create_date="2026-01-01 12:00:00"),
        _lead(73, "26.0403", "Coisa sem pista", create_date="2026-01-02 12:00:00"),
    ])
    linhas = {r["nome"]: r for r in repo.consultar_base("TRUE", [])}
    assert linhas["Aditivo Projeto 82.24.0329"]["tipo_oferta"] == "Aditivos e complementos"
    assert linhas["Retrofit PCS7 Caldeira"]["tipo_oferta"] == "Migração, retrofit e atualização"  # 1a regra que casa
    assert linhas["Painel CCM Linha 2"]["tipo_oferta"] == "Painéis e elétrica"
    assert linhas["Coisa sem pista"]["tipo_oferta"] == "Outros / não identificado"
    # Cliente X ganhou em 01/02/2026: so quem foi criada depois disso e recorrente
    assert linhas["Retrofit PCS7 Caldeira"]["cliente_recorrente"] is True
    assert linhas["Painel CCM Linha 2"]["cliente_recorrente"] is False
    assert linhas["Aditivo Projeto 82.24.0329"]["cliente_recorrente"] is False


def test_nome_mais_curto_contido_no_mais_longo_vira_a_mesma_pessoa(repo):
    repo.upsert([
        _lead(80, "26.0500", "A", user_id=[1, "Fábio Alves"]),
        _lead(81, "26.0501", "B", user_id=[2, "Fábio Alves Ferreira"]),
        _lead(82, "26.0502", "C", user_id=[3, "Adir Trevisan Jr"]),
        _lead(83, "26.0503", "D", user_id=[4, "Adir Trevisan Jr."]),
        _lead(84, "26.0504", "E", user_id=[5, "Fábio Souza"]),     # outro Fabio - nao junta
    ])
    unificado = {r["nome"]: r["responsavel_unificado"] for r in repo.consultar_base("TRUE", [])}
    assert unificado["A"] == unificado["B"] == "Fábio Alves Ferreira"
    assert unificado["C"] == unificado["D"]
    assert unificado["E"] == "Fábio Souza"


def test_aberta_no_sharepoint_que_fechou_no_odoo_usa_o_andamento_do_odoo(repo, tmp_path):
    arquivo = tmp_path / "sharepoint.csv"
    _escrever_csv(arquivo, [
        {**SHAREPOINT[0], "ID": "50", "Código": "C25.0500", "Título": "Fechou no Odoo", "Estagio": "Budget",
         "FechamentoReal": "", "Criado": "6/1/2025 9:00 AM"},
        {**SHAREPOINT[0], "ID": "51", "Código": "C25.0501", "Título": "Segue aberta", "Estagio": "Firme",
         "FechamentoReal": "", "Criado": "6/1/2025 9:00 AM"},
        {**SHAREPOINT[0], "ID": "52", "Código": "C25.0502", "Título": "Fechamento da migracao", "Estagio": "Budget",
         "FechamentoReal": "", "Criado": "6/1/2025 9:00 AM"},
    ])
    importar_historico(DSN, arquivo)
    migrada = "2025-12-15 21:24:06"
    repo.upsert([
        _lead(90, "C25.500", "Fechou no Odoo", criado=migrada, won_status="lost", active=False,
              date_closed="2026-03-10 12:00:00", lost_reason_id=[1, "Perdido para Concorrência"], stage_id=[2, "Negociação"]),
        _lead(91, "C25.501", "Segue aberta", criado=migrada, stage_id=[3, "Análise do cliente"]),
        _lead(92, "C25.502", "Fechamento da migracao", criado=migrada, won_status="won", date_closed="2025-12-15 21:24:06"),
    ])
    linhas = {r["nome"]: r for r in repo.consultar_base("TRUE", [])}
    assert set(linhas) >= {"Fechou no Odoo", "Segue aberta", "Fechamento da migracao"}
    assert all(r["base_origem"] == "SharePoint" for r in linhas.values())   # continua uma linha so, do SharePoint

    fechou = linhas["Fechou no Odoo"]
    assert (fechou["status"], str(fechou["data_fechamento"]), fechou["motivo_perda"]) == (
        "perdida", "2026-03-10", "Perdido para Concorrência")
    assert fechou["fechada_no_odoo"] is True and fechou["estagio"] == "Negociação"
    assert fechou["vertical_final"] == "F&B" and str(fechou["data_criacao"]) == "2025-06-01"   # do SharePoint

    aberta = linhas["Segue aberta"]
    assert aberta["status"] == "aberta" and aberta["estagio"] == "Análise do cliente"
    assert aberta["andamento_do_odoo"] is True and aberta["fechada_no_odoo"] is False

    # data de fechamento da carga de migracao nao conta - segue aberta como no SharePoint
    assert linhas["Fechamento da migracao"]["status"] == "aberta"


def test_carga_sql_do_historico_e_idempotente_e_igual_a_importacao(repo, tmp_path):
    from scripts.gerar_seed_historico_aging import gerar, literal
    from src.aging.historico import ler_export
    from src.aging.mapeamento import mapear_sharepoint

    assert literal("D'Agua") == "'D''Agua'" and literal(None) == "NULL"
    arquivo = tmp_path / "sharepoint.csv"
    _escrever_csv(arquivo, SHAREPOINT)
    sql = gerar([mapear_sharepoint(l) for l in ler_export(arquivo)], "teste.csv")
    with repo.conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(sql)
    assert repo.contar_base("base_origem = 'SharePoint'", []) == 3
    ganha = repo.consultar_base("codigo = 'C24.0474'", [])[0]
    assert (ganha["status"], str(ganha["data_fechamento"]), float(ganha["valor"])) == ("ganha", "2024-03-31", 4500.0)
