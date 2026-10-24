from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from jopesvpn.i18n import t

MIN_RUB_TOPUP_MINOR = 100


class PlansError(ValueError):
    pass


TRAFFIC_RESET_STRATEGIES = {
    "NO_RESET",
    "DAY",
    "WEEK",
    "MONTH",
    "MONTH_ROLLING",
}


@dataclass(frozen=True)
class PlanTerm:
    days: int
    price_rub_minor: int
    stars_amount: int
    stars_rub_equivalent_minor: int


@dataclass(frozen=True)
class Plan:
    code: str
    title: str
    days: int
    description: str
    price_minor: int | None
    currency: str
    traffic_limit_gb: int | None = None
    device_limit: int | None = None
    permanent_traffic: bool = False
    panel_tag: str | None = None
    terms: tuple[PlanTerm, ...] = ()
    traffic_limit_strategy: str = "NO_RESET"
    sellable: bool = True

    def term_for(self, days: int) -> PlanTerm | None:
        return next((term for term in self.terms if term.days == days), None)

    @property
    def available_durations(self) -> tuple[int, ...]:
        return tuple(term.days for term in self.terms)


DEFAULT_PLANS_FILE = Path(__file__).with_name("plans.json")


def load_plans(path: str | Path | None = None) -> tuple[Plan, ...]:
    source = Path(path) if path is not None else DEFAULT_PLANS_FILE
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PlansError("Plans configuration cannot be read") from error
    if not isinstance(payload, dict):
        raise PlansError("Plans configuration must be an object")
    currency = payload.get("currency")
    raw_plans = payload.get("plans")
    pricing_reference = payload.get("pricing_reference")
    if (
        not isinstance(currency, str)
        or len(currency) != 3
        or not currency.isalpha()
        or not isinstance(raw_plans, list)
        or not raw_plans
        or not isinstance(pricing_reference, dict)
    ):
        raise PlansError("Plans configuration is incomplete")
    currency = currency.upper()
    try:
        usd_rub_rate = Decimal(str(pricing_reference["usd_rub_rate"]))
        usd_per_star = Decimal(str(pricing_reference["usd_per_star"]))
        rub_discount_percent = pricing_reference["rub_discount_percent"]
    except (KeyError, InvalidOperation, TypeError, ValueError) as error:
        raise PlansError("Plans pricing reference is invalid") from error
    if (
        not usd_rub_rate.is_finite()
        or usd_rub_rate <= 0
        or not usd_per_star.is_finite()
        or usd_per_star <= 0
        or isinstance(rub_discount_percent, bool)
        or not isinstance(rub_discount_percent, int)
        or not 1 <= rub_discount_percent < 100
        or not isinstance(pricing_reference.get("date"), str)
        or not isinstance(pricing_reference.get("source"), str)
    ):
        raise PlansError("Plans pricing reference is invalid")

    plans: list[Plan] = []
    codes: set[str] = set()
    tags: set[str] = set()
    for item in raw_plans:
        if not isinstance(item, dict):
            raise PlansError("Plan entry must be an object")
        code = item.get("code")
        title = item.get("title")
        description = item.get("description", "")
        days = item.get("days")
        price_minor = item.get("price_minor")
        traffic_limit_gb = item.get("traffic_limit_gb")
        device_limit = item.get("device_limit")
        permanent_traffic = item.get("permanent_traffic", False)
        panel_tag = item.get("panel_tag")
        traffic_limit_strategy = item.get("traffic_limit_strategy", "NO_RESET")
        sellable = item.get("sellable", True)
        raw_terms = item.get("terms", [])
        if panel_tag is None:
            panel_tag = (
                f"PLAN_{re.sub(r'[^A-Z0-9]+', '_', code.upper()).strip('_')}"
                if isinstance(code, str)
                else None
            )

        if (
            not isinstance(code, str)
            or not code
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", code)
            or code in codes
            or not isinstance(title, str)
            or not title
            or not isinstance(description, str)
            or isinstance(days, bool)
            or not isinstance(days, int)
            or not 1 <= days <= 365
            or (
                price_minor is not None
                and (
                    isinstance(price_minor, bool)
                    or not isinstance(price_minor, int)
                    or price_minor <= 0
                )
            )
            or (
                traffic_limit_gb is not None
                and (
                    isinstance(traffic_limit_gb, bool)
                    or not isinstance(traffic_limit_gb, int)
                    or traffic_limit_gb <= 0
                )
            )
            or (
                device_limit is not None
                and (
                    isinstance(device_limit, bool)
                    or not isinstance(device_limit, int)
                    or device_limit <= 0
                )
            )
            or not isinstance(permanent_traffic, bool)
            or (permanent_traffic and traffic_limit_gb is None)
            or not isinstance(panel_tag, str)
            or not re.fullmatch(r"[A-Z0-9_]{1,16}", panel_tag)
            or panel_tag in tags
            or not isinstance(traffic_limit_strategy, str)
            or traffic_limit_strategy not in TRAFFIC_RESET_STRATEGIES
            or not isinstance(sellable, bool)
            or not isinstance(raw_terms, list)
        ):
            raise PlansError("Plan entry is invalid")

        terms: list[PlanTerm] = []
        prior: PlanTerm | None = None
        for raw_term in raw_terms:
            if not isinstance(raw_term, dict):
                raise PlansError("Plan term must be an object")
            term_days = raw_term.get("days")
            rub_minor = raw_term.get("price_rub_minor")
            stars_amount = raw_term.get("stars_amount")
            stars_rub_minor = raw_term.get("stars_rub_equivalent_minor")
            if (
                isinstance(term_days, bool)
                or not isinstance(term_days, int)
                or not 7 <= term_days <= 180
                or isinstance(rub_minor, bool)
                or not isinstance(rub_minor, int)
                or rub_minor <= 0
                or isinstance(stars_amount, bool)
                or not isinstance(stars_amount, int)
                or not 1 <= stars_amount <= 10_000
                or isinstance(stars_rub_minor, bool)
                or not isinstance(stars_rub_minor, int)
                or stars_rub_minor <= 0
                or (prior is not None and term_days <= prior.days)
            ):
                raise PlansError("Plan term is invalid")
            expected_stars_rub_minor = int(
                (
                    Decimal(stars_amount)
                    * usd_per_star
                    * usd_rub_rate
                    * 100
                ).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            )
            expected_rub_minor = int(
                (
                    Decimal(expected_stars_rub_minor)
                    * (100 - rub_discount_percent)
                    / 100
                ).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            )
            if (
                stars_rub_minor != expected_stars_rub_minor
                or rub_minor != expected_rub_minor
            ):
                raise PlansError("Plan prices do not match the pricing reference")
            term = PlanTerm(
                term_days, rub_minor, stars_amount, stars_rub_minor
            )
            if prior is not None and term.price_rub_minor * prior.days >= (
                prior.price_rub_minor * term.days
            ):
                raise PlansError("Longer plan terms must cost less per day")
            terms.append(term)
            prior = term

        if sellable and (
            not terms
            or terms[0].days != days
            or price_minor != terms[0].stars_amount
            or (traffic_limit_gb is None and traffic_limit_strategy != "NO_RESET")
            or (permanent_traffic and terms)
        ):
            raise PlansError("Sellable plan pricing or traffic settings are invalid")
        if not sellable and terms:
            raise PlansError("Archived plans cannot define current checkout terms")

        codes.add(code)
        tags.add(panel_tag)
        plans.append(
            Plan(
                code=code,
                title=title,
                days=days,
                description=description,
                price_minor=price_minor,
                currency=currency,
                traffic_limit_gb=traffic_limit_gb,
                device_limit=device_limit,
                permanent_traffic=permanent_traffic,
                panel_tag=panel_tag,
                terms=tuple(terms),
                traffic_limit_strategy=traffic_limit_strategy,
                sellable=sellable,
            )
        )
    return tuple(plans)


