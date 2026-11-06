# CV Pipeline — Backend

Downloads CV attachments from **Zoho Mail or Gmail**, extracts each CV into **Excel** with an
**LLM (Claude or Gemini — switchable)**, **shortlists** candidates with rules, and **scores the
shortlist against a job description** with the LLM. Everything is available from the command line
and as a **REST API** (for the React frontend).

```
 Mailbox (IMAP)                 data/cvs/                         data/output/candidates.xlsx
 ┌───────────┐  STEP 1 fetch   ┌──────────┐  STEP 2 extract        ┌───────────────────────┐
 │ new mails │ ──────────────► │ PDF/DOCX │ ──────► LLM ─────────► │ 1 row per candidate   │
 └───────────┘  (UID tracked)  │ DOC/IMG  │  (file/text) (Pydantic)│ Name, Email, Skills…  │
                               └──────────┘                        └───────────────────────┘
        data/state/mail_state.json        data/state/processed_files.json
        (last mail UID seen)              (which CVs are already in Excel)
```

```
 data/jobs/<job>/          STEP 3 shortlist               STEP 4 job fit
 ┌──────────────┐  LLM    ┌──────────────────────┐       ┌──────────────────────────────┐
 │ jd.md        │ ──────► │ criteria.json rules  │ ────► │ requirements.json (rubric)   │
 │ (your JD)    │ JD-only │ → shortlist.xlsx     │ only  │ + CV + JD → LLM per candidate│
 └──────────────┘         │ (Shortlisted/Rejected│ pass- │ → jobfit.xlsx (ranked, score,│
                          │  with reasons)       │ ed    │   matched/missing, reasoning)│
                          └──────────────────────┘       └──────────────────────────────┘
```

Status: Steps 1–4 + API ✅ · React frontend ⏳ next.

---

## 1. Project structure

```
backend/
├── main.py                     ← CLI entry point (fetch / extract / run / status / check)
├── .env.example                ← copy to .env and fill in keys
├── requirements.txt
├── app/
│   ├── core/
│   │   ├── config.py           ← reads .env once → `settings` object used everywhere
│   │   ├── models.py           ← Pydantic `Candidate` schema (= what the LLM must return)
│   │   ├── state.py            ← tiny JSON "database" for progress + file hashing
│   │   └── logger.py           ← console + data/state/pipeline.log
│   ├── fetcher/
│   │   └── mail_fetcher.py     ← STEP 1: IMAP download with UID tracking + dedupe
│   ├── extractor/
│   │   ├── converter.py        ← decides what the LLM receives (the PDF/image itself, or DOCX text)
│   │   ├── prompts.py          ← system prompt for extraction (tune here)
│   │   └── cv_extractor.py     ← STEP 2: loop over folder → LLM → Excel
│   ├── llm/
│   │   └── client.py           ← ONE interface for Claude + Gemini (via Instructor)
│   ├── storage/
│   │   └── excel_store.py      ← write/read Excel (upsert by file hash, formatted sheets)
│   ├── jobs/
│   │   └── job_store.py        ← a "job" = one opening: JD, rubric, criteria, outputs in one folder
│   ├── shortlist/
│   │   └── shortlister.py      ← STEP 3: pandas rules → Shortlisted / Rejected (with reasons)
│   ├── jobfit/
│   │   ├── requirements.py     ← STEP 4a: LLM structures the JD (JD text only, verified) + criteria
│   │   └── jobfit.py           ← STEP 4b: LLM grades each shortlisted CV on the rubric → ranking
│   └── api/
│       ├── main.py             ← FastAPI app (python main.py serve)
│       ├── routes_pipeline.py  ← /api/fetch, /api/extract, /api/candidates, /api/tasks …
│       ├── routes_jobs.py      ← /api/jobs/… (JD, requirements, criteria, shortlist, jobfit)
│       ├── tasks.py            ← background task runner with progress (for long jobs)
│       └── schemas.py          ← request bodies
├── data/
│   ├── cvs/                    ← downloaded CVs land here (you can also drop files in manually)
│   ├── output/                 ← candidates.xlsx
│   ├── jobs/<slug>/            ← one folder per job opening (Steps 3–4)
│   └── state/                  ← progress files, logs, text cache
└── tests/                      ← offline tests (fake mailbox + mocked LLM, no keys needed)
```

