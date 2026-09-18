from __future__ import annotations

# Stable import surface for the web-native MariaDB core. Definitions are split by
# responsibility to keep the model layer readable and shared-hosting friendly.
from .core_identity import Company, Security, Coverage, MarketSnapshot, Source, FinancialPeriod, RawFinancialFact, NormalizedFinancial
from .core_research import ResearchState, ResearchVersion, Expectation, ValuationModel, ValuationScenario, BearCaseItem, Catalyst, ManagementAssessment, FinancialFlow, MonitoringRule, MonitoringHistory, RiskPlan, InvestmentState, Position, PositionProfile, PortfolioRiskPlan, DecisionJournal
from .core_operations import Event, Provenance, DataQualityIssue, Job, RefreshRun, Alert, HistoricalPrice, HistoricalTestRun, HistoricalTestSample
from .core_publication import Snapshot, Publication, CalculationRun, SchemaMigration
from .research_gates import ResearchGateApproval

__all__ = [
    "Company", "Security", "Coverage", "MarketSnapshot", "Source", "FinancialPeriod",
    "RawFinancialFact", "NormalizedFinancial", "ResearchState", "ResearchVersion",
    "Expectation", "ValuationModel", "ValuationScenario", "BearCaseItem", "Catalyst",
    "ManagementAssessment", "FinancialFlow", "MonitoringRule", "MonitoringHistory",
    "RiskPlan", "InvestmentState", "Position", "PositionProfile", "PortfolioRiskPlan", "DecisionJournal", "Event", "Provenance",
    "DataQualityIssue", "Job", "RefreshRun", "Alert", "HistoricalPrice", "HistoricalTestRun",
    "HistoricalTestSample", "Snapshot", "Publication", "CalculationRun", "SchemaMigration",
    "ResearchGateApproval",
]
