from __future__ import annotations

"""Economic-reality interpretation for filed accounting data.

This module deliberately keeps reported accounting intact and builds a second,
auditable interpretation layer for investment analysis. It never rewrites a
filing fact and it never assumes that every liability is debt.
"""

from math import isfinite
from typing import Any

ENGINE_VERSION = "0.3.0"

INSTANT_TAGS: dict[str, tuple[str, ...]] = {
    "financial_debt_current": (
        "ShortTermBorrowings", "ShortTermDebtCurrent", "LongTermDebtCurrent",
        "CurrentPortionOfLongTermDebt",
    ),
    "financial_debt_noncurrent": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "finance_lease_current": ("FinanceLeaseLiabilityCurrent", "FinanceLeaseObligationCurrent"),
    "finance_lease_noncurrent": ("FinanceLeaseLiabilityNoncurrent", "FinanceLeaseObligationNoncurrent"),
    "operating_lease_current": ("OperatingLeaseLiabilityCurrent",),
    "operating_lease_noncurrent": ("OperatingLeaseLiabilityNoncurrent",),
    "operating_lease_rou_asset": ("OperatingLeaseRightOfUseAsset",),
    "finance_lease_rou_asset": ("FinanceLeaseRightOfUseAsset",),
    "marketable_securities_current": (
        "MarketableSecuritiesCurrent", "ShortTermInvestments", "ShortTermInvestmentsAvailableForSale",
    ),
    "restricted_cash_current": ("RestrictedCashAndCashEquivalentsCurrent", "RestrictedCashCurrent"),
    "restricted_cash_noncurrent": ("RestrictedCashAndCashEquivalentsNoncurrent", "RestrictedCashNoncurrent"),
    "supplier_finance_current": (
        "SupplierFinanceProgramObligationCurrent", "SupplierFinanceProgramObligationCurrentAndNoncurrent",
    ),
    "supplier_finance_noncurrent": ("SupplierFinanceProgramObligationNoncurrent",),
    "pension_liability": (
        "DefinedBenefitPensionPlanLiabilitiesNoncurrent", "PensionLiabilitiesNoncurrent",
        "DefinedBenefitPlanLiabilitiesNoncurrent",
    ),
    "postretirement_liability": (
        "PostretirementBenefitsLiabilityNoncurrent", "OtherPostretirementBenefitsLiabilityNoncurrent",
    ),
    "redeemable_nci": (
        "RedeemableNoncontrollingInterestEquityCarryingAmount",
        "RedeemableNoncontrollingInterestCarryingAmount",
    ),
    "deferred_revenue_current": ("ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent"),
    "deferred_revenue_noncurrent": ("ContractWithCustomerLiabilityNoncurrent", "DeferredRevenueNoncurrent"),
    "deferred_tax_liability": ("DeferredTaxLiabilitiesNoncurrent", "DeferredIncomeTaxLiabilitiesNet"),
    "aro_current": ("AssetRetirementObligationCurrent",),
    "aro_noncurrent": ("AssetRetirementObligationNoncurrent",),
    "goodwill": ("Goodwill",),
    "intangibles": (
        "FiniteLivedIntangibleAssetsNet", "IndefiniteLivedIntangibleAssetsExcludingGoodwill",
        "IntangibleAssetsNetExcludingGoodwill",
    ),
    "treasury_stock": ("TreasuryStockValue", "TreasuryStockCommonValue"),
}

DURATION_TAGS: dict[str, tuple[str, ...]] = {
    "operating_lease_cost": ("OperatingLeaseCost",),
    "variable_lease_cost": ("VariableLeaseCost",),
    "short_term_lease_cost": ("ShortTermLeaseCost",),
    "finance_lease_interest": ("FinanceLeaseInterestExpense",),
    "share_based_compensation": ("ShareBasedCompensation",),
    "depreciation_amortization": (
        "DepreciationDepletionAndAmortization",
        "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
    ),
    "restructuring_charges": ("RestructuringCharges", "RestructuringCosts"),
    "impairment_charges": ("AssetImpairmentCharges", "ImpairmentOfLongLivedAssetsHeldForUse"),
    "acquisition_related_costs": (
        "BusinessCombinationAcquisitionRelatedCosts", "BusinessAcquisitionCosts",
    ),
}

DEBT_LIKE_KEYS = (
    "financial_debt_current", "financial_debt_noncurrent",
    "finance_lease_current", "finance_lease_noncurrent",
    "supplier_finance_current", "supplier_finance_noncurrent",
    "pension_liability", "postretirement_liability", "redeemable_nci",
)
OPERATING_LEASE_KEYS = ("operating_lease_current", "operating_lease_noncurrent")
NON_DEBT_LIABILITY_KEYS = (
    "deferred_revenue_current", "deferred_revenue_noncurrent",
    "deferred_tax_liability", "aro_current", "aro_noncurrent",
)


