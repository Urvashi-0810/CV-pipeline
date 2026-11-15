"""Client Pool: list and search, add, edit, delete, and the checks on names and emails."""

from tests.conftest import login

ACME = {
    "name": "Acme Contracting LLC",
    "email": "jobs@acme.ae",
    "contacts": [
        {"name": "Omar Haddad", "phone": "+971 50 123 4567", "email": "omar@acme.ae"},
        {"name": "Priya Nair", "phone": "", "email": ""},
    ],
}


async def _logged_in(client, make_user):
    await make_user("ravi")
    await login(client, "ravi")


async def test_clients_need_a_login(client):
    assert (await client.get("/api/v1/clients")).status_code == 401


async def test_add_view_and_list(client, make_user):
    await _logged_in(client, make_user)

    created = await client.post("/api/v1/clients", json=ACME)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Acme Contracting LLC" and body["email"] == "jobs@acme.ae"
    assert [c["name"] for c in body["contacts"]] == ["Omar Haddad", "Priya Nair"]
    assert body["contacts"][1]["phone"] is None and body["contacts"][1]["email"] is None   # blanks stored as empty
    assert created.headers["location"] == f"/api/v1/clients/{body['id']}"

    assert (await client.get(f"/api/v1/clients/{body['id']}")).json() == body
    page = (await client.get("/api/v1/clients")).json()
    assert page["total"] == 1 and page["items"][0]["id"] == body["id"]


async def test_search_by_company_or_contact_person(client, make_user):
    await _logged_in(client, make_user)
    await client.post("/api/v1/clients", json=ACME)
    await client.post("/api/v1/clients", json={"name": "Gulf Marine", "contacts": [{"name": "Sara Khan"}]})
    await client.post("/api/v1/clients", json={"name": "Bright Builders"})

    async def names(q):
        return [c["name"] for c in (await client.get("/api/v1/clients", params={"q": q})).json()["items"]]

    assert await names("marine") == ["Gulf Marine"]
    assert await names("PRIYA") == ["Acme Contracting LLC"]           # a contact person's name
    assert await names("") == ["Acme Contracting LLC", "Bright Builders", "Gulf Marine"]   # alphabetical
    assert await names("nobody") == []


async def test_company_name_is_required_and_unique(client, make_user):
    await _logged_in(client, make_user)
    blank = await client.post("/api/v1/clients", json={"name": "   "})
    assert blank.status_code == 422 and blank.json()["errors"][0]["field"] == "name"

    await client.post("/api/v1/clients", json=ACME)
    again = await client.post("/api/v1/clients", json={"name": "  acme contracting llc "})
    assert again.status_code == 409
    assert again.json()["type"] == "urn:crm:problem:client-name-taken"
    assert again.json()["errors"] == [{"field": "name", "message": "A client with this name already exists."}]


async def test_emails_and_phones_are_checked(client, make_user):
    await _logged_in(client, make_user)
    response = await client.post("/api/v1/clients", json={
        "name": "Acme", "email": "jobs@acme",
        "contacts": [{"name": "Omar", "email": "omar acme.ae", "phone": "call me"}, {"name": ""}],
    })
    assert response.status_code == 422
    fields = {e["field"] for e in response.json()["errors"]}
    assert fields == {"email", "contacts.0.email", "contacts.0.phone", "contacts.1.name"}


async def test_edit_replaces_details_and_contacts(client, make_user):
    await _logged_in(client, make_user)
    acme = (await client.post("/api/v1/clients", json=ACME)).json()
    other = (await client.post("/api/v1/clients", json={"name": "Gulf Marine"})).json()

    edited = await client.put(f"/api/v1/clients/{acme['id']}", json={
        "name": "Acme Contracting", "email": "",
        "contacts": [{"name": "Priya Nair", "phone": "0501234567", "email": "priya@acme.ae"}],
    })
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["name"] == "Acme Contracting" and body["email"] is None
    assert [(c["name"], c["phone"]) for c in body["contacts"]] == [("Priya Nair", "0501234567")]

    # Keeping its own name is fine; taking another client's name is not.
    same = await client.put(f"/api/v1/clients/{acme['id']}", json={"name": "ACME CONTRACTING"})
    assert same.status_code == 200
    taken = await client.put(f"/api/v1/clients/{acme['id']}", json={"name": "gulf marine"})
    assert taken.status_code == 409
    assert (await client.get(f"/api/v1/clients/{other['id']}")).json()["name"] == "Gulf Marine"


async def test_delete(client, make_user):
    await _logged_in(client, make_user)
    acme = (await client.post("/api/v1/clients", json=ACME)).json()

    assert (await client.delete(f"/api/v1/clients/{acme['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/clients/{acme['id']}")).status_code == 404
    assert (await client.delete(f"/api/v1/clients/{acme['id']}")).status_code == 404
    assert (await client.get("/api/v1/clients")).json()["total"] == 0
    # The name is free again.
    assert (await client.post("/api/v1/clients", json=ACME)).status_code == 201


async def test_writes_need_the_csrf_token(client, make_user):
    await _logged_in(client, make_user)
    client.csrf = None
    response = await client.post("/api/v1/clients", json=ACME)
    assert response.status_code == 403 and response.json()["type"] == "urn:crm:problem:csrf-token-invalid"
