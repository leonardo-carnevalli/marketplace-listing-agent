"""Deterministic provider for tests, CI and the demo. No network, no randomness.

It builds answers from the request payload with plain string rules. Its first draft
deliberately packs every keyword into one title, which breaks the title length rule,
so a demo run exercises the critique and revise loop exactly like a real model that
overshoots.
"""

import re
from typing import Any

from pydantic import BaseModel

from listing_agent.providers.base import LLMRequest
from listing_agent.rules import contains_term

PRICE_PATTERN = re.compile(r"\$\s?(\d+(?:[.,]\d{1,2})?)")


class FakeProvider:
    name = "fake"

    def complete[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> T:
        builders = {"research": _research, "draft": _draft, "revise": _revise}
        return schema.model_validate(builders[request.task](request.payload))


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for item in items:
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _prices(notes: str) -> list[float]:
    return [float(m.replace(",", ".")) for m in PRICE_PATTERN.findall(notes)]


def _research(payload: dict[str, Any]) -> dict[str, Any]:
    brief = payload["brief"]
    keywords = _unique(brief["seed_keywords"] or brief["name"].lower().split())[:15]
    prices = _prices(payload["research_notes"])
    return {
        "keywords": keywords,
        "title_patterns": ["product type + material or size + main use"],
        "buyer_concerns": [f"Buyers need to know: {item}" for item in brief["limitations"]],
        "differentiators": brief["features"][:3],
        "observed_price_low": min(prices, default=None),
        "observed_price_high": max(prices, default=None),
    }


def _description(brief: dict[str, Any]) -> str:
    lines = [f"{brief['name']}, made for {brief['audience']}.", "", "What it does:"]
    lines += [f"- {feature}" for feature in brief["features"]]
    if brief["limitations"]:
        lines += ["", "What it does not do:"]
        lines += [f"- {item}" for item in brief["limitations"]]
    lines += ["", "Specifications:"]
    lines += [f"- {a['name']}: {a['value']}" for a in brief["attributes"]]
    lines += [
        "",
        "Shipping and returns: shipped through the marketplace logistics. You can return "
        "it within the period the marketplace and consumer law allow.",
    ]
    return "\n".join(lines)


def _draft(payload: dict[str, Any]) -> dict[str, Any]:
    brief, research = payload["brief"], payload["research"]
    extra = [k for k in research["keywords"] if not contains_term(brief["name"], k)]
    return {
        "titles": [" ".join([brief["name"], *extra])],
        "category": brief["category"],
        "attributes": brief["attributes"],
        "photo_plan": _photo_plan(brief["name"]),
        "description": _description(brief),
        "faq": _faq(brief),
    }


def _faq(brief: dict[str, Any]) -> list[dict[str, str]]:
    faq = [{"question": "What is it made for?", "answer": f"It is made for {brief['audience']}."}]
    if brief["limitations"]:
        answer = "Before you buy, please note: " + "; ".join(brief["limitations"]) + "."
        faq.append({"question": "Is there anything it does not do?", "answer": answer})
    return faq


def _photo_plan(name: str) -> list[str]:
    return [
        f"Cover: {name} on a plain white background, filling most of the frame",
        "Side view with a ruler or a common object for scale",
        "Close-up of the material and finish",
        "In use, in a real everyday setting",
        "Everything included in the box, laid out",
        "Simple drawing with the measurements",
    ]


def _fit_title(words: list[str], limit: int) -> str:
    title = words[0]
    for word in words[1:]:
        candidate = f"{title} {word}"
        if len(candidate) <= limit:
            title = candidate
    title = title[:limit].rstrip()
    return title[:1].upper() + title[1:]


def _revise(payload: dict[str, Any]) -> dict[str, Any]:
    draft, rules = dict(payload["draft"]), payload["rules"]
    lead, *rest = payload["research"]["keywords"]
    rotations = [rest[i:] + rest[:i] for i in range(min(3, len(rest) or 1))]
    allowed = [
        [w for w in words if not any(contains_term(w, bad) for bad in rules["forbidden_words"])]
        for words in rotations
    ]
    limit = rules["max_title_length"]
    draft["titles"] = _unique([_fit_title([lead, *words], limit) for words in allowed])
    covered = " ".join([*draft["titles"], draft["description"]])
    missing = [k for k in payload["research"]["keywords"] if not contains_term(covered, k)]
    if missing:
        draft["description"] += "\n\nAlso fits searches for: " + ", ".join(missing) + "."
    return draft
