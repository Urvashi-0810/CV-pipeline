"""Positions (project, trade, quantity, salary) read from the letter's own positions table.

The letter is the source of truth: whenever a new version is saved, the demand's positions are
replaced with the rows of the table(s) in that version. A table counts as a positions table when
its headers name a position/trade column and a quantity column; project and salary columns are
optional. A "Project: …" heading just above a table gives the project for its rows.
"""

import re

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MISSING = re.compile(r"\[\[MISSING:[^\]]*\]\]", re.IGNORECASE)
_FIRST_INT = re.compile(r"\d[\d,]*")
_CURRENCY = re.compile(r"\(([A-Za-z]{3}|[$€£₹])\)")
_PROJECT_HEADING = re.compile(r"^\s*project\s*(?:name)?\s*[:\-–—]\s*(.+)$", re.IGNORECASE)
_TOTAL_ROW = re.compile(r"^\s*(grand\s+)?total\b", re.IGNORECASE)

_HEADERS = {
    "trade": ("position", "trade", "designation", "category", "job title", "role", "profession"),
    "quantity": ("qty", "quantity", "no.", "nos", "number", "required", "vacanc", "count", "headcount"),
    "salary": ("salary", "wage", "pay", "remuneration"),
    "project": ("project", "site"),
}


def _clean(text: str | None) -> str:
    text = _BOLD.sub(r"\1", text or "")
    return _MISSING.sub("", text).strip()


def _column_roles(columns: list[str]) -> dict[str, int]:
    roles: dict[str, int] = {}
    for index, header in enumerate(columns):
        h = _clean(header).lower()
        # Salary first: "Salary (per position)" must not become the trade column.
        for role in ("salary", "quantity", "project", "trade"):
            if role not in roles and any(word in h for word in _HEADERS[role]):
                roles[role] = index
                break
    return roles


def positions_from_letter(content: dict) -> list[dict] | None:
    """The letter's positions, in order; None when the letter has no positions table."""
    found: list[dict] = []
    any_table = False
    heading_project: str | None = None
    for block in content.get("blocks") or []:
        kind = block.get("type")
        if kind in ("heading", "paragraph"):
            match = _PROJECT_HEADING.match(_clean(block.get("text")))
            if match:
                heading_project = match.group(1).strip() or None
            elif kind == "heading":
                heading_project = None
            continue
        if kind != "table":
            continue
        columns = block.get("columns") or []
        roles = _column_roles(columns)
        if "trade" not in roles or "quantity" not in roles:
            continue
        any_table = True
        currency = None
        if "salary" in roles:
            m = _CURRENCY.search(_clean(columns[roles["salary"]]))
            currency = m.group(1) if m else None

        def cell(row: list[str], role: str) -> str:
            i = roles.get(role)
            return _clean(row[i]) if i is not None and i < len(row) else ""

        for row in block.get("rows") or []:
            trade = cell(row, "trade")
            if not trade or _TOTAL_ROW.match(trade):
                continue
            qty_match = _FIRST_INT.search(cell(row, "quantity"))
            quantity = int(qty_match.group().replace(",", "")) if qty_match else 0
            if quantity <= 0:
                continue
            salary = cell(row, "salary") or None
            # "4,000–5,000" under "Salary (AED)" → "4,000–5,000 AED"
            if salary and currency and not re.search(r"[A-Za-z$€£₹]", salary):
                salary = f"{salary} {currency}"
            found.append({
                "project": cell(row, "project") or heading_project,
                "trade": trade[:200],
                "quantity": min(quantity, 100_000),
                "salary": salary[:200] if salary else None,
            })
    return found if any_table else None
