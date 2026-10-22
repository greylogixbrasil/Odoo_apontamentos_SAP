import datetime
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from src.alertas import notificar_falha
from src.config import load_config
from src.db import Historico
from src.nao_alocado import gerar_preview_nao_alocado, rodar_envio_nao_alocado

from app_acompanhamento.state import state

router = APIRouter(prefix="/nao-alocado")
logger = logging.getLogger(__name__)


def _periodo_padrao(data_inicio: str | None, data_fim: str | None) -> tuple[str, str]:
    """Default: do primeiro dia do mes ate ontem."""
    ontem = datetime.date.today() - datetime.timedelta(days=1)
    return data_inicio or ontem.replace(day=1).isoformat(), data_fim or ontem.isoformat()


def _config_ou_erro():
    try:
        return load_config(env="prod")
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/config")
def obter_config():
    config = _config_ou_erro()
    return {
        "idsap": config.nao_alocado_idsap,
        "envio_habilitado": bool(config.nao_alocado_idsap) and config.nao_alocado_envio_habilitado,
    }


class PeriodoBody(BaseModel):
    data_inicio: str | None = None  # YYYY-MM-DD
    data_fim: str | None = None


@router.post("/preview")
def preview(body: PeriodoBody):
    """So leitura - calcula o tempo nao alocado do periodo sem enviar nada nem gravar no Postgres."""
    config = _config_ou_erro()
    data_inicio, data_fim = _periodo_padrao(body.data_inicio, body.data_fim)
    try:
        linhas = gerar_preview_nao_alocado(config, data_inicio, data_fim)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc

    contagem: dict[str, int] = {}
    horas_por_status: dict[str, float] = {}
    for row in linhas:
        contagem[row["status"]] = contagem.get(row["status"], 0) + 1
        horas_por_status[row["status"]] = round(horas_por_status.get(row["status"], 0) + row["horas"], 2)

    return {
        "linhas": linhas,
        "contagem": contagem,
        "horas_por_status": horas_por_status,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "idsap": config.nao_alocado_idsap,
    }


def _run_envio(config, data_inicio: str, data_fim: str):
    # mesmo cuidado do _run_automacao (automacao.py): Historico dentro do try pra uma falha de
    # conexao nao deixar 'em_andamento' travado em True
    hist = None
    try:
        hist = Historico(config.postgres_dsn)

        def callback(pos, total, row, resultado):
            with state.lock:
                state.nao_alocado["pos"] = pos
                state.nao_alocado["total"] = total
                state.nao_alocado["logs"].append(
                    f"[{pos}/{total}] {row['colaborador']} {row['data']} {row['horas']:.2f}h -> {resultado['status']}")
                state.nao_alocado["logs"] = state.nao_alocado["logs"][-50:]

        execucao_id = rodar_envio_nao_alocado(config, hist, data_inicio, data_fim, callback_progresso=callback)
        with state.lock:
            state.nao_alocado["execucao_id"] = execucao_id
            state.nao_alocado["resumo"] = hist.resumo_execucao(execucao_id)
    except Exception as exc:
        logger.exception("falha no envio do tempo nao alocado (%s a %s)", data_inicio, data_fim)
        notificar_falha("ApontaSAP: falha no envio do tempo nao alocado", f"{data_inicio} a {data_fim}: {exc}")
        with state.lock:
            state.nao_alocado["erro"] = str(exc)
    finally:
        if hist is not None:
            hist.close()
        with state.lock:
            state.nao_alocado["em_andamento"] = False


@router.post("/enviar")
def enviar(body: PeriodoBody, background_tasks: BackgroundTasks):
    config = _config_ou_erro()
    # checagem antecipada (rodar_envio_nao_alocado checa de novo) pra responder 403 na hora,
    # em vez de so descobrir o bloqueio no background
    if not config.nao_alocado_idsap or not config.nao_alocado_envio_habilitado:
        raise HTTPException(403, "Envio do tempo não alocado bloqueado: defina NAO_ALOCADO_IDSAP e "
                                 "NAO_ALOCADO_ENVIO_HABILITADO=true no .env")
    if state.nao_alocado["em_andamento"]:
        raise HTTPException(409, "Já existe um envio de tempo não alocado em andamento")

    data_inicio, data_fim = _periodo_padrao(body.data_inicio, body.data_fim)
    with state.lock:
        state.nao_alocado.update(em_andamento=True, pos=0, total=0, logs=[], execucao_id=None,
                                 resumo=None, erro=None)
    background_tasks.add_task(_run_envio, config, data_inicio, data_fim)
    return {"iniciado": True, "data_inicio": data_inicio, "data_fim": data_fim}


@router.get("/progresso")
def progresso():
    with state.lock:
        return dict(state.nao_alocado)
