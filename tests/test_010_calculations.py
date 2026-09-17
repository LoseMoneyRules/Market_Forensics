from mfapp.calculations import build_cash_flow, build_income_statement_flow, calculate_valuation, valuation_sensitivity, financial_metrics


def test_valuation_normalizes_probabilities_and_calculates_expected_value():
    result = calculate_valuation(bear=30, base=50, bull=80, bear_probability=25, base_probability=50, bull_probability=25, current_price=40)
    assert round(result.expected_value, 2) == 52.5
    assert round(result.downside_pct, 2) == -25.0
    assert round(result.base_upside_pct, 2) == 25.0
    assert abs(result.bear_probability + result.base_probability + result.bull_probability - 1) < 1e-9


def test_financial_metrics_are_pure_and_auditable():
    row = {"revenue": 1000, "gross_profit": 400, "operating_income": 120, "net_income": 80, "fcf": 100, "receivables": 100, "inventory": 90, "payables": 60, "cogs": 600, "cash": 50, "debt": 200}
    prev = {"revenue": 900, "inventory": 80, "receivables": 90, "fcf": 80}
    metrics = financial_metrics(row, prev)
    assert round(metrics["gross_margin_pct"], 1) == 40.0
    assert round(metrics["operating_margin_pct"], 1) == 12.0
    assert round(metrics["net_debt"], 1) == 150.0
    assert round(metrics["fcf_to_net_income"], 2) == 1.25
    assert metrics["calculation_version"] == "0.1.5"


def test_negative_flow_is_not_rendered_as_fake_positive_width():
    flow = build_income_statement_flow({"fiscal_year": 2026, "revenue": 100, "gross_profit": 40, "operating_income": -10, "pretax_income": -15, "income_tax": -2, "net_income": -13})
    assert all(edge["value"] >= 0 for edge in flow["edges"])
    assert any(edge.get("signed_value", edge["value"]) < 0 for edge in flow["signed_exceptions"])


def test_cash_flow_preserves_signed_exceptions():
    flow = build_cash_flow({"fiscal_year": 2026, "cfo": -20, "capex": 5, "buybacks": 0, "dividends": 2})
    assert any(edge.get("signed_value", edge["value"]) < 0 for edge in flow["signed_exceptions"])


def test_reconciled_income_and_cash_flow_bridges_are_explicit():
    income = build_income_statement_flow({"fiscal_year": 2026, "revenue": 100, "gross_profit": 40, "operating_income": 15, "pretax_income": 12, "income_tax": 2, "net_income": 10})
    assert income["reconciliations"]
    assert all(row["ok"] for row in income["reconciliations"])
    cash = build_cash_flow({"fiscal_year": 2026, "cfo": 20, "capex": 5, "fcf": 15, "buybacks": 4, "dividends": 3})
    assert cash["reconciliations"][0]["ok"] is True
    assert any(edge["target"] == "Retained / Debt / M&A / Other" for edge in cash["edges"])


def test_valuation_sensitivity_is_explicit_and_price_aware():
    rows = valuation_sensitivity(100, 80)
    assert [round(r["shock_pct"]) for r in rows] == [-20, -10, 0, 10, 20]
    assert rows[2]["value"] == 100
    assert round(rows[2]["gap_pct"], 1) == 25.0
