# Recruitment CRM & Talent Pool

An all-in-one system for a recruitment agency: it turns **client job inquiries into approved demand
letters**, and turns **CVs arriving by email into a searchable pool of candidates** — using AI
(Claude or Gemini) for the heavy reading and writing.

Everything runs on the agency's own computer or office server, and the team uses it from a web
browser.

---

## What it does

A recruitment agency works on two sides: **clients** who need workers, and **candidates** who want
jobs. This project covers both.

### 1. Demand Letter CRM — the client side (`demand-crm/`)

- A team member uploads a client's inquiry and job descriptions into a chat.
- The AI asks about anything missing, then **writes the demand letter** on the company letterhead.
  It never makes details up — anything unknown is clearly marked *to be filled*.
- The letter is refined by chatting or by editing it in Word. Every version is kept.
- An admin **approves** it. The letter is dated, the DRAFT mark is removed, and it goes onto the
  **demand list**.
- Approved letters are exported for **advertisements** (Canva / Figma), and a **dashboard** shows orders,
  vacancies, clients and team output.

### 2. CV Pipeline / Talent Pool — the candidate side (`cv_pipeline/`)

- **Collects CVs** automatically from the company mailbox (Gmail or Zoho), or from manual uploads.
- **Reads each CV with AI** — name, contact details, passport number, trade, skills, experience,
  education and more.
- **Stores** the CV file in Amazon S3 and the details in a database. An Excel sheet of every candidate
  is kept up to date.
- **Recognises repeat applicants**, so one person's CVs stay under one candidate. Unclear cases are
  flagged for a person to review.
- The **Talent Pool** screens in the CRM let the team search, filter, open, edit and upload CVs, and
  run the pipeline with a click.
- For a specific opening, it can **shortlist** candidates by rules and **score** them against the job
  description.

### How the parts fit together

```
                         ┌──────────────────────────────────────────────┐
  Team's web browser ──► │  CRM website  (port 5173)                    │
                         │  Demand letters · Dashboard · Talent Pool    │
                         └───────┬───────────────────────────┬──────────┘
                                 │                           │
                                 ▼                           ▼
                 ┌──────────────────────────┐   ┌──────────────────────────────┐
                 │ CRM backend (port 8000)  │   │ CV Pipeline backend (8100)   │
                 │ + worker (AI letters)    │   │ fetch · extract · Talent Pool│
                 └────────────┬─────────────┘   └──┬──────────┬──────────┬─────┘
                              │                    │          │          │
                              ▼                    ▼          ▼          ▼
                 ┌─────────────────────────────────────┐  Mailbox    Amazon S3
                 │ PostgreSQL (port 5432)              │  (Gmail /   (CV files)
                 │ "crm" database · "talent" database  │   Zoho)
                 └─────────────────────────────────────┘
                     Claude or Gemini (AI) is used by both backends
```

| Part | Folder | Address | Started by |
|---|---|---|---|
| CRM website | `demand-crm/frontend` | <http://localhost:5173> | Docker |
| CRM backend + worker | `demand-crm/backend` | <http://localhost:8000> | Docker |
| Database (PostgreSQL 16) | — | `localhost:5432` | Docker |
| CV Pipeline backend | `cv_pipeline/backend` | <http://localhost:8100> | Python |

---

## Setting up the whole project