---

## 2. Setup (one time)

**Requirements:** Python **3.11 or 3.12** (3.10–3.13 work). The install is light (~400 MB) — no ML models.

### 2.1 Create a virtual environment & install

**Windows (PowerShell)**
```powershell
cd backend
python -m venv venv
venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

**macOS / Linux**
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> Optional: install **LibreOffice** if you will receive old Word `.doc` files
> (`.docx`, `.pdf` and images need nothing extra).

### 2.2 Create your `.env`

```bash
cp .env.example .env        # Windows: copy .env.example .env
```
Open `.env` and fill in the values (explained below). **Never commit `.env`** (it's in `.gitignore`).

### 2.3 LLM keys

| Provider | Where to get the key | `.env` keys |
|---|---|---|
| Claude | console.anthropic.com → API Keys | `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` |
| Gemini | aistudio.google.com → Get API key | `GEMINI_API_KEY`, `GEMINI_MODEL` |

`LLM_PROVIDER=claude` or `gemini` chooses the default. You can fill in both and switch per run with
`--provider`. Model names change over time — if you get a "model not found" error, update
`CLAUDE_MODEL` / `GEMINI_MODEL` to a current model name from the provider's docs.

### 2.4 Mailbox (IMAP) — Gmail

1. Turn on **2-Step Verification** for the Google account.
2. Create an **App Password**: Google Account → Security → *App passwords* (or open
   `myaccount.google.com/apppasswords`) → name it "CV pipeline" → copy the 16-character password.
3. In `.env`:
   ```
   IMAP_HOST=imap.gmail.com
   IMAP_USER=you@gmail.com
   IMAP_PASSWORD=abcd efgh ijkl mnop
   IMAP_FOLDER=INBOX
   ```
   Tip: in Gmail, create a filter that puts job applications under a label (e.g. `CVs`) and set
   `IMAP_FOLDER=CVs` — then only those mails are scanned.

### 2.5 Mailbox (IMAP) — Zoho Mail

1. Zoho Mail → Settings → **Mail Accounts** → IMAP → enable **IMAP Access**.
2. If 2FA is on: accounts.zoho.com (or .in) → Security → **App Passwords** → generate one.
3. In `.env` pick the host for your account/data-centre:

   | Account | Host |
   |---|---|
   | Personal, India DC | `imap.zoho.in` |
   | Personal, global | `imap.zoho.com` |
   | Organisation/paid, India DC | `imappro.zoho.in` |
   | Organisation/paid, global | `imappro.zoho.com` |

Switching between Gmail and Zoho is **only a `.env` change** — the code is the same (both are IMAP).

### 2.6 Verify everything

```bash
python main.py check
```
Expected:
```
[OK]   claude: pong
[OK]   gemini: pong            (FAIL is fine if you only use one provider)
[OK]   IMAP login you@gmail.com@imap.gmail.com. Folders: ['INBOX', ...]
```

---

## 3. Running it

**Whole flow in 6 commands:**
```bash
python main.py run                                              # 1+2: fetch mail, extract to Excel
python main.py job create "Python Developer" --jd jd.pdf        # add the opening
python main.py requirements --job python-developer              # LLM structures the JD (JD text only)
#   ...review data/jobs/python-developer/criteria.json...
python main.py shortlist --job python-developer                 # 3: rules → shortlist.xlsx
python main.py jobfit --job python-developer                    # 4: LLM → jobfit.xlsx (ranked)
python main.py serve                                            # or do it all from the API / UI
```

Steps 1–2 in detail:
```bash
python main.py fetch                      # STEP 1: download new CVs from mail → data/cvs/
python main.py extract                    # STEP 2: extract new CVs → data/output/candidates.xlsx
python main.py run                        # both steps
python main.py status                     # progress, counts, failed files
```

Useful options:
```bash
python main.py extract --provider gemini  # use Gemini for this run
python main.py extract --limit 3          # try on 3 CVs first (cost control)
python main.py extract --file "2026-09-10_uid42_John CV.pdf"   # debug one file
python main.py extract --force            # re-extract everything (rows are replaced, not duplicated)
python main.py fetch --reset              # rescan the mailbox from the start (duplicates still skipped)
```

**First run tip:** set `FETCH_SINCE_DATE=2026-09-01` (or similar) in `.env` so the very first fetch
doesn't scan years of email. After the first run the date is no longer used — the UID is.

You can also **drop CV files into `data/cvs/` manually**; `extract` processes them the same way
(the "Received From"/"Mail Subject" columns will just be empty).

---

## 4. How it works (developer notes)

### Step 1 — `mail_fetcher.py`: "don't read the same mails again"
* Every mail in an IMAP folder has a **UID** that only increases. After each batch of 50 mails we
  save the highest UID to `data/state/mail_state.json`:
  ```json
  { "you@gmail.com|imap.gmail.com|INBOX": { "uidvalidity": 1, "last_uid": 48213, ... } }
  ```
  Next run asks the server only for `UID 48214:*`. Old mails are never downloaded again.
* The key includes user+host+folder, so you can switch accounts/folders without mixing progress.
* If the server resets the folder (`UIDVALIDITY` changes), we rescan from 0.
* **Dedupe by content:** every saved file's SHA-256 is stored in
  `data/state/attachments_manifest.json`. The same CV sent twice (even renamed) is saved once.
  The manifest also stores sender, subject and date → copied into Excel.
* Saves `.pdf .docx .doc .png .jpg .jpeg .tif .bmp .webp`. Skips inline images and images
  < 40 KB (signature logos). Mails are opened with `mark_seen=False` → **your unread status is untouched**.
* Files are named `YYYY-MM-DD_uid<UID>_<original name>` so you can trace any CV back to its mail.
* Optional filters in `.env`: `SUBJECT_KEYWORDS=resume,cv,application`, `FROM_FILTER=naukri.com`.

### Step 2 — `cv_extractor.py`: resume → LLM → Excel
For each file in `data/cvs/`:
1. Hash it; if `processed_files.json` says it's already done → skip.
2. **The LLM parses the resume** (`CV_PARSER=llm`, the default):
   * **PDF / image** → the **original file** is sent to Claude/Gemini, which read PDFs and images
     natively (scans, photos, multi-column layouts and tables included). Method: `llm-file`.
   * **DOCX** → LLMs can't open Word files, so `python-docx` pulls out the raw text (paragraphs,
     tables, header) and that text is sent. Method: `docx-text`. `.doc` is converted with LibreOffice first.
   * No local parsing/OCR models are involved — **every field is extracted by the LLM**.
   * Optional `CV_PARSER=docling` converts files to Markdown locally first
     (`pip install -r requirements-docling.txt`); scans then fall back to sending the file.
   * Cost note: a PDF sent as a file costs more tokens than plain text (the LLM also "sees" each
     page). Typical 1–3 page CVs are fine; use `--limit` to gauge cost first.
3. **Instructor** forces the LLM to return the `Candidate` Pydantic model (`app/core/models.py`).
   Invalid output → automatic re-ask (up to 2 retries). Python then cleans up: de-dupes skills,
   rejects impossible ages, computes **age from DOB** (more reliable than LLM maths).
4. Row is upserted into Excel immediately (crash-safe), keyed by **File Hash**.
5. One bad CV never stops the batch — it's logged as failed and **retried on the next run**.

Any text that was sent (DOCX text) is cached in `data/state/text_cache/<hash>.md` for the job-fit step.

### Excel columns
Full Name · Email · Phone · Passport Number · Skills · Experience (Years) · Education · Current/Last Job Title ·
Current/Last Company · Age · Date of Birth · Certifications · Languages · Location · Summary ·
CV File · Received From · Mail Subject · Mail Date · Extraction Method (llm-file/docx-text) · LLM ·
Extracted At · File Hash

> Close `candidates.xlsx` in Excel while extraction runs — Windows locks open files. (The code
> retries for 15 s and tells you if it's locked.)

### Changing what gets extracted
Add a field to `Candidate` in `app/core/models.py` (its `description` is the instruction the LLM
reads), then add a column for it in `candidate_to_row()` + `COLUMNS` in `excel_store.py`.
Run `python main.py extract --force` to refill.

### LLM layer (`app/llm/client.py`)
```python
from app.llm.client import get_llm
obj = get_llm("gemini").extract(MyPydanticModel, system_prompt, text="...")   # or file=Path(...)
```
The JD-fit step will reuse this exact call with a `JobFit` model.

---

## 5. Step 3 — Shortlisting (rules, no LLM)

Shortlisting and job fit work **per job** (one opening). Create one from a JD file or text:

```bash
python main.py job create "Python Developer" --jd path/to/jd.pdf     # .pdf .docx .txt .md
python main.py job create "Data Analyst" --jd-text "We are hiring ..."
python main.py job list
python main.py job show python-developer
```
This creates `data/jobs/python-developer/` with `jd.md` and an empty `criteria.json`.

**Get the rules from the JD (recommended):**
```bash
python main.py requirements --job python-developer
```
**Only the job description you provided is used** — nothing is fetched or looked up, and the LLM
may not add skills, synonyms or "related" technologies. The LLM just structures the JD text, and
returns for every requirement the exact JD phrase it came from (`jd_quote`). Python then checks
every item against the JD and **drops anything that isn't written there**:

```
Requirements taken from the JD:
  [must] (skill) Python                               <- "Strong Python and FastAPI"
  [must] (skill) FastAPI                              <- "Strong Python and FastAPI"
  [must] (experience) 4+ years of backend development <- "4+ years of experience in backend development"
  [nice] (skill) Kubernetes                           <- "exposure to Kubernetes"

