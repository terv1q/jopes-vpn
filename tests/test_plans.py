from __future__ import annotations

import unittest

from jopesvpn.plans import (
    format_price,
    format_rubles,
    load_plans,
    term_savings,
)


class PlanCatalogTests(unittest.TestCase):
    def test_current_plans_have_unique_tags_and_valid_duration_price_curves(self):
        plans = load_plans()
        current = [plan for plan in plans if plan.sellable]
        self.assertEqual(len(current), 22)
        self.assertEqual(
            len({plan.panel_tag for plan in plans}),
            len(plans),
        )

        for plan in current:
            with self.subTest(plan=plan.code):
                self.assertGreaterEqual(plan.days, 7)
                self.assertEqual(plan.terms[0].days, plan.days)
                self.assertLessEqual(plan.terms[-1].days, 180)
                self.assertEqual(
                    plan.price_minor,
                    plan.terms[0].stars_amount,
                )
                self.assertEqual(
                    format_price(plan),
                    format_rubles(plan.terms[0].price_rub_minor),
                )
                for term in plan.terms:
                    self.assertAlmostEqual(
                        term.price_rub_minor,
                        term.stars_rub_equivalent_minor * 0.85,
                        delta=0.5,
                    )
                for shorter, longer in zip(plan.terms, plan.terms[1:]):
                    self.assertLess(
                        longer.price_rub_minor * shorter.days,
                        shorter.price_rub_minor * longer.days,
                    )
                    savings, discount = term_savings(plan, longer)
                    self.assertGreater(savings, 0)
                    self.assertGreater(discount, 0)

    def test_catalog_starting_price_does_not_expose_other_term_prices(self):
        plan = next(plan for plan in load_plans() if plan.code == "lite-1")
        self.assertEqual(format_price(plan), "10,66 ₽")
        self.assertEqual(plan.available_durations, (7, 15, 30, 90, 180))
        self.assertEqual(plan.traffic_limit_strategy, "NO_RESET")

    def test_traffic_strategies_are_catalogued_for_real_periods(self):
        plans = {plan.code: plan for plan in load_plans() if plan.sellable}
        self.assertEqual(plans["daily-2"].traffic_limit_strategy, "DAY")
        self.assertEqual(plans["week-5"].traffic_limit_strategy, "WEEK")
        self.assertEqual(plans["month-20"].traffic_limit_strategy, "MONTH")
        self.assertEqual(plans["pack-50"].traffic_limit_strategy, "NO_RESET")
        self.assertEqual(plans["month-200"].traffic_limit_gb, 8)
        unlimited = [
            plan for plan in load_plans()
            if plan.traffic_limit_gb is None and plan.sellable
        ]
        self.assertEqual(len(unlimited), 1)
        self.assertEqual(unlimited[0].available_durations, (7, 15, 30, 90, 180))
        archived = {
            plan.code for plan in load_plans()
            if plan.code in {"unlimited-30", "unlimited-90"} and not plan.sellable
        }
        self.assertEqual(archived, {"unlimited-30", "unlimited-90"})

    def test_limited_plans_are_smaller_and_discount_less_than_unlimited(self):
        plans = [plan for plan in load_plans() if plan.sellable]
        unlimited = next(plan for plan in plans if plan.traffic_limit_gb is None)
        unlimited_by_days = {term.days: term for term in unlimited.terms}
        unlimited_180_discount = term_savings(
            unlimited, unlimited.term_for(180)
        )[1]

        for plan in plans:
            if plan.traffic_limit_gb is None:
                continue
            with self.subTest(plan=plan.code):
                self.assertLessEqual(plan.traffic_limit_gb, 20)
                for term in plan.terms:
                    unlimited_term = unlimited_by_days.get(term.days)
                    if unlimited_term is not None:
                        self.assertLessEqual(
                            term.price_rub_minor * 100,
                            unlimited_term.price_rub_minor * 65,
                        )
                term_180 = plan.term_for(180)
                if term_180 is not None:
                    self.assertLess(
                        term_savings(plan, term_180)[1],
                        unlimited_180_discount,
                    )

    def test_ruble_formatting_preserves_kopeks_without_float_rounding(self):
        self.assertEqual(format_rubles(0), "0 ₽")
        self.assertEqual(format_rubles(12901), "129,01 ₽")
        self.assertEqual(format_rubles(120000), "1200 ₽")


if __name__ == "__main__":
    unittest.main()
