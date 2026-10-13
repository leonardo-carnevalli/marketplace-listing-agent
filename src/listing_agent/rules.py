"""Deterministic listing quality checks with a 0-100 score.

The checker is the evaluator of the draft/revise loop. It is plain code on purpose:
an LLM grading its own output shares its blind spots, while a rule either matches
or it does not.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from listing_agent.models import ListingDraft

WEIGHTS = {"titles": 0.30, "keywords": 0.25, "attributes": 0.25, "description": 0.20}

CONTACT_PATTERNS: dict[str, re.Pattern[str]] = {
    "e-mail address": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "link": re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|net|org|io|br)\b", re.IGNORECASE),
    "phone number": re.compile(r"(?:\+?\d[\s().-]{0,2}){10,}"),
    "social handle": re.compile(r"(?<![\w.])@[A-Za-z_][\w.]{2,}"),
    "messaging app": re.compile(r"\b(?:whats\s?app|telegram|wpp)\b", re.IGNORECASE),
}


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class RuleSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_title_length: int = Field(default=60, gt=0)
    min_description_length: int = Field(default=300, ge=0)
    min_keyword_coverage: float = Field(default=0.6, ge=0, le=1)
    min_score: int = Field(default=80, ge=0, le=100)
    forbidden_words: tuple[str, ...] = ()
    required_attributes: tuple[str, ...] = ()


class Finding(BaseModel):
    rule: str
    severity: Severity
    field: str
    message: str


class QualityReport(BaseModel):
    score: int
    passed: bool
    """No error-level finding."""
    ready: bool
    """Passed and the score reaches the rule set's minimum."""
    keyword_coverage: float
    attribute_completeness: float
    missing_keywords: list[str]
    findings: list[Finding]


def normalize(text: str) -> str:
    """Lowercase and strip accents, so 'Cerâmica' matches 'ceramica'."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def contains_term(text: str, term: str) -> bool:
    """Whole-word, accent- and case-insensitive match. Works for multi-word terms."""
    pattern = r"(?<!\w)" + re.escape(normalize(term)) + r"(?!\w)"
    return re.search(pattern, normalize(text)) is not None


def find_contact_info(text: str) -> list[str]:
    return [kind for kind, pattern in CONTACT_PATTERNS.items() if pattern.search(text)]


def _title_findings(index: int, title: str, rules: RuleSet) -> list[Finding]:
    field = f"titles[{index}]"
    findings = []
    if len(title) > rules.max_title_length:
        findings.append(
            Finding(
                rule="title_length",
                severity=Severity.ERROR,
                field=field,
                message=f"{len(title)} characters, limit is {rules.max_title_length}",
            )
        )
    for word in rules.forbidden_words:
        if contains_term(title, word):
            findings.append(
                Finding(
                    rule="forbidden_word",
                    severity=Severity.ERROR,
                    field=field,
                    message=f"contains the forbidden word '{word}'",
                )
            )
    letters = [c for c in title if c.isalpha()]
    if len(letters) > 3 and all(c.isupper() for c in letters):
        findings.append(
            Finding(
                rule="all_caps",
                severity=Severity.WARNING,
                field=field,
                message="title is all caps",
            )
        )
    return findings


def _contact_findings(listing: ListingDraft) -> list[Finding]:
    texts = [(f"titles[{i}]", t) for i, t in enumerate(listing.titles)]
    texts.append(("description", listing.description))
    texts += [(f"faq[{i}].answer", item.answer) for i, item in enumerate(listing.faq)]
    return [
        Finding(
            rule="contact_info",
            severity=Severity.ERROR,
            field=field,
            message=f"looks like a {kind}; external contact is not allowed",
        )
        for field, text in texts
        for kind in find_contact_info(text)
    ]


def _missing_attributes(listing: ListingDraft, required: Iterable[str]) -> list[str]:
    filled = {normalize(a.name) for a in listing.attributes if a.value.strip()}
    return [name for name in required if normalize(name) not in filled]


def _ratio(part: int, whole: int) -> float:
    return 1.0 if whole == 0 else part / whole


def check_listing(
    listing: ListingDraft, rules: RuleSet, keywords: Sequence[str] = ()
) -> QualityReport:
    title_findings = [f for i, t in enumerate(listing.titles) for f in _title_findings(i, t, rules)]
    contact_findings = _contact_findings(listing)
    findings = title_findings + contact_findings

    text = " ".join([*listing.titles, listing.description])
    missing_keywords = [k for k in keywords if not contains_term(text, k)]
    coverage = _ratio(len(keywords) - len(missing_keywords), len(keywords))
    if coverage < rules.min_keyword_coverage:
        findings.append(
            Finding(
                rule="keyword_coverage",
                severity=Severity.WARNING,
                field="titles+description",
                message=f"covers {coverage:.0%} of the keywords; missing: "
                + ", ".join(missing_keywords),
            )
        )

    missing_attributes = _missing_attributes(listing, rules.required_attributes)
    completeness = _ratio(
        len(rules.required_attributes) - len(missing_attributes), len(rules.required_attributes)
    )
    findings += [
        Finding(
            rule="missing_attribute",
            severity=Severity.WARNING,
            field="attributes",
            message=f"required attribute '{name}' is missing or empty",
        )
        for name in missing_attributes
    ]

    short_description = len(listing.description) < rules.min_description_length
    if short_description:
        findings.append(
            Finding(
                rule="description_length",
                severity=Severity.WARNING,
                field="description",
                message=f"{len(listing.description)} characters, "
                f"recommended at least {rules.min_description_length}",
            )
        )

    bad_titles = {f.field for f in findings if f.field.startswith("titles[")}
    components = {
        "titles": _ratio(len(listing.titles) - len(bad_titles), len(listing.titles)),
        "keywords": coverage,
        "attributes": completeness,
        "description": _description_component(contact_findings, short_description),
    }
    score = round(100 * sum(WEIGHTS[name] * value for name, value in components.items()))
    passed = not any(f.severity is Severity.ERROR for f in findings)
    return QualityReport(
        score=score,
        passed=passed,
        ready=passed and score >= rules.min_score,
        keyword_coverage=coverage,
        attribute_completeness=completeness,
        missing_keywords=missing_keywords,
        findings=findings,
    )


def _description_component(contact_findings: Sequence[Finding], short: bool) -> float:
    if any(f.field == "description" for f in contact_findings):
        return 0.0
    return 0.5 if short else 1.0
