"""API layer: the full flow over HTTP, exactly as the React app will call it."""
import time

from fastapi.testclient import TestClient

import mock_llm
from app.api.main import app
from conftest import seed_candidates

mock_llm.start()
client = TestClient(app)
JD = "Python developer with Django and AWS, 3+ years backend. Kubernetes nice to have."


def wait(task_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        t = client.get(f"/api/tasks/{task_id}").json()
        if t["status"] in ("done", "error"):
            return t
        time.sleep(0.1)
    raise TimeoutError(task_id)


def test_full_flow(clean_data):
    assert client.get("/api/health").json() == {"ok": True}
    cfg = client.get("/api/config").json()
    assert cfg["providers_configured"]["claude"] and "password" not in str(cfg).lower()

    seed_candidates([
        {"Full Name": "Full Match", "Skills": "Python, Django, AWS", "Experience (Years)": 6},
        {"Full Name": "Weak", "Skills": "Excel", "Experience (Years)": 6},
    ])
    rows = client.get("/api/candidates", params={"search": "django"}).json()
    assert rows["total"] == 1 and rows["rows"][0]["Full Name"] == "Full Match"
    assert client.get("/api/candidates/download").status_code == 200

    # create job (form data, like an HTML form / file upload)
    r = client.post("/api/jobs", data={"title": "Python Developer", "jd_text": JD})
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    assert client.post("/api/jobs", data={"title": "Python Developer", "jd_text": JD}).status_code == 400  # duplicate

    # jobfit before shortlist -> clear error
    assert client.post(f"/api/jobs/{slug}/jobfit").status_code == 400

    r = client.post(f"/api/jobs/{slug}/requirements", json={})
    assert r.status_code == 200 and r.json()["criteria_written"]

    r = client.post(f"/api/jobs/{slug}/shortlist")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["shortlisted"] == 1 and len(body["shortlisted"]) == 1
    assert body["rejected"][0]["Full Name"] == "Weak" and body["rejected"][0]["Reasons"]

    task = client.post(f"/api/jobs/{slug}/jobfit", json={"workers": 1}).json()
    done = wait(task["id"])
    assert done["status"] == "done", done["error"]
    res = client.get(f"/api/jobs/{slug}/jobfit").json()["results"]
    assert res[0]["name"] == "Full Match" and res[0]["fit_score"] > 0

    for kind in ("shortlist", "jobfit"):
        assert client.get(f"/api/jobs/{slug}/download/{kind}").status_code == 200

    detail = client.get(f"/api/jobs/{slug}").json()
    assert detail["has_jobfit"] and detail["requirements"]["job_title"] == "Python Developer"

    assert client.delete(f"/api/jobs/{slug}").status_code == 200
    assert client.get(f"/api/jobs/{slug}").status_code == 404


def test_extract_task_and_upload(clean_data):
    from reportlab.pdfgen import canvas
    import io
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, line in enumerate(["Rahul Sharma - Python developer, Django, AWS. 6 years experience at Infosys."] * 8):
        c.drawString(30, 800 - 15 * i, line)
    c.save()
    r = client.post("/api/cvs/upload", files=[("files", ("rahul.pdf", buf.getvalue(), "application/pdf")),
                                              ("files", ("notes.txt", b"x", "text/plain"))])
    assert r.json() == {"saved": ["rahul.pdf"], "skipped_unsupported": ["notes.txt"]}

    t = client.post("/api/extract", json={}).json()
    done = wait(t["id"], timeout=120)
    assert done["status"] == "done" and done["result"]["extracted"] == 1 and done["total"] == 1
    assert client.get("/api/status").json()["candidates_in_excel"] == 1
    assert client.get("/api/cvs/rahul.pdf").status_code == 200
    assert client.get("/api/cvs/..%2F..%2Fconftest.py").status_code in (400, 404)   # no path traversal


def test_fetch_without_credentials_reports_error(clean_data, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "imap_password", "")
    t = client.post("/api/fetch", json={}).json()
    done = wait(t["id"])
    assert done["status"] == "error" and "IMAP" in done["error"]
