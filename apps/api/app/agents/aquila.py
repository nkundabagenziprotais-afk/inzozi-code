from __future__ import annotations

from dataclasses import dataclass

from agents import Agent, RunConfig, Runner, set_default_openai_key

from app.agents.providers import PROVIDERS, ProviderRoute, provider_is_configured, unavailable_references
from app.agents.workspace_tools import (
    AquilaContext,
    allowed_actions_for_mode,
    create_preflight_checkpoint,
    fetch_git_diff,
    tools_for_mode,
)
from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class AquilaRunOutput:
    message: str
    provider_alias: str
    provider: str
    model: str
    checkpoint_id: str | None
    git_diff: str
    notices: tuple[str, ...]
    status: str = "completed"


def _mode_instruction(mode: str) -> str:
    if mode == "ask":
        return "Answer questions using repository evidence. Do not edit files or run mutating actions."
    if mode == "plan":
        return "Produce an implementation plan grounded in repository evidence. Do not edit files."
    if mode == "review":
        return "Review architecture and code critically. Do not edit files."
    if mode == "build":
        return "Implement the requested change. Inspect before editing, make focused changes, and run relevant approved checks."
    if mode == "debug":
        return "Diagnose the failure from evidence, make the smallest safe fix, and run relevant approved checks."
    return "Prepare and review a deployment plan only. Do not deploy, push, commit, migrate production data, or change production state."


def _base_instructions(mode: str, project_name: str, unavailable: tuple[str, ...]) -> str:
    missing = ", ".join(f"@{alias}" for alias in unavailable) or "none"
    return f"""You are Aquila, the Inzozi Code software engineering orchestrator for {project_name}.

Operating rules:
- GitHub is the source of truth; never claim a commit, push, pull request, deployment, or rollback happened unless a tool actually performed it.
- Use repository tools for evidence instead of guessing file contents.
- Never request, reveal, search for, or print secrets, private keys, API tokens, .env values, or credentials.
- Never use or suggest destructive production commands. There are no production deployment tools in this run.
- Existing files should be read before they are rewritten so optimistic locking can protect concurrent changes.
- Build/Debug runs already receive an automatic preflight checkpoint when a workspace is connected.
- If a referenced specialist is unavailable, say so. Never impersonate or claim to have consulted it.
- Keep changes practical, maintainable, responsive, secure, and reviewable.

Current mode: {mode.upper()}
Mode contract: {_mode_instruction(mode)}
Unavailable referenced providers/connectors: {missing}
"""


def _chatgpt_specialist(model: str) -> Agent[AquilaContext]:
    return Agent[AquilaContext](
        name="ChatGPT specialist",
        instructions=(
            "You are a bounded second-opinion software engineering specialist. Analyze only the input "
            "the Aquila manager gives you. Identify concrete risks, alternatives, or improvements. Do not "
            "claim to have inspected files or run commands unless the manager supplied that evidence."
        ),
        model=model,
    )


def _openai_key(settings: Settings) -> str | None:
    if not settings.openai_api_key:
        return None
    value = settings.openai_api_key.get_secret_value()
    return value or None


