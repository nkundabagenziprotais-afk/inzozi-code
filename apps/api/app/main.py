from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import get_settings
from app.routes.agent import router as agent_router
from app.routes.health import router as health_router
from app.routes.workspace import router as workspace_router

settings = get_settings()

app = FastAPI(
    title=f"{settings.app_name} API",
    version="0.1.1",
    description="Aquila orchestration bootstrap for Inzozi Code",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(agent_router)
app.include_router(workspace_router)


@app.get("/")
def root() -> dict:
    return {"name": settings.app_name, "version": "0.1.1", "agent": "Aquila"}
