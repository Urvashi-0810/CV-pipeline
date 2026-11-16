from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.deps import CurrentUser, DbSession
from app.modules.clients import service
from app.modules.clients.schemas import ClientIn, ClientOut, ClientPage

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_model=ClientPage)
async def list_clients(
    _: CurrentUser,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """The Client Pool, alphabetical. q searches the company name and the contact persons' names."""
    items, total = await service.list_clients(db, q, limit=limit, offset=offset)
    return ClientPage(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
async def create_client(body: ClientIn, current: CurrentUser, db: DbSession, response: Response):
    """Add a client. 409 if a client with the same name (ignoring case) exists."""
    client = await service.create_client(db, body, user_id=current.user.id)
    response.headers["Location"] = f"/api/v1/clients/{client.id}"
    return client


@router.get("/{client_id}", response_model=ClientOut)
async def get_client(client_id: int, _: CurrentUser, db: DbSession):
    return await service.get_client(db, client_id)


@router.put("/{client_id}", response_model=ClientOut)
async def update_client(client_id: int, body: ClientIn, _: CurrentUser, db: DbSession):
    """Save the whole client: name, email and the full list of contact persons."""
    return await service.update_client(db, client_id, body)


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(client_id: int, _: CurrentUser, db: DbSession):
    """Delete a client and its contact persons."""
    await service.delete_client(db, client_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
