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
from .extensions import db
from .models import UserPreference
from .management_promises import evaluate_promises
from .triangulation_engine import automatic_triangulation
from .decision_support import tape_series



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
        from docx.shared import Inches as _Inches, Pt as _Pt
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
        "Document": _Document, "WD_ALIGN_PARAGRAPH": _WD_ALIGN_PARAGRAPH, "Inches": _Inches, "Pt": _Pt,
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
            ("BUSINESS","business"),("NUMBERS","numbers"),("EXPECTATIONS","expectations_summary"),
            ("VALUATION","valuation_notes"),("BEAR CASE","bear_case_summary"),("CATALYSTS","catalysts_summary"),
            ("FINANCIAL FLOWS","flows_summary"),("MANAGEMENT","management_summary"),("TAPE / FLOWS","tape_summary"),
            ("RESEARCH INVALIDATION","risk_summary"),
        ]:
            lines += ["", label, _txt(data.get(key)) or "—"]
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
                promise=str(lo) if lo==hi else f"{lo}–{hi}"
                lines.append(f"FY{row.get('target_year')} · {row.get('metric')} · {promise} {unit} · actual {row.get('actual') if row.get('actual') is not None else '—'} · {row.get('status') or ''}")
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
    margin=42; font_size=9; leading=13
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
    heading_words={"RESEARCH LENSES","THESIS","COUNTER-EVIDENCE","MARKET VIEW","OUR VARIANT","EVIDENCE FOR","EVIDENCE AGAINST","BUSINESS","NUMBERS","EXPECTATIONS","VALUATION","BEAR CASE","CATALYSTS","FINANCIAL FLOWS","MANAGEMENT","TAPE / FLOWS","RESEARCH INVALIDATION","AUTOMATIC TRIANGULATION","MANAGEMENT PROMISES VS ACTUALS","SOURCES"}
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
    coverage = ctx["coverage"]
    research = ctx["research"]
    intelligence = ctx["intelligence"]
    decision_lenses = ctx.get("decision_lenses") or {}
    readiness = ctx["readiness"]
    valuation = ctx["valuation"]
    company = ctx["company"]
    security = ctx["security"]
    market = ctx["market"]

    expectations = Expectation.query.filter_by(coverage_id=coverage.id).order_by(Expectation.period_label, Expectation.metric).all()
    bears = BearCaseItem.query.filter_by(coverage_id=coverage.id).order_by(BearCaseItem.status, BearCaseItem.id).all()
    catalysts = Catalyst.query.filter_by(coverage_id=coverage.id).order_by(Catalyst.expected_date.asc(), Catalyst.id.asc()).all()
    management = ManagementAssessment.query.filter_by(coverage_id=coverage.id).order_by(ManagementAssessment.as_of.desc()).all()
    sources = Source.query.filter_by(company_id=company.id).order_by(Source.retrieved_at.desc()).limit(40).all()
    management_promises = evaluate_promises(company.id)
    triangulation = automatic_triangulation(company.id, coverage.user_id)
    tape = tape_series(security, 12)
    implied = dict((decision_lenses.get("implied_expectations") or {}))

    branding = dict(branding or {})
    return {
        "branding": {
            "title": str(branding.get("title") or "Market Forensics"),
            "prepared_by": str(branding.get("prepared_by") or ""),
            "footer": str(branding.get("footer") or "Lose Money Rules"),
            "logo_url": str(branding.get("logo_url") or ""),
        },
        "mode": "executive" if str(mode).lower() == "executive" else "full",
        "ticker": security.ticker,
        "company": company.display_name,
        "sector": company.sector or "",
        "industry": company.industry or "",
        "market_price": float(market.price) if market and market.price is not None else None,
        "market_provider": market.provider if market else "",
        "market_as_of": market.as_of.isoformat() if market and market.as_of else "",
        "action": decision_lenses.get("research_conclusion") or "DATA REVIEW",
        "stance": decision_lenses.get("value") or intelligence.get("stance") or "UNVERIFIED",
        "bias": intelligence.get("bias") or "NEUTRAL",
        "confidence": decision_lenses.get("model_confidence") or intelligence.get("confidence") or "UNVALIDATED",
        "decision_lenses": list(decision_lenses.get("rows") or []),
        "implied_expectations": implied,
        "triangulation": triangulation,
        "management_promises": [{
            "metric": p.get("metric"), "target_year": p.get("target_year"), "low": p.get("low"), "high": p.get("high"),
            "unit": p.get("unit"), "actual": p.get("actual"), "status": p.get("status"), "statement": p.get("statement"),
            "origin": p.get("origin"),
        } for p in management_promises],
        "tape_metrics": dict(tape.get("metrics") or {}),
        "diagnostic_action": intelligence.get("action") or "WAIT",
        "score": intelligence.get("score"),
        "bear": valuation.get("bear"),
        "base": valuation.get("base"),
        "bull": valuation.get("bull"),
        "expected_value": valuation.get("expected_value"),
        "base_gap_pct": intelligence.get("base_gap_pct"),
        "readiness": f"{readiness.get('done',0)}/{readiness.get('total',0)}",
        "ready_to_validate": bool(readiness.get("ready_to_validate")),
        "validation_state": (readiness.get("validation") or {}).get("state") or "NOT RUN",
        "thesis": _txt(research.thesis),
        "counter_evidence": _txt(research.counter_evidence),
        "variant_market": _txt(research.variant_market),
        "variant_us": _txt(research.variant_us),
        "variant_evidence": _txt(research.variant_evidence),
        "business": _txt(research.business),
        "numbers": _txt(research.numbers),
        "expectations_summary": _txt(research.expectations),
        "valuation_notes": _txt(research.valuation_notes),
        "bear_case_summary": _txt(research.bear_case_summary),
        "catalysts_summary": _txt(research.catalysts_summary),
        "flows_summary": _txt(research.flows_summary),
        "management_summary": _txt(research.management_summary),
        "tape_summary": _txt(research.tape_summary),
        "risk_summary": _txt(research.risk_summary),
        "supporting": list(intelligence.get("supporting_evidence") or []),
        "opposing": list(intelligence.get("opposing_evidence") or []),
        "warnings": list(intelligence.get("warnings") or []),
        "blockers": list(intelligence.get("blockers") or []),
        "expectations": [{
            "metric": x.metric, "period": x.period_label, "market": float(x.market_value) if x.market_value is not None else None,
            "ours": float(x.internal_value) if x.internal_value is not None else None, "unit": x.unit, "confidence": x.confidence,
            "notes": x.notes,
        } for x in expectations],
        "bear_items": [{
            "title": x.title, "severity": x.severity, "probability": float(x.probability) if x.probability is not None else None,
            "invalidates": bool(x.invalidates), "status": x.status, "evidence": x.evidence,
        } for x in bears],
        "catalysts": [{
            "title": x.title, "type": x.catalyst_type, "date": x.expected_date.isoformat() if x.expected_date else "",
            "direction": x.direction, "status": x.status, "evidence": x.evidence,
        } for x in catalysts],
        "management": [{
            "as_of": x.as_of.isoformat() if x.as_of else "", "capital_allocation": x.capital_allocation,
            "execution": x.execution, "red_flags": x.red_flags, "notes": x.notes,
        } for x in management],
        "sources": [{
            "provider": x.provider, "type": x.source_type, "title": x.title, "url": x.url,
            "retrieved_at": x.retrieved_at.isoformat() if x.retrieved_at else "",
        } for x in sources],
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
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(4)


def _docx_add_text(doc: Document, text: str) -> None:
    p = doc.add_paragraph(text or "—")
    p.paragraph_format.space_after = Pt(5)


def render_docx(data: dict[str, Any]) -> BytesIO:
    if not _load_report_libs():
        return _fallback_docx(_plain_research_lines(data))
    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = Inches(.55); sec.bottom_margin = Inches(.55); sec.left_margin = Inches(.65); sec.right_margin = Inches(.65)

    brand = data.get("branding") or {}
    logo = _safe_logo(str(brand.get("logo_url") or ""))
    if logo:
        doc.add_picture(logo, width=Inches(1.25))
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = title.add_run(f"{data['ticker']} · {data['company']}")
    run.bold = True; run.font.size = Pt(20)
    subtitle = f"{brand.get('title') or 'Market Forensics'} 0.2.1 · {data['action']} · {data['stance']} · {data['confidence']} confidence"
    if brand.get("prepared_by"):
        subtitle += f" · Prepared by {brand['prepared_by']}"
    p = doc.add_paragraph(subtitle)
    p.runs[0].font.size = Pt(10)

    table = doc.add_table(rows=2, cols=6)
    table.style = "Table Grid"
    headers = ["Market","Bear","Base","Bull","Base gap","Validate"]
    values = [_money(data["market_price"]),_money(data["bear"]),_money(data["base"]),_money(data["bull"]),_pct(data["base_gap_pct"]),data["validation_state"]]
    for i,v in enumerate(headers): table.cell(0,i).text=v
    for i,v in enumerate(values): table.cell(1,i).text=v

    chart = _valuation_chart_png(data)
    doc.add_picture(chart, width=Inches(6.75))

    _docx_add_heading(doc, "Investment research brief", 1)
    for label,key in [("Thesis","thesis"),("Counter-evidence","counter_evidence"),("Market view","variant_market"),("Our variant","variant_us")]:
        _docx_add_heading(doc,label,2); _docx_add_text(doc,data[key])

    _docx_add_heading(doc, "Research lenses", 1)
    lens_table = doc.add_table(rows=1, cols=2)
    lens_table.style = "Table Grid"
    lens_table.cell(0,0).text = "Lens"; lens_table.cell(0,1).text = "State"
    for row in data.get("decision_lenses") or []:
        cells = lens_table.add_row().cells
        cells[0].text = str(row.get("label") or row.get("key") or "")
        cells[1].text = str(row.get("state") or "")

    implied = data.get("implied_expectations") or {}
    if implied.get("available"):
        _docx_add_heading(doc, "Price-implied expectations", 1)
        p = doc.add_paragraph(f"Overall: {implied.get('classification')} · {implied.get('method')}")
        p.paragraph_format.space_after = Pt(4)
        t_imp = doc.add_table(rows=1, cols=4); t_imp.style = "Table Grid"
        for i,v in enumerate(["Driver","Market-implied","Our Base","Read"]): t_imp.cell(0,i).text=v
        for row in implied.get("drivers") or []:
            cells=t_imp.add_row().cells
            unit=row.get("unit")
            market=row.get("market_implied"); base=row.get("base")
            vals=[
                str(row.get("label") or ""),
                (f"{float(market)*100:.1f}%" if unit=="%" and market is not None else f"{float(market):.1f}x" if market is not None else "—"),
                (f"{float(base)*100:.1f}%" if unit=="%" and base is not None else f"{float(base):.1f}x" if base is not None else "—"),
                str(row.get("read") or ""),
            ]
            for i,v in enumerate(vals): cells[i].text=v

    _docx_add_heading(doc, "Evidence for / against", 1)
    for label,key in [("For","supporting"),("Against","opposing")]:
        _docx_add_heading(doc,label,2)
        rows=data[key]
        if rows:
            for row in rows[:8]: doc.add_paragraph(f"{row.get('label','Evidence')} — {row.get('detail','')}", style="List Bullet")
        else: _docx_add_text(doc,"No weighted evidence stored.")

    if data["mode"] == "full":
        for label,key in [
            ("Business","business"),("Numbers","numbers"),("Expectations","expectations_summary"),
            ("Valuation","valuation_notes"),("Bear Case","bear_case_summary"),("Catalysts","catalysts_summary"),
            ("Financial Flows","flows_summary"),("Management","management_summary"),("Tape / Flows","tape_summary"),
            ("Research invalidation / risk summary","risk_summary"),
        ]:
            _docx_add_heading(doc,label,1); _docx_add_text(doc,data[key])

        if data["expectations"]:
            _docx_add_heading(doc,"Expectation variants",1)
            t=doc.add_table(rows=1,cols=5); t.style="Table Grid"
            for i,v in enumerate(["Metric","Period","Market","Ours","Confidence"]): t.cell(0,i).text=v
            for row in data["expectations"]:
                cells=t.add_row().cells
                vals=[row["metric"],row["period"],str(row["market"] if row["market"] is not None else "—"),str(row["ours"] if row["ours"] is not None else "—"),row["confidence"]]
                for i,v in enumerate(vals): cells[i].text=v

        tri = data.get("triangulation") or {}
        if tri.get("available"):
            _docx_add_heading(doc,"Automatic triangulation",1)
            _docx_add_text(doc, f"{tri.get('method')} · SIC {tri.get('sic') or '—'} · {len(tri.get('peers') or [])} peers")
            for row in (tri.get("signals") or [])[:8]:
                doc.add_paragraph(f"{row.get('state')} · {row.get('detail')}", style="List Bullet")

        promises = data.get("management_promises") or []
        if promises:
            _docx_add_heading(doc,"Management promises vs actuals",1)
            t_prom=doc.add_table(rows=1,cols=5); t_prom.style="Table Grid"
            for i,v in enumerate(["FY","Metric","Promise","Actual","Status"]): t_prom.cell(0,i).text=v
            for row in promises[:20]:
                cells=t_prom.add_row().cells
                lo=row.get("low"); hi=row.get("high"); unit=row.get("unit") or ""
                promise=(str(lo) if lo==hi else f"{lo} – {hi}")+" "+unit
                vals=[str(row.get("target_year") or ""),str(row.get("metric") or ""),promise,str(row.get("actual") if row.get("actual") is not None else "—"),str(row.get("status") or "")]
                for i,v in enumerate(vals): cells[i].text=v

        tape=data.get("tape_metrics") or {}
        _docx_add_heading(doc,"Tape / positioning context",1)
        _docx_add_text(doc, " · ".join([
            f"Regime {tape.get('regime') or '—'}",
            f"Confidence {tape.get('confidence') or '—'}",
            f"Put/Call OI {tape.get('put_call_oi'):.2f}" if tape.get("put_call_oi") is not None else "Put/Call OI —",
            f"Borrow {tape.get('borrow_status') or 'unknown'}",
            f"Net tape {tape.get('net_tape'):.1f}" if tape.get("net_tape") is not None else "Net tape —",
        ]))

        _docx_add_heading(doc,"Sources",1)
        for row in data["sources"][:30]:
            doc.add_paragraph(f"{row['provider']} · {row['type']} · {row['title']} · {row['retrieved_at']}", style="List Bullet")

    footer=doc.sections[0].footer.paragraphs[0]
    footer.text=f"{brand.get('footer') or 'Lose Money Rules'} · {brand.get('title') or 'Market Forensics'} 0.2.1"
    footer.alignment=WD_ALIGN_PARAGRAPH.CENTER

    out=BytesIO(); doc.save(out); out.seek(0); return out


def render_pdf(data: dict[str, Any]) -> BytesIO:
    if not _load_report_libs():
        return _fallback_pdf(_plain_research_lines(data))
    out=BytesIO()
    doc=SimpleDocTemplate(out,pagesize=LETTER,rightMargin=.55*inch,leftMargin=.55*inch,topMargin=.5*inch,bottomMargin=.5*inch)
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name="MFTitle",parent=styles["Title"],fontSize=18,leading=21,textColor=colors.HexColor("#0b1f33"),alignment=TA_LEFT,spaceAfter=6))
    styles.add(ParagraphStyle(name="MFH2",parent=styles["Heading2"],fontSize=11,leading=14,textColor=colors.HexColor("#1f4e79"),spaceBefore=8,spaceAfter=4))
    styles.add(ParagraphStyle(name="MFBody",parent=styles["BodyText"],fontSize=8.7,leading=11,spaceAfter=5))
    brand = data.get("branding") or {}
    story=[]
    logo = _safe_logo(str(brand.get("logo_url") or ""))
    if logo:
        story += [RLImage(logo, width=1.1*inch, height=.38*inch), Spacer(1,4)]
    subtitle = f"{brand.get('title') or 'Market Forensics'} 0.2.1 · {data['action']} · {data['stance']} · {data['confidence']} confidence"
    if brand.get("prepared_by"):
        subtitle += f" · Prepared by {brand['prepared_by']}"
    story += [Paragraph(f"{data['ticker']} · {data['company']}",styles["MFTitle"]),
              Paragraph(escape(subtitle),styles["MFBody"])]
    grid=[
        ["Market","Bear","Base","Bull","Base gap","Validate"],
        [_money(data["market_price"]),_money(data["bear"]),_money(data["base"]),_money(data["bull"]),_pct(data["base_gap_pct"]),data["validation_state"]],
    ]
    t=Table(grid,colWidths=[1.05*inch]*6)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf0f5")),("TEXTCOLOR",(0,0),(-1,0),colors.HexColor("#0b1f33")),("GRID",(0,0),(-1,-1),.35,colors.HexColor("#b8c4ce")),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),8),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5)]))
    story += [t,Spacer(1,8)]
    chart = _valuation_chart_png(data)
    story += [RLImage(chart, width=6.6*inch, height=1.52*inch), Spacer(1,6)]

    def section(title: str, body: str):
        story.append(Paragraph(escape(title),styles["MFH2"]))
        story.append(Paragraph(escape(body or "—"),styles["MFBody"]))

    section("Thesis",data["thesis"]); section("Counter-evidence",data["counter_evidence"])
    section("Market view",data["variant_market"]); section("Our variant",data["variant_us"])
    story.append(Paragraph("Research lenses",styles["MFH2"]))
    lens_rows=[["Lens","State"]]+[[str(r.get("label") or r.get("key") or ""),str(r.get("state") or "")] for r in (data.get("decision_lenses") or [])]
    lens_table=Table(lens_rows,colWidths=[2.6*inch,3.9*inch])
    lens_table.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.35,colors.HexColor("#c7d1da")),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf0f5")),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),8)]))
    story += [lens_table,Spacer(1,6)]
    implied=data.get("implied_expectations") or {}
    if implied.get("available"):
        story.append(Paragraph("Price-implied expectations · "+escape(str(implied.get("classification") or "")),styles["MFH2"]))
        rows=[["Driver","Market-implied","Our Base","Read"]]
        for row in implied.get("drivers") or []:
            unit=row.get("unit"); mv=row.get("market_implied"); bv=row.get("base")
            rows.append([
                str(row.get("label") or ""),
                (f"{float(mv)*100:.1f}%" if unit=="%" and mv is not None else f"{float(mv):.1f}x" if mv is not None else "—"),
                (f"{float(bv)*100:.1f}%" if unit=="%" and bv is not None else f"{float(bv):.1f}x" if bv is not None else "—"),
                str(row.get("read") or ""),
            ])
        tt=Table(rows,colWidths=[2.35*inch,1.35*inch,1.35*inch,1.45*inch])
        tt.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.35,colors.HexColor("#c7d1da")),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf0f5")),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),7.5)]))
        story += [tt,Spacer(1,6)]

    story.append(Paragraph("Evidence for / against",styles["MFH2"]))
    for label,key in [("FOR","supporting"),("AGAINST","opposing")]:
        rows=data[key][:6]
        text="<b>"+escape(label)+"</b><br/>"+("<br/>".join("• "+escape(str(r.get("label","Evidence")))+" — "+escape(str(r.get("detail",""))) for r in rows) if rows else "—")
        story.append(Paragraph(text,styles["MFBody"]))

    if data["mode"] == "full":
        for label,key in [
            ("Business","business"),("Numbers","numbers"),("Expectations","expectations_summary"),
            ("Valuation","valuation_notes"),("Bear Case","bear_case_summary"),("Catalysts","catalysts_summary"),
            ("Financial Flows","flows_summary"),("Management","management_summary"),("Tape / Flows","tape_summary"),
            ("Research invalidation / risk summary","risk_summary"),
        ]: section(label,data[key])
        tri=data.get("triangulation") or {}
        if tri.get("available"):
            story.append(Paragraph("Automatic triangulation",styles["MFH2"]))
            story.append(Paragraph(escape(f"{tri.get('method')} · SIC {tri.get('sic') or '—'} · {len(tri.get('peers') or [])} peers"),styles["MFBody"]))
            for row in (tri.get("signals") or [])[:8]:
                story.append(Paragraph("• "+escape(f"{row.get('state')} · {row.get('detail')}"),styles["MFBody"]))

        promises=data.get("management_promises") or []
        if promises:
            story.append(Paragraph("Management promises vs actuals",styles["MFH2"]))
            rows=[["FY","Metric","Promise","Actual","Status"]]
            for row in promises[:20]:
                lo=row.get("low"); hi=row.get("high"); unit=row.get("unit") or ""
                promise=(str(lo) if lo==hi else f"{lo} – {hi}")+" "+unit
                rows.append([str(row.get("target_year") or ""),str(row.get("metric") or ""),promise,str(row.get("actual") if row.get("actual") is not None else "—"),str(row.get("status") or "")])
            tt=Table(rows,colWidths=[.55*inch,1.45*inch,1.9*inch,1.15*inch,.85*inch])
            tt.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.35,colors.HexColor("#c7d1da")),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf0f5")),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),7)]))
            story += [tt,Spacer(1,6)]

        tape=data.get("tape_metrics") or {}
        section("Tape / positioning context"," · ".join([
            f"Regime {tape.get('regime') or '—'}",
            f"Confidence {tape.get('confidence') or '—'}",
            f"Put/Call OI {tape.get('put_call_oi'):.2f}" if tape.get("put_call_oi") is not None else "Put/Call OI —",
            f"Borrow {tape.get('borrow_status') or 'unknown'}",
            f"Net tape {tape.get('net_tape'):.1f}" if tape.get("net_tape") is not None else "Net tape —",
        ]))

        if data["sources"]:
            story.append(PageBreak()); story.append(Paragraph("Sources",styles["MFH2"]))
            for row in data["sources"][:30]:
                story.append(Paragraph("• "+escape(f"{row['provider']} · {row['type']} · {row['title']} · {row['retrieved_at']}"),styles["MFBody"]))

    doc.build(story)
    out.seek(0); return out


