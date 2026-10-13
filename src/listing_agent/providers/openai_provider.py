"""Optional OpenAI provider through the Responses API with a Pydantic text format."""

import os

import openai
from pydantic import BaseModel

from listing_agent.providers.base import LLMRequest, ProviderError

DEFAULT_MODEL = "gpt-5.4-mini"


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str | None = None, client: openai.OpenAI | None = None) -> None:
        self.model = model or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
        self._client = client or openai.OpenAI()

    def complete[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> T:
        try:
            response = self._client.responses.parse(
                model=self.model,
                instructions=request.system,
                input=request.user,
                text_format=schema,
            )
        except openai.OpenAIError as error:
            raise ProviderError(f"OpenAI API error: {error}") from error
        parsed = response.output_parsed
        if not isinstance(parsed, schema):
            raise ProviderError("the response carried no structured output (refusal or empty)")
        return parsed
