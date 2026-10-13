"""Load the marketplace config and product briefs from TOML files."""

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from listing_agent.models import ProductBrief
from listing_agent.pricing import FeeSchedule
from listing_agent.rules import RuleSet


class MarketplaceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rules: RuleSet = RuleSet()
    fees: dict[str, FeeSchedule] = Field(min_length=1)

    def schedule(self, listing_type: str) -> FeeSchedule:
        try:
            return self.fees[listing_type]
        except KeyError:
            known = ", ".join(sorted(self.fees))
            raise KeyError(f"unknown listing type '{listing_type}' (known: {known})") from None


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        return tomllib.load(file)


def load_config(path: Path) -> MarketplaceConfig:
    data = _read_toml(path)
    fees = {name: {"name": name, **schedule} for name, schedule in data.pop("fees", {}).items()}
    return MarketplaceConfig.model_validate({**data, "fees": fees})


def load_brief(path: Path) -> ProductBrief:
    """A brief may point to a notes file (``notes_file``), resolved next to the brief."""
    data = _read_toml(path)
    notes_file = data.pop("notes_file", None)
    if notes_file is not None:
        data["research_notes"] = (path.parent / notes_file).read_text(encoding="utf-8")
    return ProductBrief.model_validate(data)
