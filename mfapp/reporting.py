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

from .core_models import BearCaseItem, Catalyst, Expectation, ManagementAssessment, Source
from .current_financials import annual_rows, current_row
from .extensions import db
from .models import UserPreference
from .management_promises import evaluate_promises



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
    brand = data.get("branding") or {}
    lines = [
        str(brand.get("title") or "Market Forensics"),
        f"{data.get('ticker') or ''} · {data.get('company') or ''}",
        f"Research conclusion: {data.get('action') or 'DATA REVIEW'}",
        f"Value / confidence: {data.get('stance') or '—'} · {data.get('confidence') or '—'}",
        f"Market {_money(data.get('market_price'))} · Bear {_money(data.get('bear'))} · Base {_money(data.get('base'))} · Bull {_money(data.get('bull'))} · Base gap {_pct(data.get('base_gap_pct'))}",
        "",
        "RESEARCH LENSES",
    ]
    for row in data.get("decision_lenses") or []:
        lines.append(f"{row.get('label') or row.get('key') or 'Lens'}: {row.get('state') or '—'}")
    lines += [
        "",
        "THESIS", _txt(data.get("thesis")) or "—",
        "",
        "COUNTER-EVIDENCE", _txt(data.get("counter_evidence")) or "—",
        "",
        "MARKET VIEW", _txt(data.get("variant_market")) or "—",
        "",
        "OUR VARIANT", _txt(data.get("variant_us")) or "—",
    ]
    implied = data.get("implied_expectations") or {}
    if implied.get("available"):
        lines += ["", f"PRICE-IMPLIED EXPECTATIONS · {implied.get('classification') or '—'}"]
        for row in implied.get("drivers") or []:
            unit=row.get("unit"); mv=row.get("market_implied"); bv=row.get("base")
            if unit=="%":
                mv_txt=f"{float(mv)*100:.1f}%" if mv is not None else "—"; bv_txt=f"{float(bv)*100:.1f}%" if bv is not None else "—"
            else:
                mv_txt=f"{float(mv):.1f}x" if mv is not None else "—"; bv_txt=f"{float(bv):.1f}x" if bv is not None else "—"
            lines.append(f"{row.get('label')}: market {mv_txt} · Base {bv_txt} · {row.get('read') or '—'}")
    lines += ["", "EVIDENCE FOR"]
    for row in data.get("supporting") or []:
        lines.append(f"• {row.get('label') or 'Evidence'} — {row.get('detail') or ''}")
    lines += ["", "EVIDENCE AGAINST"]
    for row in data.get("opposing") or []:
        lines.append(f"• {row.get('label') or 'Evidence'} — {row.get('detail') or ''}")
    if data.get("mode") == "full":
        for label,key in [
            ("BUSINESS","business"),("FUNDAMENTALS","numbers"),("EXPECTATIONS","expectations_summary"),
            ("VALUATION","valuation_notes"),("BEAR CASE","bear_case_summary"),("CATALYSTS","catalysts_summary"),
            ("FINANCIAL FLOWS","flows_summary"),("MANAGEMENT","management_summary"),("TAPE / FLOWS","tape_summary"),
            ("RESEARCH INVALIDATION","risk_summary"),
        ]:
            lines += ["", label, _txt(data.get(key)) or "—"]
        fundamentals=data.get("fundamentals_history") or []
        if fundamentals:
            lines += ["", "FUNDAMENTALS HISTORY"]
            for row in fundamentals[-8:]:
                cfo_ni = f"{float(row.get('cfo_to_net_income')):.2f}x" if row.get("cfo_to_net_income") is not None else "—"
                lines.append(
                    f"{row.get('period') or '—'} · Revenue {_money(row.get('revenue'))} · "
                    f"Op margin {_pct(row.get('operating_margin_pct'))} · FCF {_money(row.get('fcf'))} · "
                    f"Inv/Rev {_pct(row.get('inventory_to_revenue_pct'))} · "
                    f"Rec/Rev {_pct(row.get('receivables_to_revenue_pct'))} · "
                    f"CFO/NI {cfo_ni} · Shares YoY {_pct(row.get('share_count_growth_pct'))}"
                )

        tri=data.get("triangulation") or {}
        if tri.get("available"):
            lines += ["", "AUTOMATIC TRIANGULATION", f"{tri.get('method')} · SIC {tri.get('sic') or '—'}"]
            for row in (tri.get("signals") or [])[:12]:
                lines.append(f"• {row.get('state') or ''} — {row.get('detail') or ''}")
        promises=data.get("management_promises") or []
        if promises:
            lines += ["", "MANAGEMENT PROMISES VS ACTUALS"]
            for row in promises[:30]:
                lo=row.get("low"); hi=row.get("high"); unit=row.get("unit") or ""
                if row.get("target_text"):
                    promise=str(row.get("target_text"))
                elif lo is None or hi is None:
                    promise="—"
                else:
                    promise=(str(lo) if lo==hi else f"{lo}–{hi}") + (f" {unit}" if unit else "")
                period = row.get("target_period") or (f"FY{row.get('target_year')}" if row.get("target_year") else "UNRESOLVED")
                lines.append(f"{period} · {row.get('metric')} · {promise} · actual {row.get('actual') if row.get('actual') is not None else '—'} · {row.get('status') or ''}")
        lines += ["", "SOURCES"]
        for row in data.get("sources") or []:
            lines.append(f"• {row.get('provider')} · {row.get('type')} · {row.get('title')} · {row.get('retrieved_at')}")
    footer=str(brand.get("footer") or "Lose Money Rules")
    lines += ["", footer]
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

