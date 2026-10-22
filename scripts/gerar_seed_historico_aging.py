"""Gera db/migrations/0004_aging_historico_sharepoint.sql a partir do export da lista de oportunidades
do SharePoint (base historica ate 2025, que nao muda mais). O historico passa a viver no repositorio:
aplicar a migracao ja carrega o banco, sem precisar do arquivo no servidor.

Usa o mesmo mapeamento da importacao (src/aging/mapeamento.py::mapear_sharepoint) e grava como upsert
por (base_origem, id_origem) - aplicar duas vezes nao duplica.

Uso: python scripts/gerar_seed_historico_aging.py <export.csv|.xlsx> [saida.sql]"""
import argparse
import datetime
import sys
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.aging.historico import ler_export  # noqa: E402
from src.aging.mapeamento import mapear_sharepoint  # noqa: E402
from src.aging.repositorio import COLUNAS  # noqa: E402

SAIDA_PADRAO = RAIZ / "db" / "migrations" / "0004_aging_historico_sharepoint.sql"
LOTE = 500


def literal(valor) -> str:
    """Valor Python -> literal SQL (standard_conforming_strings, padrao do Postgres)."""
    if valor is None:
        return "NULL"
    if isinstance(valor, bool):
        return "TRUE" if valor else "FALSE"
    if isinstance(valor, (int, Decimal)):
        return str(valor)
    if isinstance(valor, datetime.datetime):
        return f"'{valor.isoformat()}'::timestamptz"
    if isinstance(valor, datetime.date):
        return f"'{valor.isoformat()}'::date"
    return "'" + str(valor).replace("'", "''") + "'"


def gerar(registros: list[dict], origem: str) -> str:
    atualizar = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUNAS if c not in ("base_origem", "id_origem"))
    partes = [
        "-- Base historica do SharePoint (oportunidades ate 2025) - GERADO, nao editar a mao.",
        f"-- Origem: {origem} ({len(registros)} linhas), gerado em {datetime.date.today():%d/%m/%Y} por",
        "-- scripts/gerar_seed_historico_aging.py com o mesmo mapeamento da importacao (mapear_sharepoint).",
        "-- Idempotente (upsert por base_origem + id_origem). Requer a 0003_aging_oportunidades.sql antes.",
        "-- Uso: docker exec -i sap_import_postgres psql -U sap_import -d sap_import -v ON_ERROR_STOP=1 -f - "
        "< db/migrations/0004_aging_historico_sharepoint.sql",
        "",
        "BEGIN;",
        "",
    ]
    for inicio in range(0, len(registros), LOTE):
        lote = registros[inicio:inicio + LOTE]
        valores = ",\n".join("(" + ", ".join(literal(r[c]) for c in COLUNAS) + ")" for r in lote)
        partes.append(f"INSERT INTO aging_oportunidade ({', '.join(COLUNAS)}) VALUES\n{valores}\n"
                      f"ON CONFLICT (base_origem, id_origem) DO UPDATE SET {atualizar}, "
                      f"excluida_na_origem = false, atualizado_em = now();\n")
    partes += [
        "-- registra a carga (uma vez) pro painel mostrar quando o historico entrou",
        "INSERT INTO aging_sync_log (tipo, finalizado_em, status, lidos, inseridos)",
        f"SELECT 'historico', now(), 'ok', {len(registros)}, {len(registros)}",
        "WHERE NOT EXISTS (SELECT 1 FROM aging_sync_log WHERE tipo = 'historico' AND status = 'ok');",
        "",
        "COMMIT;",
        "",
    ]
    return "\n".join(partes)


def main():
    parser = argparse.ArgumentParser(description="Gera a carga SQL do historico do SharePoint pro aging")
    parser.add_argument("arquivo", help="Export da lista de oportunidades (.csv ou .xlsx)")
    parser.add_argument("saida", nargs="?", default=str(SAIDA_PADRAO))
    args = parser.parse_args()

    registros = [mapear_sharepoint(linha) for linha in ler_export(args.arquivo)]
    ids = [r["id_origem"] for r in registros]
    if len(ids) != len(set(ids)):
        raise SystemExit("export com ID repetido - a chave (base_origem, id_origem) precisa ser unica")
    Path(args.saida).write_text(gerar(registros, Path(args.arquivo).name), encoding="utf-8", newline="\n")
    print(f"{len(registros)} oportunidades -> {args.saida}")


if __name__ == "__main__":
    main()
