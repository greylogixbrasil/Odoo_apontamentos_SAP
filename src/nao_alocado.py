"""Tempo nao alocado: horas que o funcionario deveria ter trabalhado (jornada do Odoo) e nao
apontou em nenhum projeto. A diferenca e lancada no SAP no centro de custo do funcionario -
os 5 primeiros caracteres ('NN.NN') do projeto em hr.employee.x_studio_related_project.

    nao alocado (dia) = horas previstas na jornada - horas apontadas no Odoo - horas ja
                        lancadas no SAP como nao alocado (mesmo funcionario/dia/ActivityType)

Horas previstas = soma dos horarios da jornada (resource.calendar.attendance) naquele dia da
semana, sem o almoco, zero em feriado da empresa e fora do periodo de contrato. Horas apontadas
= todo account.analytic.line do funcionario no dia, de qualquer projeto e status (inclusive Time
Off, que e como ferias/folga aparecem no timesheet). Descontar o que ja foi lancado no SAP deixa
reenviar o mesmo periodo sem duplicar: so a diferenca que surgiu depois e enviada.

O ActivityType (NAO_ALOCADO_IDSAP) ainda nao foi definido - sem ele nada e enviado, so o preview
funciona. O envio tambem exige NAO_ALOCADO_ENVIO_HABILITADO=true."""
import datetime
import math
import re
from collections import defaultdict
from zoneinfo import ZoneInfo

from src.agenda import _hhmm_int_para_horas
from src.config import Config
from src.core import (avaliar_linha, enviar_em_lotes, fechar_contexto, gerar_payloads_preview,
                      montar_contexto_com_linhas)
from src.db import Historico
from src.hana_client import HanaClient
from src.odoo_client import OdooClient

TAREFA = "Tempo não alocado"
ORIGEM = "nao_alocado"
_TZ_PADRAO = "America/Sao_Paulo"
_MINIMO_HORAS = 0.01
_CENTRO_CUSTO = re.compile(r"^(\d{2}\.\d{2})")


class EnvioNaoAlocadoBloqueado(Exception):
    pass


def centro_custo(nome_projeto: str | None) -> str | None:
    """'83.31 - Infrastructure' -> '83.31'. None se o nome nao comecar com NN.NN."""
    m = _CENTRO_CUSTO.match((nome_projeto or "").strip())
    return m.group(1) if m else None


def tipo_semana(data: datetime.date) -> str:
    """Semana par/impar de jornada de 2 semanas - mesma regra do Odoo
    (resource.calendar.attendance.get_week_type)."""
    return str(int(math.floor((data.toordinal() - 1) / 7) % 2))


def horas_previstas_no_dia(attendances: list[dict], data: datetime.date, duas_semanas: bool) -> float:
    """Soma os horarios da jornada no dia da semana de 'data' (dayofweek: '0' = segunda).
    Ignora o almoco (day_period 'lunch') e as linhas de secao da jornada de 2 semanas."""
    total = 0.0
    for att in attendances:
        if att.get("display_type") or att.get("day_period") == "lunch":
            continue
        if str(att["dayofweek"]) != str(data.weekday()):
            continue
        if duas_semanas and att.get("week_type") not in (None, False, tipo_semana(data)):
            continue
        total += att["hour_to"] - att["hour_from"]
    return round(total, 2)


def dias_de_feriado(feriados: list[dict], calendar_id: int | None, tz: str | None) -> set[datetime.date]:
    """Dias (no fuso da jornada) cobertos por um feriado que vale pra essa jornada - feriado sem
    calendar_id vale pra todas. date_from/date_to vem do Odoo em UTC ('YYYY-MM-DD HH:MM:SS')."""
    fuso = ZoneInfo(tz or _TZ_PADRAO)
    dias = set()
    for f in feriados:
        cal = f.get("calendar_id")
        if cal and cal[0] != calendar_id:
            continue
        inicio = _utc_para_local(f["date_from"], fuso).date()
        fim = (_utc_para_local(f["date_to"], fuso) - datetime.timedelta(seconds=1)).date()
        dia = inicio
        while dia <= fim:
            dias.add(dia)
            dia += datetime.timedelta(days=1)
    return dias


