"""Anthropic API wrapper with retry logic."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

import anthropic

from orchestrator.auth.token import get_validated_key

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str | None = None


class LLMClient:
    """Anthropic API client with retry and cost tracking."""

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 4096,
        max_retries: int = 3,
    ):
        self.conn = conn
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self._client: anthropic.Anthropic | None = None

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            api_key = get_validated_key()
            if not api_key:
                raise RuntimeError("No Anthropic API key available")
            self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def call(
        self,
        system_prompt: str,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int | None = None,
        source: str = "orchestrator_brain",
        session_id: str | None = None,
    ) -> LLMResponse:
        """Make an API call with retry and cost tracking."""
        model = model or self.model
        max_tokens = max_tokens or self.max_tokens
        client = self._get_client()

        for attempt in range(self.max_retries):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    messages=messages,
                )

                result = LLMResponse(
                    content=response.content[0].text if response.content else "",
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    model=model,
                    stop_reason=response.stop_reason,
                )

                return result

            except anthropic.RateLimitError:
                wait = 2 ** attempt
                logger.warning("Rate limited, retrying in %ds (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
            except anthropic.APIError as e:
                if attempt == self.max_retries - 1:
                    raise
                logger.warning("API error: %s, retrying...", e)
                time.sleep(1)

        raise RuntimeError("Max retries exceeded")

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate (4 chars per token)."""
        return len(text) // 4
