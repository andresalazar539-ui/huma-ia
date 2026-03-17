# ================================================================
# huma/app.py — Entry point do FastAPI
# ================================================================

import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "https://app.humaia.com.br",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID middleware
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())[:8]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    # Error handler global
    @app.exception_handler(Exception)
    async def global_error_handler(request: Request, exc: Exception):
        log.error(f"Erro não tratado | {exc}")
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
