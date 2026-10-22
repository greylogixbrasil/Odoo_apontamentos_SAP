"""Converte registros das duas bases (Odoo crm.lead e export da lista do SharePoint) pro
modelo interno da tabela aging_oportunidade. So transformacao - sem acesso a nada."""
import datetime
import re
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")

# Campos lidos do crm.lead (confirmados com fields_get no Odoo 19)
CAMPOS_ODOO = [
    "name", "partner_id", "user_id", "team_id", "expected_revenue", "probability", "stage_id",
    "active", "won_status", "lost_reason_id", "create_date", "date_closed", "date_deadline",
    "date_last_stage_update", "write_date", "tag_ids", "x_studio_id",
]

# Equipes do Odoo sem vertical propria - a vertical e inferida na view aging_base
EQUIPES_GENERICAS = {"sales", "vendas"}

_CODIGO = re.compile(r"^C?(\d{2})\.(\d+)$", re.IGNORECASE)


def normalizar_codigo(codigo) -> str | None:
    """Mesmo codigo nas duas bases tem zeros diferentes: C24.0474 (SharePoint) = C24.474 (Odoo).
    Normaliza pra 'AA.N' sem o C e sem zeros a esquerda. Vazio/'0' (sem codigo) -> None."""
    texto = str(codigo or "").strip()
    m = _CODIGO.match(texto)
    if not m:
        return None if texto in ("", "0") else texto.upper()
    return f"{m.group(1)}.{int(m.group(2))}"


def vertical_da_equipe(equipe: str | None) -> str | None:
    """'Comercial P&P' -> 'P&P'. Equipe generica (Sales/Vendas) ou vazia -> None."""
    if not equipe:
        return None
    nome = equipe.strip()
    if nome.lower() in EQUIPES_GENERICAS:
        return None
    return re.sub(r"^Comercial\s+", "", nome, flags=re.IGNORECASE)


def status_odoo(registro: dict) -> str:
    """won_status (Odoo 19) ja traz won/lost/pending. Pendente arquivada (active=False sem
    ganho nem perda) nao e oportunidade em andamento - fica de fora da analise."""
    if registro.get("won_status") == "won":
        return "ganha"
    if registro.get("won_status") == "lost":
        return "perdida"
    return "aberta" if registro.get("active", True) else "arquivada"


def _nome(m2o):
    return m2o[1] if m2o else None


