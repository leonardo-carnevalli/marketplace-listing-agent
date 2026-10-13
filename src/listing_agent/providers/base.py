"""Provider contract. A provider turns a request into a validated Pydantic object."""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

Task = Literal["research", "draft", "revise"]


class ProviderError(RuntimeError):
    """The provider answered, but not with something usable (refusal, empty output)."""


class LLMRequest(BaseModel):
    """``user`` is the rendered prompt; ``payload`` is the same input as structured data.

    Real providers send ``system`` and ``user``. The fake provider reads ``payload``,
    which keeps it deterministic without parsing prompts.
    """

    model_config = ConfigDict(frozen=True)

    task: Task
    system: str
    user: str
    payload: dict[str, Any]

    def with_feedback(self, error: str) -> "LLMRequest":
        note = f"\n\nYour previous answer was rejected: {error}\nReturn a corrected answer."
        return self.model_copy(update={"user": self.user + note})


class LLMProvider(Protocol):
    name: str

    def complete[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> T:
        """Return an instance of ``schema``. May raise ``ProviderError`` or ``ValidationError``."""
        ...