def n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _sum_present(values: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    found = [n(values.get(key)) for key in keys]
    found = [value for value in found if value is not None]
    return sum(found) if found else None


def _ratio(a: Any, b: Any) -> float | None:
    x, y = n(a), n(b)
    if x is None or y in (None, 0):
        return None
    return x / y


def _pct(a: Any, b: Any) -> float | None:
    value = _ratio(a, b)
    return value * 100.0 if value is not None else None


def _quality(*, classified: bool, material_unknown: bool, source_count: int) -> str:
    if not classified or material_unknown:
        return "LOW"
    if source_count >= 4:
        return "HIGH"
    if source_count >= 2:
        return "MEDIUM"
    return "LOW"


def build_economic_reality(
    row: dict[str, Any],
    *,
    facts: dict[str, Any] | None = None,
    fact_sources: dict[str, Any] | None = None,
    company_type: str = "",
) -> dict[str, Any]:
    """Build an auditable economic interpretation without changing reported facts."""
    facts = dict(facts or {})
    fact_sources = dict(fact_sources or {})
    revenue = n(row.get("revenue"))
    cfo = n(row.get("cfo"))
    capex = n(row.get("capex"))
    fcf = n(row.get("fcf"))
    cash = n(row.get("cash"))
    total_liabilities = n(row.get("liabilities"))
    reported_debt = n(row.get("debt"))
    equity = n(row.get("equity"))
    operating_income = n(row.get("operating_income"))
    pretax = n(row.get("pretax_income"))
    tax = n(row.get("income_tax"))

    financial_debt = _sum_present(facts, ("financial_debt_current", "financial_debt_noncurrent"))
    finance_leases = _sum_present(facts, ("finance_lease_current", "finance_lease_noncurrent"))
    operating_leases = _sum_present(facts, OPERATING_LEASE_KEYS)
    supplier_finance = _sum_present(facts, ("supplier_finance_current", "supplier_finance_noncurrent"))
    pensions = _sum_present(facts, ("pension_liability", "postretirement_liability"))
    redeemable_nci = n(facts.get("redeemable_nci"))
    liquid_investments = n(facts.get("marketable_securities_current"))
    restricted_cash = _sum_present(facts, ("restricted_cash_current", "restricted_cash_noncurrent"))
    deferred_revenue = _sum_present(facts, ("deferred_revenue_current", "deferred_revenue_noncurrent"))
    deferred_tax = n(facts.get("deferred_tax_liability"))
    aro = _sum_present(facts, ("aro_current", "aro_noncurrent"))
    goodwill = n(facts.get("goodwill"))
    intangibles = n(facts.get("intangibles"))
    treasury_stock = n(facts.get("treasury_stock"))

    classified_financing = any(
        n(facts.get(key)) is not None
        for key in (
            "financial_debt_current", "financial_debt_noncurrent",
            "finance_lease_current", "finance_lease_noncurrent",
        )
    )
    if classified_financing:
        base_financing = (financial_debt or 0.0) + (finance_leases or 0.0)
        debt_basis = "CLASSIFIED_FINANCIAL_DEBT_PLUS_FINANCE_LEASES"
    else:
        base_financing = reported_debt
        debt_basis = "REPORTED_DEBT_FALLBACK" if reported_debt is not None else "UNRESOLVED"

    debt_like_additions = sum(value or 0.0 for value in (supplier_finance, pensions, redeemable_nci))
    gross_economic_debt = (
        base_financing + debt_like_additions
        if base_financing is not None
        else (debt_like_additions if debt_like_additions else None)
    )
    liquid_offset = None
    if cash is not None or liquid_investments is not None:
        liquid_offset = (cash or 0.0) + (liquid_investments or 0.0)
    economic_net_debt = (
        gross_economic_debt - (liquid_offset or 0.0)
        if gross_economic_debt is not None else None
    )

    lease_share = _pct(operating_leases, total_liabilities)
    lease_productivity = _ratio(revenue, operating_leases)
    lease_cost = n(facts.get("operating_lease_cost"))
    total_lease_cost = sum(
        value or 0.0 for value in (
            lease_cost, n(facts.get("variable_lease_cost")), n(facts.get("short_term_lease_cost")),
        )
    )
    if total_lease_cost == 0.0 and not any(
        n(facts.get(key)) is not None
        for key in ("operating_lease_cost", "variable_lease_cost", "short_term_lease_cost")
    ):
        total_lease_cost = None
    lease_cost_to_revenue = _pct(total_lease_cost, revenue)

    da = n(facts.get("depreciation_amortization"))
    maintenance_capex_proxy = growth_capex_proxy = owner_cash_proxy = None
    if capex is not None and da is not None and capex >= 0 and da >= 0:
        maintenance_capex_proxy = min(capex, da)
        growth_capex_proxy = max(capex - da, 0.0)
        owner_cash_proxy = cfo - maintenance_capex_proxy if cfo is not None else None

    sbc = n(facts.get("share_based_compensation"))
    fcf_after_sbc = fcf - sbc if fcf is not None and sbc is not None else None
    sbc_to_revenue = _pct(sbc, revenue)

    special_values = [
        n(facts.get("restructuring_charges")),
        n(facts.get("impairment_charges")),
        n(facts.get("acquisition_related_costs")),
    ]
    special_charges = sum(value or 0.0 for value in special_values)
    if special_charges == 0.0 and not any(value is not None for value in special_values):
        special_charges = None
    normalized_operating_income_proxy = (
        operating_income + special_charges
        if operating_income is not None and special_charges is not None else None
    )
    special_charges_to_revenue = _pct(special_charges, revenue)

    tax_rate = None
    if pretax not in (None, 0) and tax is not None:
        tax_rate = max(0.0, min(0.35, tax / pretax))
    economic_invested_capital = economic_roic_pct = None
    if equity is not None and gross_economic_debt is not None and liquid_offset is not None:
        economic_invested_capital = equity + gross_economic_debt - liquid_offset
        if operating_income is not None and tax_rate is not None and economic_invested_capital > 0:
            economic_roic_pct = operating_income * (1.0 - tax_rate) / economic_invested_capital * 100.0

    lease_adjusted_capital = lease_adjusted_roic_pct = None
    if economic_invested_capital is not None and operating_leases is not None:
        lease_adjusted_capital = economic_invested_capital + operating_leases
        if operating_income is not None and tax_rate is not None and lease_adjusted_capital > 0:
            lease_adjusted_roic_pct = operating_income * (1.0 - tax_rate) / lease_adjusted_capital * 100.0

    flags: list[dict[str, str]] = []
    suppressions: list[str] = []
    if lease_share is not None and lease_share >= 35.0:
        flags.append({
            "code": "LEASE_HEAVY_LIABILITIES", "tone": "CONTEXT",
            "detail": f"Operating leases are {lease_share:.1f}% of total liabilities; do not label total liabilities as financial leverage.",
        })
        suppressions.append("RAW_LIABILITY_LEVERAGE")
    if lease_productivity is not None and lease_productivity >= 1.5:
        flags.append({
            "code": "LEASE_PRODUCTIVE_OPERATING_CAPITAL", "tone": "CONTEXT",
            "detail": f"Revenue is {lease_productivity:.2f}x operating lease liabilities; lease obligations are embedded in the operating footprint.",
        })
    if growth_capex_proxy is not None and capex not in (None, 0) and growth_capex_proxy / capex >= 0.25:
        flags.append({
            "code": "GROWTH_CAPEX_MATERIAL", "tone": "CONTEXT",
            "detail": f"Capex exceeds D&A by {growth_capex_proxy:,.0f}; reported FCF may include material growth reinvestment.",
        })
        suppressions.extend(("NEGATIVE_FCF_AUTOMATIC", "FCF_MARGIN_EROSION_AUTOMATIC"))
    if sbc_to_revenue is not None and sbc_to_revenue >= 5.0:
        flags.append({
            "code": "SBC_MATERIAL", "tone": "CAUTION",
            "detail": f"Share-based compensation is {sbc_to_revenue:.1f}% of revenue; cash FCF overstates post-dilution economics if SBC is ignored.",
        })
        suppressions.append("FCF_POSITIVE_UNADJUSTED")
    if special_charges_to_revenue is not None and special_charges_to_revenue >= 1.0:
        flags.append({
            "code": "SPECIAL_CHARGES_MATERIAL", "tone": "CONTEXT",
            "detail": f"Explicit restructuring/impairment/acquisition charges are {special_charges_to_revenue:.1f}% of revenue.",
        })
        suppressions.append("REPORTED_MARGIN_DETERIORATION_AUTOMATIC")
    if deferred_revenue is not None and total_liabilities not in (None, 0):
        deferred_share = deferred_revenue / total_liabilities * 100.0
        if deferred_share >= 20.0:
            flags.append({
                "code": "CUSTOMER_FINANCING_MATERIAL", "tone": "CONTEXT",
                "detail": f"Contract/deferred revenue is {deferred_share:.1f}% of total liabilities; it is not financial debt.",
            })
            suppressions.append("RAW_LIABILITY_LEVERAGE")
    if treasury_stock is not None and equity is not None and abs(treasury_stock) >= max(abs(equity) * 0.5, 1.0):
        flags.append({
            "code": "BUYBACK_EQUITY_DISTORTION", "tone": "CAUTION",
            "detail": "Treasury stock is large relative to reported equity; book-equity ROIC/leverage denominators can be distorted by buybacks.",
        })
        suppressions.append("BOOK_EQUITY_ROIC_AUTOMATIC")
    if equity is not None and equity <= 0:
        flags.append({
            "code": "NONPOSITIVE_BOOK_EQUITY", "tone": "CAUTION",
            "detail": "Book equity is non-positive; book-capital return and leverage ratios require manual economic interpretation.",
        })
        suppressions.append("BOOK_EQUITY_ROIC_AUTOMATIC")

    sector = str(company_type or "").lower()
    if any(token in sector for token in ("financial", "bank", "insurance", "reit")):
        flags.append({
            "code": "SECTOR_BALANCE_SHEET_POLICY", "tone": "REVIEW",
            "detail": "Financial/REIT balance sheets require sector-specific leverage and cash-flow interpretation; generic industrial rules are disabled.",
        })
        suppressions.extend(("GENERIC_LEVERAGE_SCORE", "GENERIC_WORKING_CAPITAL_SCORE"))

    material_unknown = False
    unresolved: list[str] = []
    if reported_debt is not None and abs(reported_debt) > 0 and not classified_financing:
        unresolved.append("Debt composition is unresolved; using reported debt fallback.")
        material_unknown = True
    if total_liabilities not in (None, 0) and reported_debt is None and gross_economic_debt is None:
        unresolved.append("Financial/debt-like obligations are unresolved against a non-zero liability base.")
        material_unknown = True

    source_count = len([key for key, value in facts.items() if n(value) is not None])
    quality = _quality(
        classified=(gross_economic_debt is not None or reported_debt is not None),
        material_unknown=material_unknown,
        source_count=source_count,
    )

    return {
        "engine_version": ENGINE_VERSION,
        "quality": quality,
        "material_unresolved": material_unknown,
        "unresolved": unresolved,
        "debt_basis": debt_basis,
        "reported_debt": reported_debt,
        "financial_debt": financial_debt,
        "finance_lease_liability": finance_leases,
        "operating_lease_liability": operating_leases,
        "operating_lease_rou_asset": n(facts.get("operating_lease_rou_asset")),
        "finance_lease_rou_asset": n(facts.get("finance_lease_rou_asset")),
        "supplier_finance": supplier_finance,
        "pension_postretirement": pensions,
        "redeemable_nci": redeemable_nci,
        "gross_economic_debt": gross_economic_debt,
        "cash": cash,
        "marketable_securities_current": liquid_investments,
        "restricted_cash": restricted_cash,
        "liquid_offset": liquid_offset,
        "economic_net_debt": economic_net_debt,
        "operating_lease_share_of_liabilities_pct": lease_share,
        "lease_revenue_productivity_x": lease_productivity,
        "operating_lease_cost": lease_cost,
        "total_lease_cost": total_lease_cost,
        "lease_cost_to_revenue_pct": lease_cost_to_revenue,
        "deferred_revenue": deferred_revenue,
        "deferred_tax_liability": deferred_tax,
        "asset_retirement_obligation": aro,
        "goodwill": goodwill,
        "intangibles": intangibles,
        "treasury_stock": treasury_stock,
        "depreciation_amortization": da,
        "maintenance_capex_proxy": maintenance_capex_proxy,
        "growth_capex_proxy": growth_capex_proxy,
        "owner_cash_proxy": owner_cash_proxy,
        "share_based_compensation": sbc,
        "sbc_to_revenue_pct": sbc_to_revenue,
        "fcf_after_sbc": fcf_after_sbc,
        "special_charges": special_charges,
        "special_charges_to_revenue_pct": special_charges_to_revenue,
        "normalized_operating_income_proxy": normalized_operating_income_proxy,
        "economic_invested_capital": economic_invested_capital,
        "economic_roic_pct": economic_roic_pct,
        "lease_adjusted_invested_capital": lease_adjusted_capital,
        "lease_adjusted_roic_pct": lease_adjusted_roic_pct,
        "flags": flags,
        "suppressions": sorted(set(suppressions)),
        "facts": {key: n(value) for key, value in facts.items() if n(value) is not None},
        "fact_sources": fact_sources,
    }


def economic_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return dict(((row.get("quality") or {}).get("economic_reality") or {}))


def metric(snapshot: dict[str, Any] | None, key: str) -> float | None:
    return n((snapshot or {}).get(key))


def has_suppression(snapshot: dict[str, Any] | None, code: str) -> bool:
    return str(code) in set((snapshot or {}).get("suppressions") or [])


__all__ = [
    "ENGINE_VERSION", "INSTANT_TAGS", "DURATION_TAGS", "DEBT_LIKE_KEYS",
    "OPERATING_LEASE_KEYS", "NON_DEBT_LIABILITY_KEYS", "build_economic_reality",
    "economic_from_row", "metric", "has_suppression",
]
