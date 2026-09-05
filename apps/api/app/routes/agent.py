from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents.aquila import run_aquila_workflow
from app.agents.project_policy import ProjectAIRoutingPolicy, resolve_project_provider_route
from app.agents.providers import list_provider_payloads

router = APIRouter(prefix="/v1/agent", tags=["agent"])


class AgentRequest(BaseModel):
    mode: Literal["ask", "plan", "design", "build", "debug", "review", "deploy"] = "ask"
    prompt: str = Field(min_length=1, max_length=12000)
    project_name: str = Field(default="Untitled project", max_length=120)
    workspace_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    project_policy: ProjectAIRoutingPolicy | None = None


class AgentResponse(BaseModel):
    mode: str
    status: str
    provider_alias: str
    provider: str
    model: str
    message: str
    requested_providers: list[str]
    specialists: list[str]
    unknown_mentions: list[str]
    notices: list[str]
    checkpoint_id: str | None = None
    git_diff: str = ""
    requires_approval: bool = False


@router.get("/providers")
def list_providers() -> dict:
    return {"providers": list_provider_payloads()}


@router.post("/run", response_model=AgentResponse)
async def run_agent(request: AgentRequest) -> AgentResponse:
    route = resolve_project_provider_route(
        prompt=request.prompt,
        mode=request.mode,
        policy=request.project_policy,
    )
    try:
        result = await run_aquila_workflow(
            mode=request.mode,
            prompt=request.prompt,
            project_name=request.project_name,
            workspace_id=request.workspace_id,
            route=route,
        )
    except Exception as exc:
        # Do not leak provider payloads, credentials, or internal workspace details to the browser.
        raise HTTPException(status_code=502, detail="Aquila execution failed safely. Review server logs and retry.") from exc

    return AgentResponse(
        mode=request.mode,
        status=result.status,
        provider_alias=result.provider_alias,
        provider=result.provider,
        model=result.model,
        message=result.message,
        requested_providers=list(route.requested),
        specialists=list(route.specialists),
        unknown_mentions=list(route.unknown_mentions),
        notices=list(result.notices),
        checkpoint_id=result.checkpoint_id,
        git_diff=result.git_diff,
        requires_approval=request.mode == "deploy",
    )
