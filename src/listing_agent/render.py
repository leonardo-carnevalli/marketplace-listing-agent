"""Plain-text and Markdown rendering. No terminal styling, so output pastes anywhere."""

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal

from listing_agent.money import format_money, format_percent
from listing_agent.pipeline import PipelineResult
from listing_agent.pricing import PriceBreakdown, PriceQuote, Rate
from listing_agent.rules import QualityReport

NUMBER = re.compile(r"^-?[\d,]+(\.\d+)?%?$")


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Markdown-compatible table with aligned columns; numbers are right-aligned."""
    widths = [max(len(str(c)) for c in col) for col in zip(headers, *rows, strict=True)]

    def line(cells: Sequence[str]) -> str:
        padded = [
            c.rjust(w) if NUMBER.match(c) else c.ljust(w)
            for c, w in zip(cells, widths, strict=True)
        ]
        return "| " + " | ".join(padded) + " |"

    separator = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    return "\n".join([line(headers), separator, *(line(r) for r in rows)])


BREAKDOWN_HEADERS = (
    "type",
    "price",
    "commission",
    "fixed fee",
    "shipping",
    "tax",
    "cost",
    "profit",
    "margin",
)


def breakdown_row(b: PriceBreakdown) -> list[str]:
    money = [b.price, b.commission, b.fixed_fee, b.shipping, b.tax, b.unit_cost, b.profit]
    return [b.listing_type, *(format_money(v) for v in money), format_percent(b.margin)]


def breakdown_table(rows: Sequence[PriceBreakdown]) -> str:
    return table(BREAKDOWN_HEADERS, [breakdown_row(b) for b in rows])


def quote_table(quotes: Sequence[PriceQuote]) -> str:
    rows = [
        [q.listing_type, label, *breakdown_row(b)[1:]]
        for q in quotes
        for label, b in (
            ("breakeven", q.breakeven),
            ("target", q.target),
            ("recommended", q.recommended),
        )
    ]
    return table(("type", "point", *BREAKDOWN_HEADERS[1:]), rows)


def ladder_table(ladders: Mapping[str, Mapping[Rate, Decimal | None]]) -> str:
    targets = sorted({t for ladder in ladders.values() for t in ladder})
    rows = [
        [
            name,
            *(
                format_money(p) if (p := ladder.get(t)) is not None else "unreachable"
                for t in targets
            ),
        ]
        for name, ladder in ladders.items()
    ]
    return table(("type", *(f"margin {format_percent(t)}" for t in targets)), rows)


def quality_text(report: QualityReport) -> str:
    verdict = "ready" if report.ready else ("passed" if report.passed else "failed")
    lines = [
        f"Quality score: {report.score}/100 ({verdict})",
        f"Keyword coverage: {report.keyword_coverage:.0%}  "
        f"Attribute completeness: {report.attribute_completeness:.0%}",
    ]
    lines += [f"  [{f.severity}] {f.field}: {f.message}" for f in report.findings]
    return "\n".join(lines)


def run_summary(result: PipelineResult) -> str:
    lines = [f"Trace {result.trace_id}  provider={result.provider}  status={result.status}", ""]
    lines += [
        f"  {s.step:<9} attempt {s.attempt}  {'ok' if s.ok else 'retry'}  {s.latency_ms} ms"
        + (f"  {s.detail}" if s.detail else "")
        for s in result.steps
    ]
    lines += ["", "Critique loop:"]
    lines += [
        f"  round {r.iteration}: score {r.score}, {r.errors} error(s), {r.warnings} warning(s)"
        for r in result.revisions
    ]
    lines += ["", "Titles:"]
    lines += [f"  ({len(t)}) {t}" for t in result.listing.titles]
    lines += ["", quality_text(result.quality), "", quote_table(list(result.quotes.values()))]
    lines += [f"Pricing issue: {issue}" for issue in result.pricing_issues]
    if result.market_note:
        lines.append(f"Market: {result.market_note}")
    return "\n".join(lines)


def listing_markdown(result: PipelineResult) -> str:
    listing = result.listing
    parts = [
        "# Listing draft (review before publishing)",
        f"Status: **{result.status}**. Trace `{result.trace_id}`.",
        "## Titles",
        "\n".join(f"{i}. {t} ({len(t)} chars)" for i, t in enumerate(listing.titles, 1)),
        f"## Category\n{listing.category}",
        "## Attributes",
        table(("attribute", "value"), [[a.name, a.value] for a in listing.attributes]),
        "## Photo plan",
        "\n".join(f"{i}. {shot}" for i, shot in enumerate(listing.photo_plan, 1)),
        "## Description",
        f"```text\n{listing.description}\n```",
        "## Buyer questions",
        "\n\n".join(f"**{q.question}**\n{q.answer}" for q in listing.faq),
        "## Quality",
        f"```text\n{quality_text(result.quality)}\n```",
        "## Price",
        quote_table(list(result.quotes.values())),
    ]
    if result.recommended:
        r = result.recommended.recommended
        parts.append(
            f"Suggested: **{format_money(r.price)}** as `{r.listing_type}`, "
            f"margin {format_percent(r.margin)}. The final price is the seller's call."
        )
    parts += [f"> {note}" for note in [*result.pricing_issues, result.market_note] if note]
    return "\n\n".join(parts) + "\n"