def _utc_para_local(texto: str, fuso: ZoneInfo) -> datetime.datetime:
    dt = datetime.datetime.strptime(texto, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(fuso)


def _para_data(valor) -> datetime.date | None:
    return datetime.date.fromisoformat(str(valor)[:10]) if valor else None


def calcular_tempo_nao_alocado(funcionarios: list[dict], calendarios: dict[int, dict],
                               attendances: list[dict], feriados: list[dict],
                               horas_apontadas: list[dict], data_inicio: str, data_fim: str) -> list[dict]:
    """So calculo, sem acesso a nenhum sistema. Uma linha por funcionario/dia com previsto >
    apontado. 'pendencia' preenchida quando o funcionario nao tem como ser lancado (sem centro
    de custo ou sem jornada) - a linha sai mesmo assim, pra aparecer no preview."""
    attendances_por_cal = defaultdict(list)
    for att in attendances:
        attendances_por_cal[att["calendar_id"][0]].append(att)

    apontado = defaultdict(float)
    for linha in horas_apontadas:
        apontado[(linha["employee_id"][0], str(linha["date"])[:10])] += linha["unit_amount"] or 0.0

    inicio, fim = datetime.date.fromisoformat(data_inicio), datetime.date.fromisoformat(data_fim)
    feriados_por_cal = {}
    resultado = []
    for func in funcionarios:
        projeto = func.get("x_studio_related_project")
        cc = centro_custo(projeto[1]) if projeto else None
        cal = func.get("resource_calendar_id")
        cal_id = cal[0] if cal else None
        calendario = calendarios.get(cal_id)

        pendencia = None
        if not projeto:
            pendencia = "funcionario sem x_studio_related_project (centro de custo) no Odoo"
        elif not cc:
            pendencia = f"projeto '{projeto[1]}' nao comeca com o padrao NN.NN"
        if not calendario:
            # sem jornada nao da pra saber as horas previstas - nem calcula
            continue

        if cal_id not in feriados_por_cal:
            feriados_por_cal[cal_id] = dias_de_feriado(feriados, cal_id, calendario.get("tz"))
        entrada, saida = _para_data(func.get("contract_date_start")), _para_data(func.get("departure_date"))

        dia = inicio
        while dia <= fim:
            fora_do_contrato = (entrada and dia < entrada) or (saida and dia > saida)
            if not fora_do_contrato and dia not in feriados_por_cal[cal_id]:
                previsto = horas_previstas_no_dia(attendances_por_cal[cal_id], dia,
                                                  calendario.get("two_weeks_calendar"))
                horas = round(apontado[(func["id"], dia.isoformat())], 2)
                if previsto - horas >= _MINIMO_HORAS:
                    resultado.append({
                        "odoo_employee_id": func["id"], "colaborador": func["name"],
                        "centro_custo": cc, "projeto_odoo": projeto[1] if projeto else None,
                        "data": dia.isoformat(), "previsto": previsto, "apontado": horas,
                        "nao_alocado": round(previsto - horas, 2), "pendencia": pendencia,
                    })
            dia += datetime.timedelta(days=1)
    return resultado


def horas_ja_lancadas_sap(linhas_hana: list[dict], idsap: str | None) -> dict[tuple[int, str], float]:
    """(employee_id_sap, data) -> horas ja lancadas no SAP com o ActivityType do nao alocado."""
    if not idsap:
        return {}
    total = defaultdict(float)
    for linha in linhas_hana:
        if str(linha.get("idsap")) != str(idsap):
            continue
        data = linha["data"]
        data_str = data.date().isoformat() if hasattr(data, "date") else str(data)[:10]
        total[(linha["user_id"], data_str)] += _hhmm_int_para_horas(linha.get("effect_tm"))
    return total


def _ontem() -> str:
    return (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


def montar_linhas_nao_alocado(config: Config, hana: HanaClient, odoo: OdooClient,
                              data_inicio: str, data_fim: str) -> list[dict]:
    """Calcula o nao alocado do periodo e monta cada dia no formato de linha do pipeline
    (src/core.py), com os campos extras do calculo (previsto/apontado/ja_lancado_sap).
    Nunca inclui hoje nem datas futuras - o dia ainda pode receber apontamento."""
    data_fim = min(data_fim, _ontem())
    if data_inicio > data_fim:
        return []

    funcionarios = odoo.buscar_funcionarios_ativos()
    calendarios, attendances = odoo.buscar_jornadas(
        [f["resource_calendar_id"][0] for f in funcionarios if f.get("resource_calendar_id")])
    dias = calcular_tempo_nao_alocado(
        funcionarios, calendarios, attendances, odoo.buscar_feriados(data_inicio, data_fim),
        odoo.buscar_horas_apontadas_periodo(data_inicio, data_fim), data_inicio, data_fim,
    )

    # mesmo cruzamento do fluxo principal (login do usuario no Odoo = email do funcionario no
    # SAP), com o email de trabalho do funcionario como alternativa
    empid_por_email = hana.buscar_funcionarios_ativos_com_email()
    login_por_user = {u["id"]: u["login"] for u in odoo.buscar_usuarios()}
    func_por_id = {f["id"]: f for f in funcionarios}
    ja_lancado = horas_ja_lancadas_sap(hana.buscar_lancamentos_existentes(data_inicio),
                                       config.nao_alocado_idsap)

    linhas = []
    for dia in dias:
        func = func_por_id[dia["odoo_employee_id"]]
        emails = [login_por_user.get(func["user_id"][0]) if func.get("user_id") else None,
                  func.get("work_email")]
        employee_id_sap = next((empid_por_email[e.strip().lower()] for e in emails
                                if e and e.strip().lower() in empid_por_email), None)
        ja_sap = round(ja_lancado.get((employee_id_sap, dia["data"]), 0.0), 2)
        linhas.append({
            "linha_excel": len(linhas) + 1,
            "origem": ORIGEM,
            "odoo_id": None,
            "colaborador": dia["colaborador"],
            "data": dia["data"],
            "projeto": dia["centro_custo"] or dia["projeto_odoo"] or "",
            "projeto_sap": dia["centro_custo"],
            "idsap": config.nao_alocado_idsap,
            "tarefa": TAREFA,
            "descricao": f"{TAREFA}: previsto {dia['previsto']:.2f}h, apontado {dia['apontado']:.2f}h",
            "horas": round(dia["nao_alocado"] - ja_sap, 2),
            "intervalo": None,
            "break_time": "00:00",
            "employee_id_sap": employee_id_sap,
            "local": None,
            "previsto": dia["previsto"],
            "apontado": dia["apontado"],
            "ja_lancado_sap": ja_sap,
            "pendencia": dia["pendencia"],
        })
    return linhas


def classificar(ctx, row: dict) -> tuple[str, str | None]:
    """Status de uma linha de nao alocado - avaliar_linha do pipeline + os casos proprios do
    modulo (pendencia de cadastro, ja lancado no SAP, ActivityType ainda nao definido)."""
    if row["pendencia"]:
        return "erro_dados_ausentes", row["pendencia"]
    if row["horas"] < _MINIMO_HORAS:
        return "ja_existe_sap", f"{row['ja_lancado_sap']:.2f}h ja lancadas no SAP como nao alocado nesse dia"
    status, motivo = avaliar_linha(ctx, row)
    if status == "erro_dados_ausentes" and motivo and motivo.startswith("idsap"):
        return "aguardando_idsap", "ActivityType do tempo nao alocado ainda nao definido (NAO_ALOCADO_IDSAP)"
    return status, motivo


def gerar_preview_nao_alocado(config: Config, data_inicio: str, data_fim: str) -> list[dict]:
    """So leitura (Odoo + HANA) - nao envia nada e nao grava no Postgres."""
    odoo = OdooClient(config.odoo.url, config.odoo.db, config.odoo.username, config.odoo.api_key)
    hana = HanaClient(config.hana.host, config.hana.port, config.hana.user, config.hana.password,
                      config.hana.schema, config.hana.ssl_validate_certificate)
    try:
        linhas = montar_linhas_nao_alocado(config, hana, odoo, data_inicio, data_fim)
    finally:
        hana.close()
    if not linhas:
        return []

    ctx = montar_contexto_com_linhas(config, linhas, abrir_sap=False)
    try:
        preview = []
        for row in linhas:
            status, motivo = classificar(ctx, row)
            preview.append({**row, "status": status, "motivo": motivo})
        # horario que seria enviado (encadeado depois do que o funcionario ja tem no SAP no dia)
        payloads, erros = gerar_payloads_preview(ctx, preview)
        for row in preview:
            payload = payloads.get(row["linha_excel"])
            if payload:
                lancamento = payload["PM_TimeSheetLineDataCollection"][0]
                row["start_time"], row["end_time"] = lancamento["StartTime"], lancamento["EndTime"]
            elif row["linha_excel"] in erros:
                row["status"], row["motivo"] = "erro_horario_invalido", erros[row["linha_excel"]]
        return preview
    finally:
        fechar_contexto(ctx)


def rodar_envio_nao_alocado(config: Config, hist: Historico, data_inicio: str, data_fim: str,
                            callback_progresso=None) -> int:
    """Envia o nao alocado do periodo pro SAP. Bloqueado enquanto o ActivityType nao estiver
    definido e o envio nao estiver liberado no .env."""
    if not config.nao_alocado_idsap:
        raise EnvioNaoAlocadoBloqueado("NAO_ALOCADO_IDSAP nao definido - defina o ActivityType antes de enviar")
    if not config.nao_alocado_envio_habilitado:
        raise EnvioNaoAlocadoBloqueado("envio do tempo nao alocado desligado (NAO_ALOCADO_ENVIO_HABILITADO=false)")

    odoo = OdooClient(config.odoo.url, config.odoo.db, config.odoo.username, config.odoo.api_key)
    hana = HanaClient(config.hana.host, config.hana.port, config.hana.user, config.hana.password,
                      config.hana.schema, config.hana.ssl_validate_certificate)
    try:
        linhas = montar_linhas_nao_alocado(config, hana, odoo, data_inicio, data_fim)
    finally:
        hana.close()

    execucao_id = hist.criar_execucao(config.sap.nome, f"{ORIGEM}:{data_inicio}..{data_fim}", False, origem=ORIGEM)
    ctx = montar_contexto_com_linhas(config, linhas, abrir_sap=True) if linhas else None
    try:
        prontas = []
        for row in linhas:
            status, motivo = classificar(ctx, row)
            if status == "pronto_para_envio":
                prontas.append(row)
            else:
                hist.registrar_linha(execucao_id, row["linha_excel"], row, status, motivo)
        if prontas:
            enviar_em_lotes(ctx, hist, execucao_id, prontas, config.batch_size,
                            config.batch_sleep_seconds, callback_progresso=callback_progresso)
        else:
            hist.finalizar_execucao(execucao_id)
        return execucao_id
    finally:
        if ctx is not None:
            fechar_contexto(ctx)