def format_rubles(amount_minor: int) -> str:
    rubles, kopeks = divmod(amount_minor, 100)
    amount = str(rubles) if kopeks == 0 else f"{rubles},{kopeks:02d}"
    return f"{amount} ₽"


def _topup_term(plans: tuple[Plan, ...]) -> PlanTerm:
    term = next(
        (
            term
            for plan in plans
            if plan.sellable and plan.currency == "XTR"
            for term in plan.terms
            if term.price_rub_minor > 0 and term.stars_amount > 0
        ),
        None,
    )
    if term is None:
        raise ValueError("Tariff catalog has no priced terms")
    return term


def max_rub_topup_minor(
    plans: tuple[Plan, ...], *, maximum_stars: int = 10_000
) -> int:
    term = _topup_term(plans)
    return int(
        (
            Decimal(maximum_stars)
            * Decimal(term.price_rub_minor)
            / Decimal(term.stars_amount)
        ).to_integral_value(rounding=ROUND_FLOOR)
    )


def stars_for_rub_amount(
    amount_minor: int,
    plans: tuple[Plan, ...],
    *,
    minimum_minor: int = MIN_RUB_TOPUP_MINOR,
    maximum_minor: int | None = None,
) -> int:
    maximum_minor = (
        max_rub_topup_minor(plans)
        if maximum_minor is None
        else maximum_minor
    )
    if (
        isinstance(amount_minor, bool)
        or not isinstance(amount_minor, int)
        or not minimum_minor <= amount_minor <= maximum_minor
    ):
        raise ValueError("Top-up amount is out of range")
    term = _topup_term(plans)
    rub_per_star = Decimal(term.price_rub_minor) / Decimal(term.stars_amount)
    stars = int(
        (Decimal(amount_minor) / rub_per_star).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    if not 1 <= stars <= 10_000:
        raise ValueError("Top-up Stars amount is out of range")
    return stars


def format_price(plan: Plan) -> str:
    if plan.sellable and plan.terms:
        return format_rubles(plan.terms[0].price_rub_minor)
    if plan.price_minor is None:
        return t("plans.price_missing")
    if plan.currency == "XTR":
        return f"{plan.price_minor} ⭐"
    return format_rubles(plan.price_minor)


def term_savings(plan: Plan, term: PlanTerm) -> tuple[int, int]:
    if not plan.terms:
        return 0, 0
    minimum = plan.terms[0]
    undiscounted = (
        minimum.price_rub_minor * term.days + minimum.days // 2
    ) // minimum.days
    savings = max(0, undiscounted - term.price_rub_minor)
    discount_percent = (
        (savings * 100 + undiscounted // 2) // undiscounted
        if undiscounted
        else 0
    )
    return savings, discount_percent


def title_for_panel_tag(tag: str | None) -> str | None:
    if tag is None:
        return None
    return next(
        (plan.title for plan in load_plans() if plan.panel_tag == tag),
        None,
    )
