from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.routes.agent import router as agent_router
from app.routes.auth import router as auth_router
from app.routes.git_pull_request import router as git_pull_request_router
from app.routes.git_remote import router as git_remote_router
from app.routes.github_app import router as github_app_router
from app.routes.health import router as health_router
from app.routes.workspace import router as workspace_router
from app.security.auth import AuthMiddleware
from app.security.redis_controls import (
    AuthStateUnavailableError,
    close_auth_state_store,
    init_auth_state_store,
)
from app.security.workspace_ownership import ensure_workspace_ownership_schema
from app.security.workspace_scope import WorkspaceOwnershipMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    try:
        if settings.auth_enabled:
            # Fail closed when durable auth state cannot initialize.
            try:
                await init_auth_state_store()
            except AuthStateUnavailableError as exc:
                raise RuntimeError("Authentication state service unavailable") from exc
        if settings.workspace_ownership_enforced:
            # Fail closed in staging if durable ownership cannot be initialized.
            ensure_workspace_ownership_schema()
        yield
    finally:
        # Always close Redis even if later startup steps fail after auth-store init.
        await close_auth_state_store()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.9",
        description="Aquila routing, authentication, RBAC and ownership-scoped guarded software engineering runtime for Inzozi Code",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8080", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Middleware is added inside-out by Starlette. Auth must run before ownership so
    # WorkspaceOwnershipMiddleware receives an authenticated request.state.principal.
    application.add_middleware(WorkspaceOwnershipMiddleware)
    application.add_middleware(AuthMiddleware)

    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(agent_router)
    application.include_router(workspace_router)
    application.include_router(git_remote_router)
    application.include_router(git_pull_request_router)
    application.include_router(github_app_router)

    @application.get("/")
    def root() -> dict:
        current = get_settings()
        return {"name": current.app_name, "version": "0.1.9", "agent": "Aquila"}

    return application


app = create_app()
