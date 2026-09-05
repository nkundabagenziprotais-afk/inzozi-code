from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Iterable

from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class ProviderDefinition:
    alias: str
    display_name: str
    provider: str
    kind: str
    transport: str
    implemented: bool
    capabilities: tuple[str, ...]
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderRoute:
    primary: str
    requested: tuple[str, ...]
    specialists: tuple[str, ...]
    unknown_mentions: tuple[str, ...]


_PROVIDER_DEFINITIONS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition(
        alias="aquila",
        display_name="Aquila",
        provider="openai",
        kind="orchestrator",
        transport="agents-sdk",
        implemented=True,
        capabilities=("repository", "planning", "coding", "review", "debug"),
        aliases=("inzozi",),
    ),
    ProviderDefinition(
        alias="chatgpt",
        display_name="ChatGPT",
        provider="openai",
        kind="model",
        transport="agents-sdk",
        implemented=True,
        capabilities=("reasoning", "coding", "review"),
        aliases=("openai", "gpt"),
    ),
    ProviderDefinition(
        alias="claude",
        display_name="Claude",
        provider="anthropic",
        kind="model",
        transport="provider-adapter",
        implemented=False,
        capabilities=("reasoning", "coding", "review"),
        aliases=("anthropic",),
    ),
    ProviderDefinition(
        alias="gemini",
        display_name="Gemini",
        provider="google",
        kind="model",
        transport="provider-adapter",
        implemented=False,
        capabilities=("reasoning", "coding", "multimodal"),
        aliases=("google",),
    ),
    ProviderDefinition(
        alias="lovable",
        display_name="Lovable",
        provider="lovable",
        kind="external_agent",
        transport="mcp-oauth",
        implemented=False,
        capabilities=("ui_design", "frontend", "prototype"),
    ),
    ProviderDefinition(
        alias="cursor",
        display_name="Cursor",
        provider="cursor",
        kind="external_agent",
        transport="acp",
        implemented=False,
        capabilities=("coding", "review", "agentic_workspace"),
    ),
    ProviderDefinition(
        alias="local",
        display_name="Local model",
        provider="local",
        kind="model",
        transport="openai-compatible",
        implemented=False,
        capabilities=("reasoning", "coding"),
        aliases=("selfhosted", "self-hosted"),
    ),
)

PROVIDERS: dict[str, ProviderDefinition] = {item.alias: item for item in _PROVIDER_DEFINITIONS}
_PROVIDER_ALIASES: dict[str, str] = {}
for definition in _PROVIDER_DEFINITIONS:
    _PROVIDER_ALIASES[definition.alias] = definition.alias
    for alias in definition.aliases:
        _PROVIDER_ALIASES[alias] = definition.alias

MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_-]{0,31})")


def normalize_provider_alias(value: str) -> str | None:
    return _PROVIDER_ALIASES.get(value.strip().lower())


def parse_provider_mentions(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return recognized provider mentions and unknown @mentions, preserving order."""
    recognized: list[str] = []
    unknown: list[str] = []
    for match in MENTION_RE.finditer(text):
        raw = match.group(1).lower()
        normalized = normalize_provider_alias(raw)
        target = recognized if normalized else unknown
        value = normalized or raw
        if value not in target:
            target.append(value)
    return tuple(recognized), tuple(unknown)


def resolve_provider_route(text: str) -> ProviderRoute:
    requested, unknown = parse_provider_mentions(text)
    if not requested:
        return ProviderRoute(primary="aquila", requested=(), specialists=(), unknown_mentions=unknown)
    if len(requested) == 1:
        return ProviderRoute(primary=requested[0], requested=requested, specialists=(), unknown_mentions=unknown)
    specialists = tuple(alias for alias in requested if alias != "aquila")
    return ProviderRoute(primary="aquila", requested=requested, specialists=specialists, unknown_mentions=unknown)


def _has_openai_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def provider_is_configured(alias: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    definition = PROVIDERS[alias]
    if not definition.implemented:
        return False
    if alias == "aquila" and settings.aquila_provider == "mock":
        return True
    if definition.provider == "openai":
        return _has_openai_key()
    return False


def _openai_model() -> str:
    return os.getenv("OPENAI_DEFAULT_MODEL", "gpt-5.6")


def provider_payload(definition: ProviderDefinition, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    if definition.alias == "aquila":
        configured_model = os.getenv("AQUILA_MODEL", settings.aquila_model)
        model = _openai_model() if configured_model == "mock-v0" and settings.aquila_provider != "mock" else configured_model
    elif definition.provider == "openai":
        model = _openai_model()
    else:
        model = None
    return {
        "alias": definition.alias,
        "mention": f"@{definition.alias}",
        "display_name": definition.display_name,
        "provider": definition.provider,
        "kind": definition.kind,
        "transport": definition.transport,
        "implemented": definition.implemented,
        "configured": provider_is_configured(definition.alias, settings),
        "model": model,
        "capabilities": list(definition.capabilities),
    }


def list_provider_payloads(settings: Settings | None = None) -> list[dict]:
    settings = settings or get_settings()
    return [provider_payload(item, settings) for item in _PROVIDER_DEFINITIONS]


def unavailable_references(aliases: Iterable[str], settings: Settings | None = None) -> tuple[str, ...]:
    settings = settings or get_settings()
    return tuple(alias for alias in aliases if alias in PROVIDERS and not provider_is_configured(alias, settings))
