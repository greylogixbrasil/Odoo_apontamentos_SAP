import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class SapEnvConfig:
    nome: str
    base_url: str
    company_db: str
    username: str
    password: str


@dataclass
class HanaConfig:
    host: str
    port: int
    user: str
    password: str
    schema: str
    ssl_validate_certificate: bool = False


@dataclass
class OdooConfig:
    url: str
    db: str
    username: str
    api_key: str


@dataclass
class Config:
    postgres_dsn: str
    excel_path: str
    batch_size: int
    batch_sleep_seconds: float
    tolerancia_horas: float
    enviar_linhas_zero_hora: bool
    sap: SapEnvConfig
    hana: HanaConfig
    odoo: OdooConfig
    # Modulo de tempo nao alocado (src/nao_alocado.py): ActivityType usado no SAP (vazio = ainda
    # nao definido, nada pode ser enviado) e trava geral do envio (false = so preview).
    nao_alocado_idsap: str | None = None
    nao_alocado_envio_habilitado: bool = False


def _sap_env_config(env: str) -> SapEnvConfig:
    env = env.lower()
    if env not in ("test", "prod"):
        raise ValueError(f"Ambiente SAP invalido: {env!r} (use 'test' ou 'prod')")

    prefixo = "SAP_TEST_" if env == "test" else "SAP_PROD_"
    base_url = os.environ.get(f"{prefixo}BASE_URL")
    company_db = os.environ.get(f"{prefixo}COMPANY_DB")
    username = os.environ.get(f"{prefixo}USERNAME")
    password = os.environ.get(f"{prefixo}PASSWORD")

    faltando = [k for k, v in {
        f"{prefixo}BASE_URL": base_url,
        f"{prefixo}COMPANY_DB": company_db,
        f"{prefixo}USERNAME": username,
        f"{prefixo}PASSWORD": password,
    }.items() if not v]
    if faltando:
        raise ValueError(f"Variaveis de ambiente ausentes para SAP [{env}]: {', '.join(faltando)}")

    return SapEnvConfig(nome=env, base_url=base_url, company_db=company_db,
                         username=username, password=password)


def _hana_config(env: str) -> HanaConfig:
    host = os.environ.get("HANA_HOST")
    user = os.environ.get("HANA_USER")
    password = os.environ.get("HANA_PASSWORD")
    schema = os.environ.get(f"HANA_SCHEMA_{env.upper()}")

    faltando = [k for k, v in {
        "HANA_HOST": host, "HANA_USER": user, "HANA_PASSWORD": password,
        f"HANA_SCHEMA_{env.upper()}": schema,
    }.items() if not v]
    if faltando:
        raise ValueError(f"Variaveis de ambiente ausentes para HANA [{env}]: {', '.join(faltando)}")

    ssl_validate_certificate = os.environ.get("HANA_SSL_VALIDATE_CERTIFICATE", "false").lower() == "true"
    return HanaConfig(host=host, port=int(os.environ.get("HANA_PORT", "30015")),
                       user=user, password=password, schema=schema,
                       ssl_validate_certificate=ssl_validate_certificate)


def _odoo_config() -> OdooConfig:
    url = os.environ.get("ODOO_URL")
    db = os.environ.get("ODOO_DB")
    username = os.environ.get("ODOO_USERNAME")
    api_key = os.environ.get("ODOO_API_KEY")

    faltando = [k for k, v in {
        "ODOO_URL": url, "ODOO_DB": db, "ODOO_USERNAME": username, "ODOO_API_KEY": api_key,
    }.items() if not v]
    if faltando:
        raise ValueError(f"Variaveis de ambiente ausentes para Odoo: {', '.join(faltando)}")

    return OdooConfig(url=url, db=db, username=username, api_key=api_key)


def load_config(env: str | None = None, excel_path: str | None = None) -> Config:
    env = env or os.environ.get("SAP_ENV", "test")

    postgres_dsn = os.environ.get("POSTGRES_DSN")
    if not postgres_dsn:
        raise ValueError("POSTGRES_DSN nao definido no .env")

    return Config(
        postgres_dsn=postgres_dsn,
        excel_path=excel_path or os.environ.get("EXCEL_PATH", ""),
        batch_size=int(os.environ.get("BATCH_SIZE", "50")),
        batch_sleep_seconds=float(os.environ.get("BATCH_SLEEP_SECONDS", "2")),
        tolerancia_horas=float(os.environ.get("TOLERANCIA_HORAS", "0.01")),
        enviar_linhas_zero_hora=os.environ.get("ENVIAR_LINHAS_ZERO_HORA", "false").lower() == "true",
        sap=_sap_env_config(env),
        hana=_hana_config(env),
        odoo=_odoo_config(),
        nao_alocado_idsap=os.environ.get("NAO_ALOCADO_IDSAP", "").strip() or None,
        nao_alocado_envio_habilitado=os.environ.get("NAO_ALOCADO_ENVIO_HABILITADO", "false").lower() == "true",
    )
