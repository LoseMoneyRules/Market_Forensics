from __future__ import annotations

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape
import ipaddress
import socket
import textwrap
import zipfile
from urllib.parse import urlparse

import requests

from .extensions import db
from .models import UserPreference



_REPORT_LIBS_LOADED: bool | None = None


def _load_report_libs() -> bool:
    """Load heavyweight report dependencies only when an export is requested.

    Production startup and /health must never depend on optional report packages.
    """
    global _REPORT_LIBS_LOADED
    if _REPORT_LIBS_LOADED is not None:
        return _REPORT_LIBS_LOADED
    try:
        from docx import Document as _Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH as _WD_ALIGN_PARAGRAPH
        from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT as _WD_CELL_VERTICAL_ALIGNMENT
        from docx.oxml import OxmlElement as _OxmlElement
        from docx.oxml.ns import qn as _qn
        from docx.shared import Inches as _Inches, Pt as _Pt, RGBColor as _RGBColor
        from PIL import Image as _PILImage, ImageDraw as _ImageDraw, ImageFont as _ImageFont
        from reportlab.lib import colors as _colors
        from reportlab.lib.enums import TA_LEFT as _TA_LEFT
        from reportlab.lib.pagesizes import LETTER as _LETTER, landscape as _landscape
        from reportlab.lib.styles import ParagraphStyle as _ParagraphStyle, getSampleStyleSheet as _getSampleStyleSheet
        from reportlab.lib.units import inch as _inch
        from reportlab.platypus import (
            SimpleDocTemplate as _SimpleDocTemplate, Paragraph as _Paragraph, Spacer as _Spacer,
            Table as _Table, TableStyle as _TableStyle, PageBreak as _PageBreak, Image as _RLImage,
        )
    except Exception:
        _REPORT_LIBS_LOADED = False
        return False
    globals().update({
        "Document": _Document, "WD_ALIGN_PARAGRAPH": _WD_ALIGN_PARAGRAPH, "WD_CELL_VERTICAL_ALIGNMENT": _WD_CELL_VERTICAL_ALIGNMENT,
        "OxmlElement": _OxmlElement, "qn": _qn, "RGBColor": _RGBColor, "Inches": _Inches, "Pt": _Pt,
        "PILImage": _PILImage, "ImageDraw": _ImageDraw, "ImageFont": _ImageFont,
        "colors": _colors, "TA_LEFT": _TA_LEFT, "LETTER": _LETTER, "landscape": _landscape,
        "ParagraphStyle": _ParagraphStyle, "getSampleStyleSheet": _getSampleStyleSheet, "inch": _inch,
        "SimpleDocTemplate": _SimpleDocTemplate, "Paragraph": _Paragraph, "Spacer": _Spacer,
        "Table": _Table, "TableStyle": _TableStyle, "PageBreak": _PageBreak, "RLImage": _RLImage,
    })
    _REPORT_LIBS_LOADED = True
    return True


def report_backend_status() -> dict[str, str]:
    return {
        "backend": "rich" if _load_report_libs() else "stdlib-fallback",
        "startup_safe": "yes",
    }



