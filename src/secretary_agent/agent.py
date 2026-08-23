"""Read-only dialogue agent (design §14.7 入口2: 対話 = LLMエージェント).

Tools: get_my_digest only. employee_id is never a tool argument -- it is
derived from the ADK session's user_id via ToolContext, so a session can
only ever read its own owner's digest (C-34: no reading someone else's
digest). No write-capable tool (confirm/dismiss/review) is exposed here,
and run_daily_sweep is deliberately NOT registered as an LLM tool (C-33:
a chat session must not resolve cards or consume mail_seeds as a side
effect of conversation).
"""

from __future__ import annotations

import os
from functools import cached_property

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.genai import Client

from .client import SecretaryApiClient

# Gemini 3.7 Flash per design §14.7.
_MODEL_NAME = "gemini-3.7-flash"

INSTRUCTION = (
    "You are the employee's personal morning secretary. You have exactly "
    "one tool, get_my_digest, which returns today's due-date reminders and "
    "any open stagnation or profile-diff cards for the current user. "
    "Never speculate about stagnation the tool result does not mention -- "
    "only summarize what the tool actually returned. Always present your "
    "reply as your own (the AI's) summary, never as something the user "
    "said or wrote. Reply in English."
)


class _GlobalGemini(Gemini):
    """Gemini pinned to the `global` model endpoint (design §14.7, Y-2対応).

    Agent Engine injects GOOGLE_CLOUD_LOCATION for wherever the Runtime is
    deployed (asia-northeast1), but the Gemini 3.7 Flash model endpoint is
    only served from global/us/eu. Relying on an env var override would tie
    correctness to what Runtime happens to inject, so instead this uses
    ADK's documented extension point for pinning client options the Gemini
    fields don't expose directly (see google.adk.models.google_llm.Gemini's
    class docstring: subclass and override the `api_client` cached
    property) to construct the genai Client with location="global" set
    explicitly, independent of GOOGLE_CLOUD_LOCATION.
    """

    @cached_property
    def api_client(self) -> Client:
        return Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location="global",
        )


def _build_client() -> SecretaryApiClient:
    """Factory seam so tests can substitute a fake client without touching
    real env vars or the network."""
    return SecretaryApiClient()


def get_my_digest(tool_context: ToolContext) -> dict:
    """Fetch today's morning digest (reminders + open cards) for the current
    session's user.

    Takes no employee_id argument by design (§14.7 C-34): the owner is
    always the session's own user_id, so this tool can never be used to
    read someone else's digest.
    """
    return _build_client().fetch_digest(employee_id=tool_context.user_id)


def build_secretary_llm_agent() -> LlmAgent:
    """Builds the read-only dialogue agent (入口2)."""
    return LlmAgent(
        name="secretary",
        model=_GlobalGemini(model=_MODEL_NAME),
        instruction=INSTRUCTION,
        tools=[get_my_digest],
    )