Follow these steps in order. Setup takes about **30–45 minutes** the first time; after that, starting
the system takes a minute ([see below](#starting-and-stopping-it-every-day)).

### Step 0 — Install the programs and collect the details (one time)

**Install:**

| Program | Where to get it | Notes |
|---|---|---|
| **Docker Desktop** | <https://www.docker.com/products/docker-desktop/> | Open it once after installing and wait until the engine is running. On Windows, accept if it asks to install WSL or to restart. |
| **Python 3.12** | <https://www.python.org/downloads/> | On Windows, tick **"Add python.exe to PATH"** on the first installer screen. |
| **Git** *(optional)* | <https://git-scm.com/downloads> | Or download the project as a ZIP instead. |

**Have ready:**

- An **AI key** from Claude (<https://console.anthropic.com>) or Gemini (<https://aistudio.google.com>).
- The **mailbox** that receives CVs and an **app password** for it — see
  [Mailbox setup](cv_pipeline/backend/README.md#mailbox-setup-gmail-or-zoho).
- *(Optional)* An **AWS S3 bucket** and AWS keys for storing CV files — see
  [AWS S3 setup](cv_pipeline/backend/README.md#aws-s3-setup).

**How to open a terminal:** on Windows, press Start, type **PowerShell** and open it. On macOS, press
`Cmd + Space`, type **Terminal** and press Enter. Type each command below exactly as shown and press
**Enter**. Lines starting with `#` are explanations — don't type them.

### Step 1 — Get the code

```powershell
cd $HOME\Documents
git clone https://github.com/Urvashi-0810/CV-pipeline.git cv_pipeline-1
```

No Git? On GitHub click **Code → Download ZIP**, then unzip it into your Documents folder as `cv_pipeline-1`.

### Step 2 — Set up and start the CRM

This terminal is your **CRM terminal**:

```powershell
cd $HOME\Documents\cv_pipeline-1\demand-crm
copy .env.example .env                                              # macOS: cp .env.example .env
docker compose run --rm api python -m scripts.generate_secret_key
```

The last command takes **5–10 minutes** the first time, then prints a long line of letters and
numbers. Open the settings file with `notepad .env` (macOS: `open -e .env`). Paste that line after
`APP_SECRET_KEY=`, then save and close.

Then start the CRM and create your CRM login:

```powershell
docker compose up --build -d
docker compose run --rm api python -m scripts.bootstrap
```

The last command asks for a **username**, **name**, **password** and a separate **admin password**.
Write them down.

### Step 3 — Create the Talent Pool database

Still in the CRM terminal, create a database for the Talent Pool inside the same database server.
Replace `MyTalentPassword` with a password of your choice and **remember it for step 5**:

```powershell
docker compose exec db psql -U crm -d crm -c "CREATE ROLE talent LOGIN PASSWORD 'MyTalentPassword';"
docker compose exec db psql -U crm -d crm -c "CREATE DATABASE talent OWNER talent;"
```

You should see `CREATE ROLE` and then `CREATE DATABASE`.

### Step 4 — Install the CV Pipeline backend

Open a **second terminal**. This is your **CV Pipeline terminal**:

```powershell
cd $HOME\Documents\cv_pipeline-1\cv_pipeline\backend
python -m venv venv
venv\Scripts\activate                       # macOS: source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

You should see `(venv)` at the start of the line. The install takes a few minutes.

> **Windows says "running scripts is disabled on this system"?** Run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then `venv\Scripts\activate` again.

### Step 5 — Fill in the CV Pipeline settings

```powershell
copy .env.example .env                      # macOS: cp .env.example .env
notepad .env                                # macOS: open -e .env
```

Change these lines, then save:

| Setting | What to put |
|---|---|
| `LLM_PROVIDER` | `claude` or `gemini` |
| `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` | Your AI key |
| `CLAUDE_MODEL` or `GEMINI_MODEL` | A current model name, e.g. `claude-sonnet-5-5` |
| `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_FOLDER` | The mailbox that receives CVs |
| `FETCH_SINCE_DATE` | First run only reads mails from this date, e.g. `2026-09-01` |
| `DATABASE_URL` | `postgresql+psycopg://talent:MyTalentPassword@localhost:5432/talent` (your password from step 3) |
| `AWS_S3_BUCKET` | Your S3 bucket name (if you use S3) |

### Step 6 — Prepare the Talent Pool and create its login

```powershell
alembic upgrade head
python bootstrap_auth.py
python main.py check
```

- `alembic upgrade head` creates the Talent Pool tables.
- `bootstrap_auth.py` asks for a **Talent Pool** username, name, password and **Talent Pool admin
  password**. This login is separate from the CRM login — write it down too.
- `main.py check` should show `[OK]` for your AI provider and the mailbox.

### Step 7 — Start the CV Pipeline backend

```powershell
python main.py serve
```

Leave this terminal open — closing it stops the Talent Pool.

### Step 8 — Open the system

1. Go to **<http://localhost:5173>** in Chrome or Edge and log in with your **CRM** login (step 2).
2. Open **Admin → AI settings** (enter the admin password). Add your AI provider and key, click
   **Make active**, upload the letterhead header and footer, and set the base prompt.
3. Click **Talent Pool** in the left menu and log in with your **Talent Pool** login (step 6).
4. Go to **Pipeline** and start a run to pull in CVs from the mailbox.

The system is now set up.

---

## Starting and stopping it every day

**Start** (after the computer was restarted):

1. Open **Docker Desktop** and wait until it is running.
2. CRM terminal:
   ```powershell
   cd $HOME\Documents\cv_pipeline-1\demand-crm
   docker compose up -d
   ```
3. CV Pipeline terminal:
   ```powershell
   cd $HOME\Documents\cv_pipeline-1\cv_pipeline\backend
   venv\Scripts\activate                    # macOS: source venv/bin/activate
   python main.py serve
   ```
4. Open <http://localhost:5173>.

**Stop:** press `Ctrl + C` in the CV Pipeline terminal. Then, in the CRM terminal, run `docker compose stop`.

Your data is kept between restarts. Never run `docker compose down -v` — the `-v` deletes the database.

### Using it from other computers in the office

1. On this computer, run `ipconfig` (Windows) and note the **IPv4 Address**, e.g. `192.168.1.20`.
2. Start the CV Pipeline backend with `python main.py serve --host 0.0.0.0`.
3. In `cv_pipeline\backend\.env`, add the address to `CORS_ORIGINS` and restart the backend:
   `CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://192.168.1.20:5173`
4. Colleagues open `http://192.168.1.20:5173`. If it doesn't load, allow ports **5173** and **8100**
   through Windows Firewall.

---

## Running only one module

| I want to… | Read |
|---|---|
| Run only the **Demand Letter CRM** | [demand-crm/README.md](demand-crm/README.md) |
| Run only the **CV Pipeline backend** (command line or API) | [cv_pipeline/backend/README.md → Part A](cv_pipeline/backend/README.md#part-a--run-the-backend-on-its-own) |
| Run the CV Pipeline with its screens | [cv_pipeline/backend/README.md → Part B](cv_pipeline/backend/README.md#part-b--run-the-backend-with-the-frontend-talent-pool-screens) |

The module READMEs also cover configuration, the API, the project structure, tests and design
details. If you already have CVs from before the Talent Pool existed, see
[Loading existing CVs](cv_pipeline/backend/README.md#loading-existing-cvs-one-time-optional).

---

## Repository layout

```
cv_pipeline-1/
├── README.md                                    ← you are here
├── Talent Pool Module — Phase 1 Requirements.md   Detailed requirements for the Talent Pool
├── demand-crm/                                  Demand Letter CRM
│   ├── docker-compose.yml                         database · backend · worker · website
│   ├── backend/                                   FastAPI + PostgreSQL (Python 3.12+)
│   ├── frontend/                                  React + Vite website (also hosts the Talent Pool screens)
│   └── storage/                                   Letters, uploads, letterhead
└── cv_pipeline/
    └── backend/                                 CV Pipeline / Talent Pool API + command line (Python)
        ├── main.py                                fetch · extract · serve · shortlist · jobfit
        └── data/                                  CVs, Excel output, progress files
```

**Technology:** Python (FastAPI, SQLAlchemy, Alembic), React (Vite), PostgreSQL 16, Docker, Claude /
Gemini, Amazon S3, IMAP (Gmail / Zoho).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `docker` or `python` is not recognised | The program isn't installed, or the terminal was opened before installing. Install it and open a **new** terminal. For Python, tick "Add python.exe to PATH". |
| "Cannot connect to the Docker daemon" | Start Docker Desktop and wait until the engine is running. |
| `(venv)` missing in the CV Pipeline terminal | Run `venv\Scripts\activate` again from `cv_pipeline\backend`. |
| "port is already allocated" | Another program uses that port (often a separately installed PostgreSQL on 5432). Stop that program. |
| Talent Pool says "Check that it is running at …:8100" | The CV Pipeline backend isn't running — do Step 7 / "Start", point 3. |
| `password authentication failed for user "talent"` | The password in `cv_pipeline\backend\.env` → `DATABASE_URL` must match step 3. |
| Mailbox login fails | Use an **app password**, not your normal password. See [Mailbox setup](cv_pipeline/backend/README.md#mailbox-setup-gmail-or-zoho). |
| "AI is not set up" in the CRM chat | Admin → AI settings → add a provider with a valid key → **Make active**. |
| Anything else | See the Troubleshooting sections in [demand-crm](demand-crm/README.md#troubleshooting) and [cv_pipeline/backend](cv_pipeline/backend/README.md#troubleshooting). |
