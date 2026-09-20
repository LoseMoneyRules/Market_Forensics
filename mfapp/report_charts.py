from __future__ import annotations

from io import BytesIO
from math import isfinite
from typing import Any

NAVY = "#0b1f33"
INK = "#14202b"
MUTED = "#6d7a86"
LINE = "#d9e0e6"
SOFT = "#eef3f6"
PRIMARY = "#3a6f99"
PRIMARY_SOFT = "#eaf1f7"
POSITIVE = "#1f7a54"
NEGATIVE = "#a13b3b"
CAUTION = "#8a6216"
MARKET = "#7f8a94"
WHITE = "#ffffff"


def _n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _font(size: int, *, bold: bool = False):
    from PIL import ImageFont
    candidates = (
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "Arial Bold.ttf" if bold else "Arial.ttf",
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _image(width: int, height: int):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (width, height), WHITE)
    return image, ImageDraw.Draw(image)


def _png(image) -> BytesIO:
    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


def _money(value: Any) -> str:
    value = _n(value)
    if value is None:
        return "-"
    if abs(value) >= 1_000_000_000:
        return "$" + f"{value/1_000_000_000:,.1f}B"
    if abs(value) >= 1_000_000:
        return "$" + f"{value/1_000_000:,.1f}M"
    if abs(value) >= 1_000:
        return "$" + f"{value/1_000:,.1f}K"
    return "$" + f"{value:,.2f}"


def _title(draw, title: str, subtitle: str | None = None, *, x: int = 46, y: int = 30) -> int:
    draw.text((x, y), title, fill=NAVY, font=_font(24, bold=True))
    if subtitle:
        draw.text((x, y + 34), subtitle, fill=MUTED, font=_font(15))
        return y + 74
    return y + 48


def _empty(title: str, message: str, width: int = 1400, height: int = 360) -> BytesIO:
    image, draw = _image(width, height)
    y = _title(draw, title)
    draw.rounded_rectangle((46, y + 18, width - 46, height - 38), radius=14, fill="#f8fafb", outline=LINE, width=2)
    draw.text((75, y + 70), message, fill=MUTED, font=_font(19))
    return _png(image)


def valuation_map(data: dict[str, Any], width: int = 1400, height: int = 330) -> BytesIO:
    valuation = dict(data.get("valuation") or {})
    points = [
        ("CURRENT", _n(valuation.get("current_price")), MARKET),
        ("BEAR", _n(valuation.get("bear")), NEGATIVE),
        ("BASE", _n(valuation.get("base")), PRIMARY),
        ("BULL", _n(valuation.get("bull")), POSITIVE),
    ]
    numeric = [v for _, v, _ in points if v is not None]
    if not numeric:
        return _empty("Valuation map", "Valuation scenarios unavailable.", width, height)
    low, high = min(numeric), max(numeric)
    span = max(high - low, max(abs(high), 1.0) * .12)
    low -= span * .12
    high += span * .12
    image, draw = _image(width, height)
    y0 = _title(draw, "Valuation map", "Current market price vs stored Bear / Base / Bull scenarios")
    x0, x1, y = 82, width - 82, y0 + 78
    draw.line((x0, y, x1, y), fill=LINE, width=5)
    for idx in range(5):
        x = x0 + (x1 - x0) * idx / 4
        value = low + (high - low) * idx / 4
        draw.line((x, y - 9, x, y + 9), fill=LINE, width=2)
        draw.text((x - 34, y + 24), _money(value), fill=MUTED, font=_font(13))
    for label, value, color in points:
        if value is None:
            continue
        x = x0 + (value - low) / (high - low) * (x1 - x0)
        h = 55 if label == "BASE" else 42
        line_w = 9 if label == "BASE" else 6
        draw.line((x, y - h, x, y + h), fill=color, width=line_w)
        bbox = draw.textbbox((0, 0), label, font=_font(15, bold=True))
        draw.text((x - (bbox[2]-bbox[0])/2, y - h - 32), label, fill=color, font=_font(15, bold=True))
        value_text = _money(value)
        vb = draw.textbbox((0, 0), value_text, font=_font(18, bold=(label=="BASE")))
        draw.text((x - (vb[2]-vb[0])/2, y + h + 8), value_text, fill=NAVY, font=_font(18, bold=(label=="BASE")))
    quality = str(valuation.get("base_quality") or valuation.get("quality") or "DATA WARNING").replace("_", " ")
    qcolor = PRIMARY if valuation.get("decision_grade") else CAUTION
    draw.rounded_rectangle((width-390, 30, width-46, 72), radius=16, fill="#f8fafb", outline=LINE, width=1)
    draw.text((width-370, 42), f"BASE QUALITY  {quality}", fill=qcolor, font=_font(14, bold=True))
    return _png(image)


