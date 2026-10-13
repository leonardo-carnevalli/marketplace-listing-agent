import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from listing_agent.cli import app
from listing_agent.models import ListingDraft

from .conftest import BRIEF_PATH, CONFIG_PATH

runner = CliRunner()
CONFIG = ["--config", str(CONFIG_PATH)]


def test_price_prints_quotes_ladder_and_units() -> None:
    result = runner.invoke(
        app,
        [
            "price",
            "--cost",
            "31",
            "--shipping",
            "21,90",
            "--tax-rate",
            "0.06",
            "--margin",
            "0.2",
            "--monthly-fixed",
            "100",
            *CONFIG,
        ],
    )
    assert result.exit_code == 0, result.output
    rows = [line for line in result.output.splitlines() if "| recommended |" in line]
    assert "60.90" in rows[0]
    assert "margin 20.0%" in result.output
    assert "classic: 9 sale(s)/month at 60.90 cover 100 of fixed costs" in result.output


def test_price_without_charm_for_one_type() -> None:
    result = runner.invoke(
        app,
        ["price", "--cost", "14", "--margin", "0.2", "--type", "classic", "--no-charm", *CONFIG],
    )
    assert result.exit_code == 0, result.output
    assert "premium" not in result.output


def test_price_unreachable_margin_exits_1() -> None:
    result = runner.invoke(app, ["price", "--cost", "10", "--margin", "0.95", *CONFIG])
    assert result.exit_code == 1
    assert "unreachable" in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--cost", "abc", "--margin", "0.2"], "is not a number"),
        (["--cost", "1", "--margin", "0.2", "--type", "gold"], "unknown listing type"),
        (["--cost", "1", "--margin", "0.2", "--config", "missing.toml"], "config file not found"),
    ],
)
def test_price_bad_parameters(args: list[str], message: str) -> None:
    result = runner.invoke(app, ["price", *args, *([] if "--config" in args else CONFIG)])
    assert result.exit_code == 2
    assert message in result.output


def test_scenarios_table() -> None:
    result = runner.invoke(
        app,
        [
            "scenarios",
            "--cost",
            "31",
            "--shipping",
            "21.90",
            "--tax-rate",
            "0.06",
            "--price",
            "78.90",
            "--price",
            "80.90",
            *CONFIG,
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.startswith("| classic")]
    assert len(lines) == 2
    assert "26.95" in lines[0]  # profit just below the free-shipping threshold
    assert "13.44" in lines[1]  # and just above it


def test_check_passes_and_fails(tmp_path: Path, good_listing: ListingDraft) -> None:
    path = tmp_path / "listing.json"
    path.write_text(good_listing.model_dump_json(), encoding="utf-8")
    ok = runner.invoke(app, ["check", str(path), "--keyword", "ceramic mug", *CONFIG])
    assert ok.exit_code == 0, ok.output
    assert "100/100 (ready)" in ok.output

    bad = good_listing.model_copy(update={"titles": ["Best mug, call 11 99999-0000"]})
    path.write_text(bad.model_dump_json(), encoding="utf-8")
    failed = runner.invoke(app, ["check", str(path), *CONFIG])
    assert failed.exit_code == 1
    assert "[error] titles[0]" in failed.output


def test_run_writes_files_and_check_accepts_them(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = runner.invoke(app, ["run", str(BRIEF_PATH), "--out", str(out), *CONFIG])
    assert result.exit_code == 0, result.output
    assert "status=ready_for_review" in result.output
    data = json.loads((out / "listing.json").read_text(encoding="utf-8"))
    assert data["status"] == "ready_for_review"
    markdown = (out / "listing.md").read_text(encoding="utf-8")
    assert "The final price is the seller's call." in markdown
    recheck = runner.invoke(app, ["check", str(out / "listing.json"), *CONFIG])
    assert recheck.exit_code == 0, recheck.output


def test_run_with_unknown_provider_exits_2() -> None:
    result = runner.invoke(app, ["run", str(BRIEF_PATH), "--provider", "llama", *CONFIG])
    assert result.exit_code == 2
    assert "unknown provider" in result.output
