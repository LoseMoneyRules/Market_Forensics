from __future__ import annotations

"""Bounded filing-level iXBRL fallback for SEC custom taxonomy concepts.

SEC companyfacts deliberately aggregates standard-taxonomy, whole-entity facts.
When a filer uses an extension concept for a statement line, the primary filing
is the authoritative fallback. This module extracts only numeric, non-dimensional
whole-entity facts and maps them through exact statement labels supplied by the
caller. It does not perform ticker-specific inference.
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import re
from typing import Any, Iterable
from xml.etree import ElementTree

import requests


STANDARD_PREFIXES = {
    "us-gaap", "dei", "srt", "ifrs-full", "country", "currency", "exch", "naics", "sic", "stpr",
}
XLINK = "{http://www.w3.org/1999/xlink}"


def normalize_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    for ch in ",.()[]{}:/_-&":
        text = text.replace(ch, " ")
    return " ".join(text.split())


def humanize_concept(local_name: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(local_name or ""))
    return normalize_label(text)


def parse_numeric(text: str, *, scale: str = "", sign: str = "") -> Decimal | None:
    raw = str(text or "").replace("\xa0", " ").strip()
    if not raw or raw in {"-", "—", "–"}:
        return None
    negative_parentheses = raw.startswith("(") and raw.endswith(")")
    cleaned = raw.strip("()").replace(",", "").replace("$", "").replace("€", "").replace("£", "").strip()
    cleaned = cleaned.replace("%", "")
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    try:
        if str(scale or "").strip():
            value *= Decimal(10) ** int(str(scale).strip())
    except (ValueError, InvalidOperation):
        return None
    if negative_parentheses:
        value = -abs(value)
    if str(sign or "").strip() == "-":
        value = -abs(value)
    return value if value.is_finite() else None


class InlineFactsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contexts: dict[str, dict[str, Any]] = {}
        self.facts: list[dict[str, Any]] = []
        self._context: dict[str, Any] | None = None
        self._context_depth = 0
        self._context_capture: str | None = None
        self._context_text: list[str] = []
        self._fact: dict[str, Any] | None = None
        self._fact_depth = 0
        self._exclude_depth = 0

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {str(key).lower(): str(value or "") for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = str(tag or "").lower()
        a = self._attrs(attrs)

        if self._context is not None:
            self._context_depth += 1
            if (
                low.endswith(":explicitmember")
                or low.endswith(":typedmember")
                or low.endswith(":segment")
                or low.endswith(":scenario")
            ):
                self._context["dimensional"] = True
            if low.endswith(":startdate"):
                self._context_capture = "start"
                self._context_text = []
            elif low.endswith(":enddate"):
                self._context_capture = "end"
                self._context_text = []
            elif low.endswith(":instant"):
                self._context_capture = "instant"
                self._context_text = []

        if low.endswith(":context") and self._context is None:
            context_id = a.get("id", "")
            self._context = {
                "id": context_id,
                "start": None,
                "end": None,
                "instant": None,
                "dimensional": False,
            }
            self._context_depth = 1
            self._context_capture = None
            self._context_text = []
            return

        if self._fact is not None:
            self._fact_depth += 1
            if low == "ix:exclude":
                self._exclude_depth += 1
            return

        if low == "ix:nonfraction":
            self._fact = {
                "name": a.get("name", ""),
                "contextref": a.get("contextref", ""),
                "unitref": a.get("unitref", ""),
                "scale": a.get("scale", ""),
                "sign": a.get("sign", ""),
                "format": a.get("format", ""),
                "text": [],
            }
            self._fact_depth = 1
            self._exclude_depth = 0

    def handle_endtag(self, tag: str) -> None:
        low = str(tag or "").lower()

        if self._fact is not None:
            if low == "ix:exclude" and self._exclude_depth:
                self._exclude_depth -= 1
            self._fact_depth -= 1
            if low == "ix:nonfraction" and self._fact_depth <= 0:
                fact = self._fact
                fact["text"] = "".join(fact.get("text") or [])
                self.facts.append(fact)
                self._fact = None
                self._fact_depth = 0
                self._exclude_depth = 0

        if self._context is not None:
            if self._context_capture and (
                (self._context_capture == "start" and low.endswith(":startdate"))
                or (self._context_capture == "end" and low.endswith(":enddate"))
                or (self._context_capture == "instant" and low.endswith(":instant"))
            ):
                self._context[self._context_capture] = "".join(self._context_text).strip()
                self._context_capture = None
                self._context_text = []
            self._context_depth -= 1
            if low.endswith(":context") and self._context_depth <= 0:
                if self._context.get("id"):
                    self.contexts[str(self._context["id"])] = dict(self._context)
                self._context = None
                self._context_depth = 0
                self._context_capture = None
                self._context_text = []

    def handle_data(self, data: str) -> None:
        if self._context is not None and self._context_capture:
            self._context_text.append(str(data or ""))
        if self._fact is not None and self._exclude_depth == 0:
            self._fact["text"].append(str(data or ""))


def parse_inline_numeric_facts(html: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    parser = InlineFactsParser()
    parser.feed(str(html or ""))
    parser.close()
    return parser.contexts, parser.facts


def parse_label_linkbase(xml_text: str) -> dict[str, str]:
    """Map local custom concept names to their standard human-readable labels."""
    if not str(xml_text or "").strip():
        return {}
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return {}

    loc_to_concept: dict[str, str] = {}
    resource_labels: dict[str, tuple[int, str]] = {}
    arcs: list[tuple[str, str]] = []

    for node in root.iter():
        local_tag = node.tag.rsplit("}", 1)[-1].lower()
        if local_tag == "loc":
            loc_label = node.attrib.get(XLINK + "label", "")
            href = node.attrib.get(XLINK + "href", "")
            fragment = href.split("#")[-1]
            local = fragment.split("_", 1)[-1] if "_" in fragment else fragment
            if loc_label and local:
                loc_to_concept[loc_label] = local
        elif local_tag == "label":
            resource = node.attrib.get(XLINK + "label", "")
            role = node.attrib.get(XLINK + "role", "")
            text = "".join(node.itertext()).strip()
            if resource and text:
                score = 2 if role.endswith("/label") else 1
                previous = resource_labels.get(resource)
                if previous is None or score > previous[0]:
                    resource_labels[resource] = (score, text)
        elif local_tag == "labelarc":
            source = node.attrib.get(XLINK + "from", "")
            target = node.attrib.get(XLINK + "to", "")
            if source and target:
                arcs.append((source, target))

    out: dict[str, str] = {}
    for source, target in arcs:
        concept = loc_to_concept.get(source)
        resource = resource_labels.get(target)
        if concept and resource:
            out.setdefault(concept, resource[1])
    return out


def _field_for_label(label: str, label_aliases: dict[str, Iterable[str]]) -> str | None:
    normalized = normalize_label(label)
    for field, aliases in label_aliases.items():
        if normalized in {normalize_label(alias) for alias in aliases}:
            return field
    return None


def _filing_base(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(str(cik))}/{str(accession).replace('-', '')}"


def _get(url: str, user_agent: str, timeout: int = 15) -> requests.Response | None:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
        )
        return response if response.status_code == 200 else None
    except Exception:
        return None


def extract_extension_concepts(
    *,
    cik: str,
    accession: str,
    primary_document: str,
    form: str,
    filed: str,
    user_agent: str,
    label_aliases: dict[str, Iterable[str]],
) -> dict[str, dict[str, Any]]:
    """Extract exact-label custom facts from one SEC filing.

    Output is shaped like a Companyfacts namespace so the normal SEC resolver can
    apply the same annual/quarter rules and provenance to standard and extension
    concepts.
    """
    base = _filing_base(cik, accession)
    index_response = _get(f"{base}/index.json", user_agent)
    items = []
    if index_response is not None:
        try:
            items = (((index_response.json() or {}).get("directory") or {}).get("item") or [])
        except Exception:
            items = []

    primary = str(primary_document or "").strip()
    if not primary:
        html_names = [
            str((item or {}).get("name") or "")
            for item in items
            if re.search(r"\.html?$", str((item or {}).get("name") or ""), re.I)
        ]
        primary = html_names[0] if html_names else ""
    if not primary:
        return {}

    primary_response = _get(f"{base}/{primary}", user_agent, 20)
    if primary_response is None:
        return {}
    contexts, facts = parse_inline_numeric_facts(primary_response.text)
    if not facts:
        return {}

    label_map: dict[str, str] = {}
    label_names = [
        str((item or {}).get("name") or "")
        for item in items
        if re.search(r"(?:_lab|_label)\.xml$", str((item or {}).get("name") or ""), re.I)
    ]
    for name in label_names[:2]:
        response = _get(f"{base}/{name}", user_agent)
        if response is not None:
            label_map.update(parse_label_linkbase(response.text))

    concepts: dict[str, dict[str, Any]] = {}
    form = str(form or "").upper()
    for fact in facts:
        name = str(fact.get("name") or "")
        if ":" not in name:
            continue
        prefix, local = name.split(":", 1)
        if prefix.lower() in STANDARD_PREFIXES:
            continue
        context = contexts.get(str(fact.get("contextref") or ""))
        if not context or context.get("dimensional"):
            continue
        end = str(context.get("instant") or context.get("end") or "")[:10]
        start = str(context.get("start") or "")[:10]
        if not end:
            continue
        value = parse_numeric(
            str(fact.get("text") or ""),
            scale=str(fact.get("scale") or ""),
            sign=str(fact.get("sign") or ""),
        )
        if value is None:
            continue
        label = label_map.get(local) or humanize_concept(local)
        field = _field_for_label(label, label_aliases)
        if not field:
            continue
        unit = str(fact.get("unitref") or "USD")
        record = {
            "start": start or None,
            "end": end,
            "val": value,
            "form": form,
            "fp": "FY" if form in {"10-K", "10-K/A"} else "",
            "filed": str(filed or "")[:10],
            "accn": str(accession or ""),
            "unit": unit,
            "tag": local,
            "namespace": prefix,
            "_mf_filing_extension": True,
            "_mf_extension_label": label,
            "_mf_field": field,
        }
        node = concepts.setdefault(
            local,
            {"label": label, "_mf_field": field, "units": defaultdict(list)},
        )
        node["units"][unit].append(record)

    # Convert defaultdicts to normal dictionaries for predictable JSON-like shape.
    for node in concepts.values():
        node["units"] = dict(node.get("units") or {})
    return concepts


__all__ = [
    "extract_extension_concepts",
    "humanize_concept",
    "normalize_label",
    "parse_inline_numeric_facts",
    "parse_label_linkbase",
    "parse_numeric",
]