def revenue_profitability(data: dict[str, Any], width: int = 1400, height: int = 590) -> BytesIO | None:
    rows = [r for r in list((data.get("fundamentals") or {}).get("history") or []) if _n(r.get("revenue")) is not None][-8:]
    if len(rows) < 2:
        return None
    image, draw = _image(width, height)
    y0 = _title(draw, "Revenue / profitability trend", "Scale-separated operating history - revenue above, margins below")
    left, right = 90, width - 55
    top1, bot1 = y0 + 20, y0 + 220
    top2, bot2 = y0 + 270, height - 65
    revs = [_n(r.get("revenue")) or 0 for r in rows]
    max_rev = max(revs) or 1
    slot = (right-left) / max(1, len(rows))
    for i, row in enumerate(rows):
        x = left + i*slot + slot*.18
        bw = slot*.64
        rev = _n(row.get("revenue")) or 0
        h = (rev/max_rev)*(bot1-top1-18)
        draw.rounded_rectangle((x, bot1-h, x+bw, bot1), radius=4, fill=PRIMARY_SOFT, outline=PRIMARY, width=2)
        draw.text((x, bot1+8), str(row.get("period") or ""), fill=MUTED, font=_font(13))
    draw.text((left, top1-2), "REVENUE", fill=MUTED, font=_font(13, bold=True))
    draw.text((right-180, top1-2), "Peak " + _money(max_rev), fill=MUTED, font=_font(13))
    series = [
        ("Gross margin", "gross_margin_pct", MARKET),
        ("Operating margin", "operating_margin_pct", PRIMARY),
        ("FCF margin", "fcf_margin_pct", POSITIVE),
    ]
    vals = [_n(r.get(k)) for _, k, _ in series for r in rows if _n(r.get(k)) is not None]
    if vals:
        lo, hi = min(vals), max(vals)
        pad = max(3.0, (hi-lo)*.2)
        lo -= pad
        hi += pad
        if lo == hi:
            hi += 1
        for g in range(4):
            yy = top2 + (bot2-top2)*g/3
            draw.line((left, yy, right, yy), fill=LINE, width=1)
            v = hi - (hi-lo)*g/3
            draw.text((20, yy-8), f"{v:.0f}%", fill=MUTED, font=_font(12))
        for label, key, color in series:
            pts = []
            for i, row in enumerate(rows):
                v = _n(row.get(key))
                if v is None:
                    continue
                x = left + i*slot + slot*.5
                y = bot2 - (v-lo)/(hi-lo)*(bot2-top2)
                pts.append((x,y))
            if len(pts) >= 2:
                draw.line(pts, fill=color, width=4)
                for p in pts:
                    draw.ellipse((p[0]-4,p[1]-4,p[0]+4,p[1]+4), fill=color)
        lx = left
        for label, _, color in series:
            draw.line((lx, height-30, lx+28, height-30), fill=color, width=4)
            draw.text((lx+36, height-40), label, fill=INK, font=_font(13))
            lx += 250
    return _png(image)