def _utc_para_data_local(texto) -> datetime.date | None:
    """Datetime do Odoo vem em UTC ('YYYY-MM-DD HH:MM:SS') - o dia que vale e o de Sao Paulo."""
    if not texto:
        return None
    dt = datetime.datetime.strptime(str(texto)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(FUSO).date()


def _utc(texto) -> datetime.datetime | None:
    if not texto:
        return None
    return datetime.datetime.strptime(str(texto)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)


def mapear_odoo(registro: dict, tags_por_id: dict[int, str] | None = None,
                empresa_por_contato: dict[int, str] | None = None) -> dict:
    """empresa_por_contato: res.partner.id -> nome da empresa (commercial_partner_id). Sem ele, o
    cliente fica com o nome de exibicao do Odoo, que pra contato vem 'Empresa, Empresa, Contato'."""
    status = status_odoo(registro)
    parceiro = registro.get("partner_id")
    cliente = (empresa_por_contato or {}).get(parceiro[0]) if parceiro else None
    equipe = _nome(registro.get("team_id"))
    fechamento, fonte = None, None
    if status in ("ganha", "perdida"):
        for campo in ("date_closed", "date_last_stage_update", "write_date"):
            if registro.get(campo):
                fechamento, fonte = _utc_para_data_local(registro[campo]), f"Odoo ({campo})"
                break
    tags = [(tags_por_id or {}).get(t, str(t)) for t in registro.get("tag_ids") or []]
    return {
        "base_origem": "Odoo",
        "id_origem": str(registro["id"]),
        "codigo": registro.get("x_studio_id") or None,
        "codigo_norm": normalizar_codigo(registro.get("x_studio_id")),
        "nome": registro.get("name"),
        "cliente": cliente or _nome(parceiro),
        "responsavel": _nome(registro.get("user_id")),
        "equipe": equipe,
        "vertical": vertical_da_equipe(equipe),
        "status": status,
        "estagio": _nome(registro.get("stage_id")),
        "data_criacao": _utc_para_data_local(registro.get("create_date")),
        "data_fechamento": fechamento,
        "fonte_data_fechamento": fonte,
        "data_prevista_fechamento": datetime.date.fromisoformat(registro["date_deadline"])
                                    if registro.get("date_deadline") else None,
        "valor": Decimal(str(registro.get("expected_revenue") or 0)).quantize(Decimal("0.01")),
        "probabilidade": Decimal(str(registro.get("probability") or 0)).quantize(Decimal("0.01")),
        "motivo_perda": _nome(registro.get("lost_reason_id")),
        "tags": ", ".join(tags) or None,
        "chance": None,
        "write_date_origem": _utc(registro.get("write_date")),
    }


# ── SharePoint (export CSV/Excel da lista de oportunidades) ─────────────────────

def _data_sharepoint(texto) -> datetime.date | None:
    """Export da lista: data americana, com ou sem hora ('12/11/2025 5:32 PM', '1/26/2026').
    Vindo de Excel pode chegar como datetime."""
    if texto is None or texto == "":
        return None
    if isinstance(texto, datetime.datetime):
        return texto.date()
    if isinstance(texto, datetime.date):
        return texto
    data = str(texto).strip().split(" ")[0]
    if not data:
        return None
    mes, dia, ano = data.split("/")
    return datetime.date(int(ano), int(mes), int(dia))


def _datetime_sharepoint(texto) -> datetime.datetime | None:
    if not texto:
        return None
    if isinstance(texto, datetime.datetime):
        return texto.replace(tzinfo=FUSO) if texto.tzinfo is None else texto
    dt = datetime.datetime.strptime(str(texto).strip(), "%m/%d/%Y %I:%M %p")
    return dt.replace(tzinfo=FUSO)


def _valor_sharepoint(texto) -> Decimal | None:
    """'R$ 1,061,284.32' -> Decimal('1061284.32'). Vazio -> None."""
    if texto is None or str(texto).strip() == "":
        return None
    if isinstance(texto, (int, float, Decimal)):
        return Decimal(str(texto)).quantize(Decimal("0.01"))
    limpo = re.sub(r"[^\d.\-]", "", str(texto).replace(",", ""))
    try:
        return Decimal(limpo).quantize(Decimal("0.01")) if limpo else None
    except InvalidOperation:
        return None


def _texto(valor) -> str | None:
    texto = str(valor).strip() if valor is not None else ""
    return texto or None


# Colunas do export do SharePoint -> campos internos (mapeamento mostrado no README do modulo)
def mapear_sharepoint(linha: dict) -> dict:
    estagio = _texto(linha.get("Estagio"))
    status = {"ganha": "ganha", "perdida": "perdida"}.get((estagio or "").lower(), "aberta")

    fechamento, fonte = None, None
    if status != "aberta":
        fechamento = _data_sharepoint(linha.get("FechamentoReal"))
        fonte = "SharePoint (FechamentoReal)"
        if fechamento is None:
            fechamento = _data_sharepoint(linha.get("Modificado"))
            fonte = "SharePoint (Modificado) - sem FechamentoReal"

    valor = _valor_sharepoint(linha.get("ValFechado")) if status == "ganha" else None
    if valor is None:
        valor = _valor_sharepoint(linha.get("ValEstimado"))

    return {
        "base_origem": "SharePoint",
        "id_origem": str(linha["ID"]).strip(),
        "codigo": _texto(linha.get("Código")),
        "codigo_norm": normalizar_codigo(linha.get("Código")),
        "nome": _texto(linha.get("Título")),
        "cliente": _texto(linha.get("Cliente")),
        "responsavel": _texto(linha.get("Responsavel")),
        "equipe": None,
        "vertical": _texto(linha.get("Vertical")),
        "status": status,
        "estagio": estagio,
        "data_criacao": _data_sharepoint(linha.get("Criado")),
        "data_fechamento": fechamento,
        "fonte_data_fechamento": fonte,
        "data_prevista_fechamento": _data_sharepoint(linha.get("FechamentoEstimado")),
        "valor": valor,
        "probabilidade": None,
        "motivo_perda": _texto(linha.get("Perdidas")),
        "tags": None,
        "chance": _texto(linha.get("Chance")),
        "write_date_origem": _datetime_sharepoint(linha.get("Modificado")),
    }
