"""Agent card construction.

Every agent publishes one of these at ``/.well-known/agent-card.json``. The
card is the contract: name, version, transports, skills, and the security
scheme a caller must satisfy. The orchestrator does not hardcode any agent's
abilities - it resolves cards at start-up and dispatches by skill id.
"""

from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    APIKeySecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)

from prdforge.config import settings

PROTOCOL_VERSION = "1.0"
JSON_MODE = "application/json"
TEXT_MODE = "text/plain"

API_KEY_SCHEME_NAME = "prdforgeApiKey"


def api_key_scheme() -> SecurityScheme:
    cfg = settings().security
    return SecurityScheme(
        api_key_security_scheme=APIKeySecurityScheme(
            description="Shared secret for intra-mesh calls. Rotate per environment.",
            location="header",
            name=cfg.header,
        )
    )


def skill(
    skill_id: str,
    name: str,
    description: str,
    *,
    tags: list[str] | None = None,
    examples: list[str] | None = None,
    input_modes: list[str] | None = None,
    output_modes: list[str] | None = None,
) -> AgentSkill:
    return AgentSkill(
        id=skill_id,
        name=name,
        description=description,
        tags=tags or ["prd"],
        examples=examples or [],
        input_modes=input_modes or [JSON_MODE],
        output_modes=output_modes or [JSON_MODE],
    )


def build_card(
    *,
    name: str,
    description: str,
    url: str,
    skills: list[AgentSkill],
    version: str = "0.1.0",
    streaming: bool = True,
    default_input_modes: list[str] | None = None,
    default_output_modes: list[str] | None = None,
) -> AgentCard:
    cfg = settings()
    card = AgentCard(
        name=name,
        description=description,
        version=version,
        provider=AgentProvider(organization="PRD Forge", url="https://example.internal/prd-forge"),
        capabilities=AgentCapabilities(streaming=streaming, push_notifications=False),
        default_input_modes=default_input_modes or [JSON_MODE, TEXT_MODE],
        default_output_modes=default_output_modes or [JSON_MODE, TEXT_MODE],
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=url,
                protocol_version=PROTOCOL_VERSION,
            )
        ],
        skills=skills,
    )
    if cfg.security.enforce:
        card.security_schemes[API_KEY_SCHEME_NAME].CopyFrom(api_key_scheme())
        card.security_requirements.append(
            SecurityRequirement(schemes={API_KEY_SCHEME_NAME: StringList(list=[])})
        )
    return card


def public_url(port: int) -> str:
    """URL other agents should use to reach this one."""
    return f"http://{settings().public_host}:{port}"