def cash_conversion(data: dict[str, Any], width: int = 1400, height: int = 500) -> BytesIO | None:
    rows = list((data.get("fundamentals") or {}).get("history") or [])[-8:]
    rows = [r for r in rows if _n(r.get("fcf")) is not None or _n(r.get("cfo_to_net_income")) is not None]
    if len(rows) < 2:
        return None
    image, draw = _image(width, height)
    y0 = _title(draw, "Free cash flow / cash conversion", "FCF scale separated from CFO / Net Income conversion")
    left, right = 90, width-55
    mid = y0 + 185
    bot = height-62
    max_abs = max([abs(_n(r.get("fcf")) or 0) for r in rows] + [1])
    slot=(right-left)/len(rows)
    zero_y=y0+145
    draw.line((left,zero_y,right,zero_y),fill=LINE,width=2)
    for i,r in enumerate(rows):
        fcf=_n(r.get("fcf"))
        if fcf is None:
            continue
        h=abs(fcf)/max_abs*110
        x=left+i*slot+slot*.2
        bw=slot*.6
        if fcf>=0:
            draw.rectangle((x,zero_y-h,x+bw,zero_y),fill=PRIMARY_SOFT,outline=PRIMARY,width=2)
        else:
            draw.rectangle((x,zero_y,x+bw,zero_y+h),fill="#fbefef",outline=NEGATIVE,width=2)
        draw.text((x,zero_y+20),str(r.get("period") or ""),fill=MUTED,font=_font(13))
    draw.text((left,y0+5),"FREE CASH FLOW",fill=MUTED,font=_font(13,bold=True))
    conv=[_n(r.get("cfo_to_net_income")) for r in rows if _n(r.get("cfo_to_net_income")) is not None]
    if conv:
        lo=min(0,min(conv))
        hi=max(1.5,max(conv))
        draw.text((left,mid+2),"CFO / NET INCOME",fill=MUTED,font=_font(13,bold=True))
        for v in (0,0.5,1.0,1.5):
            if v>hi:
                continue
            yy=bot-(v-lo)/(hi-lo)*(bot-mid-25)
            draw.line((left,yy,right,yy),fill=LINE,width=1)
            draw.text((30,yy-7),f"{v:.1f}x",fill=MUTED,font=_font(12))
        pts=[]
        for i,r in enumerate(rows):
            v=_n(r.get("cfo_to_net_income"))
            if v is None:
                continue
            x=left+i*slot+slot*.5
            y=bot-(v-lo)/(hi-lo)*(bot-mid-25)
            pts.append((x,y))
        if len(pts)>=2:
            draw.line(pts,fill=POSITIVE,width=4)
        for p in pts:
            draw.ellipse((p[0]-4,p[1]-4,p[0]+4,p[1]+4),fill=POSITIVE)
    return _png(image)


def working_capital(data: dict[str, Any], width: int = 1400, height: int = 430) -> BytesIO | None:
    rows = list((data.get("fundamentals") or {}).get("history") or [])[-8:]
    keys = ("inventory_to_revenue_pct","receivables_to_revenue_pct")
    if sum(1 for r in rows for k in keys if _n(r.get(k)) is not None) < 4:
        return None
    image, draw = _image(width,height)
    y0=_title(draw,"Working-capital forensics","Inventory / Revenue and Receivables / Revenue - divergence is the signal")
    left,right,top,bot=90,width-55,y0+15,height-65
    vals=[_n(r.get(k)) for r in rows for k in keys if _n(r.get(k)) is not None]
    lo=min(vals)
    hi=max(vals)
    pad=max(2,(hi-lo)*.2)
    lo-=pad
    hi+=pad
    if hi==lo:
        hi+=1
    for g in range(4):
        yy=top+(bot-top)*g/3
        draw.line((left,yy,right,yy),fill=LINE,width=1)
        draw.text((28,yy-7),f"{hi-(hi-lo)*g/3:.0f}%",fill=MUTED,font=_font(12))
    slot=(right-left)/max(1,len(rows)-1)
    for label,key,color in [("Inventory / Revenue","inventory_to_revenue_pct",CAUTION),("Receivables / Revenue","receivables_to_revenue_pct",PRIMARY)]:
        pts=[]
        for i,r in enumerate(rows):
            v=_n(r.get(key))
            if v is None:
                continue
            x=left+i*slot
            y=bot-(v-lo)/(hi-lo)*(bot-top)
            pts.append((x,y))
            draw.ellipse((x-4,y-4,x+4,y+4),fill=color)
        if len(pts)>=2:
            draw.line(pts,fill=color,width=4)
    for i,r in enumerate(rows):
        draw.text((left+i*slot-18,bot+16),str(r.get("period") or ""),fill=MUTED,font=_font(12))
    draw.line((left,height-28,left+28,height-28),fill=CAUTION,width=4)
    draw.text((left+36,height-38),"Inventory / Revenue",fill=INK,font=_font(13))
    draw.line((left+250,height-28,left+278,height-28),fill=PRIMARY,width=4)
    draw.text((left+286,height-38),"Receivables / Revenue",fill=INK,font=_font(13))
    return _png(image)