async def run_aquila_workflow(
    *,
    mode: str,
    prompt: str,
    project_name: str,
    workspace_id: str | None,
    route: ProviderRoute,
    settings: Settings | None = None,
) -> AquilaRunOutput:
    settings = settings or get_settings()
    primary = PROVIDERS[route.primary]
    unavailable = unavailable_references(route.requested or (route.primary,), settings)

    if route.primary == "aquila" and settings.aquila_provider == "mock":
        return AquilaRunOutput(
            message=(
                "Aquila is running in mock mode. Provider routing is active, but a live model key must be "
                "mounted into the API runtime before model execution can start."
            ),
            provider_alias="aquila",
            provider="mock",
            model="mock-v0",
            checkpoint_id=None,
            git_diff="",
            notices=tuple(f"@{alias} is not configured" for alias in unavailable),
            status="mock",
        )

    if not primary.implemented or not provider_is_configured(route.primary, settings):
        return AquilaRunOutput(
            message=(
                f"@{route.primary} is registered in Inzozi Code but its connector is not active yet. "
                "No other provider was silently substituted."
            ),
            provider_alias=route.primary,
            provider=primary.provider,
            model="unconfigured",
            checkpoint_id=None,
            git_diff="",
            notices=(f"@{route.primary} connector is not configured",),
            status="unavailable",
        )

    if primary.provider != "openai":
        return AquilaRunOutput(
            message=f"@{route.primary} is not executable in this milestone.",
            provider_alias=route.primary,
            provider=primary.provider,
            model="unconfigured",
            checkpoint_id=None,
            git_diff="",
            notices=("Provider adapter pending",),
            status="unavailable",
        )

    key = _openai_key(settings)
    if not key:
        return AquilaRunOutput(
            message="OpenAI is selected but OPENAI_API_KEY is not available to the API runtime.",
            provider_alias=route.primary,
            provider="openai",
            model=settings.openai_default_model,
            checkpoint_id=None,
            git_diff="",
            notices=("Mount the key as a server-side secret; never send it from the browser.",),
            status="unavailable",
        )

    set_default_openai_key(key, use_for_tracing=True)

    model = settings.aquila_model if route.primary == "aquila" else settings.openai_default_model
    context = AquilaContext(
        workspace_id=workspace_id,
        workspace_service_url=settings.workspace_service_url,
        project_name=project_name,
        mode=mode,
        selected_provider=route.primary,
        allowed_actions=allowed_actions_for_mode(mode),
    )

    checkpoint_id: str | None = None
    if mode in {"build", "debug"} and workspace_id:
        checkpoint_id = await create_preflight_checkpoint(context, f"Aquila {mode} preflight")

    base_tools = tools_for_mode(mode, bool(workspace_id))
    specialist_tools = []
    if route.primary == "aquila" and "chatgpt" in route.specialists and provider_is_configured("chatgpt", settings):
        specialist_tools.append(
            _chatgpt_specialist(settings.openai_default_model).as_tool(
                tool_name="consult_chatgpt",
                tool_description=(
                    "Ask the ChatGPT specialist for a bounded second opinion on architecture, code, UI, "
                    "debugging, or review evidence gathered by Aquila."
                ),
            )
        )

    if route.primary == "chatgpt":
        name = "ChatGPT project specialist"
        instructions = (
            _base_instructions(mode, project_name, unavailable)
            + "\nYou are the explicitly requested @chatgpt specialist. Work directly on the user's task within the same guarded tool policy."
        )
    else:
        name = "Aquila"
        instructions = _base_instructions(mode, project_name, unavailable)

    agent = Agent[AquilaContext](
        name=name,
        instructions=instructions,
        model=model,
        tools=[*base_tools, *specialist_tools],
    )

    routing_context = ""
    if route.requested:
        routing_context = "\n\nProvider mentions requested by the user: " + ", ".join(f"@{alias}" for alias in route.requested)
    if route.unknown_mentions:
        routing_context += "\nUnknown @mentions (do not treat as providers): " + ", ".join(f"@{alias}" for alias in route.unknown_mentions)

    result = await Runner.run(
        agent,
        input=prompt + routing_context,
        context=context,
        max_turns=settings.aquila_max_turns,
        run_config=RunConfig(trace_include_sensitive_data=False),
    )

    diff = await fetch_git_diff(context) if workspace_id else ""
    notices = [f"@{alias} is referenced but not configured" for alias in unavailable]
    if checkpoint_id:
        notices.append(f"Preflight checkpoint created: {checkpoint_id[:8]}")

    return AquilaRunOutput(
        message=str(result.final_output),
        provider_alias=route.primary,
        provider="openai",
        model=model,
        checkpoint_id=checkpoint_id,
        git_diff=diff,
        notices=tuple(notices),
    )
