import pytest
from pydantic import ValidationError

from app.agents.project_policy import ProjectAIRoutingPolicy, resolve_project_provider_route


def test_primary_provider_is_used_when_prompt_has_no_explicit_reference() -> None:
    policy = ProjectAIRoutingPolicy(primary_provider="chatgpt")
    route = resolve_project_provider_route(prompt="Review the service boundaries", mode="plan", policy=policy)
    assert route.primary == "chatgpt"
    assert route.requested == ("chatgpt",)


def test_design_mode_uses_project_design_specialist() -> None:
    policy = ProjectAIRoutingPolicy(design_provider="lovable")
    route = resolve_project_provider_route(prompt="Propose a cleaner dashboard", mode="design", policy=policy)
    assert route.primary == "lovable"


def test_review_mode_uses_project_reviewer() -> None:
    policy = ProjectAIRoutingPolicy(review_provider="chatgpt")
    route = resolve_project_provider_route(prompt="Review this migration", mode="review", policy=policy)
    assert route.primary == "chatgpt"


def test_explicit_provider_always_overrides_project_role_default() -> None:
    policy = ProjectAIRoutingPolicy(primary_provider="aquila", design_provider="lovable", review_provider="chatgpt")
    route = resolve_project_provider_route(prompt="@gemini review the API contract", mode="review", policy=policy)
    assert route.primary == "gemini"
    assert route.requested == ("gemini",)


def test_project_specialist_limit_caps_multi_provider_consultation() -> None:
    policy = ProjectAIRoutingPolicy(max_specialists=1)
    route = resolve_project_provider_route(
        prompt="@chatgpt @claude @gemini compare the architecture",
        mode="review",
        policy=policy,
    )
    assert route.primary == "aquila"
    assert route.specialists == ("chatgpt",)
    assert route.requested == ("chatgpt", "claude", "gemini")


def test_provider_aliases_are_normalized_in_policy() -> None:
    policy = ProjectAIRoutingPolicy(primary_provider="OpenAI", review_provider="gpt")
    assert policy.primary_provider == "chatgpt"
    assert policy.review_provider == "chatgpt"


def test_unknown_project_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ProjectAIRoutingPolicy(primary_provider="unknown-provider")
