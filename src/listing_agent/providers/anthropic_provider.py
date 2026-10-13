"""Claude through the official Anthropic SDK, with schema-validated structured output."""

import os

import anthropic
from pydantic import BaseModel

from listing_agent.providers.base import LLMRequest, ProviderError

DEFAULT_MODEL = "claude-opus-5-5"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        model: str | None = None,
        client: anthropic.Anthropic | None = None,
        max_tokens: int = 16000,
    ) -> None:
        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens
        self._client = client or anthropic.Anthropic()

    def complete[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> T:
        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=self.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                output_format=schema,
            )
        except anthropic.APIError as error:
            raise ProviderError(f"Anthropic API error: {error}") from error
        if response.stop_reason == "refusal":
            raise ProviderError("the model declined this request")
        if response.stop_reason == "max_tokens":
            raise ProviderError("the answer was cut off at max_tokens")
        parsed = response.parsed_output
        if not isinstance(parsed, schema):
            raise ProviderError("the response carried no structured output")
        return parsed
