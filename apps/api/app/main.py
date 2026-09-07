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
from app.security.workspace_ownership import ensure_workspace_ownership_schema
from app.security.workspace_scope import WorkspaceOwnershipMiddleware

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.workspace_ownership_enforced:
        # Fail closed in staging if durable ownership cannot be initialized.
        ensure_workspace_ownership_schema()
    yield


app = FastAPI(
    title=f"{settings.app_name} API",
    version="0.1.8",
    description="Aquila routing, authentication, RBAC and ownership-scoped guarded software engineering runtime for Inzozi Code",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Middleware is added inside-out by Starlette. Auth must run before ownership so
# WorkspaceOwnershipMiddleware receives an authenticated request.state.principal.
app.add_middleware(WorkspaceOwnershipMiddleware)
app.add_middleware(AuthMiddleware)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(agent_router)
app.include_router(workspace_router)
app.include_router(git_remote_router)
app.include_router(git_pull_request_router)
app.include_router(github_app_router)


@app.get("/")
def root() -> dict:
    return {"name": settings.app_name, "version": "0.1.8", "agent": "Aquila"}
