# CV Pipeline — Backend (Part 1: Fetch + Extract)

Downloads CV attachments from **Zoho Mail or Gmail**, remembers where it stopped, and extracts
each CV into a row of an **Excel** sheet using an **LLM (Claude or Gemini — switchable)**.

```
 Mailbox (IMAP)                 data/cvs/                         data/output/candidates.xlsx
 ┌───────────┐  STEP 1 fetch   ┌──────────┐  STEP 2 extract        ┌───────────────────────┐
 │ new mails │ ──────────────► │ PDF/DOCX │ ─► Docling ─► LLM ───► │ 1 row per candidate   │
 └───────────┘  (UID tracked)  │ DOC/IMG  │   (markdown) (Pydantic)│ Name, Email, Skills…  │
                               └──────────┘                        └───────────────────────┘
        data/state/mail_state.json        data/state/processed_files.json
        (last mail UID seen)              (which CVs are already in Excel)
```

Next parts (not built yet): **Step 3 shortlist** (pandas rules on the Excel) → **Step 4 JD fit** (LLM)
→ FastAPI layer → React frontend.

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
│   │   ├── converter.py        ← STEP 2a: Docling file → Markdown (with fallbacks)
│   │   ├── prompts.py          ← system prompt for extraction (tune here)
│   │   └── cv_extractor.py     ← STEP 2: loop over folder → LLM → Excel
│   ├── llm/
│   │   └── client.py           ← ONE interface for Claude + Gemini (via Instructor)
│   └── storage/
│       └── excel_store.py      ← write/read Excel (upsert by file hash)
├── data/
│   ├── cvs/                    ← downloaded CVs land here (you can also drop files in manually)
│   ├── output/                 ← candidates.xlsx
│   └── state/                  ← progress files, logs, text cache
└── tests/                      ← offline tests (fake mailbox + mocked LLM, no keys needed)
```

---

## 2. Setup (one time)

**Requirements:** Python **3.11 or 3.12** (3.10–3.13 work). ~3 GB disk for Docling's ML dependencies.

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
# Linux only, optional but saves ~3 GB of CUDA downloads if you have no NVIDIA GPU:
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
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

### Step 2 — `cv_extractor.py`: file → text → LLM → Excel
For each file in `data/cvs/`:
1. Hash it; if `processed_files.json` says it's already done → skip.
2. **Docling** converts it to Markdown (multi-column layouts, tables, OCR for scans).
   Fallbacks in `converter.py`: OCR fails → retry without OCR → (PDF) plain text layer.
3. Enough text → send Markdown to the LLM. Little/no text (bad scan / image) → send the
   **file itself** to the LLM's vision input (Claude and Gemini both read PDFs/images).
   Either way **all fields are extracted by the LLM**; Docling only provides text.
4. **Instructor** forces the LLM to return the `Candidate` Pydantic model (`app/core/models.py`).
   Invalid output → automatic re-ask (up to 2 retries). Python then cleans up: de-dupes skills,
   rejects impossible ages, computes **age from DOB** (more reliable than LLM maths).
5. Row is upserted into Excel immediately (crash-safe), keyed by **File Hash**.
6. One bad CV never stops the batch — it's logged as failed and **retried on the next run**.

Markdown of each CV is cached in `data/state/text_cache/<hash>.md` for the JD-fit step later.

### Excel columns
Full Name · Email · Phone · Skills · Experience (Years) · Education · Current/Last Job Title ·
Current/Last Company · Age · Date of Birth · Certifications · Languages · Location · Summary ·
CV File · Received From · Mail Subject · Mail Date · Extraction Method (text/vision) · LLM ·
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

## 5. Tests (no API keys or mailbox needed)

```bash
pip install pytest reportlab python-docx
pytest
```
Covers: UID tracking across runs, duplicate CVs, UIDVALIDITY reset, subject filter, logo
skipping, extraction → Excel for PDF/DOCX/image, skip-on-rerun, `--force` with the other
provider, and a broken file not stopping the batch. The LLM SDKs are mocked (`tests/mock_llm.py`).

---

## 6. Troubleshooting

| Problem | Fix |
|---|---|
| `AUTHENTICATIONFAILED` / `Invalid credentials` | Use an **App Password**, not your normal password. Zoho: IMAP access enabled? correct `.in`/`.com` host? |
| `IMAP_FOLDER not in list` | Run `python main.py check` and copy the exact folder name it prints. |
| First Docling run is slow | It downloads layout/OCR models once (HuggingFace). Later runs are fast. |
| Docling can't download models (corporate firewall) | Pipeline still works: digital PDFs use the text layer, scans/images go to LLM vision. Or set `DOCLING_OCR=false`. |
| `model not found` | Update `CLAUDE_MODEL` / `GEMINI_MODEL` in `.env`. |
| A CV keeps failing | `python main.py status` shows the error; `data/state/pipeline.log` has details. |
| Want to start completely fresh | Delete the files in `data/state/`, `data/output/` (and `data/cvs/` if you want). |