def render_discovery_pdf(scan: dict[str, Any], branding: dict[str, str] | None = None) -> BytesIO:
    branding = dict(branding or {})
    if not _load_report_libs():
        lines=[str(branding.get("title") or "Market Forensics")+" · Discovery",
               "Market-wide lightweight screen · candidates require deep research before valuation or portfolio use.",""]
        for idx,row in enumerate(list(scan.get("candidates") or [])[:60], start=1):
            move=f"{float(row.get('move_pct')):+.1f}%" if row.get("move_pct") is not None else "—"
            lines.append(f"{idx}. {row.get('ticker') or ''} · score {float(row.get('scan_score') or 0):.1f} · move {move} · {' · '.join(row.get('lenses') or [])}")
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
    styles.add(ParagraphStyle(name="MFDiscBody", parent=styles["BodyText"], fontSize=7.5, leading=9.5, spaceAfter=3))
    story = []
    logo = _safe_logo(str(branding.get("logo_url") or ""))
    if logo:
        story += [RLImage(logo, width=1.0*inch, height=.34*inch), Spacer(1,3)]
    story += [
        Paragraph(escape(str(branding.get("title") or "Market Forensics"))+" · Discovery", styles["MFDiscTitle"]),
        Paragraph("Market-wide lightweight screen · candidates require deep research before valuation or portfolio use.", styles["MFDiscBody"]),
    ]
    candidates = list(scan.get("candidates") or [])[:60]
    rows = [["#","Ticker","Score","Move","Activity","Evidence lenses"]]
    for idx,row in enumerate(candidates, start=1):
        move = f"{float(row.get('move_pct')):+.1f}%" if row.get("move_pct") is not None else "—"
        activity = f"#{row.get('activity_rank')}" if row.get("activity_rank") else "—"
        rows.append([str(idx), str(row.get("ticker") or ""), f"{float(row.get('scan_score') or 0):.1f}", move, activity, " · ".join(row.get("lenses") or [])])
    table = Table(rows, colWidths=[.35*inch,.65*inch,.65*inch,.7*inch,.65*inch,7.25*inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf0f5")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.HexColor("#0b1f33")),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),6.8),
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


__all__=["get_report_branding","set_report_branding","research_report_data","render_docx","render_pdf","render_discovery_pdf","report_backend_status"]
