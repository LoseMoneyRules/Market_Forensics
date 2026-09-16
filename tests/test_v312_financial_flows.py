from mfengine.v312.financial_flows import cash_flow_flow, income_statement_flow


def test_income_flow_does_not_require_gross_profit():
    pack = income_statement_flow({
        "revenue": 1000,
        "gross_profit": None,
        "operating_income": 120,
        "pretax": 100,
        "tax": 20,
        "net_income": 80,
    })
    assert pack["ok"] is True
    assert "Revenue" in pack["labels"]
    assert "Operating income" in pack["labels"]
    assert "Net income" in pack["labels"]
    assert "Gross profit" not in pack["labels"]


def test_income_flow_handles_loss_without_negative_sankey_widths():
    pack = income_statement_flow({
        "revenue": 1000,
        "gross_profit": 300,
        "operating_income": 50,
        "pretax": -20,
        "tax": 0,
        "net_income": -35,
    })
    assert pack["ok"] is True
    assert "Net loss" in pack["labels"]
    assert all(value > 0 for _, _, value in pack["links"])


def test_cash_flow_handles_negative_cfo_and_capex_without_fabrication():
    pack = cash_flow_flow({
        "net_income": -10,
        "cfo": -50,
        "capex": 25,
        "fcf": -75,
        "buybacks": 0,
        "dividends": 0,
    })
    assert pack["ok"] is True
    assert "Cash used in operations" in pack["labels"]
    assert all(value > 0 for _, _, value in pack["links"])


def test_cash_flow_capex_above_cfo_shows_financing_shortfall():
    pack = cash_flow_flow({
        "net_income": 80,
        "cfo": 100,
        "capex": 140,
        "fcf": -40,
        "buybacks": 10,
        "dividends": 5,
    })
    assert pack["ok"] is True
    assert "External financing / cash draw for Capex" in pack["labels"]
    assert "Negative free cash flow" in pack["labels"]
