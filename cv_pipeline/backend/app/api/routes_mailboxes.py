"""Mailboxes CVs are fetched from. Any logged-in user can list them and their folders to start a fetch;
adding, editing, removing and testing them needs Talent Pool admin."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.auth.deps import AdminUser, DbSession
from app.talent import mailboxes

router = APIRouter(prefix="/api/mailboxes", tags=["mailboxes"])


class MailboxIn(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None          # blank on edit keeps the current one
    host: Optional[str] = None
    port: Optional[int] = None
    default_folder: Optional[str] = None


@router.get("")
def list_mailboxes(db: DbSession):
    return {"items": mailboxes.list_mailboxes(db)}


@router.get("/{mailbox_id}/folders")
def folders(mailbox_id: int, db: DbSession):
    return {"items": mailboxes.folders(db, mailbox_id)}


@router.post("", status_code=status.HTTP_201_CREATED)
def create(body: MailboxIn, _: AdminUser, db: DbSession):
    mailbox = mailboxes.create(db, body.model_dump())
    db.commit()
    return mailboxes.describe(mailbox)


@router.patch("/{mailbox_id}")
def update(mailbox_id: int, body: MailboxIn, _: AdminUser, db: DbSession):
    mailbox = mailboxes.update(db, mailbox_id, body.model_dump(exclude_unset=True))
    db.commit()
    return mailboxes.describe(mailbox)


@router.delete("/{mailbox_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove(mailbox_id: int, _: AdminUser, db: DbSession):
    """Removes the connection only. Candidates and CVs that came from it stay."""
    mailboxes.remove(db, mailbox_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{mailbox_id}/test")
def test(mailbox_id: int, _: AdminUser, db: DbSession):
    return mailboxes.test(db, mailbox_id)
