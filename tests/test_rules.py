import pytest

from listing_agent.models import FAQ, Attribute, ListingDraft
from listing_agent.rules import (
    RuleSet,
    Severity,
    check_listing,
    contains_term,
    find_contact_info,
    normalize,
)

RULES = RuleSet(
    max_title_length=60,
    min_description_length=100,
    forbidden_words=("best", "free", "promoção"),
    required_attributes=("Brand", "Model", "Material", "Capacity", "Color"),
)
KEYWORDS = ["ceramic mug", "350 ml", "stoneware", "handmade"]


def with_title(listing: ListingDraft, title: str) -> ListingDraft:
    return listing.model_copy(update={"titles": [title]})


def rules_hit(listing: ListingDraft) -> set[str]:
    return {f.rule for f in check_listing(listing, RULES, KEYWORDS).findings}


def test_normalize_strips_accents_and_case() -> None:
    assert normalize("Cerâmica PROMOÇÃO") == "ceramica promocao"


@pytest.mark.parametrize(
    ("text", "term", "expected"),
    [
        ("Caneca de Cerâmica 350 ml", "ceramica", True),
        ("Ceramic mug 350 ml", "350 ml", True),
        ("Ceramic mugs", "mug", False),
        ("Bestseller mug", "best", False),
        ("The best mug", "BEST", True),
    ],
)
def test_contains_term_is_whole_word(text: str, term: str, expected: bool) -> None:
    assert contains_term(text, term) is expected


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("write to seller@example.com", "e-mail address"),
        ("see https://example.test", "link"),
        ("visit www.example.test", "link"),
        ("our site shop.com.br", "link"),
        ("call 11 99999-0000", "phone number"),
        ("call +1 (555) 010-9999", "phone number"),
        ("follow @studio_example", "social handle"),
        ("message me on WhatsApp", "messaging app"),
        ("chat on telegram", "messaging app"),
    ],
)
def test_contact_info_detected(text: str, kind: str) -> None:
    assert kind in find_contact_info(text)


@pytest.mark.parametrize(
    "text", ["Holds 350 ml", "Size 10 x 8 cm, 420 g", "Model Daily-350", "Ships in 2 days"]
)
def test_ordinary_text_is_not_contact_info(text: str) -> None:
    assert find_contact_info(text) == []


def test_good_listing_is_ready(good_listing: ListingDraft) -> None:
    report = check_listing(good_listing, RULES, KEYWORDS)
    assert report.findings == []
    assert report.score == 100
    assert report.passed
    assert report.ready


# Labeled cases for the critic. Each row is (case, rules that must fire). An empty set
# means the listing must come out clean. This doubles as the evaluator's eval suite:
# a change to the checker must keep every row green.
EVAL_CASES: list[tuple[str, str, set[str]]] = [
    ("clean title", "Ceramic mug 350 ml handmade stoneware", set()),
    ("exactly 60 chars", "Ceramic mug 350 ml handmade stoneware coffee cup gift set ab", set()),
    ("61 chars", "Ceramic mug 350 ml handmade stoneware coffee cup gift set abc", {"title_length"}),
    ("forbidden word", "Best ceramic mug 350 ml handmade stoneware", {"forbidden_word"}),
    ("forbidden any case", "Ceramic mug FREE 350 ml handmade stoneware", {"forbidden_word"}),
    (
        "forbidden accents",
        "Caneca promocao ceramic mug 350 ml stoneware handmade",
        {"forbidden_word"},
    ),
    ("forbidden inside word ok", "Bestow ceramic mug 350 ml handmade stoneware", set()),
    ("all caps", "CERAMIC MUG 350 ML HANDMADE STONEWARE", {"all_caps"}),
    ("short caps ok", "UK ceramic mug 350 ml handmade stoneware", set()),
    ("phone in title", "Ceramic mug 350 ml handmade stoneware 11 99999-0000", {"contact_info"}),
    ("email in title", "Ceramic mug 350 ml stoneware handmade a@b.co", {"contact_info"}),
    ("handle in title", "Ceramic mug 350 ml stoneware handmade @studio_x", {"contact_info"}),
    ("whatsapp in title", "Ceramic mug 350 ml stoneware handmade whatsapp", {"contact_info"}),
    ("link in title", "Ceramic mug 350 ml stoneware handmade www.shop.test", {"contact_info"}),
    (
        "too long and forbidden",
        "Best ceramic mug 350 ml handmade stoneware coffee cup gift set",
        {"title_length", "forbidden_word"},
    ),
    ("number is not a phone", "Ceramic mug 350 ml handmade stoneware 2026", set()),
]


