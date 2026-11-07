"""Positions read from the letter's positions table (no database needed)."""

from app.modules.letters.positions import positions_from_letter


def table(columns, rows):
    return {"type": "table", "columns": columns, "rows": rows}


def test_reads_position_quantity_salary_and_currency():
    letter = {"blocks": [table(["Position", "Quantity", "Salary Range (AED)"], [
        ["**Foreman (Civil)**", "261", "4,000–5,000"],
        ["Mason - Block", "1,200", "AED 1,400 + food"],
        ["Total", "1,461", ""],
    ])]}
    assert positions_from_letter(letter) == [
        {"project": None, "trade": "Foreman (Civil)", "quantity": 261, "salary": "4,000–5,000 AED"},
        {"project": None, "trade": "Mason - Block", "quantity": 1200, "salary": "AED 1,400 + food"},
    ]


def test_project_from_column_or_heading():
    letter = {"blocks": [
        {"type": "heading", "text": "Project: Katara Cultural Village"},
        table(["Trade", "Qty", "Monthly salary"], [["Welder", "4 nos", "QAR 2,000"]]),
        table(["Project", "Designation", "No. required"], [["MSY", "Cleaner", "10"]]),
    ]}
    assert positions_from_letter(letter) == [
        {"project": "Katara Cultural Village", "trade": "Welder", "quantity": 4, "salary": "QAR 2,000"},
        {"project": "MSY", "trade": "Cleaner", "quantity": 10, "salary": None},
    ]


def test_placeholders_and_other_tables():
    # Only placeholders: a positions table with no positions yet.
    assert positions_from_letter({"blocks": [table(["Position", "Quantity", "Salary"], [
        ["[[MISSING: Position]]", "[[MISSING: Quantity]]", "[[MISSING: Salary]]"]])]}) == []
    # A missing salary is left empty, not copied as the placeholder.
    assert positions_from_letter({"blocks": [table(["Position", "Qty", "Salary"], [
        ["Driver", "3", "[[MISSING: Salary]]"]])]})[0]["salary"] is None
    # No positions table at all → None (the positions are left as they are).
    assert positions_from_letter({"blocks": [table(["Benefit", "Details"], [["Food", "Provided"]])]}) is None
    assert positions_from_letter({"blocks": [{"type": "paragraph", "text": "Hello"}]}) is None
