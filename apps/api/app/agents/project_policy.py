from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.agents.providers import ProviderRoute, normalize_provider_alias, parse_provider_mentions, resolve_provider_route


class ProjectAIRoutingPolicy(BaseModel):
    """Non-secret provider preferences for one project/workspace.

    In the alpha UI this policy is persisted in browser local storage per repository URL. It is sent
    with each run and validated server-side. It must never contain provider credentials.
    """

    primary_provider: str = "aquila"
    design_provider: str = "aquila"
    review_provider: str = "chatgpt"
    max_specialists: int = Field(default=2, ge=0, le=4)

    @field_validator("primary_provider", "design_provider", "review_provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        normalized = normalize_provider_alias(value)
        if not normalized:
            raise ValueError(f"Unknown provider alias: {value}")
        return normalized


def resolve_project_provider_route(
    *,
    prompt: str,
    mode: str,
    policy: ProjectAIRoutingPolicy | None,
) -> ProviderRoute:
    """Resolve explicit @mentions first, then fall back to project role preferences."""
    explicit = resolve_provider_route(prompt)
    if explicit.requested:
        if len(explicit.specialists) <= (policy.max_specialists if policy else 2):
            return explicit
        return ProviderRoute(
            primary=explicit.primary,
            requested=explicit.requested,
            specialists=explicit.specialists[: policy.max_specialists],
            unknown_mentions=explicit.unknown_mentions,
        )

    if not policy:
        return explicit

    if mode == "design":
        primary = policy.design_provider
    elif mode == "review":
        primary = policy.review_provider
    else:
        primary = policy.primary_provider

    _, unknown = parse_provider_mentions(prompt)
    return ProviderRoute(
        primary=primary,
        requested=(primary,),
        specialists=(),
        unknown_mentions=unknown,
    )
