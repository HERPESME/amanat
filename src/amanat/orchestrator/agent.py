"""The LLM layer — thin by design.

The agent reaches money only through `AgentSession`, whose every method routes
through the policy engine. Refusals come back as ordinary tool results, so the
model can adapt — ask the human, lower the ceiling, stop — instead of retrying
blindly.

Which model runs this is a free choice, and deliberately so: the model holds no
authority, so swapping providers changes nothing about what the system will
permit. See `backends.py`.

The governed core in `session.py` needs no credential at all, which is where the
containment tests live.
"""
from __future__ import annotations

from amanat.orchestrator.backends import (
    SYSTEM, AnthropicBackend, GeminiBackend, LLMBackend, resolve_backend,
)
from amanat.orchestrator.session import AgentSession

__all__ = [
    "SYSTEM", "AnthropicBackend", "GeminiBackend", "LLMBackend",
    "resolve_backend", "run",
]


def run(session: AgentSession, user_input: str,
        backend: LLMBackend | None = None) -> str:
    """Run one agent turn against a governed session. Returns the final text."""
    backend = backend or resolve_backend()
    if backend is None:
        raise RuntimeError(
            "No LLM credential found. Set GEMINI_API_KEY or ANTHROPIC_API_KEY "
            "(a .env file at the project root is read automatically). "
            "The governed core needs neither — try: python -m amanat.demo"
        )
    return backend.run(session, user_input)
