from mfapp.secdata import _annual_duration, _quarter_duration_values


def _row(val, start, end, filed, form, fp, fy, tag):
    return {"val": val, "start": start, "end": end, "filed": filed, "form": form, "fp": fp, "fy": fy, "accn": f"{fy}-{fp}-{tag}", "frame": None}


def _facts(tag, rows):
    return {"facts": {"us-gaap": {tag: {"units": {"USD": rows}}}}}


def test_quarter_reconstruction_uses_ytd_differences_and_fy_residual_for_q4():
    tag = "RevenueFromContractWithCustomerExcludingAssessedTax"
    rows = [
        _row(100, "2025-01-01", "2025-03-31", "2025-05-01", "10-Q", "Q1", 2025, tag),
        _row(220, "2025-01-01", "2025-06-30", "2025-08-01", "10-Q", "Q2", 2025, tag),
        _row(350, "2025-01-01", "2025-09-30", "2025-11-01", "10-Q", "Q3", 2025, tag),
        _row(500, "2025-01-01", "2025-12-31", "2026-02-15", "10-K", "FY", 2025, tag),
    ]
    companyfacts = _facts(tag, rows)
    annual = _annual_duration(companyfacts, [tag])
    quarters = _quarter_duration_values(companyfacts, [tag], annual)
    assert float(quarters[(2025, "Q1")]["value"]) == 100
    assert float(quarters[(2025, "Q2")]["value"]) == 120
    assert quarters[(2025, "Q2")]["method"] == "YTD_DIFFERENCE"
    assert float(quarters[(2025, "Q3")]["value"]) == 130
    assert float(quarters[(2025, "Q4")]["value"]) == 150
    assert quarters[(2025, "Q4")]["method"] == "FY_MINUS_Q1_Q2_Q3"


def test_share_metric_never_subtracts_ytd_weighted_average_shares():
    tag = "WeightedAverageNumberOfDilutedSharesOutstanding"
    rows = [
        _row(1000, "2025-01-01", "2025-03-31", "2025-05-01", "10-Q", "Q1", 2025, tag),
        _row(980, "2025-01-01", "2025-06-30", "2025-08-01", "10-Q", "Q2", 2025, tag),
        _row(970, "2025-01-01", "2025-09-30", "2025-11-01", "10-Q", "Q3", 2025, tag),
        _row(960, "2025-01-01", "2025-12-31", "2026-02-15", "10-K", "FY", 2025, tag),
    ]
    companyfacts = {"facts": {"us-gaap": {tag: {"units": {"shares": rows}}}}}
    annual = _annual_duration(companyfacts, [tag])
    quarters = _quarter_duration_values(companyfacts, [tag], annual, shares_metric=True)
    assert float(quarters[(2025, "Q2")]["value"]) == 980
    assert quarters[(2025, "Q2")]["method"] == "YTD_SHARE_PROXY"
    assert float(quarters[(2025, "Q4")]["value"]) == 960
