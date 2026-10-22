"""Dashboard de aging de oportunidades.

router (PUBLICO, sem login - ver main.py): so leitura dos indicadores, da base e do status da sync.
router_admin (com login): forcar sincronizacao com o Odoo."""
import csv
import datetime
import io
import logging
import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from src.aging.indicadores import COLUNAS_INDICADORES, Filtros, calcular_indicadores, montar_where
from src.aging.repositorio import AgingRepositorio
from src.aging.sync import sincronizar
from src.alertas import notificar_falha
from src.odoo_json2 import OdooJson2Client

router = APIRouter(prefix="/aging")
router_admin = APIRouter(prefix="/aging-admin")
logger = logging.getLogger(__name__)

COLUNAS_BASE = [
    ("codigo", "Código"), ("nome", "Oportunidade"), ("tipo_oferta", "Tipo de oferta"), ("cliente", "Cliente"),
    ("cliente_recorrente", "Cliente já ganhou antes"), ("responsavel", "Responsável"),
    ("responsavel_unificado", "Responsável (unificado)"), ("vertical_final", "Vertical"),
    ("base_origem", "Base de origem"), ("status", "Status"), ("estagio", "Estágio"),
    ("data_criacao", "Data de criação"), ("data_fechamento", "Data de ganho/perda"),
    ("fonte_data_fechamento", "Fonte da data de ganho/perda"), ("aging_dias", "Aging (dias)"),
    ("faixa_aging", "Faixa de aging"), ("valor", "Valor (R$)"), ("probabilidade", "Probabilidade (%)"),
    ("data_prevista_fechamento", "Fechamento previsto"), ("motivo_perda", "Motivo da perda"),
    ("usar_na_analise", "Usar na análise"), ("motivo_fora_analise", "Motivo fora da análise"),
]
_ORDENS = {c for c, _ in COLUNAS_BASE}


def _repo() -> AgingRepositorio:
    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        raise HTTPException(500, "POSTGRES_DSN nao configurado")
    return AgingRepositorio(dsn)


def filtros(criada_de: datetime.date | None = None, criada_ate: datetime.date | None = None,
            vertical: list[str] = Query(default=[]), cliente: str | None = None,
            responsavel: list[str] = Query(default=[]), base_origem: str | None = None,
            tipo_oferta: list[str] = Query(default=[]), busca: str | None = None,
            so_validas: bool = True) -> Filtros:
    """Filtros globais do dashboard - mesmos parametros em todas as rotas de leitura."""
    return Filtros(criada_de, criada_ate, [v for v in vertical if v], (cliente or "").strip() or None,
                   [r for r in responsavel if r], base_origem or None, (busca or "").strip() or None,
                   so_validas, [t for t in tipo_oferta if t])


def _where_base(f: Filtros, status: str | None, faixa: str | None) -> tuple[str, list]:
    """Filtros globais + os da aba Base de dados (status e faixa de aging)."""
    where, params = montar_where(f)
    if status:
        where += " AND status = %s"
        params.append(status)
    if faixa:
        where += " AND faixa_aging = %s"
        params.append(faixa)
    return where, params


def _serializar(row: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, (datetime.date, datetime.datetime))
                else float(v) if hasattr(v, "as_tuple") else v) for k, v in row.items()}


@router.get("/status")
def status():
    """Ultima sincronizacao bem-sucedida - alimenta o "Atualizado ha X min"."""
    repo = _repo()
    try:
        ultima = repo.ultima_sync_ok()
        historico = repo.ultima_sync_ok(tipos=("historico",))
    finally:
        repo.close()
    return {
        "ultima_sync_ok": ultima["finalizado_em"].isoformat() if ultima else None,
        "importacao_historica": historico["finalizado_em"].isoformat() if historico else None,
    }


@router.get("/filtros")
def opcoes_filtros():
    repo = _repo()
    try:
        return repo.opcoes_filtros()
    finally:
        repo.close()


@router.get("/indicadores")
def indicadores(f: Filtros = Depends(filtros)):
    where, params = montar_where(f)
    repo = _repo()
    try:
        linhas = repo.consultar_base(where, params, colunas=COLUNAS_INDICADORES, ordem="id")
    finally:
        repo.close()
    return calcular_indicadores(linhas)


@router.get("/qualidade")
def qualidade():
    """Pontos que afetam a leitura dos numeros - base completa, sem filtros."""
    repo = _repo()
    try:
        return repo.qualidade_dos_dados()
    finally:
        repo.close()


@router.get("/base")
def base(f: Filtros = Depends(filtros), status: str | None = None, faixa: str | None = None,
         pagina: int = Query(1, ge=1), tamanho: int = Query(50, ge=1, le=500),
         ordem: str = "data_criacao", desc: bool = True):
    where, params = _where_base(f, status, faixa)
    coluna = ordem if ordem in _ORDENS else "data_criacao"
    repo = _repo()
    try:
        total = repo.contar_base(where, params)
        linhas = repo.consultar_base(where, params, colunas=", ".join(c for c, _ in COLUNAS_BASE),
                                     ordem=f"{coluna} {'DESC' if desc else 'ASC'} NULLS LAST",
                                     limite=tamanho, offset=(pagina - 1) * tamanho)
    finally:
        repo.close()
    return {"total": total, "pagina": pagina, "tamanho": tamanho, "colunas": COLUNAS_BASE,
            "linhas": [_serializar(r) for r in linhas]}


@router.get("/base.csv")
def base_csv(f: Filtros = Depends(filtros), status: str | None = None, faixa: str | None = None):
    where, params = _where_base(f, status, faixa)
    repo = _repo()
    try:
        linhas = repo.consultar_base(where, params, colunas=", ".join(c for c, _ in COLUNAS_BASE))
    finally:
        repo.close()

    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow([titulo for _, titulo in COLUNAS_BASE])
    for row in linhas:
        valores = []
        for c, _ in COLUNAS_BASE:
            v = row[c]
            if isinstance(v, bool):
                v = "Sim" if v else "Não"
            elif isinstance(v, datetime.date):
                v = v.strftime("%d/%m/%Y")
            elif hasattr(v, "as_tuple"):
                v = f"{v:.2f}".replace(".", ",")
            valores.append("" if v is None else v)
        escritor.writerow(valores)
    nome = f"aging_oportunidades_{datetime.date.today():%Y%m%d}.csv"
    # BOM pro Excel abrir os acentos certo
    return StreamingResponse(iter(["﻿" + saida.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{nome}"'})


# ── admin (com login) ─────────────────────────────────────────
def _rodar_sync(completa: bool):
    try:
        odoo = OdooJson2Client(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_API_KEY"])
        sincronizar(os.environ["POSTGRES_DSN"], odoo, completa=completa)
    except Exception as exc:
        logger.exception("falha na sincronizacao manual do aging")
        notificar_falha("ApontaSAP: falha na sincronizacao manual do aging", str(exc))


@router_admin.post("/sincronizar")
def sincronizar_agora(background_tasks: BackgroundTasks, completa: bool = False):
    background_tasks.add_task(_rodar_sync, completa)
    return {"iniciado": True, "completa": completa}


@router_admin.get("/syncs")
def ultimas_syncs():
    repo = _repo()
    try:
        return {"syncs": [_serializar(s) for s in repo.ultimas_syncs(20)]}
    finally:
        repo.close()
