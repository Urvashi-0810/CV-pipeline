"""Talent Pool settings page: Talent Pool admin only. Reads and writes the backend's .env."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.auth.deps import AdminUser
from app.talent import connection_tests, settings_admin

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list, description="secrets to remove")


@router.get("")
def get_settings(_: AdminUser):
    """Every setting with its saved value. Secrets only say whether they're set."""
    return settings_admin.describe()


@router.put("")
def save_settings(body: SettingsUpdate, admin: AdminUser):
    result = settings_admin.save(body.values, body.clear, username=admin.user.username)
    return result | {"settings": settings_admin.describe()}


@router.post("/test/{target}")
def test_connection(target: Literal["mail", "s3", "database", "llm"], _: AdminUser):
    return connection_tests.TESTS[target]()
