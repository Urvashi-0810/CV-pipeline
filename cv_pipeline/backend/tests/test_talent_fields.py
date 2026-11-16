"""The comparison rules behind matching and "did this field change?". Pure functions, no database."""
from decimal import Decimal

import pytest

from app.talent import fields as F


@pytest.mark.parametrize("value,key", [
    ("P1234567", "P1234567"),
    (" p-123 4567 ", "P1234567"),
    ("Z 76-54-321", "Z7654321"),
    ("A/1234567", "A/1234567"),             # only spaces and dashes are removed
])
def test_passport_key(value, key):
    assert F.passport_key(value) == key


@pytest.mark.parametrize("value", [None, "", "N/A", "n.a.", "NA", "Not Mentioned", "not-available", "NIL", "None",
                                   "-", "---", "0", "000000000", "12345", "Applied for", "XXXXXX"])
def test_passport_placeholders_count_as_no_passport(value):
    assert F.passport_key(value) is None


@pytest.mark.parametrize("value,key", [
    ("+91 98765 43210", "9876543210"),
    ("098765 43210", "9876543210"),
    ("(022) 2345-6789", "2223456789"),
    ("12345", None),                        # fewer than 7 digits
    (None, None),
])
def test_phone_key_is_the_last_10_digits(value, key):
    assert F.phone_key(value) == key


def test_email_key():
    assert F.email_key(" Rahul.Sharma@Example.COM ") == "rahul.sharma@example.com"
    assert F.email_key("not an email") is None


@pytest.mark.parametrize("first,second,similar", [
    ("Rahul Sharma", "SHARMA Rahul", True),
    ("Rahul Sharma", "Rahul K. Sharma", True),
    ("R. Sharma", "r sharma", True),
    ("Rahul Sharma", "Rahul Verma", False),
    ("Rahul", "Rahul Sharma", False),        # one word isn't enough
    ("Rahul", "rahul", True),                # unless the names are identical
    ("Rahul Sharma", None, False),
])
def test_similar_names(first, second, similar):
    assert F.similar_names(first, second) is similar
    assert F.similar_names(second, first) is similar


def test_changed_ignores_case_spacing_and_list_order():
    assert F.same_value("job_title", "Pipe  Fitter", "pipe fitter")
    assert F.same_value("skills", ["Welding", "MIG"], ["mig", " welding "])
    assert F.same_value("experience_years", 7.5, "7.50")
    assert not F.same_value("skills", ["Welding"], ["Welding", "TIG"])
    assert not F.same_value("location", "Pune", "Mumbai")


def test_values_are_cleaned_for_the_table():
    assert F.to_column("skills", "Welding, MIG, , welding") == ["Welding", "MIG"]
    assert F.to_column("experience_years", "7.46") == Decimal("7.5")
    assert F.to_column("experience_years", "8.25") == Decimal("8.3")        # halves round up
    assert F.to_column("experience_years", "about 5") is None
    assert F.to_column("age", "99999") is None
    assert F.to_column("full_name", "  ") is None
