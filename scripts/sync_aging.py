"""Sincroniza o CRM do Odoo com o dashboard de aging. Agendado no cron do container (docker/crontab):
incremental de hora em hora e completa 1x por dia (--completa).

Uso: python scripts/sync_aging.py [--completa]"""
import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from src.aging.sync import sincronizar  # noqa: E402
from src.alertas import notificar_falha  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402
from src.odoo_json2 import OdooJson2Client  # noqa: E402

logger = logging.getLogger("sync_aging")


def main():
    parser = argparse.ArgumentParser(description="Sincronizacao Odoo -> dashboard de aging")
    parser.add_argument("--completa", action="store_true",
                        help="Le todas as oportunidades e marca as excluidas no Odoo (default: incremental)")
    args = parser.parse_args()

    load_dotenv()
    dsn = os.environ["POSTGRES_DSN"]
    configurar_logging(dsn)
    odoo = OdooJson2Client(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_API_KEY"])
    try:
        print(sincronizar(dsn, odoo, completa=args.completa))
    except Exception as exc:
        logger.exception("falha na sincronizacao do aging")
        notificar_falha("ApontaSAP: falha na sincronizacao do aging", str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
