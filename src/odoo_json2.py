import requests

from src.retry import com_retry

# So falha de rede (antes de qualquer resposta) - seguro pra retry, este cliente so le.
_ERROS_TRANSITORIOS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)


class OdooJson2Error(Exception):
    pass


class OdooJson2Client:
    """Cliente minimo da API externa JSON-2 do Odoo (19+): POST /json/2/<modelo>/<metodo>,
    API key no header Authorization: bearer. Usado SOMENTE para leitura (modulo de aging do
    CRM) - o envio de apontamentos continua no cliente XML-RPC (src/odoo_client.py)."""

    def __init__(self, url: str, db: str, api_key: str, timeout: int = 120):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"bearer {api_key}",
            "X-Odoo-Database": db,
            "Content-Type": "application/json",
        })

    def versao(self) -> str:
        resp = com_retry(self.session.post, f"{self.url}/web/webclient/version_info",
                         json={"jsonrpc": "2.0", "params": {}}, timeout=30, excecoes=_ERROS_TRANSITORIOS)
        resp.raise_for_status()
        return resp.json()["result"]["server_version"]

    def chamar(self, modelo: str, metodo: str, **corpo):
        resp = com_retry(self.session.post, f"{self.url}/json/2/{modelo}/{metodo}", json=corpo,
                         timeout=self.timeout, excecoes=_ERROS_TRANSITORIOS, tentativas=4, espera_inicial=2.0)
        if not resp.ok:
            raise OdooJson2Error(f"Odoo {modelo}.{metodo}: HTTP {resp.status_code} - {resp.text[:500]}")
        return resp.json()

    def search_read_paginado(self, modelo: str, domain: list, fields: list[str], context: dict | None = None,
                             pagina: int = 1000) -> list[dict]:
        resultado, offset = [], 0
        while True:
            rows = self.chamar(modelo, "search_read", domain=domain, fields=fields, limit=pagina,
                               offset=offset, order="id", context=context or {})
            resultado.extend(rows)
            if len(rows) < pagina:
                return resultado
            offset += pagina

    def search_ids(self, modelo: str, domain: list, context: dict | None = None) -> list[int]:
        return self.chamar(modelo, "search", domain=domain, context=context or {})
