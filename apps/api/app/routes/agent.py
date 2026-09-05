from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, Field
from app.core.config import get_settings

router = APIRouter(prefix="/v1/agent", tags=["agent"])


class AgentRequest(BaseModel):
    mode: Literal["ask", "plan", "build", "debug", "review", "deploy"] = "ask"
    prompt: str = Field(min_length=1, max_length=12000)
    project_name: str = Field(default="Untitled project", max_length=120)


class AgentResponse(BaseModel):
    mode: str
    provider: str
    model: str
    message: str
    requires_approval: bool = False


@router.post("/run", response_model=AgentResponse)
def run_agent(request: AgentRequest) -> AgentResponse:
    settings = get_settings()
    approval = request.mode == "deploy"
    return AgentResponse(
        mode=request.mode,
        provider=settings.aquila_provider,
        model=settings.aquila_model,
        requires_approval=approval,
        message=(
            f"Aquila bootstrap received a {request.mode.upper()} request for "
            f"{request.project_name!r}. The live coding provider is not connected yet. "
            "This V0.1 endpoint proves the product/API contract while preserving the "
            "approval boundary for deployment actions."
        ),
    )
