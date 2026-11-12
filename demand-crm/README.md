# Demand Letter CRM

A web application for a recruitment agency's team to **receive client job inquiries, write demand
letters with AI, get them approved, and track every order** — all in one place.

In plain words:

- A team member uploads a client's inquiry and job descriptions (Word, Excel, PDF, images…) into a chat.
- The AI asks about anything missing, then **writes the demand letter** on the company letterhead.
  It never invents details — anything unknown is clearly marked *to be filled*.
- The letter can be refined by chatting, or edited in Word and uploaded back. Every version is kept.
- An admin **approves** the letter; it then gets today's date, loses its DRAFT mark, and moves to the
  **demand list**.
- Approved letters can be exported for **advertisements** (Canva / Figma), and a **dashboard** shows
  orders, vacancies, clients and team output.

The same website also hosts the **Talent Pool** screens of the CV Pipeline module — see
[Talent Pool screens](#talent-pool-screens-optional).

---

## Contents

- [Features](#features)
- [Before you start](#before-you-start)
- [Quick start (with Docker, recommended)](#quick-start-with-docker-recommended)
- [First-time setup inside the app](#first-time-setup-inside-the-app)
- [Starting and stopping it later](#starting-and-stopping-it-later)
- [Running without Docker (developers)](#running-without-docker-developers)
- [Talent Pool screens (optional)](#talent-pool-screens-optional)
- [Configuration reference (`.env`)](#configuration-reference-env)
- [Tests](#tests)
- [Project structure](#project-structure)
- [API](#api)
- [Design decisions](#design-decisions)
- [Troubleshooting](#troubleshooting)

---

## Features

### Accounts and admin
- Login with server-side sessions; **My Profile**; change your own password. Name changes need admin approval.
- **Admin** pages sit behind a separate admin password: users and roles, password resets, name-change
  approvals, letter approvals, AI settings, usage and budget.

### Demand register
- Demand IDs are given out automatically (AA00001 … AA99999, AB00001 …), lowest free number first.
  Each demand gets its own folder for files.
- **Yet to be approved** drafts with *Continue chat* and *Delete*. Approved letters can't be deleted.
- **Demand list** with search, filters, sorting, pages and Excel export. Clicking a row shows a
  read-only preview; the download icon gets the newest `.docx`.

### AI letter chat
- **+ Add Demand Letter** opens a chat (one chat = one letter). Attach files and mark each as
  **Inquiry**, **JD** or **Reference letter** (format only). Supported: `.docx`, `.xlsx`, `.csv`,
  `.txt`, `.pdf`, `.png`, `.jpg`, `.webp`, `.gif`.
- The AI **asks about missing details** before writing and **never invents information**; unknown
  details appear as yellow *[Field — to be filled]* markers.
- Generation runs in a background worker — you can close the page and come back.
- **Live preview** next to the chat; download as `.docx` with letterhead, Demand Order ID, date and a
  red **DRAFT** mark.
- Ask for changes in the chat (each reply is a new version), or ask questions about the letter
  without changing it. Unrelated requests get a polite fixed reply.
- The AI fills **country, employer, vacancy and positions** on the list; fields a person edited by hand
  are never overwritten. **Positions** are always read from the letter's own positions table.
- Relevant pictures from JDs can be placed in the letter.
- Limits: 10 MB per file, 20 MB of files per letter, 20 images per letter, images up to 8000 px,
  PDFs up to 100 pages, Excel sheets up to 2,000 rows.

### Approval, editing and versions
- **Send for approval** → an admin finds it under **Admin → Letter approvals** and clicks **Approve**
  or **Send back** (with an optional reason).
- **Approve** writes today's date (India time) into the letter, removes the DRAFT mark and moves it to
  the demand list with *Created by* and *Approved by*. A warning lists any fields still to be filled.
- **Edit in Word, then upload** — the uploaded file is kept exactly as saved and becomes the newest
  version. A file showing another demand's ID is refused.
- **Versions** — view, download or **restore** any earlier version. Nothing is ever overwritten.

### Advertisements
- **Create Advertisement** lists approved letters with the same search, filters and export as the
  demand list. Set **Ad assigned to** and **Ad status** (Not started / In progress / Done); every change
  records who and when.
- **Export** for **Canva** (`.docx` or PDF) or **Figma** (PNG or JPG, one image per page, zipped when
  there are several). PDF and images need LibreOffice (already included in the Docker setup).

### Dashboard
- Counts **approved letters only** (1 letter = 1 order) for this month, last month, this year or a
  custom range, in India time, each compared with the previous period.
- Orders, vacancies, clients, new clients · orders over time · current pipeline · top clients ·
  orders by country · repeat clients · most-demanded positions · advertisement status · team output.
  Everything exports to Excel.

### AI settings and budget (admin)
- Choose **Claude** or **Gemini** and the model; API keys are stored encrypted (only the last 4
  characters are ever shown).
- Edit the **base prompt** (every save is a version that can be restored), upload the **letterhead**
  header and footer, keep a library of **reference letters** used for format.
- **Usage & budget**: monthly AI spend limit (can be switched off), spend this month, cost per day and
  per model. When the limit is reached, new chat messages are paused until the month ends.

---

## Before you start

### What you need to install (one time)

| Program | Why | Where to get it |
|---|---|---|
| **Docker Desktop** | Runs the whole CRM (database, backend, worker, website) with one command | <https://www.docker.com/products/docker-desktop/> |
| **Git** *(optional)* | Downloads the code | <https://git-scm.com/downloads> — or download the project as a ZIP from GitHub |

After installing Docker Desktop, **open it once** and wait until it shows that the engine is running.
On Windows it may ask you to restart the computer or to install "WSL" — accept both.

You will also need an **AI key** from Claude (<https://console.anthropic.com>) or Gemini
(<https://aistudio.google.com>). You enter it inside the app, not in a file.

### How to open a terminal

- **Windows:** press Start, type **PowerShell**, and open **Windows PowerShell**.
- **macOS:** press `Cmd + Space`, type **Terminal**, and press Enter.

Type (or paste) each command exactly as shown and press **Enter**. Lines starting with `#` are
explanations — you don't need to type them.

---

## Quick start (with Docker, recommended)

### Step 1 — Get the code

If you have Git:

```powershell
cd $HOME\Documents
git clone https://github.com/Urvashi-0810/CV-pipeline.git cv_pipeline-1
```

Otherwise download the ZIP from GitHub (**Code → Download ZIP**) and unzip it into your Documents
folder as `cv_pipeline-1`.

### Step 2 — Go into the CRM folder

```powershell
cd $HOME\Documents\cv_pipeline-1\demand-crm
```

### Step 3 — Create your settings file

**Windows**
```powershell
copy .env.example .env
```

**macOS**
```bash
cp .env.example .env
```

### Step 4 — Create a secret key

The CRM uses a secret key to lock away the AI keys you store in it. Generate one:

```powershell
docker compose run --rm api python -m scripts.generate_secret_key
```

The first time, this downloads and builds everything, so it can take **5–10 minutes**. At the end it
prints one long line of letters and numbers. Open the settings file:

```powershell
notepad .env          # macOS: open -e .env
```

Find the line `APP_SECRET_KEY=` and paste the long line right after the `=`, for example:

```
APP_SECRET_KEY=q3Xv8...the long line you copied...=
```

Save and close Notepad. **Keep a safe copy of this key** — if it is lost, the AI keys must be entered again.

### Step 5 — Start the CRM

```powershell
docker compose up --build -d
```

This starts four parts in the background: the **database**, the **backend**, the **worker** (which
runs the AI) and the **website**. It takes a minute or two.

### Step 6 — Create your login (first time only)

```powershell
docker compose run --rm api python -m scripts.bootstrap
```

It asks for a username, your name, a role (optional), a password, and a separate **admin password**
(used to open the Admin pages). Write these down.

### Step 7 — Open the CRM

Open **<http://localhost:5173>** in Chrome or Edge and log in with the username and password from step 6.

---

## First-time setup inside the app

Before the AI can write letters, go to **Admin** (enter the admin password), then **AI settings**:

1. **Add a provider** — choose Claude or Gemini, pick the model, paste your API key, and click **Make active**.
2. **Upload the letterhead** — the header and footer images (PNG/JPG, without the "Demand Order ID" and "Date" text).
3. **Set the base prompt** — replace the starting prompt with your company's instructions.
4. *(Optional)* Add **reference letters** — sample letters the AI copies the format from.
5. *(Optional)* Set a monthly AI budget under **Usage & budget**.

Add your team under **Admin → Users**. You're ready: click **+ Add Demand Letter** to start.

---

## Starting and stopping it later

Setup is a one-time job. After that:

| To… | Do this |
|---|---|
| **Start** | Open Docker Desktop, then in a terminal: `cd $HOME\Documents\cv_pipeline-1\demand-crm` and `docker compose up -d`. Open <http://localhost:5173>. |
| **Stop** | In the same folder: `docker compose stop` |
| **Update after new code** | `docker compose up --build -d` — the database is upgraded automatically |
| **See what's running** | `docker compose ps` |
| **See error messages** | `docker compose logs api --tail 100` (or `worker`, `web`, `db`) |
| **Reset a forgotten admin password** | `docker compose run --rm api python -m scripts.bootstrap --reset-admin-password` |

Your data stays safe when you stop or restart. It is kept in the Docker database volume and in the
`storage/` folder. Don't run `docker compose down -v` — the `-v` deletes the database.

### Using it from other computers in the office

Find this computer's address (on Windows run `ipconfig` and note the **IPv4 Address**, e.g.
`192.168.1.20`). Others open `http://192.168.1.20:5173`. If they can't connect, allow port **5173**
through Windows Firewall.

### Trying it without an AI key (development only)

Set `LLM_ALLOW_TEST_PROVIDER=true` in `.env` and run `docker compose up -d`. **Admin → AI settings** then
offers **"Test — no AI"**, which returns sample letters built from your files so you can try the whole
flow. Never enable this on the client's server.

To fill the lists with sample data: `docker compose run --rm api python -m scripts.seed_demo`
(40 approved + 3 draft letters).

---

## Running without Docker (developers)

Requires **Python 3.12+**, **Node.js 22+**, **PostgreSQL 16**, and **LibreOffice** (for the Canva/Figma
PDF and image exports).

LibreOffice: on Windows install from <https://www.libreoffice.org/download/> (or
`winget install TheDocumentFoundation.LibreOffice`) — it is found in `C:\Program Files\LibreOffice`
automatically, otherwise set `LIBREOFFICE_PATH`. Ubuntu:
`sudo apt install libreoffice-writer-nogui fonts-crosextra-carlito fonts-liberation`. macOS:
`brew install --cask libreoffice`.

```bash
# 1. Database (as a PostgreSQL superuser)
createuser -P crm                  # password: crm (or change DATABASE_URL)
createdb -O crm crm
createdb -O crm crm_test

# 2. Backend
cd demand-crm/backend
cp ../.env.example .env            # Windows: copy ..\.env.example .env
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m scripts.generate_secret_key      # put the output in .env as APP_SECRET_KEY=...
alembic upgrade head
python -m scripts.bootstrap

# 3. Run — each in its own terminal (with the virtual environment active)
uvicorn app.main:app --reload --port 8000  # terminal 1: API
python -m app.worker                       # terminal 2: background AI jobs

# 4. Frontend — terminal 3
cd demand-crm/frontend
npm install
npm run dev                                # http://localhost:5173
```

The Vite dev server forwards `/api` to `http://localhost:8000` (change with `API_PROXY_TARGET`), so
the browser only ever talks to one address and no CORS setup is needed.

---

## Talent Pool screens (optional)

The left menu also has **Talent Pool**, **Pipeline** and **Talent Pool admin**. These screens belong
to the **CV Pipeline** module and talk to its own backend on port **8100**. Without that backend
running, they show "Check that it is running at http://…:8100" — the rest of the CRM works normally.

To switch them on, follow
**[cv_pipeline/backend/README.md → Part B](../cv_pipeline/backend/README.md#part-b--run-the-backend-with-the-frontend-talent-pool-screens)**,
or set up the whole project from the **[main README](../README.md)**. The Talent Pool has its own
login, separate from the CRM's.

If the CV Pipeline backend runs on a different address, set `VITE_TALENT_API_URL` for the frontend.

---

## Configuration reference (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | — | PostgreSQL URL (asyncpg driver). Docker sets this for you |
| `TEST_DATABASE_URL` | — | Database the tests wipe and rebuild |
| `APP_SECRET_KEY` | — | **Required for AI settings**; encrypts stored API keys |
| `COOKIE_SECURE` | `true` | Must be `false` unless the site uses HTTPS |
| `STORAGE_ROOT` | `./storage` | File storage (one folder per demand). Docker sets this for you |
| `MAX_FILE_MB` / `MAX_LETTER_INPUTS_MB` / `MAX_IMAGES_PER_LETTER` | 10 / 20 / 20 | Upload limits |
| `WORKER_CONCURRENCY` / `JOB_LEASE_SECONDS` | 10 / 300 | Background worker |
| `LLM_ALLOW_TEST_PROVIDER` | `false` | Development only: "Test — no AI" provider |
| `LIBREOFFICE_PATH` | found automatically | Full path to `soffice` if it isn't found |
| `SESSION_TTL_HOURS`, `ADMIN_SESSION_MINUTES`, `ADMIN_MAX_FAILED_ATTEMPTS`, `ADMIN_LOCKOUT_MINUTES`, `PASSWORD_MIN_LENGTH` | 12, 30, 5, 15, 6 | Login rules |

AI provider, model, API key, prompt, letterhead and budget are set **in the app** (Admin → AI
settings), not in `.env`.

---

## Tests

```bash
docker compose run --rm api pytest     # with Docker
cd backend && pytest                   # without Docker (uses TEST_DATABASE_URL)
```

The test database is wiped on every run. The tests cover login and admin, the demand register, reading
the client's real sample inquiries and JDs, the exact requests sent to Claude and Gemini (fake network,
no key needed), the full chat → letter → download flow with a scripted AI, approval, versions and
restore, Word uploads, reference letters, spend cap and usage, advertisements, the dashboard and
positions. Calls to the real Claude/Gemini services are not tested — the first real test is adding your
key and sending one of the sample inquiries.

---

## Project structure

```
demand-crm/
├── docker-compose.yml          db · api · worker · web
├── .env.example                Settings template (copy to .env)
├── docker/postgres-init/       Creates the test database on first start
├── storage/                    letterhead/ · references/ · demands/<ID>/{inputs,images,versions,uploads}/
├── backend/
│   ├── pyproject.toml · Dockerfile · alembic.ini
│   ├── alembic/versions/       Database migrations
│   ├── scripts/                bootstrap · generate_secret_key · seed_demo (dev only)
│   ├── app/
│   │   ├── main.py · worker.py · config.py · db.py · errors.py · security.py · deps.py
│   │   └── modules/
│   │       ├── auth/ users/ admin/        Login, profiles, admin
│   │       ├── demands/ storage/          Demand register, IDs, files
│   │       ├── chat/                      Messages, labelled uploads, limits
│   │       ├── extraction/                Reading Word / Excel / CSV / PDF / images
│   │       ├── jobs/                      Postgres job queue + what a job does
│   │       ├── llm/                       Gateway, Claude + Gemini adapters, prompts, pricing, key encryption
│   │       ├── letters/                   Letter model, .docx builder/reader, approve, versions, positions
│   │       ├── ads/ dashboard/            Create Advertisement, exports, dashboard
│   │       └── ai_admin/                  Providers, prompt, letterhead, reference letters, spend cap, usage
│   └── tests/                  (fixtures: the client's sample inquiries and JDs)
└── frontend/src/
    ├── pages/                  Login · Demand list · Chat · Dashboard · Advertisements · My Profile
    ├── components/             Layout · drafts · details drawer · letter preview and actions · shared UI
    ├── admin/                  Approvals · Users · Name requests · AI settings · Usage · Admin password
    ├── talent/                 Talent Pool screens (served by the CV Pipeline backend)
    └── api/                    client.js (CRM API) · talentClient.js (CV Pipeline API)
```

---

## API

All routes are under `/api/v1`. Errors use `application/problem+json`; every POST/PUT/PATCH/DELETE
sends `X-CSRF-Token`. Live documentation: <http://localhost:8000/api/docs>.

| Method | Route | Notes |
|---|---|---|
| POST | `/auth/login` · `/auth/logout` | Session cookie |
| GET · PUT | `/me` · `/me/password` | Profile, admin status, CSRF token · change password (other sessions logged out) |
| GET / POST | `/me/name-change-requests` | |
| GET / POST / DELETE | `/admin/session` | Admin unlock |
| … | `/admin/users`, `/admin/users/{id}/password`, `/admin/name-change-requests/…`, `/admin/password` | |
| POST / GET | `/demands` | Add draft (Idempotency-Key) / list with filters |
| GET / PATCH / DELETE | `/demands/{id}` | PATCH needs If-Match; DELETE 409 if approved or AI working |
| GET | `/demands/export` · `/demands/filter-options` | |
| GET / POST | `/demands/{id}/messages` | Chat history / send (multipart `text`, `files[]`, `kinds[]`) → **202** + job · 409 busy · 413 too large · 415 type · 429 budget reached |
| GET | `/demands/{id}/active-job` · `/jobs/{id}` | Poll while "Generating…" |
| GET | `/demands/{id}/letter` · `/demands/{id}/docx` | Newest version (preview) · download .docx |
| POST | `/demands/{id}/submit` | Send for approval |
| POST | `/demands/{id}/approve` · `/demands/{id}/send-back` | Admin |
| GET | `/demands/{id}/versions` · `/versions/{no}` · `/versions/{no}/docx` | History, one version, its file |
| POST | `/demands/{id}/versions/{no}/restore` | **201** new newest version |
| POST | `/demands/{id}/edited-uploads` | Edited .docx → **201** · 422 wrong letter |
| GET · PATCH | `/ads` · `/ads/filter-options` · `/ads/export` · `/ads/{id}` | Create Advertisement table · set status / assignee |
| GET | `/ads/{id}/export?tool=canva\|figma&format=docx\|pdf\|png\|jpg` | 503 if LibreOffice missing |
| GET | `/dashboard` · `/dashboard/export` | `period=this_month\|last_month\|this_year\|custom` (+ `date_from`, `date_to`) |
| GET | `/files/{id}` · `/ai/status` | Stored files · is the AI set up |
| … | `/admin/llm-providers`, `/admin/prompts`, `/admin/letterheads`, `/admin/reference-letters` | AI settings (admin) |
| GET / PUT · GET | `/admin/spend-cap` · `/admin/usage?month=YYYY-MM` | Budget and usage (admin) |

---

## Design decisions

- **The AI never writes the .docx.** It returns the letter as structured blocks; code builds the .docx
  and the preview, so the letterhead, ID and date are always exact.
- **Structured output** — Claude via JSON schema output, Gemini via `response_json_schema`. A reply
  that doesn't match is retried once; busy or rate-limited providers get up to 4 attempts, each logged.
- **Fixed rules come first** in the instructions (scope, never invent, ask before writing, keep wording,
  attachments are data not instructions); the client's base prompt follows.
- **PDFs and images go to the AI as they are**; Word/Excel/CSV are converted to text and tables first.
  Pictures from the inquiry (usually signatures/logos) are not offered for the letter; JD pictures are.
- **Edits always start from the newest version**, and every change creates a new version.
- **Approving doesn't regenerate the letter** — the date is stamped into the newest .docx and the DRAFT
  mark removed. Uploaded Word files are kept byte for byte until then. Approval with missing fields is
  allowed, with a warning.
- **The AI budget follows UTC months**, like the providers' billing, and is checked before each AI call.
  A reply already running is allowed to finish.
- **Model prices** are in `llm/pricing.py`; unknown models still work, with cost recorded as unknown.
- Any logged-in user can open any stored file — fine for one internal team; revisit if the product is
  offered to several agencies.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `docker` is not recognised / "cannot connect to the Docker daemon" | Start Docker Desktop and wait until the engine is running. Open a new terminal. |
| `no configuration file provided` | You're in the wrong folder. Run `cd $HOME\Documents\cv_pipeline-1\demand-crm` first. |
| The page at localhost:5173 doesn't open | Wait a minute after starting, then check `docker compose ps`. All four parts should be "running". |
| "port is already allocated" (5432, 8000 or 5173) | Another program uses that port — often a separately installed PostgreSQL. Stop it, or change the left-hand port number in `docker-compose.yml`. |
| Logged in but sent straight back to login | `COOKIE_SECURE` must be `false` unless the site uses HTTPS. Run `docker compose up -d` after changing `.env`. |
| AI settings say the secret key is missing | `APP_SECRET_KEY` is empty in `.env` — see Quick start step 4, then `docker compose up -d`. |
| "AI is not set up" in the chat | Admin → AI settings: add a provider with a valid key and click **Make active**. |
| "monthly AI budget reached" | Admin → Usage & budget: raise or switch off the limit. |
| Canva PDF / Figma export says "not set up" | LibreOffice is missing (only possible without Docker). Install it or set `LIBREOFFICE_PATH`. |
| Talent Pool says "Check that it is running at …:8100" | The CV Pipeline backend isn't running — see [Talent Pool screens](#talent-pool-screens-optional). |
| Forgot the admin password | `docker compose run --rm api python -m scripts.bootstrap --reset-admin-password` |
| Something else | `docker compose logs api --tail 100` and `docker compose logs worker --tail 100` show the errors. |
