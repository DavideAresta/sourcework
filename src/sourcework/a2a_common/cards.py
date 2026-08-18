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

        provider=AgentProvider(
            organization="SourceWork", url="https://github.com/DavideAresta/sourcework"
        ),        capabilities=AgentCapabilities(streaming=streaming, push_notifications=False),
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