@pytest.mark.parametrize(("case", "title", "expected"), EVAL_CASES, ids=[c[0] for c in EVAL_CASES])
def test_critic_eval_titles(
    good_listing: ListingDraft, case: str, title: str, expected: set[str]
) -> None:
    assert rules_hit(with_title(good_listing, title)) == expected, case


def test_critic_eval_missing_attribute(good_listing: ListingDraft) -> None:
    listing = good_listing.model_copy(
        update={"attributes": [*good_listing.attributes[:3], Attribute(name="Capacity", value=" ")]}
    )
    report = check_listing(listing, RULES, KEYWORDS)
    assert {f.message for f in report.findings} == {
        "required attribute 'Capacity' is missing or empty",
        "required attribute 'Color' is missing or empty",
    }
    assert report.attribute_completeness == pytest.approx(0.6)
    assert report.passed  # warnings only
    assert report.score == 90


def test_critic_eval_attribute_names_ignore_case(good_listing: ListingDraft) -> None:
    attrs = [Attribute(name=a.name.upper(), value=a.value) for a in good_listing.attributes]
    assert rules_hit(good_listing.model_copy(update={"attributes": attrs})) == set()


def test_critic_eval_low_keyword_coverage(good_listing: ListingDraft) -> None:
    listing = good_listing.model_copy(
        update={"titles": ["Coffee cup"], "description": "A cup for coffee. " * 10}
    )
    report = check_listing(listing, RULES, KEYWORDS)
    assert report.keyword_coverage == 0
    assert report.missing_keywords == KEYWORDS
    assert "keyword_coverage" in {f.rule for f in report.findings}
    assert not report.ready


def test_critic_eval_contact_in_description_and_faq(good_listing: ListingDraft) -> None:
    listing = good_listing.model_copy(
        update={
            "description": good_listing.description + " Questions? seller@example.com",
            "faq": [FAQ(question="Can I call?", answer="Yes, at 11 99999-0000")],
        }
    )
    report = check_listing(listing, RULES, KEYWORDS)
    assert {f.field for f in report.findings} == {"description", "faq[0].answer"}
    assert all(f.severity is Severity.ERROR for f in report.findings)
    assert not report.passed
    assert report.score == 80  # description component drops to zero


def test_critic_eval_short_description(good_listing: ListingDraft) -> None:
    listing = good_listing.model_copy(
        update={"description": "Ceramic mug, 350 ml, stoneware, handmade."}
    )
    report = check_listing(listing, RULES, KEYWORDS)
    assert [f.rule for f in report.findings] == ["description_length"]
    assert report.score == 90


def test_score_counts_each_bad_title_once(good_listing: ListingDraft) -> None:
    listing = good_listing.model_copy(
        update={"titles": ["Ceramic mug 350 ml stoneware handmade", "BEST FREE MUG EVER"]}
    )
    report = check_listing(listing, RULES, KEYWORDS)
    assert report.score == 85  # half of the 30 title points


def test_no_keywords_and_no_required_attributes_count_as_complete(
    good_listing: ListingDraft,
) -> None:
    report = check_listing(good_listing, RuleSet(min_description_length=0))
    assert report.keyword_coverage == 1
    assert report.attribute_completeness == 1
    assert report.ready


def test_min_score_controls_ready(good_listing: ListingDraft) -> None:
    listing = good_listing.model_copy(
        update={"description": "Ceramic mug 350 ml stoneware handmade"}
    )
    strict = RULES.model_copy(update={"min_score": 95})
    report = check_listing(listing, strict, KEYWORDS)
    assert report.passed
    assert not report.ready
