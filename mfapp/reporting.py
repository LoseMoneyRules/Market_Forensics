from __future__ import annotations

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

from .core_models import BearCaseItem, Catalyst, Expectation, ManagementAssessment, Source


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


def research_report_data(ctx: dict[str, Any], *, mode: str = "full") -> dict[str, Any]:
    coverage = ctx["coverage"]
    research = ctx["research"]
    intelligence = ctx["intelligence"]
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

    return {
        "mode": "executive" if str(mode).lower() == "executive" else "full",
        "ticker": security.ticker,
        "company": company.display_name,
        "sector": company.sector or "",
        "industry": company.industry or "",
        "market_price": float(market.price) if market and market.price is not None else None,
        "market_provider": market.provider if market else "",
        "market_as_of": market.as_of.isoformat() if market and market.as_of else "",
        "action": intelligence.get("action") or "WAIT",
        "stance": intelligence.get("stance") or "NO EDGE",
        "bias": intelligence.get("bias") or "NEUTRAL",
        "confidence": intelligence.get("confidence") or "LOW",
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

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = title.add_run(f"{data['ticker']} · {data['company']}")
    run.bold = True; run.font.size = Pt(20)
    p = doc.add_paragraph(f"Market Forensics 0.2.0 · {data['action']} · {data['stance']} · {data['confidence']} confidence")
    p.runs[0].font.size = Pt(10)

    table = doc.add_table(rows=2, cols=6)
    table.style = "Table Grid"
    headers = ["Market","Bear","Base","Bull","Base gap","Validate"]
    values = [_money(data["market_price"]),_money(data["bear"]),_money(data["base"]),_money(data["bull"]),_pct(data["base_gap_pct"]),data["validation_state"]]
    for i,v in enumerate(headers): table.cell(0,i).text=v
    for i,v in enumerate(values): table.cell(1,i).text=v

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
    footer.text="Lose Money Rules · Market Forensics 0.2.0"
    footer.alignment=WD_ALIGN_PARAGRAPH.CENTER

    out=BytesIO(); doc.save(out); out.seek(0); return out


def render_pdf(data: dict[str, Any]) -> BytesIO:
    out=BytesIO()
    doc=SimpleDocTemplate(out,pagesize=LETTER,rightMargin=.55*inch,leftMargin=.55*inch,topMargin=.5*inch,bottomMargin=.5*inch)
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name="MFTitle",parent=styles["Title"],fontSize=18,leading=21,textColor=colors.HexColor("#0b1f33"),alignment=TA_LEFT,spaceAfter=6))
    styles.add(ParagraphStyle(name="MFH2",parent=styles["Heading2"],fontSize=11,leading=14,textColor=colors.HexColor("#1f4e79"),spaceBefore=8,spaceAfter=4))
    styles.add(ParagraphStyle(name="MFBody",parent=styles["BodyText"],fontSize=8.7,leading=11,spaceAfter=5))
    story=[Paragraph(f"{data['ticker']} · {data['company']}",styles["MFTitle"]),
           Paragraph(f"Market Forensics 0.2.0 · {data['action']} · {data['stance']} · {data['confidence']} confidence",styles["MFBody"])]
    grid=[
        ["Market","Bear","Base","Bull","Base gap","Validate"],
        [_money(data["market_price"]),_money(data["bear"]),_money(data["base"]),_money(data["bull"]),_pct(data["base_gap_pct"]),data["validation_state"]],
    ]
    t=Table(grid,colWidths=[1.05*inch]*6)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf0f5")),("TEXTCOLOR",(0,0),(-1,0),colors.HexColor("#0b1f33")),("GRID",(0,0),(-1,-1),.35,colors.HexColor("#b8c4ce")),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),8),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5)]))
    story += [t,Spacer(1,8)]

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


__all__=["research_report_data","render_docx","render_pdf"]
