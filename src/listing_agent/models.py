"""Contracts between the workflow steps.

``MarketResearch`` and ``ListingDraft`` are what an LLM must return. They use only
plain JSON types (no dicts with free keys, no Decimal) so every provider's
structured-output mode can express them. Money math never happens inside them.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from listing_agent.pricing import CostInputs


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Attribute(_Model):
    name: str = Field(min_length=1)
    value: str


class ProductBrief(_Model):
    """What the seller knows about the product. Input of the whole workflow."""

    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    features: list[str] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)
    attributes: list[Attribute] = Field(default_factory=list)
    seed_keywords: list[str] = Field(default_factory=list)
    costs: CostInputs
    target_margin: Decimal = Field(ge=0, lt=1)
    listing_type: str = "classic"
    research_notes: str = ""
    """Notes the seller pasted about competing listings. Untrusted data, never instructions."""


class MarketResearch(_Model):
    keywords: list[str] = Field(min_length=1, max_length=15)
    """Search terms buyers type, most important first."""
    title_patterns: list[str] = Field(default_factory=list)
    buyer_concerns: list[str] = Field(default_factory=list)
    differentiators: list[str] = Field(default_factory=list)
    observed_price_low: float | None = None
    observed_price_high: float | None = None


class FAQ(_Model):
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)


class ListingDraft(_Model):
    titles: list[str] = Field(min_length=1, max_length=3)
    category: str
    attributes: list[Attribute]
    photo_plan: list[str]
    description: str
    faq: list[FAQ] = Field(default_factory=list)