def _plain_research_lines(data: dict[str, Any]) -> list[str]:
    """Readable emergency text representation of the canonical report contract."""
    brand = data.get("branding") or {}
    ident = data.get("identity") or {}
    valuation = data.get("valuation") or {}
    lenses = data.get("decision_lenses") or {}
    thesis = data.get("thesis") or {}
    evidence = data.get("evidence") or {}
    lines = [
        str(brand.get("title") or "Market Forensics"),
        f"{ident.get('ticker') or ''} | {ident.get('company') or ''}",
        f"RESEARCH CONCLUSION: {data.get('conclusion') or 'DATA REVIEW'}",
        f"Current {_money(valuation.get('current_price'))} | Bear {_money(valuation.get('bear'))} | Base {_money(valuation.get('base'))} | Bull {_money(valuation.get('bull'))} | Base gap {_pct(valuation.get('base_gap_pct'))}",
        f"Valuation quality: {str(valuation.get('base_quality') or 'DATA_WARNING').replace('_',' ')}",
        f"Value {lenses.get('value') or '-'} | Expectations {lenses.get('expectations') or '-'} | Variant {lenses.get('variant') or '-'} | Path {lenses.get('path') or '-'} | Model confidence {lenses.get('model_confidence') or '-'}",
        "",
        "MARKET VIEW", _txt(thesis.get("market_view")) or "-",
        "", "OUR VIEW / VARIANT", _txt(thesis.get("our_view")) or "-",
        "", "WHAT MUST BE TRUE",
    ]
    for row in (thesis.get("what_must_be_true") or [])[:6]:
        lines.append("• " + _txt(row.get("text")))
    lines += ["", "WHAT WOULD PROVE US WRONG"]
    for row in (thesis.get("what_proves_wrong") or [])[:6]:
        lines.append("• " + _txt(row.get("text")))
    lines += ["", "EVIDENCE FOR"]
    for row in (evidence.get("for") or [])[:8]:
        lines.append(f"• {row.get('label') or 'Evidence'} - {row.get('detail') or ''}")
    lines += ["", "EVIDENCE AGAINST"]
    for row in (evidence.get("against") or [])[:8]:
        lines.append(f"• {row.get('label') or 'Evidence'} - {row.get('detail') or ''}")
    if data.get("mode") == "full":
        fundamentals = data.get("fundamentals") or {}
        lines += ["", "BUSINESS", _txt((data.get("business") or {}).get("summary")) or "-"]
        lines += ["", "FUNDAMENTALS"]
        for row in (fundamentals.get("history") or [])[-8:]:
            lines.append(
                f"{row.get('period') or '-'} | Revenue {_money(row.get('revenue'))} | "
                f"Op margin {_pct(row.get('operating_margin_pct'))} | FCF {_money(row.get('fcf'))} | "
                f"Inv/Rev {_pct(row.get('inventory_to_revenue_pct'))} | Rec/Rev {_pct(row.get('receivables_to_revenue_pct'))}"
            )
        lines += ["", "MANAGEMENT PROMISES"]
        for row in ((data.get("management") or {}).get("promises") or [])[:20]:
            lines.append(f"{row.get('target_period') or row.get('target_year') or '-'} | {row.get('metric') or '-'} | {row.get('status') or '-'} | actual {row.get('actual') if row.get('actual') is not None else '-'}")
        lines += ["", "TAPE"]
        tm=(data.get("tape") or {}).get("metrics") or {}
        lines.append(f"Regime {tm.get('regime') or '-'} | Rank {tm.get('rank') or '-'} | Confidence {tm.get('confidence') or '-'} | Net Tape {tm.get('net_tape') if tm.get('net_tape') is not None else '-'}")
        monitoring=data.get("monitoring") or {}
        lines += ["", "THESIS INVALIDATION", _txt(monitoring.get("thesis_invalidation")) or "-"]
        lines += ["", "VALIDATION"]
        validation=data.get("validation") or {}
        lines.append(
            f"{validation.get('state') or 'NOT RUN'} | Reliability {validation.get('reliability') if validation.get('reliability') is not None else '-'} | "
            f"Samples {validation.get('sample_count') or 0}"
        )
        lines += ["", "SOURCES"]
        for row in (data.get("sources") or [])[:40]:
            lines.append(f"• {row.get('provider') or '-'} | {row.get('type') or '-'} | {row.get('title') or '-'} | {row.get('retrieved_at') or '-'}")
    lines += ["", str(brand.get("footer") or "Lose Money Rules")]
    return lines

def _pdf_escape(text: str) -> str:
    return str(text).replace("\\","\\\\").replace("(","\\(").replace(")","\\)")


def _fallback_pdf(lines: list[str], *, landscape_page: bool = False) -> BytesIO:
    width,height=(792,612) if landscape_page else (612,792)
    margin=42; font_size=11; leading=15
    usable=max(20,int((height-2*margin)/leading))
    wrapped: list[str] = []
    wrap_width=125 if landscape_page else 92
    for line in lines:
        chunks=textwrap.wrap(str(line or ""), width=wrap_width, replace_whitespace=False, drop_whitespace=True) or [""]
        wrapped.extend(chunks)
    pages=[wrapped[i:i+usable] for i in range(0,len(wrapped),usable)] or [[]]
    objects: list[bytes]=[]
    # IDs: 1 catalog, 2 pages, 3 font; then page/content pairs.
    page_ids=[]
    for idx in range(len(pages)):
        page_ids.append(4+idx*2)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids=" ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for idx,page_lines in enumerate(pages):
        page_id=4+idx*2; content_id=page_id+1
        stream=["BT",f"/F1 {font_size} Tf",f"{margin} {height-margin} Td",f"{leading} TL"]
        for j,line in enumerate(page_lines):
            if j: stream.append("T*")
            safe=_pdf_escape(str(line).encode("cp1252","replace").decode("cp1252"))
            stream.append(f"({safe}) Tj")
        stream.append("ET")
        content="\n".join(stream).encode("cp1252","replace")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode())
        objects.append(f"<< /Length {len(content)} >>\nstream\n".encode()+content+b"\nendstream")
    out=BytesIO(); out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets=[0]
    for i,obj in enumerate(objects, start=1):
        offsets.append(out.tell()); out.write(f"{i} 0 obj\n".encode()); out.write(obj); out.write(b"\nendobj\n")
    xref=out.tell(); out.write(f"xref\n0 {len(objects)+1}\n".encode()); out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]: out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    out.seek(0); return out


