"""Talent Pool login, the current user, and the Talent Pool admin area."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict

from app.auth import service
from app.auth.deps import AdminUser, CurrentUser, DbSession
from app.auth.security import clear_session_cookie, set_session_cookie

router = APIRouter(prefix="/api", tags=["auth"])
NO_CONTENT = status.HTTP_204_NO_CONTENT


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime]


class LoginRequest(BaseModel):
    username: str
    password: str


class AdminUnlock(BaseModel):
    admin_password: str


class AdminPasswordChange(BaseModel):
    current_password: str
    new_password: str


class UserCreate(BaseModel):
    username: str
    display_name: str
    initial_password: str


class UserUpdate(BaseModel):
    is_active: bool


class PasswordReset(BaseModel):
    new_password: str


# ---------- login ----------

@router.post("/auth/login")
def login(body: LoginRequest, response: Response, db: DbSession):
    user, token, csrf = service.login(db, body.username, body.password)
    set_session_cookie(response, token)
    return {"user": UserOut.model_validate(user).model_dump(mode="json"), "csrf_token": csrf}


@router.post("/auth/logout", status_code=NO_CONTENT)
def logout(current: CurrentUser, db: DbSession):
    service.logout(db, current.session)
    response = Response(status_code=NO_CONTENT)
    clear_session_cookie(response)
    return response


@router.get("/me")
def me(current: CurrentUser):
    user = current.user
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "admin_active": current.admin_active,
        "admin_until": current.session.admin_until if current.admin_active else None,
        # Send this back in the X-CSRF-Token header on every POST/PUT/PATCH/DELETE.
        "csrf_token": current.session.csrf_token,
    }


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


@router.put("/me/password", status_code=NO_CONTENT)
def change_my_password(body: PasswordChange, current: CurrentUser, db: DbSession):
    """Change your own password. Your other logged-in browsers are logged out."""
    service.change_own_password(db, user=current.user, current_session_id=current.session.id,
                                current_password=body.current_password, new_password=body.new_password)
    return Response(status_code=NO_CONTENT)


# ---------- admin unlock ----------

@router.get("/admin/session")
def admin_session_status(current: CurrentUser, db: DbSession):
    return {
        "active": current.admin_active,
        "expires_at": current.session.admin_until if current.admin_active else None,
        "password_configured": service.admin_password_configured(db),
    }


@router.post("/admin/session", status_code=NO_CONTENT)
def unlock_admin(body: AdminUnlock, current: CurrentUser, db: DbSession):
    service.unlock_admin(db, current.session, current.user, body.admin_password)
    return Response(status_code=NO_CONTENT)


@router.delete("/admin/session", status_code=NO_CONTENT)
def lock_admin(current: CurrentUser, db: DbSession):
    service.lock_admin(db, current.session)
    return Response(status_code=NO_CONTENT)


@router.put("/admin/password", status_code=NO_CONTENT)
def change_admin_password(body: AdminPasswordChange, admin: AdminUser, db: DbSession):
    service.change_admin_password(db, user_id=admin.user.id, current=body.current_password, new=body.new_password)
    return Response(status_code=NO_CONTENT)


# ---------- users ----------

@router.get("/admin/users")
def list_users(_: AdminUser, db: DbSession):
    return {"items": [UserOut.model_validate(user).model_dump(mode="json") for user in service.list_users(db)]}


@router.post("/admin/users", status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, _: AdminUser, db: DbSession):
    user = service.create_user(db, username=body.username, display_name=body.display_name,
                               password=body.initial_password)
    return UserOut.model_validate(user).model_dump(mode="json")


@router.patch("/admin/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, admin: AdminUser, db: DbSession):
    user = service.set_user_active(db, acting_user_id=admin.user.id, user_id=user_id, is_active=body.is_active)
    return UserOut.model_validate(user).model_dump(mode="json")


@router.put("/admin/users/{user_id}/password", status_code=NO_CONTENT)
def reset_user_password(user_id: int, body: PasswordReset, _: AdminUser, db: DbSession):
    """Set a new password for a user who forgot theirs. Logs that user out everywhere."""
    service.reset_password(db, user_id=user_id, new_password=body.new_password)
    return Response(status_code=NO_CONTENT)
