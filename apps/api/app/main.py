from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.product.store import ProductStoreError, ensure_product_schema
from app.routes.agent import router as agent_router
from app.routes.auth import router as auth_router
from app.routes.git_pull_request import router as git_pull_request_router
from app.routes.git_remote import router as git_remote_router
from app.routes.github_app import router as github_app_router
from app.routes.health import router as health_router
from app.routes.products import router as products_router
from app.routes.workspace import router as workspace_router
from app.security.auth import AuthenticationMiddleware
from app.security.identity import IdentityStoreError, ensure_identity_schema
from app.security.sessions import SessionStoreError, ensure_session_store_ready


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if settings.auth_enabled and settings.identity_backend == "database":
        try:
            ensure_identity_schema()
            ensure_product_schema()
        except (IdentityStoreError, ProductStoreError) as exc:
            raise RuntimeError("Required database schema initialization failed") from exc
    if settings.auth_enabled and settings.session_backend == "redis":
        try:
            ensure_session_store_ready()
        except SessionStoreError as exc:
            raise RuntimeError("Required session store initialization failed") from exc
    yield


app = FastAPI(title="Inzozi Code API", version="0.2.0", lifespan=lifespan)
app.add_middleware(AuthenticationMiddleware)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(products_router)
app.include_router(agent_router)
app.include_router(workspace_router)
app.include_router(git_remote_router)
app.include_router(git_pull_request_router)
app.include_router(github_app_router)


@app.get("/")
def root() -> dict:
    return {
        "name": "Inzozi Code API",
        "version": "0.2.0",
        "status": "ok",
    }
