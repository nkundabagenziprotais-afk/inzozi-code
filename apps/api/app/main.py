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

settings = get_settings()

app = FastAPI(
    title=f"{settings.app_name} API",
    version="0.1.7",
    description="Aquila provider routing, staged authentication, RBAC and guarded software engineering runtime for Inzozi Code",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
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
    return {"name": settings.app_name, "version": "0.1.7", "agent": "Aquila"}