def price_context(data: dict[str, Any], width: int = 1400, height: int = 430) -> BytesIO | None:
    rows=list(data.get("price_history") or [])
    if len(rows)<20:
        return None
    rows=rows[-504:]
    valuation=dict(data.get("valuation") or {})
    levels=[("Bear",_n(valuation.get("bear")),NEGATIVE),("Base",_n(valuation.get("base")),PRIMARY),("Bull",_n(valuation.get("bull")),POSITIVE)]
    prices=[_n(r.get("price")) for r in rows if _n(r.get("price")) is not None]
    lvals=[v for _,v,_ in levels if v is not None]
    if not prices:
        return None
    lo=min(prices+lvals)
    hi=max(prices+lvals)
    pad=max(1,(hi-lo)*.08)
    lo-=pad
    hi+=pad
    image,draw=_image(width,height)
    y0=_title(draw,"Price context vs current fair-value scenarios","Historical market price with today's stored Bear / Base / Bull levels - not historical model values")
    left,right,top,bot=85,width-55,y0+10,height-65
    for g in range(4):
        yy=top+(bot-top)*g/3
        draw.line((left,yy,right,yy),fill=LINE,width=1)
        draw.text((22,yy-7),"$"+f"{hi-(hi-lo)*g/3:.0f}",fill=MUTED,font=_font(12))
    pts=[]
    for i,r in enumerate(rows):
        v=_n(r.get("price"))
        if v is None:
            continue
        x=left+i*(right-left)/max(1,len(rows)-1)
        y=bot-(v-lo)/(hi-lo)*(bot-top)
        pts.append((x,y))
    if len(pts)>=2:
        draw.line(pts,fill=MARKET,width=3)
    for label,val,color in levels:
        if val is None:
            continue
        yy=bot-(val-lo)/(hi-lo)*(bot-top)
        draw.line((left,yy,right,yy),fill=color,width=4 if label=="Base" else 2)
        draw.rectangle((right-175,yy-12,right,yy+12),fill=WHITE)
        draw.text((right-170,yy-9),label+" "+_money(val),fill=color,font=_font(12,bold=(label=="Base")))
    draw.text((left,bot+18),str(rows[0].get("date") or "")[:10],fill=MUTED,font=_font(12))
    draw.text((right-85,bot+18),str(rows[-1].get("date") or "")[:10],fill=MUTED,font=_font(12))
    return _png(image)


def tape_price_flow(data: dict[str, Any], width: int = 1400, height: int = 520) -> BytesIO | None:
    tape=dict(data.get("tape") or {})
    market=list(tape.get("daily_market") or [])
    flows=list(tape.get("institutional_flow") or [])
    if len(market)<10 or not flows:
        return None
    flow_map={str(r.get("date") or "")[:10]:_n(r.get("net_large")) for r in flows}
    rows=[r for r in market[-180:] if _n(r.get("price")) is not None]
    if len(rows)<10:
        return None
    image,draw=_image(width,height)
    y0=_title(draw,"Price + institutional flow","Stored market price and Net Large trade-size proxy - decision context, not owner identity")
    left,right=85,width-55
    top=y0+10
    mid=y0+185
    bot=height-65
    prices=[_n(r.get("price")) for r in rows]
    lo=min(prices)
    hi=max(prices)
    pad=max(.5,(hi-lo)*.08)
    lo-=pad
    hi+=pad
    pts=[]
    for i,r in enumerate(rows):
        x=left+i*(right-left)/max(1,len(rows)-1)
        v=_n(r.get("price"))
        y=mid-(v-lo)/(hi-lo)*(mid-top)
        pts.append((x,y))
    if len(pts)>=2:
        draw.line(pts,fill=PRIMARY,width=4)
    flow_vals=[v for r in rows if (v:=flow_map.get(str(r.get("date") or "")[:10])) is not None]
    max_abs=max([abs(v) for v in flow_vals]+[1])
    zero=(mid+bot)/2
    draw.line((left,zero,right,zero),fill=LINE,width=2)
    for i,r in enumerate(rows):
        v=flow_map.get(str(r.get("date") or "")[:10])
        if v is None:
            continue
        x=left+i*(right-left)/max(1,len(rows)-1)
        h=abs(v)/max_abs*(bot-mid)*.42
        draw.line((x,zero,x,zero-h if v>=0 else zero+h),fill=POSITIVE if v>=0 else NEGATIVE,width=3)
    draw.text((left,top-2),"PRICE",fill=MUTED,font=_font(12,bold=True))
    draw.text((left,mid+12),"NET LARGE FLOW",fill=MUTED,font=_font(12,bold=True))
    return _png(image)


