"""Command line interface: price, scenarios, check and run."""

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

import typer

from listing_agent.config import MarketplaceConfig, load_brief, load_config
from listing_agent.models import ListingDraft
from listing_agent.pipeline import Settings, StepFailedError, run
from listing_agent.pricing import (
    CostInputs,
    UnreachableMarginError,
    breakeven_units,
    margin_ladder,
    quote,
    scenarios,
)
from listing_agent.providers import PROVIDERS, ProviderError, get_provider
from listing_agent.render import (
    breakdown_table,
    ladder_table,
    listing_markdown,
    quality_text,
    quote_table,
    run_summary,
)
from listing_agent.rules import check_listing

app = typer.Typer(
    no_args_is_help=True, help="Research, draft, check and price marketplace listings."
)

DEFAULT_CONFIG = Path("config/marketplace.example.toml")
LADDER = [Decimal(m) for m in ("0", "0.10", "0.20", "0.30")]


def _decimal(value: object) -> Decimal:
    """Accepts "28.50" or "28,50". Typer also passes the Decimal defaults through here."""
    try:
        return Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        raise typer.BadParameter(f"'{value}' is not a number") from None


Money = Annotated[Decimal, typer.Option(parser=_decimal, help="Amount, e.g. 28.50")]
ConfigOption = Annotated[Path, typer.Option("--config", "-c", help="Marketplace TOML config.")]


def _config(path: Path) -> MarketplaceConfig:
    try:
        return load_config(path)
    except FileNotFoundError:
        raise typer.BadParameter(f"config file not found: {path}") from None


def _schedules(config: MarketplaceConfig, listing_types: list[str] | None) -> list[str]:
    names = listing_types or list(config.fees)
    try:
        for name in names:
            config.schedule(name)
    except KeyError as error:
        raise typer.BadParameter(str(error.args[0])) from None
    return names


@app.command()
def price(
    cost: Money,
    margin: Annotated[Decimal, typer.Option(parser=_decimal, help="Target margin, 0.2 = 20%.")],
    shipping: Money = Decimal(0),
    tax_rate: Annotated[Decimal, typer.Option(parser=_decimal, help="0.06 = 6%.")] = Decimal(0),
    listing_type: Annotated[list[str] | None, typer.Option("--type")] = None,
    monthly_fixed: Money = Decimal(0),
    charm_ending: Annotated[Decimal, typer.Option(parser=_decimal)] = Decimal("0.90"),
    no_charm: bool = False,
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Breakeven, target and recommended price per listing type."""
    cfg = _config(config)
    costs = CostInputs(unit_cost=cost, shipping_cost=shipping, tax_rate=tax_rate)
    names = _schedules(cfg, listing_type)
    quotes = []
    for name in names:
        try:
            quotes.append(
                quote(costs, cfg.schedule(name), margin, None if no_charm else charm_ending)
            )
        except UnreachableMarginError as error:
            typer.echo(f"{name}: {error}", err=True)
    if quotes:
        typer.echo(quote_table(quotes))
    ladders = {name: margin_ladder(costs, cfg.schedule(name), LADDER) for name in names}
    typer.echo("\nRequired price by target margin:\n" + ladder_table(ladders))
    if monthly_fixed > 0:
        for q in quotes:
            units = breakeven_units(monthly_fixed, q.recommended)
            typer.echo(
                f"{q.listing_type}: {units} sale(s)/month at "
                f"{q.recommended.price} cover {monthly_fixed} of fixed costs"
            )
    if not quotes:
        raise typer.Exit(1)


@app.command(name="scenarios")
def scenarios_command(
    cost: Money,
    prices: Annotated[list[str], typer.Option("--price", help="Repeat for each price point.")],
    shipping: Money = Decimal(0),
    tax_rate: Annotated[Decimal, typer.Option(parser=_decimal)] = Decimal(0),
    listing_type: Annotated[list[str] | None, typer.Option("--type")] = None,
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Profit and margin at each price point, for every listing type."""
    cfg = _config(config)
    costs = CostInputs(unit_cost=cost, shipping_cost=shipping, tax_rate=tax_rate)
    schedules = [cfg.schedule(n) for n in _schedules(cfg, listing_type)]
    typer.echo(breakdown_table(scenarios(costs, schedules, [_decimal(p) for p in prices])))


@app.command()
def check(
    listing: Annotated[Path, typer.Argument(help="Listing JSON (a ListingDraft).")],
    keyword: Annotated[list[str] | None, typer.Option(help="Keyword to cover; repeatable.")] = None,
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Score a listing against the marketplace rules. Exit code 1 when it fails."""
    data = json.loads(listing.read_text(encoding="utf-8"))
    draft = ListingDraft.model_validate(data.get("listing", data))
    report = check_listing(draft, _config(config).rules, keyword or [])
    typer.echo(quality_text(report))
    if not report.passed:
        raise typer.Exit(1)


@app.command(name="run")
def run_command(
    brief: Annotated[Path, typer.Argument(help="Product brief TOML.")],
    provider: Annotated[str, typer.Option(help=f"One of: {', '.join(PROVIDERS)}.")] = "fake",
    model: Annotated[str | None, typer.Option(help="Override the provider's model.")] = None,
    out: Annotated[Path, typer.Option(help="Output folder.")] = Path("out"),
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Run research -> draft -> critique -> price. Writes files; never publishes."""
    try:
        result = run(load_brief(brief), _config(config), get_provider(provider, model), Settings())
    except (ProviderError, StepFailedError, KeyError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from None
    out.mkdir(parents=True, exist_ok=True)
    (out / "listing.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    (out / "listing.md").write_text(listing_markdown(result), encoding="utf-8")
    typer.echo(run_summary(result))
    typer.echo(f"\nWrote {out / 'listing.json'} and {out / 'listing.md'}")