def _fallback_docx(lines: list[str]) -> BytesIO:
    def p(text: str, bold: bool=False) -> str:
        safe=escape(str(text or ""))
        rpr="<w:rPr><w:b/></w:rPr>" if bold else ""
        return f'<w:p><w:r>{rpr}<w:t xml:space="preserve">{safe}</w:t></w:r></w:p>'
    body=[]
    heading_words={"RESEARCH LENSES","THESIS","COUNTER-EVIDENCE","MARKET VIEW","OUR VARIANT","EVIDENCE FOR","EVIDENCE AGAINST","BUSINESS","FUNDAMENTALS","EXPECTATIONS","VALUATION","BEAR CASE","CATALYSTS","FINANCIAL FLOWS","MANAGEMENT","TAPE / FLOWS","RESEARCH INVALIDATION","AUTOMATIC TRIANGULATION","MANAGEMENT PROMISES VS ACTUALS","SOURCES"}
    for idx,line in enumerate(lines):
        body.append(p(line, bold=(idx<3 or str(line).upper() in heading_words)))
    document='<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'+        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+        ''.join(body)+'<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720"/></w:sectPr></w:body></w:document>'
    content_types='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    rels='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    out=BytesIO()
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",content_types)
        z.writestr("_rels/.rels",rels)
        z.writestr("word/document.xml",document)
    out.seek(0); return out


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError, ArithmeticError):
        return "—"


def _pct(value: Any) -> str:
    try:
        return f"{float(value):+.1f}%"
    except (TypeError, ValueError, ArithmeticError):
        return "—"


def _txt(value: Any) -> str:
    return str(value or "").strip()


def get_report_branding(user_id: int, default_logo_url: str = "") -> dict[str, str]:
    row = UserPreference.query.filter_by(user_id=user_id, key="report_branding").first()
    value = dict((row.value or {}) if row else {})
    return {
        "title": str(value.get("title") or "Market Forensics"),
        "prepared_by": str(value.get("prepared_by") or ""),
        "footer": str(value.get("footer") or "Lose Money Rules"),
        "logo_url": str(value.get("logo_url") or default_logo_url or ""),
    }


def set_report_branding(user_id: int, *, title: str, prepared_by: str, footer: str, logo_url: str) -> dict[str, str]:
    parsed = urlparse(logo_url) if logo_url else None
    if parsed and parsed.scheme not in {"https"}:
        raise ValueError("Report logo URL must use HTTPS.")
    row = UserPreference.query.filter_by(user_id=user_id, key="report_branding").first()
    if row is None:
        row = UserPreference(user_id=user_id, key="report_branding", value={})
        db.session.add(row)
    row.value = {
        "title": str(title or "Market Forensics")[:100],
        "prepared_by": str(prepared_by or "")[:120],
        "footer": str(footer or "Lose Money Rules")[:120],
        "logo_url": str(logo_url or "")[:500],
    }
    db.session.commit()
    return dict(row.value)


