"""The workflow: research -> draft -> critique/revise loop -> price.

Topology is a short sequential chain with one evaluator-optimizer loop. Only the
writing steps use an LLM. The critic (rule checker) and the pricing are plain code,
so a model never grades its own work and never does arithmetic.

Failure handling, per step:
- invalid structured output or a provider error: retry with the error as feedback,
  up to ``max_attempts``; then stop with :class:`StepFailedError`;
- the revise loop stops at ``max_revisions`` or as soon as the score stops improving,
  and the result is marked ``needs_attention`` for a human;
- nothing is ever published. The output is a file a person reviews.
"""

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ValidationError

from listing_agent.config import MarketplaceConfig
from listing_agent.models import ListingDraft, MarketResearch, ProductBrief
from listing_agent.pricing import PriceQuote, UnreachableMarginError, quote
from listing_agent.prompts import draft_request, research_request, revise_request
from listing_agent.providers.base import LLMProvider, LLMRequest, ProviderError
from listing_agent.rules import QualityReport, check_listing

Status = Literal["ready_for_review", "needs_attention"]


class StepFailedError(RuntimeError):
    def __init__(self, step: str, attempts: int, error: str) -> None:
        super().__init__(f"step '{step}' failed after {attempts} attempt(s): {error}")
        self.step = step


class StepRecord(BaseModel):
    step: str
    attempt: int
    ok: bool
    latency_ms: int
    detail: str = ""


class Revision(BaseModel):
    iteration: int
    score: int
    errors: int
    warnings: int


class PipelineResult(BaseModel):
    trace_id: str
    provider: str
    status: Status
    research: MarketResearch
    listing: ListingDraft
    quality: QualityReport
    revisions: list[Revision]
    quotes: dict[str, PriceQuote]
    pricing_issues: list[str]
    recommended: PriceQuote | None
    market_note: str | None
    steps: list[StepRecord]


@dataclass(frozen=True)
class Settings:
    max_attempts: int = 2
    max_revisions: int = 2
    charm_ending: Decimal | None = Decimal("0.90")


class _Runner:
    """Calls the provider for one step at a time and keeps the step log."""

    def __init__(self, provider: LLMProvider, settings: Settings, clock: Callable[[], float]):
        self.provider = provider
        self.settings = settings
        self.clock = clock
        self.steps: list[StepRecord] = []

    def call[T: BaseModel](self, step: str, request: LLMRequest, schema: type[T]) -> T:
        error = ""
        for attempt in range(1, self.settings.max_attempts + 1):
            started = self.clock()
            try:
                result = self.provider.complete(request, schema)
            except (ValidationError, ProviderError) as exc:
                error = str(exc).splitlines()[0]
                self._log(step, attempt, started, ok=False, detail=error)
                request = request.with_feedback(str(exc))
                continue
            self._log(step, attempt, started, ok=True)
            return result
        raise StepFailedError(step, self.settings.max_attempts, error)

    def _log(self, step: str, attempt: int, started: float, *, ok: bool, detail: str = "") -> None:
        latency = round((self.clock() - started) * 1000)
        self.steps.append(
            StepRecord(step=step, attempt=attempt, ok=ok, latency_ms=latency, detail=detail)
        )


def _summary(iteration: int, report: QualityReport) -> Revision:
    errors = sum(1 for f in report.findings if f.severity == "error")
    return Revision(
        iteration=iteration,
        score=report.score,
        errors=errors,
        warnings=len(report.findings) - errors,
    )


def _improve(
    runner: _Runner, draft: ListingDraft, research: MarketResearch, config: MarketplaceConfig
) -> tuple[ListingDraft, QualityReport, list[Revision]]:
    report = check_listing(draft, config.rules, research.keywords)
    history = [_summary(0, report)]
    for iteration in range(1, runner.settings.max_revisions + 1):
        if report.ready:
            break
        request = revise_request(draft, report, research, config.rules)
        candidate = runner.call("revise", request, ListingDraft)
        candidate_report = check_listing(candidate, config.rules, research.keywords)
        history.append(_summary(iteration, candidate_report))
        if candidate_report.score <= report.score and candidate_report.passed <= report.passed:
            break  # plateau: another round is unlikely to help, hand it to a human
        draft, report = candidate, candidate_report
    return draft, report, history


def _quotes(
    brief: ProductBrief, config: MarketplaceConfig, settings: Settings
) -> tuple[dict[str, PriceQuote], list[str]]:
    quotes: dict[str, PriceQuote] = {}
    issues: list[str] = []
    for name, schedule in config.fees.items():
        try:
            quotes[name] = quote(brief.costs, schedule, brief.target_margin, settings.charm_ending)
        except UnreachableMarginError as error:
            issues.append(str(error))
    return quotes, issues


def market_note(price: Decimal, research: MarketResearch) -> str | None:
    """Compare a price with the range seen in the research notes, if any."""
    low, high = research.observed_price_low, research.observed_price_high
    if high is not None and price > Decimal(str(high)):
        return f"{price} is above the highest observed price ({high}). Justify it in the listing."
    if low is not None and price < Decimal(str(low)):
        return f"{price} is below the lowest observed price ({low}). There may be room to raise it."
    return None


def run(
    brief: ProductBrief,
    config: MarketplaceConfig,
    provider: LLMProvider,
    settings: Settings | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> PipelineResult:
    settings = settings or Settings()
    config.schedule(brief.listing_type)  # fail fast on an unknown listing type
    runner = _Runner(provider, settings, clock)

    research = runner.call("research", research_request(brief), MarketResearch)
    draft = runner.call("draft", draft_request(brief, research, config.rules), ListingDraft)
    listing, report, revisions = _improve(runner, draft, research, config)
    quotes, issues = _quotes(brief, config, settings)

    recommended = quotes.get(brief.listing_type)
    note = market_note(recommended.recommended.price, research) if recommended else None
    status: Status = "ready_for_review" if report.ready and recommended else "needs_attention"
    return PipelineResult(
        trace_id=uuid.uuid4().hex,
        provider=provider.name,
        status=status,
        research=research,
        listing=listing,
        quality=report,
        revisions=revisions,
        quotes=quotes,
        pricing_issues=issues,
        recommended=recommended,
        market_note=note,
        steps=runner.steps,
    )
