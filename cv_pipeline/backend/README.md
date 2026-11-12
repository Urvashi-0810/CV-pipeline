# CV Pipeline — Backend (Talent Pool)

The CV Pipeline reads job applications from a company mailbox, pulls out each candidate's details
with AI, and keeps every candidate in one searchable **Talent Pool**.

In plain words, it:

1. **Collects CVs** — downloads CV attachments from Gmail or Zoho Mail (or takes CVs you upload by hand).
2. **Reads each CV with AI** — Claude or Gemini fills in name, email, phone, passport number, skills,
   experience, education and more.
3. **Stores them safely** — the CV file goes to Amazon S3, the details go into a PostgreSQL database,
   and an Excel sheet with every candidate is kept up to date.
4. **Recognises repeat applicants** — a new CV from someone already in the pool is attached to that
   person instead of creating a duplicate.
5. **Helps you pick people for a job** — filters candidates by your rules (shortlist) and scores the
   shortlist against a job description (job fit).

Everything can be used from the **command line**, or through the **Talent Pool screens in the CRM**
(the demand-crm frontend), which talk to this backend's REST API.

---

## Contents

- [Before you start](#before-you-start)
- [Part A — Run the backend on its own](#part-a--run-the-backend-on-its-own)
- [Part B — Run the backend with the frontend (Talent Pool screens)](#part-b--run-the-backend-with-the-frontend-talent-pool-screens)
- [Starting it again next time](#starting-it-again-next-time)
- [Configuration reference (`.env`)](#configuration-reference-env)
- [Command line reference](#command-line-reference)
- [How it works](#how-it-works)
- [REST API](#rest-api)
- [Project structure](#project-structure)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)

---

## Before you start

### What you need to install (one time)

| Program | Why | Where to get it |
|---|---|---|
| **Python 3.12** | Runs the backend | <https://www.python.org/downloads/> — on Windows, tick **"Add python.exe to PATH"** on the first installer screen |
| **Docker Desktop** | Runs the database (and the CRM in Part B) with one command | <https://www.docker.com/products/docker-desktop/> — start it once after installing and wait until it says "Engine running" |
| **Git** *(optional)* | Downloads the code | <https://git-scm.com/downloads> — or download the project as a ZIP from GitHub instead |
| **LibreOffice** *(optional)* | Only needed if you receive old Word `.doc` files | <https://www.libreoffice.org/download/> |

> Already have PostgreSQL installed on your computer? You can use it instead of Docker — see
> [Option 2 in step A3](#a3-start-the-database).

### What you need to have ready

| Item | Needed for | How to get it |
|---|---|---|
| An **AI key** (Claude *or* Gemini) | Reading CVs | Claude: <https://console.anthropic.com> → API Keys. Gemini: <https://aistudio.google.com> → Get API key |
| The **mailbox address** and an **app password** | Downloading CVs from mail | See [Mailbox setup](#mailbox-setup-gmail-or-zoho) below |
| *(Optional)* **AWS S3 bucket** and AWS keys | Storing CV files in the cloud so they can be opened from the CRM | See [AWS S3 setup](#aws-s3-setup) below. Without it CVs are still read, but can't be opened from the CRM |

### How to open a terminal

All the steps below are typed into a **terminal** (a window where you type commands).

- **Windows:** press the Start button, type **PowerShell**, and open **Windows PowerShell**.
- **macOS:** press `Cmd + Space`, type **Terminal**, and press Enter.

Type (or paste) each command exactly as shown and press **Enter**. Lines starting with `#` are
explanations — you don't need to type them.

---

## Part A — Run the backend on its own

Use this when you only want to collect and read CVs (from the command line), or to try the API in
your browser — without the CRM.

### A1. Get the code

If you have Git:

```powershell
cd $HOME\Documents
git clone https://github.com/Urvashi-0810/CV-pipeline.git cv_pipeline-1
```

Otherwise download the ZIP from GitHub (**Code → Download ZIP**) and unzip it into your Documents
folder as `cv_pipeline-1`.

### A2. Install the backend

Go into the backend folder:

```powershell
cd $HOME\Documents\cv_pipeline-1\cv_pipeline\backend
```

Create a private Python space for this project (a "virtual environment") and switch it on:

**Windows**
```powershell
python -m venv venv
venv\Scripts\activate
```

**macOS**
```bash
python3 -m venv venv
source venv/bin/activate
```

You should now see `(venv)` at the start of the line. Install everything the backend needs
(this takes a few minutes the first time):

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> **Windows says "running scripts is disabled on this system"?** Run this once, then try
> `venv\Scripts\activate` again:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### A3. Start the database

The Talent Pool keeps candidates and logins in a PostgreSQL database called `talent`.

> **Only want the command line (fetch and extract)?** The database is optional for that. Skip to
> step A4 and leave `DATABASE_URL` empty — CVs will go to the Excel file only. The API (and the CRM
> screens) do need the database.

**Option 1 — with Docker (recommended).** This uses the same database server as the CRM module, so
nothing extra is installed. Make sure Docker Desktop is running, open a **second terminal**, and run:

```powershell
cd $HOME\Documents\cv_pipeline-1\demand-crm
docker compose up -d db
```

Wait about 10 seconds, then create the `talent` database and its user. Replace
`MyTalentPassword` with a password of your choice — **you will need it in the next step**:

```powershell
docker compose exec db psql -U crm -d crm -c "CREATE ROLE talent LOGIN PASSWORD 'MyTalentPassword';"
docker compose exec db psql -U crm -d crm -c "CREATE DATABASE talent OWNER talent;"
```

Both commands should answer `CREATE ROLE` and `CREATE DATABASE`. You only do this once.

**Option 2 — your own PostgreSQL.** Open pgAdmin or `psql` as the `postgres` user and run:

```sql
CREATE ROLE talent LOGIN PASSWORD 'MyTalentPassword';
CREATE DATABASE talent OWNER talent;
CREATE DATABASE talent_test OWNER talent;   -- only needed to run the tests
```

### A4. Fill in your settings

Go back to the first terminal (the one showing `(venv)`). Make your own copy of the settings file
and open it:

**Windows**
```powershell
copy .env.example .env
notepad .env
```

**macOS**
```bash
cp .env.example .env
open -e .env
```

Change these lines and save the file (everything else can stay as it is for now):

| Setting | What to put |
|---|---|
| `LLM_PROVIDER` | `claude` or `gemini` — whichever key you have |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | Your AI key |
| `CLAUDE_MODEL` / `GEMINI_MODEL` | A current model name from the provider (e.g. `claude-sonnet-5-5`) |
| `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_FOLDER` | Your mailbox (see [Mailbox setup](#mailbox-setup-gmail-or-zoho)) |
| `FETCH_SINCE_DATE` | Only read mails from this date onward on the first run, e.g. `2026-09-01` |
| `DATABASE_URL` | `postgresql+psycopg://talent:MyTalentPassword@localhost:5432/talent` (use your password) |
| `AWS_S3_BUCKET` | Your S3 bucket name, if you use S3 (see [AWS S3 setup](#aws-s3-setup)) |

> Never share or upload the `.env` file — it holds your passwords and keys.

### A5. Prepare the database and create your login

These create the tables, then ask you for a username, your name, a password, and a separate
**Talent Pool admin password** (used to unlock admin pages):

```powershell
alembic upgrade head
python bootstrap_auth.py
```

### A6. Check that everything is connected

```powershell
python main.py check
```

You should see `[OK]` for your AI provider and for the mailbox. (A `FAIL` for the AI provider you
don't use is fine.)

### A7. Use it

**From the command line:**

```powershell
python main.py run       # download new CVs from the mailbox and read them with AI
python main.py status    # how many CVs were read, and any that failed
```

The candidate list is saved as an Excel file in `data\output\candidates.xlsx`.

**Through the API in your browser:**

```powershell
python main.py serve
```

Leave this window open, and visit **<http://127.0.0.1:8100/docs>**. This page lists every API
action and lets you try them. Log in first with `POST /api/auth/login`, using the username and
password from step A5. To stop the server, click the terminal and press `Ctrl + C`.

---

## Part B — Run the backend with the frontend (Talent Pool screens)

This backend has no separate website of its own. Its screens — **Talent Pool**, **Pipeline** and
**Talent Pool admin** — are part of the **CRM website** (the `demand-crm` module). The CRM website
needs its own backend to log you in, so in this mode three things run together:

```
 Your browser ──► CRM website (port 5173) ──► CRM backend (port 8000)    demand letters, CRM login
                        │
                        └───────────────────► CV Pipeline backend (port 8100)   Talent Pool
                                                       │
                              PostgreSQL (port 5432): "crm" database + "talent" database
```

### B1. Set up the CV Pipeline backend

Do **Part A, steps A1 to A6**, using **Option 1 (Docker)** for the database.

### B2. Start the CRM (website + CRM backend)

In the **second terminal** (in the `demand-crm` folder), follow
**[demand-crm/README.md → Quick start](../../demand-crm/README.md#quick-start-with-docker-recommended)**.
In short:

```powershell
cd $HOME\Documents\cv_pipeline-1\demand-crm
copy .env.example .env
docker compose run --rm api python -m scripts.generate_secret_key
#   copy the long line it prints into demand-crm\.env after APP_SECRET_KEY=
docker compose up --build -d
docker compose run --rm api python -m scripts.bootstrap
#   choose your CRM username, password and CRM admin password
```

### B3. Start the CV Pipeline backend

In the **first terminal** (`cv_pipeline\backend`, showing `(venv)`):

```powershell
python main.py serve
```

Leave this window open.

### B4. Open the Talent Pool

1. Open **<http://localhost:5173>** in Chrome or Edge.
2. Log in with your **CRM** username and password (from step B2).
3. Click **Talent Pool** in the left menu.
4. Log in again with your **Talent Pool** username and password (from step A5). The two logins are
   kept separate on purpose.

You can now search candidates, open CVs, upload a CV by hand, and run the pipeline from the
**Pipeline** page.

### Using it from other computers in the office

1. Find this computer's address: on Windows run `ipconfig` and note the **IPv4 Address**
   (for example `192.168.1.20`).
2. Start the backend so other computers can reach it:
   ```powershell
   python main.py serve --host 0.0.0.0
   ```
3. In `cv_pipeline\backend\.env`, add that address to `CORS_ORIGINS`, then restart the backend:
   ```
   CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://192.168.1.20:5173
   ```
4. Other people open `http://192.168.1.20:5173`. If they can't connect, allow ports **5173** and
   **8100** through Windows Firewall.

---

## Starting it again next time

Setup is a one-time job. After restarting your computer:

1. Start **Docker Desktop** and wait until it is running.
2. Start the database (and, for Part B, the CRM):
   ```powershell
   cd $HOME\Documents\cv_pipeline-1\demand-crm
   docker compose up -d db          # Part A
   docker compose up -d             # Part B (database + CRM)
   ```
3. Start the CV Pipeline backend:
   ```powershell
   cd $HOME\Documents\cv_pipeline-1\cv_pipeline\backend
   venv\Scripts\activate            # macOS: source venv/bin/activate
   python main.py serve
   ```
4. For Part B, open <http://localhost:5173>.

To stop everything: press `Ctrl + C` in the backend window, then run `docker compose stop` in the
`demand-crm` folder.

---

## Configuration reference (`.env`)

All settings live in `cv_pipeline/backend/.env` (copied from `.env.example`). Once the Talent Pool is
running, an admin can also change most of them from **Talent Pool admin → Settings** in the CRM.

| Setting | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `claude` | Default AI: `claude` or `gemini` (override per run with `--provider`) |
| `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` | — | Claude key and model |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | — | Gemini key and model |
| `CV_PARSER` | `llm` | `llm` = the AI reads the file itself. `docling` = convert locally first (needs `requirements-docling.txt`) |
| `IMAP_HOST`, `IMAP_PORT`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_FOLDER` | Gmail / 993 | Mailbox to read |
| `FETCH_SINCE_DATE` | — | First run only: ignore older mails |
| `SUBJECT_KEYWORDS`, `FROM_FILTER` | — | Optional filters on mail subject / sender (comma separated) |
| `MAX_ATTACHMENT_MB` | `15` | Larger attachments are skipped |
| `CV_FOLDER`, `OUTPUT_EXCEL`, `STATE_FOLDER`, `JOBS_FOLDER` | `data/...` | Where files are kept |
| `AWS_S3_BUCKET`, `AWS_REGION`, `AWS_S3_PREFIX` | — / `ap-south-1` / `cvs` | CV file storage |
| `DATABASE_URL` | — | Talent database. Empty = command line only, Excel only |
| `TEST_DATABASE_URL` | — | Database the tests wipe; its name must end in `_test` |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Every address the CRM website is opened from |
| `COOKIE_SECURE` | `false` | Set `true` only when served over HTTPS |
| `SESSION_TTL_HOURS`, `ADMIN_SESSION_MINUTES`, `ADMIN_MAX_FAILED_ATTEMPTS`, `ADMIN_LOCKOUT_MINUTES`, `PASSWORD_MIN_LENGTH` | 12, 30, 5, 15, 6 | Login rules |
| `JOBFIT_WORKERS` | `3` | Candidates scored in parallel in job fit; lower it on rate-limit errors |

The database, folders, `COOKIE_SECURE` and `CORS_ORIGINS` are read at startup — restart the backend
after changing them.

### Mailbox setup (Gmail or Zoho)

The pipeline uses IMAP, so switching between Gmail and Zoho is only a change in `.env`.

**Gmail**
1. Turn on **2-Step Verification** for the Google account.
2. Create an **App Password** at <https://myaccount.google.com/apppasswords> (name it "CV pipeline")
   and copy the 16-character password.
3. In `.env`: `IMAP_HOST=imap.gmail.com`, `IMAP_USER=you@gmail.com`, `IMAP_PASSWORD=<app password>`.

Tip: create a Gmail filter that puts applications under a label such as `CVs`, and set
`IMAP_FOLDER=CVs` so only those mails are read.

**Zoho Mail**
1. Zoho Mail → Settings → **Mail Accounts** → IMAP → enable **IMAP Access**.
2. If two-factor login is on, create an **App Password** in Zoho Accounts → Security.
3. Set `IMAP_HOST` for your account:

   | Account | Host |
   |---|---|
   | Personal, India | `imap.zoho.in` |
   | Personal, global | `imap.zoho.com` |
   | Organisation (paid), India | `imappro.zoho.in` |
   | Organisation (paid), global | `imappro.zoho.com` |

Mails are read without being marked as read, so your unread status is not changed.

### AWS S3 setup

CVs are stored in S3 and opened through short-lived links. Without S3 the pipeline still reads CVs,
but marks each one **Failed** under *Uploaded to S3*, and it can't be opened from the CRM.

1. **Settings in `.env`:** `AWS_S3_BUCKET=<bucket>`, `AWS_REGION=ap-south-1`, `AWS_S3_PREFIX=cvs`.
   `AWS_S3_PREFIX` must match `S3_PREFIX` in `upload_existing_cvs_to_s3.py` (`cvs`). Keys look like
   `cvs/<mail-account>/<Month-Year>/<file>.pdf`; manual uploads use `cvs/manual-upload/<Month-Year>/<file>.pdf`.
2. **An IAM user with only this policy** (replace `BUCKET`):
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       { "Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": "arn:aws:s3:::BUCKET" },
       { "Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
         "Resource": "arn:aws:s3:::BUCKET/cvs/*" }
     ]
   }
   ```
   | Permission | Used for |
   |---|---|
   | `s3:PutObject` | Uploading CVs |
   | `s3:GetObject` | Links that open and download CVs |
   | `s3:ListBucket` | Finding files already uploaded |
   | `s3:DeleteObject` | Deleting a candidate |
3. **Credentials on the machine.** Keys don't go in `.env`; boto3's normal lookup is used. Run
   `aws configure` (AWS CLI), or set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` as Windows user
   environment variables. (The older `.env` names `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` and
   `S3_BUCKET_NAME` are still understood.)

### Loading existing CVs (one time, optional)

If the client already has CVs from before the Talent Pool existed, load them in this order.
Close `candidates.xlsx` in Excel first.

```powershell
python upload_existing_cvs_to_s3.py            # 1. upload old CV files to S3 (set DRY_RUN=True in the file first to preview)
python backfill_mail_details.py --dry-run      # 2. preview filling in missing mail details (no AI calls)
python backfill_mail_details.py
python backfill_talent.py --dry-run            # 3. preview loading every CV into the talent database
python backfill_talent.py
```

- `upload_existing_cvs_to_s3.py` skips files already in S3 at the same size, keeps going past single
  failures, writes failures to `s3_upload_failures.txt`, and stops after 25 failures in a row.
- `backfill_mail_details.py` fills blank *Received From*, *Mail Subject* and *Mail Date* cells and keeps
  the old file as `candidates.before_mail_backfill.xlsx`.
- `backfill_talent.py` matches CVs oldest first so each person's CVs end up under one candidate, lists
  CVs with no file in S3 in `talent_backfill_missing.csv`, and keeps the old Excel as
  `candidates.before_talent_backfill.xlsx`. It is safe to run again.

---

## Command line reference

Run these from `cv_pipeline/backend` with the virtual environment switched on.

| Command | What it does |
|---|---|
| `python main.py check` | Tests the AI keys and the mailbox login |
| `python main.py fetch` | Step 1 — download new CVs from the mailbox into `data/cvs/` |
| `python main.py extract` | Step 2 — read new CVs with AI into the database and Excel |
| `python main.py run` | Steps 1 and 2 together |
| `python main.py status` | Progress, counts, and CVs that failed |
| `python main.py serve` | Start the API on port 8100 (`--host 0.0.0.0` for the office network, `--reload` while developing) |
| `python main.py job create "Title" --jd jd.pdf` | Add a job opening (`--jd-text "..."` for pasted text) |
| `python main.py job list` · `job show <slug>` | List jobs · show one |
| `python main.py requirements --job <slug>` | AI turns the JD into a checklist and suggested rules |
| `python main.py shortlist --job <slug>` | Step 3 — apply the rules → `shortlist.xlsx` |
| `python main.py jobfit --job <slug>` | Step 4 — AI scores the shortlist → `jobfit.xlsx` |
| `python main.py pipeline --job <slug>` | Requirements + shortlist + job fit in one go |
| `python bootstrap_auth.py` | Create the first Talent Pool user and admin password |
| `python bootstrap_auth.py --reset-admin-password` | Replace a forgotten Talent Pool admin password |

Useful options:

```bash
python main.py extract --provider gemini     # use Gemini for this run
python main.py extract --limit 3             # try 3 CVs first (cost control)
python main.py extract --file "name.pdf"     # one file only (debugging)
python main.py extract --force               # re-read everything (rows replaced, not duplicated)
python main.py fetch --reset                 # rescan the mailbox from the start (duplicates still skipped)
python main.py jobfit --job <slug> --limit 3 # score 3 candidates first
```

You can also drop CV files into `data/cvs/` by hand; `extract` processes them the same way.

---

## How it works

### The flow for one CV

```
 Mailbox (IMAP) / manual upload
        │  STEP 1  fetch: new mails only (UID tracked), attachments merged into one PDF
        ▼
   data/cvs/
        │  STEP 2  extract: the AI reads the file → validated Candidate record
        ▼
   rename to Trade_Passport_Name.pdf ──► upload to S3 (cvs/<account>/<Month-Year>/...)
        │
        ▼
   match to an existing candidate or create a new one ──► PostgreSQL "talent" database
        │
        ▼
   rebuild data/output/candidates.xlsx from the database
```

### Step 1 — Fetch (`app/fetcher/mail_fetcher.py`)

- Every mail in an IMAP folder has a **UID** that only increases. After each batch of 50 mails the
  highest UID is saved to `data/state/mail_state.json`, so old mails are never downloaded again.
  Progress is kept per user + host + folder; if the server resets the folder (`UIDVALIDITY` changes),
  the folder is rescanned.
- **Duplicates are detected by content:** each saved file's SHA-256 is stored in
  `data/state/attachments_manifest.json`, so the same CV sent twice (even renamed) is saved once. The
  manifest also keeps sender, subject and date.
- Saves `.pdf .docx .doc .png .jpg .jpeg .tif .bmp .webp`; skips inline images and images under 40 KB
  (signature logos). A mail's attachments are merged into one PDF (`pdf_merger.py`).
- Each batch is extracted as it is fetched, so a long fetch shows CVs appearing as it goes.

### Step 2 — Extract (`app/extractor/cv_extractor.py`)

1. Each file is hashed; files already done (`processed_files.json`) are skipped.
2. **The AI reads the CV.** PDFs and images are sent to Claude/Gemini as they are (scans and
   multi-column layouts included). Word `.docx` text is pulled out with `python-docx` and sent as text;
   `.doc` is converted with LibreOffice first.
3. **Instructor** forces the reply into the `Candidate` model (`app/core/models.py`); invalid replies
   are retried up to twice. Python then tidies the result: de-duplicates skills, rejects impossible
   ages, and computes age from date of birth.
4. The file is renamed, uploaded to S3, stored in Postgres and matched to a candidate before it is
   marked done. One bad CV never stops the batch — it is logged and retried on the next run.

**Matching** (`app/talent/matching.py`) decides whether a new CV belongs to an existing person
(passport number first, then weaker rules). Uncertain matches create a **review item** that a user
resolves in the CRM (merge / different people / keep edit / accept). The full rules are in
*Talent Pool Module — Phase 1 Requirements.md* at the project root.

**Excel columns:** Full Name · Email · Phone · Passport Number · Skills · Experience (Years) ·
Education · Current/Last Job Title · Current/Last Company · Age · Date of Birth · Certifications ·
Languages · Location · Summary · CV File · Received From · Mail Subject · Mail Date · Extraction
Method · LLM · Extracted At · File Hash. The Excel is generated from Postgres — don't edit it by hand.

To extract a new field: add it to `Candidate` in `app/core/models.py` (its `description` is the
instruction the AI reads), add the column in `excel_store.py`, then run `python main.py extract --force`.

### Step 3 — Shortlist (`app/shortlist/shortlister.py`, no AI)

Each job opening lives in `data/jobs/<slug>/` with `jd.md`, `criteria.json`, `requirements.json` and
its outputs. `python main.py requirements --job <slug>` asks the AI to structure the JD — **using
only the JD text**. Every requirement must quote the JD; anything the AI adds that isn't written in
the JD is dropped and listed (`ground_requirements()` in `app/jobfit/requirements.py`).

`criteria.json` holds the rules (all optional):

```json
{
  "min_experience": 2, "max_experience": null, "keep_unknown_experience": true,
  "min_age": null, "max_age": 35, "keep_unknown_age": true,
  "required_skills": ["Python"],
  "any_skills": ["Django", "Flask", "FastAPI"], "min_any_skills": 1,
  "locations": ["Pune", "Mumbai", "Remote"], "keep_unknown_location": true,
  "education_keywords": ["B.Tech", "MCA"],
  "job_title_keywords": ["developer", "engineer"],
  "languages": ["English"],
  "exclude_keywords": ["intern"],
  "max_candidates": 30
}
```

Skills are matched case-insensitively as whole words (`Java` ≠ `JavaScript`) with a small alias list
(`SKILL_ALIASES`). `shortlist.xlsx` has **Shortlisted** (ranked), **Rejected** (with reasons) and
**Criteria** sheets. Check local employment law before filtering on age.

### Step 4 — Job fit (`app/jobfit/jobfit.py`)

Every shortlisted candidate is graded by the AI on the same checklist (`requirements.json`): each
item is *met / partial / not met* with an evidence quote, plus strengths, gaps, reasoning and three
interview questions. Python computes the score:

```
Criteria Score = weighted % met   (must = 2, nice = 1; met 1, partial 0.5, not met 0)
Fit Score      = 0.6 × Criteria Score + 0.4 × AI holistic score
Recommendation : ≥80 Strong Fit (no must-have missing) · ≥65 Good Fit · ≥45 Partial Fit · else Not a Fit
```

Results are cached in `jobfit_results.json`, so re-running only scores new candidates. The AI is told
to ignore age, gender and other protected attributes.

### Logins and security

- The Talent Pool has **its own login**, separate from the CRM: a session cookie (`talent_session`),
  a CSRF token on every change, and a shared **Talent Pool admin password** that unlocks admin pages
  for 30 minutes. Five wrong admin passwords lock admin for that user for 15 minutes.
- Every API route needs a login except `/api/health` and `/api/auth/login`. The command line needs no
  login.
- Secrets edited from the Settings page are never sent back to the browser — only whether they are set.

### Database changes (developers)

Tables are defined in `app/db/models.py` and created with Alembic migrations in `migrations/versions/`.
After changing a model:

```bash
alembic revision --autogenerate -m "what changed"   # review the generated file
alembic upgrade head
```

---

## REST API

Start with `python main.py serve`; live documentation is at <http://127.0.0.1:8100/docs>.
Errors use `application/problem+json`.

| Method | Endpoint | What it does |
|---|---|---|
| POST | `/api/auth/login` · `/api/auth/logout` | Log in (cookie) · log out |
| GET · PUT | `/api/me` · `/api/me/password` | Current user and CSRF token · change own password |
| GET/POST/DELETE | `/api/admin/session` | Admin unlock status · unlock · lock |
| … | `/api/admin/password` · `/api/admin/users` · `/api/admin/users/{id}` · `.../password` | Admin: admin password, users, deactivate, reset password |
| GET | `/api/talent/candidates` | Talent Pool list with search, filters, sorting and paging |
| GET | `/api/talent/filter-options` · `/api/talent/suggest?field=&q=` | Filter values · type-ahead suggestions |
| GET · PATCH · DELETE | `/api/talent/candidates/{id}` | Detail (`ETag`) · edit (`If-Match`) · delete (admin) |
| GET | `/api/talent/candidates/{id}/history` · `/api/talent/cvs/{cv_id}/url` | Edit history · short-lived S3 link |
| POST | `/api/talent/flags/{id}/merge` · `different-people` · `keep-edit` · `accept` | Resolve a review item |
| POST | `/api/talent/s3/retry` | Re-upload CVs whose S3 upload failed → task |
| GET | `/api/pipeline/runs?limit=` | Recent runs and their summaries |
| POST | `/api/fetch` · `/api/extract` · `/api/run` | Start Step 1 / 2 / both → task |
| GET | `/api/tasks/{id}` | Task status and progress |
| POST | `/api/tasks/{id}/pause` · `/resume` · `/cancel` | Control a running fetch / extract / run / S3 retry |
| POST | `/api/cvs/upload` | Manual upload of one person's CV (files merged into one PDF) → task |
| GET | `/api/cvs/{file_hash}/url` | Short-lived S3 link (`?download=true` to download) |
| GET | `/api/candidates` · `/api/candidates/download` | Candidate rows · Excel file |
| GET | `/api/health` · `/api/config` · `/api/status` | Health · non-secret settings · counts |
| GET/PUT · POST | `/api/admin/settings` · `/api/admin/settings/test/{target}` | Admin: Settings page (edit `.env`) · connection tests |
| GET/POST · GET/DELETE | `/api/jobs` · `/api/jobs/{slug}` | Job openings |
| PUT · POST/PUT · GET/PUT | `/api/jobs/{slug}/jd` · `/requirements` · `/criteria` | JD, checklist, rules |
| POST/GET | `/api/jobs/{slug}/shortlist` · `/api/jobs/{slug}/jobfit` | Run / read shortlist and job fit |
| GET | `/api/jobs/{slug}/download/shortlist` · `/download/jobfit` | Excel files |

**Long operations** (fetch, extract, run, upload, S3 retry, job fit) return a task straight away;
poll `GET /api/tasks/{id}` until `status` is `done`, `error` or `cancelled`. Pause and cancel act only
at safe points (after the current CV, or between batches of 50 mails), so nothing is left half done.
Starting a second conflicting run returns **409**; a manual upload instead waits for the current run.

The API has no logic of its own — each endpoint calls the same function as the command line.

---

## Project structure

```
cv_pipeline/backend/
├── main.py                     Command line entry point
├── .env.example                Settings template (copy to .env)
├── requirements.txt            Python packages
├── alembic.ini · migrations/   Database migrations
├── bootstrap_auth.py           First Talent Pool user + admin password
├── upload_existing_cvs_to_s3.py, backfill_mail_details.py, backfill_talent.py   One-time loaders
├── app/
│   ├── core/        config.py (reads .env) · models.py (Candidate) · state.py · logger.py · control.py
│   ├── fetcher/     mail_fetcher.py · manual_upload.py · pdf_merger.py · resume_naming.py
│   ├── extractor/   converter.py · prompts.py · cv_extractor.py
│   ├── llm/         client.py — one interface for Claude and Gemini
│   ├── storage/     excel_store.py · s3_store.py
│   ├── db/          models.py · session.py (PostgreSQL)
│   ├── talent/      ingest.py · matching.py · service.py · excel_export.py · runs.py · settings_admin.py
│   ├── auth/        login, sessions, CSRF, admin unlock
│   ├── jobs/ · shortlist/ · jobfit/     Steps 3 and 4
│   └── api/         main.py (FastAPI app) · routes_*.py · tasks.py · problems.py
├── data/
│   ├── cvs/         Downloaded CVs
│   ├── output/      candidates.xlsx
│   ├── jobs/<slug>/ One folder per job opening
│   └── state/       Progress files, pipeline.log
└── tests/           Offline tests (fake mailbox, mocked AI and S3)
```

---

## Tests

```bash
pytest
```

No AI keys, mailbox or AWS account are needed: the AI SDKs and S3 are mocked (`tests/mock_llm.py`).
The database tests need a real Postgres: set `TEST_DATABASE_URL` to a database whose name ends in
`_test` (they wipe it). Without it, those tests are skipped.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` is not recognised | Reinstall Python and tick **"Add python.exe to PATH"**, then open a new terminal. On Windows, `py` also works in place of `python`. |
| "running scripts is disabled on this system" | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again. |
| `(venv)` is missing at the start of the line | You opened a new terminal. Go to `cv_pipeline\backend` and run `venv\Scripts\activate` again. |
| `docker` is not recognised / cannot connect | Start Docker Desktop and wait until it says the engine is running. |
| `connection refused` on port 5432 | The database isn't running: `docker compose up -d db` in the `demand-crm` folder. |
| `password authentication failed for user "talent"` | The password in `DATABASE_URL` doesn't match the one used in `CREATE ROLE`. |
| `AUTHENTICATIONFAILED` from the mailbox | Use an **app password**, not your normal password. Zoho: IMAP enabled, correct `.in`/`.com` host? |
| `IMAP_FOLDER not in list` | Run `python main.py check` and copy the exact folder name it prints. |
| `model not found` | Update `CLAUDE_MODEL` / `GEMINI_MODEL` to a current model name. |
| CRM shows "Check that it is running at http://...:8100" | Start this backend (`python main.py serve`). From another PC, use `--host 0.0.0.0` and add the address to `CORS_ORIGINS`. |
| Logged in to the Talent Pool but it logs you out straight away | `COOKIE_SECURE` must be `false` unless the site uses HTTPS. |
| Excel file "is locked" | Close `candidates.xlsx` in Excel while the pipeline runs. |
| A CV keeps failing | `python main.py status` shows the error; `data/state/pipeline.log` has details. |
| Rate-limit (429) errors in job fit | Lower `JOBFIT_WORKERS` to `1` and run again — finished candidates are cached. |
| `409` from the API | That kind of run is already going; wait for it or cancel it. |
| Shortlist is empty | Open the **Rejected** sheet: the Reasons column shows which rule removed people. Relax `criteria.json`. |
