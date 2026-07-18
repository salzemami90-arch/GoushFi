import json
from pathlib import Path

from services.financial_calm_brief import FinancialCalmBriefEngine


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "financial_calm_brief_demo.json"


def _demo():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_demo_brief_is_deterministic_and_has_exactly_three_ranked_decisions():
    demo = _demo()
    engine = FinancialCalmBriefEngine()

    first = engine.build(**demo["input"])
    second = engine.build(**demo["input"])

    assert first == second
    assert len(first["decisions"]) == 3
    assert [item["rank"] for item in first["decisions"]] == [1, 2, 3]
    assert len({item["decision_id"] for item in first["decisions"]}) == 3
    assert [item["decision_id"] for item in first["decisions"]] == demo["expected"]["decision_ids"]
    assert [item["metric"]["value"] for item in first["decisions"]] == demo["expected"]["metric_values"]


def test_every_displayed_metric_is_backed_by_a_python_fact():
    demo = _demo()
    brief = FinancialCalmBriefEngine().build(**demo["input"])
    facts = {fact["fact_id"]: fact["value"] for fact in brief["facts"]}

    for decision in brief["decisions"]:
        assert decision["fact_ids"]
        assert all(fact_id in facts for fact_id in decision["fact_ids"])
        assert decision["metric"]["value"] in {facts[fact_id] for fact_id in decision["fact_ids"]}


def test_negative_coverage_gap_is_ranked_ahead_of_general_outlook():
    demo = _demo()
    demo["input"]["coverage"]["net_coverage"] = -420.0
    brief = FinancialCalmBriefEngine().build(**demo["input"])

    ids = [item["decision_id"] for item in brief["decisions"]]
    assert ids[0] == "follow_up_open_items"
    assert ids[1] == "close_coverage_gap"
    assert brief["decisions"][1]["metric"]["value"] == 420.0


def test_sparse_data_still_returns_a_safe_deterministic_fallback():
    brief = FinancialCalmBriefEngine().build(
        month_key="2026-يوليو",
        currency="KWD",
        current={},
        comparison={},
        coverage={},
        cash_flow={},
        savings={},
        seasonal={},
        category_signal={},
    )

    assert len(brief["decisions"]) == 3
    assert [item["decision_id"] for item in brief["decisions"]] == [
        "protect_cash_outlook",
        "steady_current_month",
        "strengthen_data_readiness",
    ]
