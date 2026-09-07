import os

from app.agents.providers import (
    parse_provider_mentions,
    provider_is_configured,
    resolve_provider_route,
)


def test_provider_mentions_are_normalized_without_treating_email_as_provider() -> None:
    recognized, unknown = parse_provider_mentions(
        "Ask @OpenAI and @lovable, email design@example.com, then note @madeup."
    )
    assert recognized == ("chatgpt", "lovable")
    assert unknown == ("madeup",)


def test_single_provider_becomes_primary() -> None:
    route = resolve_provider_route("@chatgpt review this migration")
    assert route.primary == "chatgpt"
    assert route.requested == ("chatgpt",)
    assert route.specialists == ()


def test_multiple_providers_keep_aquila_as_manager() -> None:
    route = resolve_provider_route("@chatgpt @claude review this architecture")
    assert route.primary == "aquila"
    assert route.requested == ("chatgpt", "claude")
    assert route.specialists == ("chatgpt", "claude")


def test_unknown_mentions_do_not_silently_change_provider() -> None:
    route = resolve_provider_route("Ask @frontend-team to check spacing")
    assert route.primary == "aquila"
    assert route.requested == ()
    assert route.unknown_mentions == ("frontend-team",)


def test_openai_provider_configuration_comes_only_from_process_environment(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert provider_is_configured("chatgpt") is False

    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder-not-a-real-key")
    assert provider_is_configured("chatgpt") is True


def test_registered_external_connectors_are_not_faked_as_available(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder-not-a-real-key")
    assert provider_is_configured("lovable") is False
    assert provider_is_configured("cursor") is False
    assert provider_is_configured("claude") is False
    assert provider_is_configured("gemini") is False
