"""
Command-line entry point.

    python main.py fetch                      # STEP 1: download new CVs from mail
    python main.py fetch --reset              # forget saved progress, rescan mailbox (no duplicates saved)
    python main.py extract                    # STEP 2: extract new CVs -> Excel (default LLM from .env)
    python main.py extract --provider gemini  # use Gemini for this run
    python main.py extract --force            # re-extract everything
    python main.py extract --file "x.pdf"     # just one file (debugging)
    python main.py run                        # fetch + extract
    python main.py status                     # show progress / counts
    python main.py check                      # test .env, mail login and LLM keys

    # --- Steps 3-4 work per JOB (one opening = one job folder in data/jobs/<slug>/)
    python main.py job create "Python Developer" --jd jd.pdf      # or --jd-text "..."
    python main.py job list
    python main.py job show python-developer
    python main.py requirements --job python-developer            # LLM: JD -> rubric (+ suggested criteria.json)
    python main.py shortlist   --job python-developer             # STEP 3: rules from criteria.json
    python main.py jobfit      --job python-developer             # STEP 4: LLM scores the shortlist
    python main.py pipeline    --job python-developer             # requirements + shortlist + jobfit

    python main.py serve                      # start the API for the React frontend (http://localhost:8000/docs)
"""
from __future__ import annotations

import argparse
import json
import sys

from app.core.config import settings
from app.core.state import JsonState


def cmd_fetch(a):
    from app.fetcher.mail_fetcher import fetch_cvs
    r = fetch_cvs(reset=a.reset)
    print(json.dumps({k: v for k, v in r.as_dict().items() if k != "saved_files"}, indent=2))


def cmd_extract(a):
    from app.extractor.cv_extractor import run_extraction
    r = run_extraction(provider=a.provider, force=a.force, only_file=a.file, limit=a.limit)
    print(json.dumps(r.as_dict(), indent=2))
    print(f"\nExcel: {settings.output_excel}")


def cmd_run(a):
    cmd_fetch(a)
    cmd_extract(a)


def cmd_status(_):
    from app.extractor.cv_extractor import list_cv_files
    from app.storage.excel_store import load_candidates
    mail = JsonState(settings.state_folder / "mail_state.json").data
    proc = JsonState(settings.state_folder / "processed_files.json").data
    ok = sum(1 for v in proc.values() if v.get("status") == "ok")
    err = {v["file"]: v.get("error") for v in proc.values() if v.get("status") == "error"}
    print("Mail progress:", json.dumps(mail, indent=2))
    print(f"CV files in folder : {len(list_cv_files(settings.cv_folder))}")
    print(f"Extracted OK       : {ok}")
    print(f"Failed             : {len(err)}  {json.dumps(err, indent=2) if err else ''}")
    print(f"Rows in Excel      : {len(load_candidates())}  ({settings.output_excel})")


def cmd_check(a):
    ok = True
    print(f"LLM_PROVIDER = {settings.llm_provider}")
    for prov in (["claude", "gemini"] if not a.provider else [a.provider]):
        try:
            from pydantic import BaseModel

            from app.llm.client import get_llm

            class Ping(BaseModel):
                answer: str

            r = get_llm(prov).extract(Ping, "Reply in the schema.", text="Say 'pong'.")
            print(f"[OK]   {prov}: {r.answer}")
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {prov}: {e}")
            ok = ok and prov != settings.llm_provider
    try:
        from imap_tools import MailBox
        with MailBox(settings.imap_host, port=settings.imap_port).login(settings.imap_user, settings.imap_password) as mb:
            folders = [f.name for f in mb.folder.list()]
            print(f"[OK]   IMAP login {settings.imap_user}@{settings.imap_host}. Folders: {folders[:15]}")
            if settings.imap_folder not in folders:
                print(f"[WARN] IMAP_FOLDER '{settings.imap_folder}' not in the list above")
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] IMAP: {e}")
        ok = False
    sys.exit(0 if ok else 1)


# ------------------------------------------------------------------ Steps 3-4
def cmd_job(a):
    from app.jobs.job_store import Job, create_job, delete_job, list_jobs
    if a.action == "create":
        if not a.title:
            raise ValueError('Give a title:  python main.py job create "Python Developer" --jd jd.pdf')
        job = create_job(a.title, slug=a.slug, jd_text=a.jd_text, jd_file=a.jd)
        print(f"Created job '{job.slug}' in {job.dir}")
        print(f"Next: python main.py requirements --job {job.slug}")
    elif a.action == "list":
        for j in list_jobs():
            print(f"{j['slug']:30} {j['title']:30} jd={j['has_jd']} req={j['has_requirements']} "
                  f"shortlist={j['has_shortlist']} jobfit={j['has_jobfit']}")
    elif a.action == "show":
        job = Job(a.title or a.slug or "").require()
        print(json.dumps(job.summary(), indent=2))
        req = job.load_requirements()
        if req:
            print("\nRequirements:")
            for r in req.requirements:
                print(f"  [{r.importance:4}] ({r.category}) {r.criterion}")
        print("\nCriteria:", job.load_criteria().model_dump_json(indent=2, exclude_defaults=True))
    elif a.action == "delete":
        delete_job(a.title or a.slug)
        print("deleted")


