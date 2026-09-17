from __future__ import annotations

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape
import ipaddress
import socket
from urllib.parse import urlparse

import requests

from docx import Document
from PIL import Image as PILImage, ImageDraw, ImageFont
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image as RLImage

from .core_models import BearCaseItem, Catalyst, Expectation, ManagementAssessment, Source
from .extensions import db
from .models import UserPreference


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
    if not url:
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
    subtitle = f"{brand.get('title') or 'Market Forensics'} 0.2.0 · {data['action']} · {data['stance']} · {data['confidence']} confidence"
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

        _docx_add_heading(doc,"Sources",1)
        for row in data["sources"][:30]:
            doc.add_paragraph(f"{row['provider']} · {row['type']} · {row['title']} · {row['retrieved_at']}", style="List Bullet")

    footer=doc.sections[0].footer.paragraphs[0]
    footer.text=f"{brand.get('footer') or 'Lose Money Rules'} · {brand.get('title') or 'Market Forensics'} 0.2.0"
    footer.alignment=WD_ALIGN_PARAGRAPH.CENTER

    out=BytesIO(); doc.save(out); out.seek(0); return out


def render_pdf(data: dict[str, Any]) -> BytesIO:
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
    subtitle = f"{brand.get('title') or 'Market Forensics'} 0.2.0 · {data['action']} · {data['stance']} · {data['confidence']} confidence"
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
        if data["sources"]:
            story.append(PageBreak()); story.append(Paragraph("Sources",styles["MFH2"]))
            for row in data["sources"][:30]:
                story.append(Paragraph("• "+escape(f"{row['provider']} · {row['type']} · {row['title']} · {row['retrieved_at']}"),styles["MFBody"]))

    doc.build(story)
    out.seek(0); return out


__all__=["get_report_branding","set_report_branding","research_report_data","render_docx","render_pdf"]