def _safe_logo(url: str) -> BytesIO | None:
    if not url or not _load_report_libs():
        return None
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return None
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        for addr in addresses:
            ip = ipaddress.ip_address(addr[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return None
        response = requests.get(url, timeout=8, allow_redirects=False, stream=True)
        if response.status_code != 200:
            return None
        content_type = str(response.headers.get("content-type") or "").lower()
        if not content_type.startswith("image/"):
            return None
        raw = response.raw.read(2_000_001, decode_content=True)
        if len(raw) > 2_000_000:
            return None
        image = PILImage.open(BytesIO(raw))
        image.thumbnail((1200, 400))
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA")
        out = BytesIO()
        image.save(out, format="PNG")
        out.seek(0)
        return out
    except Exception:
        return None



def research_report_data(ctx: dict[str, Any], *, mode: str = "full", branding: dict[str, str] | None = None) -> dict[str, Any]:
    """Build the 0.2.14 canonical report contract from stored/materialized data only."""
    from .report_contract import build_report_data
    return build_report_data(ctx, mode=mode, branding=branding)


def safe_research_report_data(ctx: dict[str, Any], *, mode: str = "full", branding: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        return research_report_data(ctx, mode=mode, branding=branding)
    except Exception:
        security = ctx.get("security")
        company = ctx.get("company")
        market = ctx.get("market")
        research = ctx.get("research")
        risk = ctx.get("risk")
        valuation = dict(ctx.get("valuation") or {})
        intelligence = dict(ctx.get("intelligence") or {})
        lenses = dict(ctx.get("decision_lenses") or {})
        readiness = dict(ctx.get("readiness") or {})
        brand = dict(branding or {})
        current_price = None
        try:
            current_price = float(market.price) if market and market.price is not None else None
        except Exception:
            current_price = None
        return {
            "contract_version": "0.2.14-safe",
            "mode": "executive" if str(mode).lower() == "executive" else "full",
            "branding": {
                "title": str(brand.get("title") or "Market Forensics"),
                "prepared_by": str(brand.get("prepared_by") or ""),
                "footer": str(brand.get("footer") or "Lose Money Rules"),
                "logo_url": str(brand.get("logo_url") or ""),
            },
            "identity": {
                "ticker": getattr(security, "ticker", "UNKNOWN"),
                "company": getattr(company, "display_name", "Unknown company"),
                "sector": getattr(company, "sector", "") or "",
                "industry": getattr(company, "industry", "") or "",
                "market_provider": getattr(market, "provider", "") if market else "",
                "market_as_of": market.as_of.isoformat() if market and getattr(market, "as_of", None) else "",
            },
            "conclusion": lenses.get("research_conclusion") or "DATA REVIEW",
            "decision_lenses": {
                "value": lenses.get("value") or intelligence.get("stance") or "UNVERIFIED",
                "expectations": lenses.get("expectations") or "UNAVAILABLE",
                "variant": lenses.get("variant") or "UNPROVEN",
                "path": lenses.get("path") or "UNCLEAR",
                "model_confidence": lenses.get("model_confidence") or intelligence.get("confidence") or "UNVALIDATED",
                "thesis_control": lenses.get("thesis_control") or "UNRESOLVED",
                "business": lenses.get("business") or "UNPROVEN",
                "rows": list(lenses.get("rows") or []),
            },
            "valuation": {
                "current_price": current_price,
                "bear": valuation.get("bear"), "base": valuation.get("base"), "bull": valuation.get("bull"),
                "expected_value": valuation.get("expected_value"),
                "base_gap_pct": intelligence.get("base_gap_pct"),
                "quality": valuation.get("quality") or "DATA_WARNING",
                "base_quality": valuation.get("base_quality") or "DATA_WARNING",
                "decision_grade": bool(valuation.get("decision_grade")),
                "provisional": not bool(valuation.get("decision_grade")),
                "warnings": ["Report generated from safe stored-data fallback."],
                "share_basis": {"shares": None, "source": "UNRESOLVED", "verified": False, "note": ""},
                "weights": {}, "horizon_years": 5, "calibration": {}, "scenarios": [], "engine_version": "",
            },
            "thesis": {
                "thesis": _txt(getattr(research, "thesis", "")),
                "counter_evidence": _txt(getattr(research, "counter_evidence", "")),
                "market_view": _txt(getattr(research, "variant_market", "")),
                "our_view": _txt(getattr(research, "variant_us", "")),
                "variant_evidence": _txt(getattr(research, "variant_evidence", "")),
                "what_must_be_true": [], "what_proves_wrong": [],
            },
            "evidence": {"for": [], "against": [], "score": None, "warnings": ["Safe report fallback."], "blockers": []},
            "business": {"summary": _txt(getattr(research, "business", ""))},
            "fundamentals": {"current": {}, "history": [], "summary": _txt(getattr(research, "numbers", ""))},
            "expectations": {"summary": _txt(getattr(research, "expectations", "")), "implied": {}, "rows": []},
            "flows": {"summary": _txt(getattr(research, "flows_summary", "")), "periods": []},
            "management": {"summary": _txt(getattr(research, "management_summary", "")), "engine": {}, "accountability": [], "promises": [], "assessments": []},
            "catalysts": [], "bear_case": [],
            "tape": {"summary": _txt(getattr(research, "tape_summary", "")), "metrics": {}, "market": [], "daily_market": [], "short_interest": [], "short_volume": [], "institutional_flow": [], "tape_daily": [], "ats": [], "what_changed": "", "what_would_change_regime": ""},
            "monitoring": {"thesis_invalidation": _txt(getattr(risk, "thesis_invalidation", "")), "locked_at": "", "summary": _txt(getattr(research, "risk_summary", "")), "rules": []},
            "validation": {"state": (readiness.get("validation") or {}).get("state") or "NOT RUN", "available": False, "status": "NOT RUN", "sample_count": 0, "samples": []},
            "sources": [], "price_history": [],
            "readiness": {"done": int(readiness.get("done") or 0), "total": int(readiness.get("total") or 0), "ready_to_validate": bool(readiness.get("ready_to_validate"))},
            "triangulation": {},
            "data_contract": {"materialized_cache_event": None, "cache_generated_at": None, "provider_refresh_started": False, "heavy_analytics_started": False},
        }

# Regression contract retained in the V2 renderer: ["Period","Metric","Promise","Actual","Status"] and row.get("target_period").
def render_docx(data: dict[str, Any]) -> BytesIO:
    if not _load_report_libs():
        return _fallback_docx(_plain_research_lines(data))
    from .report_render_v2 import render_docx_v2
    brand = data.get("branding") or {}
    logo = _safe_logo(str(brand.get("logo_url") or ""))
    return render_docx_v2(data, logo_stream=logo)


def render_pdf(data: dict[str, Any]) -> BytesIO:
    if not _load_report_libs():
        return _fallback_pdf(_plain_research_lines(data))
    from .report_render_v2 import render_pdf_v2
    brand = data.get("branding") or {}
    logo = _safe_logo(str(brand.get("logo_url") or ""))
    return render_pdf_v2(data, logo_stream=logo)

def emergency_research_report_stream(fmt: str, *, ticker: str = "", company: str = "", mode: str = "full") -> BytesIO:
    """Last-resort, dependency-free report stream.

    This is intentionally tiny and uses only the stdlib fallback writers. A
    report request must still return a valid downloadable artifact when rich
    rendering, branding, or audit persistence fails unexpectedly.
    """
    lines = [
        "Market Forensics",
        f"{ticker or 'Security'} · {company or 'Research report'}",
        f"{str(mode or 'full').upper()} REPORT",
        "",
        "Report rendering degraded safely. Core research remains available in the application.",
        "Retry after reviewing Settings / Data if rich report diagnostics show an issue.",
        "",
        "Lose Money Rules",
    ]
    return _fallback_docx(lines) if str(fmt).lower() == "docx" else _fallback_pdf(lines)


def emergency_discovery_report_stream() -> BytesIO:
    return _fallback_pdf([
        "Market Forensics · Discovery",
        "",
        "Discovery report rendering degraded safely.",
        "The application retained the underlying scan; retry the export after reviewing report diagnostics.",
        "",
        "Lose Money Rules",
    ], landscape_page=True)


def render_docx_safe(data: dict[str, Any]) -> BytesIO:
    try:
        return render_docx(data)
    except Exception:
        return _fallback_docx(_plain_research_lines(data))


def render_pdf_safe(data: dict[str, Any]) -> BytesIO:
    try:
        return render_pdf(data)
    except Exception:
        return _fallback_pdf(_plain_research_lines(data))



def render_discovery_pdf(scan: dict[str, Any], branding: dict[str, str] | None = None) -> BytesIO:
    """Render Discovery as an investment-opportunity landscape, not a raw scan dump."""
    branding = dict(branding or {})
    candidates = list(scan.get("candidates") or [])[:60]

    def _number(value, digits=2):
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError, ArithmeticError):
            return "—"

    def _gap(value):
        try:
            return f"{float(value):+.1f}%"
        except (TypeError, ValueError, ArithmeticError):
            return "—"

    def _compact_money(value):
        try:
            value=float(value)
        except (TypeError, ValueError, ArithmeticError):
            return "—"
        sign="-" if value < 0 else ""
        value=abs(value)
        if value >= 1_000_000_000:
            return f"{sign}\${value/1_000_000_000:.1f}B"
        if value >= 1_000_000:
            return f"{sign}\${value/1_000_000:.1f}M"
        if value >= 1_000:
            return f"{sign}\${value/1_000:.0f}K"
        return f"{sign}\${value:,.0f}"

    if not _load_report_libs():
        lines = [str(branding.get("title") or "Market Forensics") + " · Discovery", "Opportunity landscape · latest completed broad-universe scan", ""]
        for idx, row in enumerate(candidates, start=1):
            lines.append(
                f"{idx}. {row.get('ticker') or ''} · {row.get('priority') or 'WATCH'} · {row.get('research_side') or 'RESEARCH'} · "
                f"price {_number(row.get('price'))} · Bear {_number(row.get('bear'))} · Base {_number(row.get('base') if row.get('base') is not None else row.get('fair_value'))} · "
                f"Bull {_number(row.get('bull'))} · gap {_gap(row.get('base_gap_pct'))} · {row.get('radar_label') or ''}"
            )
        lines += ["", str(branding.get("footer") or "Lose Money Rules")]
        return _fallback_pdf(lines, landscape_page=True)

    out = BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(LETTER), rightMargin=.34*inch, leftMargin=.34*inch, topMargin=.32*inch, bottomMargin=.34*inch)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="MFDiscBrand", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=7.4, leading=9, textColor=colors.HexColor("#3a6f99"), spaceAfter=1))
    styles.add(ParagraphStyle(name="MFDiscTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=17, leading=19, textColor=colors.HexColor("#0b1f33"), alignment=TA_LEFT, spaceAfter=2))
    styles.add(ParagraphStyle(name="MFDiscBody", parent=styles["BodyText"], fontSize=8.6, leading=10.8, textColor=colors.HexColor("#344454"), spaceAfter=3))
    styles.add(ParagraphStyle(name="MFDiscSmall", parent=styles["BodyText"], fontSize=6.5, leading=8.0, textColor=colors.HexColor("#667788"), spaceAfter=1))
    story = []
    logo = _safe_logo(str(branding.get("logo_url") or ""))
    if logo:
        story += [RLImage(logo, width=.82*inch, height=.28*inch), Spacer(1,2)]
    story += [
        Paragraph(escape(str(branding.get("title") or "MARKET FORENSICS").upper()), styles["MFDiscBrand"]),
        Paragraph("Discovery · Opportunity Landscape", styles["MFDiscTitle"]),
        Paragraph("Latest completed broad-universe scan. P1/P2 are research leads; WATCH preserves emerging or verification-needed dislocations. Discovery is a research funnel, not an investment recommendation.", styles["MFDiscBody"]),
    ]

    summary = [
        ("CANDIDATES", str(len(candidates))),
        ("LONG", str(scan.get("long_count") or sum(1 for x in candidates if x.get("research_side")=="LONG" and x.get("priority")!="WATCH"))),
        ("SHORT", str(scan.get("short_count") or sum(1 for x in candidates if x.get("research_side")=="SHORT" and x.get("priority")!="WATCH"))),
        ("WATCH", str(scan.get("watch_count") or sum(1 for x in candidates if x.get("priority")=="WATCH"))),
        ("P1", str(scan.get("p1_count") or sum(1 for x in candidates if x.get("priority")=="P1"))),
        ("P2", str(scan.get("p2_count") or sum(1 for x in candidates if x.get("priority")=="P2"))),
    ]
    summary_cells=[Paragraph('<font size="6.5" color="#667788"><b>'+escape(label)+'</b></font><br/><font size="11" color="#0b1f33"><b>'+escape(value)+'</b></font>',styles["MFDiscBody"]) for label,value in summary]
    summary_table=Table([summary_cells], colWidths=[1.62*inch]*6)
    summary_table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#f7fafc")),("BOX",(0,0),(-1,-1),.4,colors.HexColor("#d9e0e6")),
        ("INNERGRID",(0,0),(-1,-1),.25,colors.HexColor("#d9e0e6")),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("ALIGN",(0,0),(-1,-1),"CENTER"),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    story += [summary_table, Spacer(1,5)]

    rows = [["#","Ticker","Tier","Side","Price","Bear","Base","Bull","Gap","Methods","$ Vol","Research reason / invalidation"]]
    for idx, row in enumerate(candidates, start=1):
        signals=list(row.get("forensic_signals") or [])
        signal = str((signals[0] or {}).get("detail") or "") if signals and isinstance(signals[0],dict) else ""
        accounting=list(row.get("accounting_context") or [])
        accounting_note = str((accounting[0] or {}).get("detail") or "") if accounting and isinstance(accounting[0],dict) else ""
        reason = " · ".join(x for x in [
            str(row.get("radar_label") or ""),
            str(row.get("priority_reason") or ""),
            str(row.get("stage2_selection_reason") or ""),
            signal,
            accounting_note,
        ] if x)
        invalidates=str(row.get("what_invalidates") or "")
        if invalidates:
            reason += (" | Invalidates: " if reason else "Invalidates: ") + invalidates
        rows.append([
            str(idx), str(row.get("ticker") or ""), str(row.get("priority") or "WATCH"), str(row.get("research_side") or ""),
            _number(row.get("price")), _number(row.get("bear")), _number(row.get("base") if row.get("base") is not None else row.get("fair_value")),
            _number(row.get("bull")), _gap(row.get("base_gap_pct")), str(int(row.get("valuation_methods") or 0)),
            _compact_money(row.get("dollar_volume")), reason,
        ])
    table = Table(rows, colWidths=[.22*inch,.42*inch,.40*inch,.42*inch,.48*inch,.47*inch,.47*inch,.47*inch,.48*inch,.43*inch,.58*inch,4.00*inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf0f5")),("TEXTCOLOR",(0,0),(-1,0),colors.HexColor("#0b1f33")),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,0),7.1),("FONTSIZE",(0,1),(-1,-1),7.0),
        ("TEXTCOLOR",(0,1),(-1,-1),colors.HexColor("#263746")),("GRID",(0,0),(-1,-1),.22,colors.HexColor("#d9e0e6")),
        ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),2.5),("RIGHTPADDING",(0,0),(-1,-1),2.5),
        ("TOPPADDING",(0,0),(-1,-1),2.8),("BOTTOMPADDING",(0,0),(-1,-1),2.8),
    ]))
    story += [table, Spacer(1,4)]

    source_bits=[
        f"Universe: {scan.get('universe_source') or 'Discovery universe'}", f"Contract: {scan.get('contract_version') or '—'}",
        f"Stage 0: {scan.get('stage0_count') or 0}", f"Stage 1 scanned: {scan.get('stage1_scanned_count') or 0}",
        f"Stage 1.5 hypotheses: {scan.get('stage15_mispricing_count') or 0}", f"Stage 2 enriched: {scan.get('stage2_enriched_count') or 0}", f"Provider calls: {scan.get('provider_call_total') or 0}",
        f"Materialized: {scan.get('materialized_at') or '—'}",
    ]
    story.append(Paragraph(escape(" · ".join(source_bits)), styles["MFDiscSmall"]))
    story.append(Paragraph(escape(f"{branding.get('footer') or 'Lose Money Rules'} · Discovery data provenance / scan diagnostics"), styles["MFDiscSmall"]))
    doc.build(story)
    out.seek(0)
    return out