Dropped (LLM added these but they are not in the JD):
  - Docker  (quote not found in JD)
  - Django  (skill name not written in JD)
  - location=Mumbai  (not in JD)
```
Checks (`ground_requirements()` in `app/jobfit/requirements.py`): the quote must appear in the JD;
a skill name must appear in the JD as a whole word (no synonyms — `Postgres` ≠ `PostgreSQL`);
other requirements' words must come from the JD; experience years / location / seniority must be
in the JD or they're cleared. Dropped items are saved in `job.json` → `requirements_dropped`.

It writes `requirements.json` (the rubric for Step 4) and, if your `criteria.json` is still empty,
a `criteria.json` built **only from the JD's own values**: `min/max_experience` = the JD's years
(no tolerance added), `required_skills` = the JD's must-have skills. Nice-to-have skills are not
used for filtering (they're scored in Step 4). If that's too strict for your CV pool, edit
`criteria.json` — e.g. move some skills to `any_skills`. Your edits are never overwritten
(`--overwrite-criteria` to force).

Prefer no LLM at all for this? Skip `requirements` and write `criteria.json` (and, for Step 4,
`requirements.json`) by hand.

**Edit `data/jobs/<slug>/criteria.json`** — every rule is optional:
```json
{
  "min_experience": 2,          "max_experience": null,   "keep_unknown_experience": true,
  "min_age": null,              "max_age": 35,            "keep_unknown_age": true,
  "required_skills": ["Python"],                          // ALL must be present
  "any_skills": ["Django", "Flask", "FastAPI"],  "min_any_skills": 1,   // at least N of these
  "locations": ["Pune", "Mumbai", "Remote"],     "keep_unknown_location": true,
  "education_keywords": ["B.Tech", "B.E.", "MCA"],
  "job_title_keywords": ["developer", "engineer"],
  "languages": ["English"],
  "exclude_keywords": ["intern"],
  "max_candidates": 30          // keep only the top N
}
```
Run it:
```bash
python main.py shortlist --job python-developer
```
Output `data/jobs/python-developer/shortlist.xlsx`:
* **Shortlisted** — ranked by number of skills matched, then experience.
* **Rejected** — with a **Reasons** column, e.g. `experience 1y < min 2y; missing required skills: Python`.
* **Criteria** — the rules that were applied (audit trail).

Matching details (`shortlister.py`): case-insensitive; skills are searched in Skills + Certifications
+ Job Title + Summary using **whole-word** matching (`Java` ≠ `JavaScript`, `C` ≠ `C++`), with a small
alias list (`k8s`=`Kubernetes`, `postgres`=`PostgreSQL`, `js`=`JavaScript` …) you can extend in
`SKILL_ALIASES`. Locations match by substring (`Pune` matches `Pune, Maharashtra`).

> Age filters: most CVs don't state age (it stays empty rather than guessed), so `keep_unknown_age`
> defaults to `true`. Check your local employment laws before filtering on age.

---

## 6. Step 4 — Job fit (LLM)

```bash
python main.py jobfit --job python-developer               # scores the shortlist
python main.py jobfit --job python-developer --limit 3     # try 3 first (cost control)
python main.py jobfit --job python-developer --provider gemini --force
python main.py pipeline --job python-developer             # requirements + shortlist + jobfit in one go
```

How a candidate is scored (`jobfit.py`):
1. **Rubric (once per job):** `requirements.json` — e.g. `[must] Python`, `[must] 3+ years backend`,
   `[nice] Kubernetes`. Every candidate is graded on the *same* list → scores are comparable.
   You can edit this file (add/remove items, change must/nice); cached scores reset automatically.
2. **Per candidate:** JD + rubric + extracted profile + the **CV itself** (the original PDF/image
   file, or the DOCX text) go to the LLM — told to use only the JD and the CV, which returns for *each* requirement `met / partial / not_met` + a short **evidence**
   quote, plus a holistic score, strengths, gaps, reasoning and 3 interview questions.
3. **Python computes the score** (transparent, reproducible):
   ```
   Criteria Score = weighted % met   (must = weight 2, nice = 1;  met 1, partial 0.5, not met 0)
   Fit Score      = 0.6 × Criteria Score + 0.4 × LLM holistic score
   Recommendation: ≥80 Strong Fit (and no must-have missing) · ≥65 Good Fit · ≥45 Partial Fit · else Not a Fit
   ```
   Tune `WEIGHT`, `VALUE`, `CRITERIA_SHARE` and `recommendation()` at the top of `jobfit.py`.

Output `data/jobs/<slug>/jobfit.xlsx`:
* **Ranking** — Rank, Name, Fit Score, Recommendation, Criteria/LLM score, Must-haves Missing,
  Matched / Partial / Missing, Strengths, Gaps, Reasoning, Interview Questions, contact details.
* **Criteria Detail** — one row per candidate × requirement with status and evidence.
* **Requirements** — the rubric used.

Results are cached in `jobfit_results.json`; re-running only scores **new** shortlisted candidates.
Candidates are scored in parallel (`JOBFIT_WORKERS=3` in `.env`; lower it if you hit rate limits).
The prompt tells the LLM to ignore age, gender and other protected attributes when judging fit.

---

## 7. API (for the React frontend)

```bash
python main.py serve                 # http://127.0.0.1:8000  — add --reload while developing
```
Open **http://127.0.0.1:8000/docs** — interactive docs where you can try every endpoint.

| Method | Endpoint | What it does |
|---|---|---|
| GET | `/api/health`, `/api/config`, `/api/status` | health, non-secret settings, progress counts |
| POST | `/api/fetch` · `/api/extract` · `/api/run` | start Step 1 / 2 / both → returns a **task** |
| GET | `/api/tasks/{id}` | task status: `queued/running/done/error`, `done/total`, `result` |
| GET | `/api/candidates?search=` · `/api/candidates/download` | candidate rows (JSON) · Excel file |
| GET | `/api/cvs/{filename}` | open the original CV (PDFs display inline) |
| POST | `/api/cvs/upload` | upload CV files manually (multipart `files`) |
| GET/POST | `/api/jobs` | list jobs · create (form: `title`, `jd_text` **or** `jd_file`) |
| GET/DELETE | `/api/jobs/{slug}` | job details (JD, rubric, criteria) · delete |
| PUT | `/api/jobs/{slug}/jd` | replace JD text (resets rubric) |
| POST/PUT | `/api/jobs/{slug}/requirements` | generate rubric with LLM · save edited rubric |
| GET/PUT | `/api/jobs/{slug}/criteria` | read/save shortlisting rules |
| POST/GET | `/api/jobs/{slug}/shortlist` | run rules (instant) · get shortlisted + rejected rows |
| POST | `/api/jobs/{slug}/jobfit` | start job-fit scoring → **task** |
| GET | `/api/jobs/{slug}/jobfit` | ranked results (JSON) |
| GET | `/api/jobs/{slug}/download/shortlist` · `/download/jobfit` | Excel files |

**Long operations** (fetch, extract, jobfit) return immediately with a task, e.g.
`{"id": "a1b2c3", "status": "queued", ...}`; poll `GET /api/tasks/a1b2c3` every 1–2 s for
`done/total` (progress bar) until `status` is `done` (see `result`) or `error`. Conflicting work is
refused with **409** (e.g. two extractions at once). Errors come back as `400` with a readable message.

The API has **no logic of its own** — each endpoint calls the same function as the CLI, so the CLI
and the UI always behave identically. CORS allows `localhost:5173` (Vite) and `:3000`; change with
`CORS_ORIGINS` in `.env`. The API has no login yet — run it on your machine/intranet only.

---

## 8. Tests (no API keys or mailbox needed)

```bash
pytest          # test packages are already in requirements.txt
```
Covers (19 tests): UID tracking across runs, duplicate CVs, UIDVALIDITY reset, subject filter,
logo skipping, extraction → Excel for PDF/DOCX/image, skip-on-rerun, `--force` with the other
provider, a broken file not stopping the batch, skill word-matching (Java ≠ JavaScript), every
shortlist rule + reasons, score maths, JD → rubric → criteria → shortlist → job fit end to end,
caching and cache invalidation, JD grounding (invented skills/years/locations dropped), both
parser modes, and the whole flow over the HTTP API. The LLM SDKs are mocked
(`tests/mock_llm.py`), so tests are free and run in ~15 s.

---

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| `AUTHENTICATIONFAILED` / `Invalid credentials` | Use an **App Password**, not your normal password. Zoho: IMAP access enabled? correct `.in`/`.com` host? |
| `IMAP_FOLDER not in list` | Run `python main.py check` and copy the exact folder name it prints. |
| Too many requirements dropped | `python main.py job show <slug>` lists them with reasons. Usually the JD wording differs (e.g. bullet text split oddly) — add the item to `requirements.json` by hand. |
| Shortlist too strict after `requirements` | `required_skills` = every must-have skill in the JD. Move some to `any_skills` in `criteria.json`. |
| `CV_PARSER=docling` errors | `pip install -r requirements-docling.txt`; first run downloads models (slow once). Or switch back to `CV_PARSER=llm`. |
| Scanned **JD** PDF rejected | JDs need a text layer (for the word-for-word check) — paste the JD as text (`--jd-text`). |
| `model not found` | Update `CLAUDE_MODEL` / `GEMINI_MODEL` in `.env`. |
| A CV keeps failing | `python main.py status` shows the error; `data/state/pipeline.log` has details. |
| `Job 'x' not found` | `python main.py job list` shows the exact slugs. |
| Shortlist is empty | Open the **Rejected** sheet — the Reasons column says which rule removed people; relax `criteria.json`. |
| Rate-limit (429) errors in job fit | Lower `JOBFIT_WORKERS` (e.g. 1) and re-run — already-scored candidates are cached. |
| `409` from the API | That task is already running; poll the task id in the response. |
| Want to start completely fresh | Delete the files in `data/state/`, `data/output/`, `data/jobs/` (and `data/cvs/` if you want). |
