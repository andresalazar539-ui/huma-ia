# ================================================================
# huma/app.py — Entry point do FastAPI
# ================================================================

import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from huma.config import APP_TITLE, APP_VERSION, APP_DESCRIPTION
from huma.routes.api import router
from huma.utils.logger import get_logger

log = get_logger("app")


def create_app() -> FastAPI:
    """Cria e configura a aplicação FastAPI."""

    app = FastAPI(
        title=APP_TITLE,
        version=APP_VERSION,
        description=APP_DESCRIPTION,
    )

    # CORS — Sprint 1 / item 14: restringido (antes era "*")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://localhost:5173",
            "https://app.humaia.com.br",
            "https://*.up.railway.app",
            "https://andresalazar539-ui.github.io",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Webhook-Secret",
            "X-Twilio-Signature",
            "x-signature",
            "x-request-id",
            "X-Playground-Token",
        ],
    )

    # Request ID middleware
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())[:8]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    # Sprint 1 / item 15 — handler dedicado pra HTTPException antes do generic.
    # Antes o handler genérico engolia HTTPException e transformava 404 em 500.
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"status": "error", "detail": exc.detail},
        )

    # Error handler global — só pra exceções inesperadas (não-HTTP)
    @app.exception_handler(Exception)
    async def global_error_handler(request: Request, exc: Exception):
        # Garantia adicional: se chegou aqui sendo HTTPException, delega pro handler certo
        if isinstance(exc, (HTTPException, StarletteHTTPException)):
            return await http_exception_handler(request, exc)
        log.error(f"Erro não tratado | {type(exc).__name__}: {exc}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": "Erro interno. Tente novamente."},
        )

    # Rotas
    app.include_router(router)

    # Startup
    @app.on_event("startup")
    async def startup():
        log.info(f"HUMA IA v{APP_VERSION} iniciando...")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("huma.app:app", host="0.0.0.0", port=8000, reload=True)
