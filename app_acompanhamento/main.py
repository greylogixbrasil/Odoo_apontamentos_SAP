import os

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware

from src.logging_setup import configurar_logging

configurar_logging(os.environ.get("POSTGRES_DSN", ""))

from app_acompanhamento.api.aging import router as aging_router  # noqa: E402
from app_acompanhamento.api.aging import router_admin as aging_admin_router  # noqa: E402
from app_acompanhamento.api.automacao import router as automacao_router  # noqa: E402
from app_acompanhamento.api.historico import router as historico_router  # noqa: E402
from app_acompanhamento.api.nao_alocado import router as nao_alocado_router  # noqa: E402
from app_acompanhamento.auth import exigir_login  # noqa: E402

# Login (HTTP Basic - ver app_acompanhamento/auth.py) aplicado rota a rota via LOGIN, em vez de
# na app inteira: o dashboard de aging (/aging e /api/aging/*) e PUBLICO de proposito - os
# colaboradores entram pelo link vindo de outra aplicacao, sem senha, e so le dados. Toda rota
# nova precisa de dependencies=LOGIN, a menos que tambem deva ser publica.
# O mount de /static (CSS/JS) sempre foi publico - StaticFiles fica fora das dependencies.
LOGIN = [Depends(exigir_login)]

app = FastAPI(title="ApontaSAP", version="1.0.0")

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.mount("/static", StaticFiles(directory="app_acompanhamento/static"), name="static")
templates = Jinja2Templates(directory="app_acompanhamento/templates")

app.include_router(historico_router, prefix="/api", dependencies=LOGIN)
app.include_router(automacao_router, prefix="/api", dependencies=LOGIN)
app.include_router(nao_alocado_router, prefix="/api", dependencies=LOGIN)
app.include_router(aging_admin_router, prefix="/api", dependencies=LOGIN)
app.include_router(aging_router, prefix="/api")  # publico


@app.get("/", dependencies=LOGIN)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/nao-alocado", dependencies=LOGIN)
async def nao_alocado(request: Request):
    return templates.TemplateResponse("nao_alocado.html", {"request": request})


@app.get("/aging")
async def aging(request: Request):
    """Publico - sem menu nem links pras telas do ApontaSAP."""
    return templates.TemplateResponse("aging.html", {"request": request})
