import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

# Deliberately simple: something@domain.tld, no spaces. Catches typos without rejecting real addresses.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
PHONE_RE = re.compile(r"^\+?[0-9 ()./-]{6,30}$")


def _blank_to_none(value):
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _check_email(value: str | None) -> str | None:
    if value is not None and not EMAIL_RE.match(value):
        raise ValueError("Enter a valid email address, like name@company.com.")
    return value


def _check_phone(value: str | None) -> str | None:
    if value is not None and (not PHONE_RE.match(value) or sum(ch.isdigit() for ch in value) < 6):
        raise ValueError("Enter a valid phone number: digits, spaces, + ( ) - only.")
    return value


# Optional fields: a blank value becomes None; anything else is trimmed, length-checked, then format-checked.
Email = Annotated[
    Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)] | None,
    BeforeValidator(_blank_to_none),
    AfterValidator(_check_email),
]
Phone = Annotated[
    Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] | None,
    BeforeValidator(_blank_to_none),
    AfterValidator(_check_phone),
]
CompanyName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ContactIn(BaseModel):
    name: PersonName
    phone: Phone = None
    email: Email = None


class ClientIn(BaseModel):
    """Create, or replace on edit: the contacts sent become the client's full list."""

    name: CompanyName
    email: Email = None
    contacts: list[ContactIn] = Field(default_factory=list, max_length=50)


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str | None
    email: str | None


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str | None
    contacts: list[ContactOut]
    created_at: datetime
    updated_at: datetime


class ClientPage(BaseModel):
    items: list[ClientOut]
    total: int
    limit: int
    offset: int
