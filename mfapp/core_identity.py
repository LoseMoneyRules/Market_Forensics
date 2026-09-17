from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Index, UniqueConstraint

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Company(db.Model):
    __tablename__ = "mf_company"
    id = db.Column(db.Integer, primary_key=True)
    legal_name = db.Column(db.String(200), nullable=False)
    display_name = db.Column(db.String(200), nullable=False)
    cik = db.Column(db.String(16), index=True)
    lei = db.Column(db.String(32), index=True)
    country = db.Column(db.String(64), nullable=False, default="")
    sector = db.Column(db.String(120), nullable=False, default="")
    industry = db.Column(db.String(160), nullable=False, default="")
    fiscal_year_end = db.Column(db.String(8), nullable=False, default="")
    website = db.Column(db.String(300), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

class Security(db.Model):
    __tablename__ = "mf_security"
    __table_args__ = (
        UniqueConstraint("ticker", "exchange", name="uq_mf_security_symbol_exchange"),
        Index("ix_mf_security_active_ticker", "active", "ticker"),
    )
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("mf_company.id"), nullable=False, index=True)
    ticker = db.Column(db.String(24), nullable=False, index=True)
    exchange = db.Column(db.String(32), nullable=False, default="")
    security_type = db.Column(db.String(32), nullable=False, default="COMMON_STOCK")
    currency = db.Column(db.String(8), nullable=False, default="USD")
    provider_symbol = db.Column(db.String(40), nullable=False, default="")
    is_primary = db.Column(db.Boolean, nullable=False, default=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    validation_source = db.Column(db.String(80), nullable=False, default="")
    validated_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    company = db.relationship("Company", backref="securities")

class Coverage(db.Model):
    __tablename__ = "mf_coverage"
    __table_args__ = (UniqueConstraint("user_id", "security_id", name="uq_mf_coverage_user_security"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    security_id = db.Column(db.Integer, db.ForeignKey("mf_security.id"), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default="MONITOR", index=True)
    research_state = db.Column(db.String(32), nullable=False, default="UNRATED", index=True)
    priority = db.Column(db.Integer, nullable=False, default=0)
    owner_summary = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    security = db.relationship("Security", backref="coverage_rows")

class MarketSnapshot(db.Model):
    __tablename__ = "mf_market_snapshot"
    __table_args__ = (Index("ix_mf_market_snapshot_security_asof", "security_id", "as_of"),)
    id = db.Column(db.Integer, primary_key=True)
    security_id = db.Column(db.Integer, db.ForeignKey("mf_security.id"), nullable=False, index=True)
    provider = db.Column(db.String(80), nullable=False)
    price = db.Column(db.Numeric(24, 8), nullable=False)
    currency = db.Column(db.String(8), nullable=False, default="USD")
    as_of = db.Column(db.DateTime, nullable=False, index=True)
    quality = db.Column(db.String(32), nullable=False, default="OBSERVED")
    payload = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    security = db.relationship("Security", backref="market_snapshots")

class Source(db.Model):
    __tablename__ = "mf_source"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("mf_company.id"), index=True)
    provider = db.Column(db.String(80), nullable=False)
    source_type = db.Column(db.String(48), nullable=False)
    title = db.Column(db.String(300), nullable=False, default="")
    url = db.Column(db.Text, nullable=False, default="")
    accession_no = db.Column(db.String(40), nullable=False, default="")
    published_at = db.Column(db.DateTime)
    retrieved_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    content_hash = db.Column(db.String(64), nullable=False, default="")
    meta = db.Column(db.JSON, nullable=False, default=dict)
    company = db.relationship("Company", backref="sources")

class FinancialPeriod(db.Model):
    __tablename__ = "mf_financial_period"
    __table_args__ = (
        UniqueConstraint("company_id", "period_type", "fiscal_year", "end_date", name="uq_mf_financial_period"),
        Index("ix_mf_financial_period_company_year", "company_id", "fiscal_year"),
    )
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("mf_company.id"), nullable=False, index=True)
    source_id = db.Column(db.Integer, db.ForeignKey("mf_source.id"), index=True)
    period_type = db.Column(db.String(16), nullable=False, default="FY")
    fiscal_year = db.Column(db.Integer, nullable=False)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date, nullable=False)
    filed_at = db.Column(db.Date)
    accession_no = db.Column(db.String(40), nullable=False, default="")
    currency = db.Column(db.String(8), nullable=False, default="USD")
    is_restated = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    company = db.relationship("Company", backref="financial_periods")

class RawFinancialFact(db.Model):
    __tablename__ = "mf_raw_financial_fact"
    __table_args__ = (Index("ix_mf_raw_fact_period_tag", "financial_period_id", "tag"),)
    id = db.Column(db.Integer, primary_key=True)
    financial_period_id = db.Column(db.Integer, db.ForeignKey("mf_financial_period.id"), nullable=False, index=True)
    source_id = db.Column(db.Integer, db.ForeignKey("mf_source.id"), index=True)
    taxonomy = db.Column(db.String(32), nullable=False, default="us-gaap")
    tag = db.Column(db.String(180), nullable=False)
    unit = db.Column(db.String(40), nullable=False, default="")
    value = db.Column(db.Numeric(34, 8))
    context_hash = db.Column(db.String(64), nullable=False, default="")
    filed_at = db.Column(db.Date)
    raw_payload = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

class NormalizedFinancial(db.Model):
    __tablename__ = "mf_normalized_financial"
    __table_args__ = (UniqueConstraint("financial_period_id", name="uq_mf_normalized_period"),)
    id = db.Column(db.Integer, primary_key=True)
    financial_period_id = db.Column(db.Integer, db.ForeignKey("mf_financial_period.id"), nullable=False, unique=True, index=True)
    revenue = db.Column(db.Numeric(34, 8))
    cogs = db.Column(db.Numeric(34, 8))
    gross_profit = db.Column(db.Numeric(34, 8))
    operating_expenses = db.Column(db.Numeric(34, 8))
    operating_income = db.Column(db.Numeric(34, 8))
    pretax_income = db.Column(db.Numeric(34, 8))
    income_tax = db.Column(db.Numeric(34, 8))
    net_income = db.Column(db.Numeric(34, 8))
    cfo = db.Column(db.Numeric(34, 8))
    capex = db.Column(db.Numeric(34, 8))
    fcf = db.Column(db.Numeric(34, 8))
    buybacks = db.Column(db.Numeric(34, 8))
    dividends = db.Column(db.Numeric(34, 8))
    diluted_shares = db.Column(db.Numeric(34, 8))
    shares_outstanding = db.Column(db.Numeric(34, 8))
    cash = db.Column(db.Numeric(34, 8))
    debt = db.Column(db.Numeric(34, 8))
    receivables = db.Column(db.Numeric(34, 8))
    inventory = db.Column(db.Numeric(34, 8))
    payables = db.Column(db.Numeric(34, 8))
    assets = db.Column(db.Numeric(34, 8))
    liabilities = db.Column(db.Numeric(34, 8))
    equity = db.Column(db.Numeric(34, 8))
    source_map = db.Column(db.JSON, nullable=False, default=dict)
    quality = db.Column(db.JSON, nullable=False, default=dict)
    calculation_version = db.Column(db.String(32), nullable=False, default="0.2.0")
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    period = db.relationship("FinancialPeriod", backref=db.backref("normalized", uselist=False))
