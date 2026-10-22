"""Importacao unica da base historica do SharePoint (oportunidades ate 2025) pro dashboard de aging.
Idempotente - rodar de novo atualiza as mesmas linhas, nao duplica.

Uso: python scripts/importar_historico_aging.py data/historico_sharepoint.csv   (ou .xlsx)"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from src.aging.historico import importar_historico  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Importa o export do SharePoint pro dashboard de aging")
    parser.add_argument("arquivo", help="Export da lista de oportunidades (.csv ou .xlsx)")
    args = parser.parse_args()

    load_dotenv()
    dsn = os.environ["POSTGRES_DSN"]
    configurar_logging(dsn)
    print(importar_historico(dsn, args.arquivo))


if __name__ == "__main__":
    main()
