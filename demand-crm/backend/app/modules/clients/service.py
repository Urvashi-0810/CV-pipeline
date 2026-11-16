"""Client Pool: the companies that send us demand inquiries, and who to contact there."""

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ProblemError
from app.modules.clients.models import Client, ClientContact
from app.modules.clients.schemas import ClientIn


def _name_taken(name: str) -> ProblemError:
    return ProblemError(
        409, "client-name-taken", "This client already exists",
        f'A client named "{name}" is already in the Client Pool.',
        errors=[{"field": "name", "message": "A client with this name already exists."}],
    )


async def _check_name_free(db: AsyncSession, name: str, except_id: int | None = None) -> None:
    query = select(Client.id).where(func.lower(func.btrim(Client.name)) == name.strip().lower())
    if except_id is not None:
        query = query.where(Client.id != except_id)
    if (await db.execute(query)).first():
        raise _name_taken(name)


async def _get(db: AsyncSession, client_id: int) -> Client:
    client = await db.get(Client, client_id)
    if client is None:
        raise ProblemError(404, "client-not-found", "Client not found", "It may have been deleted.")
    return client


def _contacts(body: ClientIn) -> list[ClientContact]:
    return [
        ClientContact(name=c.name, phone=c.phone, email=c.email, sort_order=order)
        for order, c in enumerate(body.contacts)
    ]


async def _commit(db: AsyncSession, name: str) -> None:
    try:
        await db.commit()
    except IntegrityError:  # two people saving the same name at the same moment
        await db.rollback()
        raise _name_taken(name)


async def list_clients(db: AsyncSession, q: str | None, *, limit: int, offset: int) -> tuple[list[Client], int]:
    """Alphabetical. q matches the company name or any contact person's name."""
    query = select(Client)
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(
            Client.name.ilike(like),
            exists().where(ClientContact.client_id == Client.id, ClientContact.name.ilike(like)),
        ))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await db.execute(query.order_by(func.lower(Client.name), Client.id).limit(limit).offset(offset))
    ).scalars().all()
    return list(rows), total


async def get_client(db: AsyncSession, client_id: int) -> Client:
    return await _get(db, client_id)


async def create_client(db: AsyncSession, body: ClientIn, *, user_id: int) -> Client:
    await _check_name_free(db, body.name)
    client = Client(name=body.name, email=body.email, created_by=user_id, contacts=_contacts(body))
    db.add(client)
    await _commit(db, body.name)
    await db.refresh(client)
    return client


async def update_client(db: AsyncSession, client_id: int, body: ClientIn) -> Client:
    client = await _get(db, client_id)
    await _check_name_free(db, body.name, except_id=client_id)
    client.name = body.name
    client.email = body.email
    client.contacts = _contacts(body)            # the form sends the full list
    client.updated_at = func.now()               # a contacts-only edit still counts as an update
    await _commit(db, body.name)
    await db.refresh(client)
    return client


async def delete_client(db: AsyncSession, client_id: int) -> None:
    client = await _get(db, client_id)
    await db.delete(client)                      # its contacts go with it
    await db.commit()