def _valuation_chart_png(data: dict[str, Any]) -> BytesIO:
    if not _load_report_libs():
        raise RuntimeError("Rich report backend unavailable")
    values = [
        ("Market", data.get("market_price"), "#7f8a94"),
        ("Bear", data.get("bear"), "#a13b3b"),
        ("Base", data.get("base"), "#3a6f99"),
        ("Bull", data.get("bull"), "#1f7a54"),
    ]
    numeric = [float(v) for _, v, _ in values if v is not None]
    out = BytesIO()
    image = PILImage.new("RGB", (1000, 230), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    if not numeric:
        draw.text((30, 90), "Valuation scenarios unavailable", fill="#52606d", font=font)
        image.save(out, format="PNG")
        out.seek(0)
        return out
    low, high = min(numeric), max(numeric)
    span = max(high - low, max(abs(high), 1.0) * .08)
    low -= span * .08
    high += span * .08
    x0, x1, y = 75, 935, 115
    draw.line((x0, y, x1, y), fill="#c8d1da", width=3)
    for label, value, color in values:
        if value is None:
            continue
        v = float(value)
        x = int(x0 + (v - low) / (high - low) * (x1 - x0))
        draw.line((x, y - 42, x, y + 42), fill=color, width=5)
        draw.text((max(10, x - 30), y - 72), label, fill=color, font=font)
        draw.text((max(10, x - 32), y + 52), "$" + format(v, ",.2f"), fill="#0b1f33", font=font)
    draw.text((x0, 190), "Range $" + format(low, ",.2f"), fill="#6d7a86", font=font)
    draw.text((x1 - 95, 190), "$" + format(high, ",.2f"), fill="#6d7a86", font=font)
    image.save(out, format="PNG")
    out.seek(0)
    return out


def _docx_add_heading(doc: Document, text: str, level: int = 1) -> None:
    p = doc.add_heading(text, level=level)
    p.paragraph_format.space_before = Pt(9)
    p.paragraph_format.space_after = Pt(4)


def _docx_add_text(doc: Document, text: str) -> None:
    p = doc.add_paragraph(text or "—")
    p.paragraph_format.space_after = Pt(5)


def _docx_hex(hex_value: str):
    value = str(hex_value or "0B1F33").strip().lstrip("#")
    return RGBColor(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _docx_shade(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill.replace("#", "").upper())


def _docx_cell(cell, text: str, *, bold: bool = False, size: float = 10.5, color: str = "#0b1f33",
               align=None) -> None:
    cell.text = ""
    p = cell.paragraphs[0]
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    r = p.add_run(str(text if text not in (None, "") else "—"))
    r.bold = bold
    r.font.size = Pt(size)
    r.font.color.rgb = _docx_hex(color)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _docx_kpi_strip(doc: Document, items: list[tuple[str, str, str]]) -> None:
    table = doc.add_table(rows=2, cols=len(items))
    table.style = "Table Grid"
    for idx, (label, value, tone) in enumerate(items):
        _docx_cell(table.cell(0, idx), label.upper(), bold=True, size=8.5, color="#607384", align=WD_ALIGN_PARAGRAPH.CENTER)
        _docx_cell(table.cell(1, idx), value, bold=True, size=11.5, color=tone, align=WD_ALIGN_PARAGRAPH.CENTER)
        _docx_shade(table.cell(0, idx), "EAF0F5")
        _docx_shade(table.cell(1, idx), "FFFFFF")
    doc.add_paragraph().paragraph_format.space_after = Pt(1)


def _docx_two_panel(doc: Document, left_title: str, left_text: str, right_title: str, right_text: str,
                    *, left_fill: str = "F4F7F9", right_fill: str = "F4F7F9") -> None:
    table = doc.add_table(rows=2, cols=2)
    table.style = "Table Grid"
    _docx_cell(table.cell(0, 0), left_title.upper(), bold=True, size=9, color="#0b1f33")
    _docx_cell(table.cell(0, 1), right_title.upper(), bold=True, size=9, color="#0b1f33")
    _docx_shade(table.cell(0, 0), left_fill); _docx_shade(table.cell(0, 1), right_fill)
    _docx_cell(table.cell(1, 0), left_text or "—", size=10)
    _docx_cell(table.cell(1, 1), right_text or "—", size=10)
    doc.add_paragraph().paragraph_format.space_after = Pt(1)



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

    if not _load_report_libs():
        lines = [
            str(branding.get("title") or "Market Forensics") + " · Discovery",
            "Broad-universe staged forensic screen · P1/P2 are stronger research leads; WATCH preserves emerging or verification-needed dislocations.",
            "",
        ]
        for idx, row in enumerate(candidates, start=1):
            lines.append(
                f"{idx}. {row.get('ticker') or ''} · {row.get('priority') or 'WATCH'} · {row.get('research_side') or 'RESEARCH'} · "
                f"price {_number(row.get('price'))} · Base {_number(row.get('base') if row.get('base') is not None else row.get('fair_value'))} · "
                f"gap {_gap(row.get('base_gap_pct'))} · {int(row.get('valuation_methods') or 0)} methods · "
                f"{row.get('radar_label') or ''}"
            )
        lines += ["", str(branding.get("footer") or "Lose Money Rules")]
        return _fallback_pdf(lines, landscape_page=True)

    out = BytesIO()
    doc = SimpleDocTemplate(
        out,
        pagesize=landscape(LETTER),
        rightMargin=.35*inch, leftMargin=.35*inch, topMargin=.35*inch, bottomMargin=.35*inch,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="MFDiscTitle", parent=styles["Title"], fontSize=17, leading=20, textColor=colors.HexColor("#0b1f33"), alignment=TA_LEFT, spaceAfter=5))
    styles.add(ParagraphStyle(name="MFDiscBody", parent=styles["BodyText"], fontSize=9.5, leading=12, spaceAfter=3))
    story = []
    logo = _safe_logo(str(branding.get("logo_url") or ""))
    if logo:
        story += [RLImage(logo, width=1.0*inch, height=.34*inch), Spacer(1,3)]
    story += [
        Paragraph(escape(str(branding.get("title") or "Market Forensics")) + " · Discovery", styles["MFDiscTitle"]),
        Paragraph("Broad-universe staged forensic screen · P1/P2 are stronger research leads; WATCH preserves emerging or verification-needed dislocations.", styles["MFDiscBody"]),
    ]
    rows = [["#","Ticker","Tier","Side","Price","Bear","Base","Bull","Gap","Methods","Research reason"]]
    for idx, row in enumerate(candidates, start=1):
        rows.append([
            str(idx),
            str(row.get("ticker") or ""),
            str(row.get("priority") or "WATCH"),
            str(row.get("research_side") or ""),
            _number(row.get("price")),
            _number(row.get("bear")),
            _number(row.get("base") if row.get("base") is not None else row.get("fair_value")),
            _number(row.get("bull")),
            _gap(row.get("base_gap_pct")),
            str(int(row.get("valuation_methods") or 0)),
            str(row.get("radar_label") or "") + (" · " + str((row.get("forensic_signals") or [{}])[0].get("detail") or "") if row.get("forensic_signals") else ""),
        ])
    table = Table(
        rows,
        colWidths=[.25*inch,.48*inch,.48*inch,.48*inch,.58*inch,.56*inch,.56*inch,.56*inch,.58*inch,.50*inch,3.85*inch],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf0f5")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.HexColor("#0b1f33")),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),8.2),
        ("GRID",(0,0),(-1,-1),.25,colors.HexColor("#c7d1da")),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),3),
        ("RIGHTPADDING",(0,0),(-1,-1),3),
        ("TOPPADDING",(0,0),(-1,-1),3),
        ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    story += [table, Spacer(1,4), Paragraph(
        escape(f"{branding.get('footer') or 'Lose Money Rules'} · {scan.get('universe_source') or 'Discovery'} · {len(candidates)} candidates"),
        styles["MFDiscBody"],
    )]
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
