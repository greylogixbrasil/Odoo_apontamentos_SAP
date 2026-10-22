import datetime
from decimal import Decimal

from src.aging.mapeamento import (mapear_odoo, mapear_sharepoint, normalizar_codigo, status_odoo,
                                  vertical_da_equipe)


def test_normalizar_codigo_casa_as_duas_bases():
    assert normalizar_codigo("C24.0474") == normalizar_codigo("C24.474") == "24.474"
    assert normalizar_codigo("26.0916") == "26.916"
    assert normalizar_codigo(" c19.371 ") == "19.371"
    assert normalizar_codigo("0") is None
    assert normalizar_codigo("") is None
    assert normalizar_codigo(None) is None


def test_vertical_da_equipe_tira_o_prefixo_e_ignora_equipe_generica():
    assert vertical_da_equipe("Comercial P&P") == "P&P"
    assert vertical_da_equipe("Comercial LITMA") == "LITMA"
    assert vertical_da_equipe("Sales") is None
    assert vertical_da_equipe("Vendas") is None
    assert vertical_da_equipe(None) is None


def test_status_odoo_usa_won_status():
    assert status_odoo({"won_status": "won", "active": False}) == "ganha"
    assert status_odoo({"won_status": "lost", "active": False}) == "perdida"
    assert status_odoo({"won_status": "pending", "active": True}) == "aberta"
    assert status_odoo({"won_status": "pending", "active": False}) == "arquivada"


def _lead(**extra):
    base = {
        "id": 18453, "name": "Aditivo Projeto 82.24.0329", "partner_id": [1999, "Cia. IGUAÇU De Café Solúvel"],
        "user_id": [204, "Claudio Luiz Sentone"], "team_id": [4, "Comercial F&B"], "stage_id": [7, "Negociação"],
        "lost_reason_id": [6, "Projeto cancelado ou suspenso pelo cliente"], "tag_ids": [1],
        "create_date": "2025-12-19 09:49:48", "date_closed": "2026-05-16 13:26:07", "date_deadline": "2026-08-20",
        "date_last_stage_update": "2026-05-16 13:26:07", "write_date": "2026-05-16 13:26:07",
        "x_studio_id": "C24.0474", "won_status": "lost", "active": False, "expected_revenue": 16584.45,
        "probability": 0.0,
    }
    return {**base, **extra}


def test_mapear_odoo_resolve_nomes_e_datas_no_fuso_de_sao_paulo():
    r = mapear_odoo(_lead(create_date="2026-03-02 01:30:00"), {1: "SCA"})
    assert r["base_origem"] == "Odoo" and r["id_origem"] == "18453"
    assert (r["cliente"], r["responsavel"], r["equipe"], r["vertical"]) == (
        "Cia. IGUAÇU De Café Solúvel", "Claudio Luiz Sentone", "Comercial F&B", "F&B")
    # 01:30 UTC de 02/03 ainda e 01/03 em Sao Paulo
    assert r["data_criacao"] == datetime.date(2026, 3, 1)
    assert r["data_fechamento"] == datetime.date(2026, 5, 16)
    assert r["status"] == "perdida" and r["motivo_perda"] == "Projeto cancelado ou suspenso pelo cliente"
    assert r["codigo_norm"] == "24.474" and r["tags"] == "SCA"
    assert r["valor"] == Decimal("16584.45")


def test_mapear_odoo_fechada_sem_date_closed_usa_ultima_mudanca_de_estagio():
    r = mapear_odoo(_lead(date_closed=False, date_last_stage_update="2026-04-10 15:00:00"))
    assert r["data_fechamento"] == datetime.date(2026, 4, 10)
    assert r["fonte_data_fechamento"] == "Odoo (date_last_stage_update)"


def test_mapear_odoo_aberta_nao_tem_fechamento():
    r = mapear_odoo(_lead(won_status="pending", active=True))
    assert r["status"] == "aberta" and r["data_fechamento"] is None


def _sp(**extra):
    base = {"ID": "5638", "Código": "C25.1011", "Título": "Horas Técnicas Automação", "Cliente": "Petrobras",
            "Responsavel": "Adir Trevisan Jr", "Vertical": "NVT", "Estagio": "Ganha",
            "Criado": "12/11/2025 5:32 PM", "Modificado": "12/15/2025 9:05 AM", "FechamentoReal": "12/14/2025",
            "FechamentoEstimado": "1/26/2026", "ValEstimado": "R$ 20,000.00", "ValFechado": "R$ 1,061,284.32",
            "Perdidas": "", "Chance": "Alta (Last Call)"}
    return {**base, **extra}


def test_mapear_sharepoint_le_data_americana_e_valor_em_reais():
    r = mapear_sharepoint(_sp())
    assert r["base_origem"] == "SharePoint" and r["id_origem"] == "5638"
    assert r["data_criacao"] == datetime.date(2025, 12, 11)
    assert r["data_fechamento"] == datetime.date(2025, 12, 14)
    assert r["fonte_data_fechamento"] == "SharePoint (FechamentoReal)"
    assert r["valor"] == Decimal("1061284.32")  # ganha: ValFechado
    assert r["status"] == "ganha" and r["vertical"] == "NVT" and r["codigo_norm"] == "25.1011"


def test_mapear_sharepoint_sem_fechamento_real_usa_modificado():
    r = mapear_sharepoint(_sp(Estagio="Perdida", FechamentoReal="", Perdidas="Perdido para Concorrência"))
    assert r["data_fechamento"] == datetime.date(2025, 12, 15)
    assert r["fonte_data_fechamento"] == "SharePoint (Modificado) - sem FechamentoReal"
    assert r["valor"] == Decimal("20000.00")  # perdida: ValEstimado
    assert r["motivo_perda"] == "Perdido para Concorrência"


def test_mapear_sharepoint_estagios_em_andamento_viram_aberta():
    for estagio in ("Budget", "Firme", "Negociação"):
        r = mapear_sharepoint(_sp(Estagio=estagio))
        assert r["status"] == "aberta" and r["data_fechamento"] is None


def test_mapear_sharepoint_aceita_celulas_de_excel():
    r = mapear_sharepoint(_sp(Criado=datetime.datetime(2025, 12, 11, 17, 32), ValFechado=1500.5,
                              Modificado=datetime.datetime(2025, 12, 15, 9, 5)))
    assert r["data_criacao"] == datetime.date(2025, 12, 11)
    assert r["valor"] == Decimal("1500.50")


def test_cliente_e_a_empresa_do_contato_nao_o_nome_de_exibicao():
    lead = _lead(partner_id=[1761, "ANDRITZ BRASIL LTDA, ANDRITZ BRASIL LTDA, André Madalozzo"])
    assert mapear_odoo(lead, empresa_por_contato={1761: "ANDRITZ BRASIL LTDA"})["cliente"] == "ANDRITZ BRASIL LTDA"
    # sem o mapa (ou contato fora dele) cai no nome de exibicao
    assert mapear_odoo(lead)["cliente"] == "ANDRITZ BRASIL LTDA, ANDRITZ BRASIL LTDA, André Madalozzo"