def tape_pressure(data: dict[str, Any], width: int = 1400, height: int = 430) -> BytesIO | None:
    rows=list((data.get("tape") or {}).get("tape_daily") or [])[-120:]
    keys=[("Absorption","absorption",POSITIVE),("Short pressure","short_pressure",NEGATIVE),("Net tape","net_tape",PRIMARY)]
    rows=[r for r in rows if any(_n(r.get(k)) is not None for _,k,_ in keys)]
    if len(rows)<5:
        return None
    vals=[_n(r.get(k)) for r in rows for _,k,_ in keys if _n(r.get(k)) is not None]
    lo=min(vals+[0])
    hi=max(vals+[100])
    pad=(hi-lo)*.05
    lo-=pad
    hi+=pad
    image,draw=_image(width,height)
    y0=_title(draw,"Tape pressure history","Absorption, short pressure and Net Tape on their stored score scale")
    left,right,top,bot=85,width-55,y0+10,height-65
    for g in range(5):
        yy=top+(bot-top)*g/4
        v=hi-(hi-lo)*g/4
        draw.line((left,yy,right,yy),fill=LINE,width=1)
        draw.text((28,yy-7),f"{v:.0f}",fill=MUTED,font=_font(12))
    slot=(right-left)/max(1,len(rows)-1)
    for label,key,color in keys:
        pts=[]
        for i,r in enumerate(rows):
            v=_n(r.get(key))
            if v is None:
                continue
            x=left+i*slot
            y=bot-(v-lo)/(hi-lo)*(bot-top)
            pts.append((x,y))
        if len(pts)>=2:
            draw.line(pts,fill=color,width=4)
    lx=left
    for label,_,color in keys:
        draw.line((lx,height-28,lx+28,height-28),fill=color,width=4)
        draw.text((lx+36,height-38),label,fill=INK,font=_font(13))
        lx+=220
    return _png(image)


def validation_chart(data: dict[str, Any], width: int = 1400, height: int = 480) -> BytesIO | None:
    rows=list((data.get("validation") or {}).get("samples") or [])
    rows=[r for r in rows if _n(r.get("price_then")) is not None]
    if len(rows)<2:
        return None
    vals=[_n(r.get(k)) for r in rows for k in ("price_then","bear_then","base_then","bull_then","price_1y") if _n(r.get(k)) is not None]
    if not vals:
        return None
    lo=min(vals)
    hi=max(vals)
    pad=max(1,(hi-lo)*.08)
    lo-=pad
    hi+=pad
    image,draw=_image(width,height)
    y0=_title(draw,"Validation - price then vs model then","Point-in-time samples; 1Y outcome shown only when stored")
    left,right,top,bot=85,width-55,y0+10,height-75
    for g in range(4):
        yy=top+(bot-top)*g/3
        v=hi-(hi-lo)*g/3
        draw.line((left,yy,right,yy),fill=LINE,width=1)
        draw.text((20,yy-7),"$"+f"{v:.0f}",fill=MUTED,font=_font(12))
    slot=(right-left)/max(1,len(rows)-1)
    series=[("Price then","price_then",MARKET),("Bear","bear_then",NEGATIVE),("Base","base_then",PRIMARY),("Bull","bull_then",POSITIVE),("1Y outcome","price_1y",NAVY)]
    for label,key,color in series:
        pts=[]
        for i,r in enumerate(rows):
            v=_n(r.get(key))
            if v is None:
                continue
            x=left+i*slot
            y=bot-(v-lo)/(hi-lo)*(bot-top)
            pts.append((x,y))
        if len(pts)>=2:
            draw.line(pts,fill=color,width=4 if key=="base_then" else 2)
        for p in pts:
            draw.ellipse((p[0]-3,p[1]-3,p[0]+3,p[1]+3),fill=color)
    lx=left
    for label,_,color in series:
        draw.line((lx,height-31,lx+24,height-31),fill=color,width=3)
        draw.text((lx+31,height-40),label,fill=INK,font=_font(12))
        lx+=185
    return _png(image)


def chart_bundle(data: dict[str, Any]) -> dict[str, BytesIO]:
    builders = {
        "valuation_map": valuation_map,
        "revenue_profitability": revenue_profitability,
        "cash_conversion": cash_conversion,
        "working_capital": working_capital,
        "price_context": price_context,
        "tape_price_flow": tape_price_flow,
        "tape_pressure": tape_pressure,
        "validation": validation_chart,
    }
    out: dict[str, BytesIO] = {}
    for key, builder in builders.items():
        try:
            stream = builder(data)
        except Exception:
            stream = None
        if stream is not None:
            out[key] = stream
    return out


__all__ = [
    "NAVY","INK","MUTED","LINE","SOFT","PRIMARY","PRIMARY_SOFT","POSITIVE","NEGATIVE","CAUTION","MARKET",
    "valuation_map","revenue_profitability","cash_conversion","working_capital","price_context",
    "tape_price_flow","tape_pressure","validation_chart","chart_bundle",
]