def cmd_requirements(a):
    from app.jobfit.requirements import prepare_job
    r = prepare_job(a.job, a.provider, a.force, a.overwrite_criteria)
    print("Requirements taken from the JD:")
    for x in r["requirements"]["requirements"]:
        print(f"  [{x['importance']:4}] ({x['category']}) {x['criterion']:35}  <- \"{x['jd_quote']}\"")
    if r["dropped_not_in_jd"]:
        print("\nDropped (LLM added these but they are not in the JD):")
        for d in r["dropped_not_in_jd"]:
            print(f"  - {d['criterion']}  ({d['reason']})")
    print("\ncriteria.json", "written from the JD's values" if r["criteria_written"] else "kept (already customised)")
    print("Review/edit data/jobs/<job>/criteria.json, then: python main.py shortlist --job", a.job)


def cmd_shortlist(a):
    from app.shortlist.shortlister import run_shortlist
    print(json.dumps(run_shortlist(a.job), indent=2))


def cmd_jobfit(a):
    from app.jobfit.jobfit import ranked_results, run_jobfit
    r = run_jobfit(a.job, provider=a.provider, force=a.force, workers=a.workers, limit=a.limit)
    print(json.dumps(r, indent=2))
    print("\nTop candidates:")
    for i, c in enumerate(ranked_results(a.job)[:10], 1):
        print(f"  {i:2}. {str(c['name']):28} {c['fit_score']:3}  {c['recommendation']}")


def cmd_pipeline(a):
    from app.jobfit.requirements import prepare_job
    prepare_job(a.job, a.provider)
    cmd_shortlist(a)
    cmd_jobfit(a)


def cmd_serve(a):
    import uvicorn
    print(f"API docs: http://{a.host}:{a.port}/docs")
    uvicorn.run("app.api.main:app", host=a.host, port=a.port, reload=a.reload)


def main():
    p = argparse.ArgumentParser(description="CV pipeline: fetch -> extract -> shortlist -> job fit")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download new CV attachments from mail")
    f.add_argument("--reset", action="store_true", help="ignore saved UID progress and rescan")
    f.set_defaults(func=cmd_fetch)

    def add_extract_args(sp):
        sp.add_argument("--provider", choices=["claude", "gemini"], help="override LLM_PROVIDER")
        sp.add_argument("--force", action="store_true", help="re-extract already processed CVs")
        sp.add_argument("--file", help="only this file name")
        sp.add_argument("--limit", type=int, help="max CVs to extract this run")

    e = sub.add_parser("extract", help="extract CVs in the folder into Excel")
    add_extract_args(e)
    e.set_defaults(func=cmd_extract)

    r = sub.add_parser("run", help="fetch + extract")
    r.add_argument("--reset", action="store_true")
    add_extract_args(r)
    r.set_defaults(func=cmd_run)

    sub.add_parser("status", help="show progress").set_defaults(func=cmd_status)

    c = sub.add_parser("check", help="verify .env, mail login, LLM keys")
    c.add_argument("--provider", choices=["claude", "gemini"])
    c.set_defaults(func=cmd_check)

    j = sub.add_parser("job", help="create/list/show/delete jobs (job descriptions)")
    j.add_argument("action", choices=["create", "list", "show", "delete"])
    j.add_argument("title", nargs="?", help="job title (create) or slug (show/delete)")
    j.add_argument("--slug", help="folder id (default: from title)")
    j.add_argument("--jd", help="JD file: .pdf/.docx/.txt/.md")
    j.add_argument("--jd-text", help="JD as text")
    j.set_defaults(func=cmd_job)

    q = sub.add_parser("requirements", help="LLM structures the JD into requirements (JD text only) + criteria")
    q.add_argument("--job", required=True)
    q.add_argument("--provider", choices=["claude", "gemini"])
    q.add_argument("--force", action="store_true", help="regenerate even if cached")
    q.add_argument("--overwrite-criteria", action="store_true", help="replace criteria.json with suggestion")
    q.set_defaults(func=cmd_requirements)

    s = sub.add_parser("shortlist", help="STEP 3: apply criteria.json to the Excel")
    s.add_argument("--job", required=True)
    s.set_defaults(func=cmd_shortlist)

    def add_fit_args(sp):
        sp.add_argument("--job", required=True)
        sp.add_argument("--provider", choices=["claude", "gemini"])
        sp.add_argument("--force", action="store_true", help="re-score everyone (ignore cache)")
        sp.add_argument("--workers", type=int, help="parallel LLM calls (default JOBFIT_WORKERS)")
        sp.add_argument("--limit", type=int, help="score at most N candidates this run")

    jf = sub.add_parser("jobfit", help="STEP 4: LLM job-fit scoring of the shortlist")
    add_fit_args(jf)
    jf.set_defaults(func=cmd_jobfit)

    pl = sub.add_parser("pipeline", help="requirements + shortlist + jobfit for a job")
    add_fit_args(pl)
    pl.set_defaults(func=cmd_pipeline)

    sv = sub.add_parser("serve", help="start the API server")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--reload", action="store_true", help="auto-restart on code changes (dev)")
    sv.set_defaults(func=cmd_serve)

    a = p.parse_args()
    try:
        a.func(a)
    except (RuntimeError, ValueError) as e:      # config problems -> clean message, no traceback
        print(f"\nERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
