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


def main():
    p = argparse.ArgumentParser(description="CV pipeline - Part 1 (fetch + extract)")
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

    a = p.parse_args()
    try:
        a.func(a)
    except (RuntimeError, ValueError) as e:      # config problems -> clean message, no traceback
        print(f"\nERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
