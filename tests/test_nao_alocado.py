import datetime

from src.nao_alocado import (calcular_tempo_nao_alocado, centro_custo, dias_de_feriado,
                             horas_ja_lancadas_sap, horas_previstas_no_dia, tipo_semana)

# Jornada 44h: seg-qui 08-12 + 13-18 (9h), sex 08-12 + 13-17 (8h), almoco cadastrado a parte
JORNADA_44H = (
    [{"calendar_id": [1, "CLT 44h"], "dayofweek": str(d), "hour_from": 8.0, "hour_to": 12.0,
      "day_period": "morning", "week_type": False, "display_type": False} for d in range(5)]
    + [{"calendar_id": [1, "CLT 44h"], "dayofweek": str(d), "hour_from": 12.0, "hour_to": 13.0,
        "day_period": "lunch", "week_type": False, "display_type": False} for d in range(5)]
    + [{"calendar_id": [1, "CLT 44h"], "dayofweek": str(d), "hour_from": 13.0, "hour_to": 18.0 if d < 4 else 17.0,
        "day_period": "afternoon", "week_type": False, "display_type": False} for d in range(5)]
)
CALENDARIOS = {1: {"id": 1, "name": "CLT 44h", "two_weeks_calendar": False, "tz": "America/Sao_Paulo"}}


def _func(**extra):
    base = {"id": 10, "name": "Adenilson Mendes Correa", "x_studio_related_project": [5, "83.31 - Infrastructure"],
            "resource_calendar_id": [1, "CLT 44h"], "contract_date_start": False, "departure_date": False}
    return {**base, **extra}


def test_centro_custo_pega_os_5_primeiros_digitos():
    assert centro_custo("83.31 - Infrastructure") == "83.31"
    assert centro_custo("82.43 - Software Team 3") == "82.43"
    assert centro_custo("Infrastructure") is None
    assert centro_custo(None) is None


def test_horas_previstas_ignora_almoco_e_fim_de_semana():
    assert horas_previstas_no_dia(JORNADA_44H, datetime.date(2026, 10, 19), False) == 9.0   # segunda
    assert horas_previstas_no_dia(JORNADA_44H, datetime.date(2026, 10, 23), False) == 8.0   # sexta
    assert horas_previstas_no_dia(JORNADA_44H, datetime.date(2026, 10, 24), False) == 0.0   # sabado


def test_jornada_de_duas_semanas_usa_so_a_semana_certa():
    dia = datetime.date(2026, 10, 19)
    semana = tipo_semana(dia)
    outra = "1" if semana == "0" else "0"
    attendances = [
        {"dayofweek": "0", "hour_from": 8.0, "hour_to": 12.0, "day_period": "morning", "week_type": semana, "display_type": False},
        {"dayofweek": "0", "hour_from": 8.0, "hour_to": 18.0, "day_period": "morning", "week_type": outra, "display_type": False},
        {"dayofweek": "0", "hour_from": 0.0, "hour_to": 0.0, "day_period": "morning", "week_type": semana, "display_type": "line_section"},
    ]
    assert horas_previstas_no_dia(attendances, dia, True) == 4.0


def test_feriado_em_utc_vira_o_dia_local():
    # 02/11 00:00-23:59 em Sao Paulo = 02/11 03:00 ate 03/11 03:00 UTC
    feriados = [{"date_from": "2026-11-02 03:00:00", "date_to": "2026-11-03 03:00:00", "calendar_id": False}]
    assert dias_de_feriado(feriados, 1, "America/Sao_Paulo") == {datetime.date(2026, 11, 2)}


def test_feriado_de_outra_jornada_nao_vale():
    feriados = [{"date_from": "2026-11-02 03:00:00", "date_to": "2026-11-03 03:00:00", "calendar_id": [2, "Outra"]}]
    assert dias_de_feriado(feriados, 1, "America/Sao_Paulo") == set()


def test_calcula_a_diferenca_entre_previsto_e_apontado():
    apontadas = [{"employee_id": [10, "x"], "date": "2026-10-19", "unit_amount": 6.5},
                 {"employee_id": [10, "x"], "date": "2026-10-19", "unit_amount": 1.0},
                 {"employee_id": [10, "x"], "date": "2026-10-20", "unit_amount": 9.0}]
    dias = calcular_tempo_nao_alocado([_func()], CALENDARIOS, JORNADA_44H, [], apontadas, "2026-10-19", "2026-10-20")
    assert [(d["data"], d["previsto"], d["apontado"], d["nao_alocado"], d["centro_custo"]) for d in dias] == [
        ("2026-10-19", 9.0, 7.5, 1.5, "83.31"),
    ]


def test_feriado_e_dia_fora_do_contrato_nao_geram_nao_alocado():
    feriados = [{"date_from": "2026-11-02 03:00:00", "date_to": "2026-11-03 03:00:00", "calendar_id": False}]
    func = _func(contract_date_start="2026-11-04")
    dias = calcular_tempo_nao_alocado([func], CALENDARIOS, JORNADA_44H, feriados, [], "2026-11-02", "2026-11-04")
    assert [d["data"] for d in dias] == ["2026-11-04"]


def test_funcionario_sem_centro_de_custo_sai_com_pendencia():
    dias = calcular_tempo_nao_alocado([_func(x_studio_related_project=False)], CALENDARIOS, JORNADA_44H, [], [],
                                      "2026-10-19", "2026-10-19")
    assert dias[0]["centro_custo"] is None
    assert "x_studio_related_project" in dias[0]["pendencia"]


def test_horas_ja_lancadas_sap_soma_so_o_activity_type_do_nao_alocado():
    linhas_hana = [
        {"user_id": 859, "data": datetime.datetime(2026, 10, 19), "idsap": 27, "effect_tm": 130},   # 1:30
        {"user_id": 859, "data": datetime.datetime(2026, 10, 19), "idsap": 27, "effect_tm": 30},    # 0:30
        {"user_id": 859, "data": datetime.datetime(2026, 10, 19), "idsap": 22, "effect_tm": 800},
    ]
    assert horas_ja_lancadas_sap(linhas_hana, "27") == {(859, "2026-10-19"): 2.0}
    assert horas_ja_lancadas_sap(linhas_hana, None) == {}
