"""Sincronizacao Odoo (crm.lead) -> aging_oportunidade.

 * incremental (de hora em hora): oportunidades com write_date desde a ultima sync ok, com 10 min
   de margem de seguranca. Sem nenhuma sync ok anterior, vira completa.
 * completa (1x por dia): le tudo e marca como excluido o que nao existe mais no Odoo.

So uma sync roda por vez (advisory lock no Postgres). Se falhar, o dashboard segue mostrando o
ultimo dado gravado - nada e apagado antes da leitura terminar."""
import datetime
import logging

from src.aging.mapeamento import CAMPOS_ODOO, mapear_odoo
from src.aging.repositorio import AgingRepositorio
from src.odoo_json2 import OdooJson2Client

logger = logging.getLogger(__name__)

MARGEM = datetime.timedelta(minutes=10)
# Inclui as perdidas e arquivadas (active=False) - sem isso o Odoo so devolve as ativas
CONTEXTO = {"active_test": False}
DOMINIO_BASE = [["type", "=", "opportunity"]]


def sincronizar(dsn: str, odoo: OdooJson2Client, completa: bool = False) -> dict:
    repo = AgingRepositorio(dsn)
    try:
        if not repo.tentar_lock():
            logger.info("sync do aging ja em andamento - pulando")
            return {"status": "pulado", "motivo": "outra sincronizacao em andamento"}
        try:
            return _executar(repo, odoo, completa)
        finally:
            repo.liberar_lock()
    finally:
        repo.close()


def _empresa_por_contato(odoo: OdooJson2Client, ids: set[int]) -> dict[int, str]:
    """Cliente da oportunidade = empresa do contato (commercial_partner_id), nao o contato."""
    empresas = {}
    ids = sorted(ids)
    for inicio in range(0, len(ids), 1000):
        for p in odoo.chamar("res.partner", "search_read", domain=[["id", "in", ids[inicio:inicio + 1000]]],
                             fields=["commercial_partner_id"], context=CONTEXTO):
            if p.get("commercial_partner_id"):
                empresas[p["id"]] = p["commercial_partner_id"][1]
    return empresas


def _executar(repo: AgingRepositorio, odoo: OdooJson2Client, completa: bool) -> dict:
    ultima = None if completa else repo.ultima_sync_ok()
    tipo = "incremental" if ultima else "completa"
    log_id = repo.iniciar_log(tipo)
    try:
        dominio = list(DOMINIO_BASE)
        if ultima:
            desde = (ultima["iniciado_em"] - MARGEM).astimezone(datetime.timezone.utc)
            dominio.append(["write_date", ">=", desde.strftime("%Y-%m-%d %H:%M:%S")])

        registros = odoo.search_read_paginado("crm.lead", dominio, CAMPOS_ODOO, context=CONTEXTO)
        tags = {t["id"]: t["name"] for t in odoo.search_read_paginado("crm.tag", [], ["name"])}
        empresas = _empresa_por_contato(odoo, {r["partner_id"][0] for r in registros if r.get("partner_id")})
        inseridos, atualizados = repo.upsert([mapear_odoo(r, tags, empresas) for r in registros])

        excluidos = 0
        if tipo == "completa":
            ids = [str(i) for i in odoo.search_ids("crm.lead", DOMINIO_BASE, context=CONTEXTO)]
            if not ids:
                raise RuntimeError("Odoo devolveu zero oportunidades na sync completa - abortado pra nao "
                                   "marcar a base inteira como excluida")
            excluidos = repo.marcar_excluidos("Odoo", ids)

        resumo = {"status": "ok", "tipo": tipo, "lidos": len(registros), "inseridos": inseridos,
                  "atualizados": atualizados, "marcados_excluidos": excluidos}
        repo.finalizar_log(log_id, "ok", len(registros), inseridos, atualizados, excluidos)
        logger.info("sync do aging concluida: %s", resumo)
        return resumo
    except Exception as exc:
        repo.finalizar_log(log_id, "falhou", erro=str(exc)[:2000])
        raise