def render_discovery_pdf_safe(scan: dict[str, Any], branding: dict[str, str] | None = None) -> BytesIO:
    try:
        return render_discovery_pdf(scan, branding)
    except Exception:
        branding = dict(branding or {})
        lines = [str(branding.get("title") or "Market Forensics")+" · Discovery", ""]
        for idx, row in enumerate(list(scan.get("candidates") or [])[:60], start=1):
            try:
                gap = f"{float(row.get('base_gap_pct')):+.1f}%" if row.get("base_gap_pct") is not None else "—"
            except (TypeError, ValueError, ArithmeticError):
                gap = "—"
            lines.append(
                f"{idx}. {row.get('ticker') or ''} · {row.get('priority') or 'WATCH'} · {row.get('research_side') or 'RESEARCH'} · "
                f"Base gap {gap} · {int(row.get('valuation_methods') or 0)} methods"
            )
        lines += ["", str(branding.get("footer") or "Lose Money Rules")]
        return _fallback_pdf(lines, landscape_page=True)


__all__=["get_report_branding","set_report_branding","research_report_data","safe_research_report_data","render_docx","render_pdf","render_discovery_pdf","render_docx_safe","render_pdf_safe","render_discovery_pdf_safe","emergency_research_report_stream","emergency_discovery_report_stream","report_backend_status"]
