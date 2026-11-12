# Talent Pool Module — Phase 1 Requirements


## Overview and scope

Phase 1 delivers a Talent Pool module inside the CRM. Users can browse, filter, open, edit and upload candidate CVs stored in S3, and run the CV pipeline from the CRM.

**Environment for phase 1**

- Fetch, extract, the backend API and Postgres all run on the **client's machine**. The Talent Pool and Pipeline screens in the CRM frontend call the `cv_pipeline` backend directly over the office network, so that backend is reachable from office PCs and requires a login.
- CV files are stored in the existing S3 bucket (`shield-global-cvs-bucket-…`, ap-south-1).
- Postgres is self-hosted, not Amazon RDS. Moving to RDS is a later decision.
- The Talent Pool has **its own login**, separate from the CRM login, with its own users and its own **Talent Pool admin**. See Talent Pool authentication and admin.
- The Talent Pool is its own module, shown inside the CRM and styled like the demand-letter list.

**Volume:** about 40,000 historical CVs, plus about 2,000 new CVs a month.

**End-to-end flow for one CV**

1. Fetch the mail (or take a manual upload) and convert or merge its attachments into one PDF.
2. Extract the fields with the LLM.
3. Rename the file to `Trade_Passport_Name.pdf`.
4. Upload it to S3 as `cvs/<mail-account>/<Month-Year>/<file>.pdf`.
5. Match it to an existing candidate or create a new one, and write it to Postgres.
6. Regenerate the candidate Excel from Postgres.

## Backend fixes and AWS setup

Five changes to the existing pipeline come before the Talent Pool.

| # | Item | Requirement |
| --- | --- | --- |
| 1 | Mail details missing from Excel (issue 3, Fix A) | The extractor looks up mail details under `PDF:<hash>` first, then `<hash>`. This covers both label formats, since the client's register holds a mix. A one-time backfill fills the blank Received From, Mail Subject and Mail Date fields. It uses no LLM calls. |
| 2 | Register path after rename | After the extractor renames a CV, it updates that file's path in `attachments_manifest.json`. Then the fetcher's duplicate check finds the file, and a re-sent identical CV isn't saved twice. |
| 3 | Open CVs from S3 | CVs are served from S3, not from local disk. The backend returns a short-lived presigned URL. The view link opens inline; the download link downloads. |
| 4 | Manual upload | The current upload endpoint imports a function that no longer exists. It's rebuilt as described under Frontend → Manual upload. |
| 5 | AWS setup documentation | Add `boto3` to `requirements.txt`. Add `AWS_S3_BUCKET`, `AWS_REGION` and `AWS_S3_PREFIX` to `.env.example`. Document the AWS credentials and the bucket permissions the pipeline needs. Note that the extractor and `upload_existing_cvs_to_s3.py` must use the same prefix (`cvs`), or the S3 keys won't match. |

## Data storage

Postgres is the single source of truth. The candidate Excel is generated from it and never edited directly.

**What Postgres holds**

| Record | One per | Holds |
| --- | --- | --- |
| Candidate | Person | The current value of every field, after any manual edits. A "needs review" state. |
| CV document | CV received (many per candidate) | S3 key, file hash, file name; source (mail account, sender, subject, mail date) or "Manual upload"; the values extracted from this CV; extraction method, LLM used, extracted-at time; S3 upload status |
| Edit history | Manual edit | Field, old value, new value, user (Talent Pool login), time. When a newer CV later replaces a manual edit, that's recorded too. Fields that only ever change through CVs get no history. |
| Review flag | Open issue | Type (possible duplicate, or manual edit replaced by a new CV), the candidates and fields involved, status (open or resolved), who resolved it and when |

**Candidate Excel**

- **One row per CV**, as today. The columns are the current ones, filled from Postgres, including any manual edits. Every row for a person shows that person's current values. CV File, mail details and dates stay per CV.
- Regenerated automatically after every extraction run, edit and delete. It's also downloadable from the CRM.

**New CVs**

- The pipeline writes each extracted CV to Postgres and runs the matching rules. Then it regenerates the Excel.
- `processed_files.json` and `attachments_manifest.json` keep their current roles: skipping files already done, and holding mail details.

**Historical CVs (about 40,000)**

1. The client uploads the files to S3 from their machine with `upload_existing_cvs_to_s3.py`. The files are already downloaded, arranged and renamed.
2. A one-time backfill loads the client's `candidates.xlsx` into Postgres.
3. Each row's S3 key is rebuilt from its File Hash. The hash gives the full local path in `processed_files.json`. That path, relative to `data/cvs`, becomes `cvs/<account>/<Month-Year>/<file>.pdf`.
4. Mail details come from `attachments_manifest.json`, read under both label formats.
5. The matching rules run during the backfill, so the same person's CVs are grouped under one candidate.
6. Any rows whose S3 file can't be found in the bucket are still loaded, marked as missing, and reported.

## Matching and new CVs vs manual edits

An incoming CV merges into an existing candidate automatically only when the passport numbers match. Every weaker match is flagged for a person to review.

**Matching rules** (applied to new CVs, manual uploads and the historical backfill)

| # | Condition | Action |
| --- | --- | --- |
| 1 | Same passport number, compared after removing spaces and dashes and uppercasing | Attach the CV to that candidate automatically |
| 2 | Different passport numbers, but the same email or phone | Create a new candidate and flag both as a possible duplicate |
| 3 | No passport on one side, plus the same email or phone and a similar name | Create a new candidate and flag both as a possible duplicate |
| 4 | Name only | Never treated as a match |

When a person resolves a duplicate flag, the choices are **Same person → merge** (all CVs move under one candidate) or **Different people** (the flag is closed).

**How the rules compare values**

- **Passport:** spaces and dashes removed, uppercased. Placeholders count as no passport: values with no letters or digits, words like N/A, NIL, NONE or NOT MENTIONED, all zeros, or fewer than 6 characters.
- **Phone:** digits only, then the last 10 digits. Numbers with fewer than 7 digits are ignored.
- **Email:** lowercased, spaces removed.
- **Similar name:** case, dots and spacing are ignored, and the words can be in any order. Two names are similar when every word of the shorter name is in the longer one, and the shorter name has at least two words (or the names are identical).
- **Agency contacts:** an email or phone already seen with 3 or more different names is treated as an agency contact and never triggers a flag.
- **No passport on either side:** treated like rule 3. A shared contact with a similar name is flagged, and a shared contact with a different name is not.
- **Passport matches but the name is clearly different:** the CV is still attached, and the candidate is flagged so a person can check it.
- **Different people:** the decision is permanent. That pair is never flagged again.
- **Same person → merge:** the values come from the newest CV across both candidates, and manual edits from both are kept. If both edited the same field, the more recent edit wins. All CVs and edit history move to the merged candidate.

**Which CV is newer:** the mail date, or the upload time for manual uploads. An older CV that arrives late is attached, but it only fills blank fields and never overwrites.

**When a newer CV arrives for a candidate with manual edits**

- **A field whose value didn't change between the previous extraction and the new one:** the manual edit stays.
- **A field whose value did change:** the new CV's value replaces the manual edit. The candidate is flagged, for example "New CV received: 2 manually edited fields were replaced."
- **In the detail panel**, each replaced field shows the old manual value next to the new value, with a **Keep my edit** button. Clicking it restores the manual value and records the change in the edit history.
- **Fields that were never edited by hand** simply take the new CV's values, with no flag.
- **A blank field in the new CV** never clears the current value.
- **"Changed"** ignores case, spacing and the order of list items such as skills. Summary never replaces a manual edit, because the LLM rewrites it every time. Summary still updates when it was never edited.
- **Re-extracting the same CV file** updates that CV's values. They're applied to the candidate, with the rules above, only if it's the candidate's newest CV.

## Talent Pool authentication and admin

The `cv_pipeline` backend has its own authentication, because the CRM frontend calls it directly over the office network. This is built right after the Postgres schema. Its backend and frontend are built together, using the CRM's authentication as the reference.

**Logins**

- Talent Pool users are separate from CRM users. Someone using both logs in to each one.
- Every `cv_pipeline` API route requires a login. The command line is not affected.
- Sessions, password storage and protection on write requests work the same way as in the CRM.
- Any user, including an admin, can change their own password. This needs their current password and logs them out of their other browsers.

**Talent Pool admin**

- There is one shared Talent Pool admin password, separate from the CRM's. Any logged-in user who enters it unlocks admin for their session, for a limited time, as in the CRM. Repeated wrong attempts lock them out for a period.
- The Talent Pool admin can:
  - delete candidates
  - manage users: create, deactivate and reset passwords
  - change the Talent Pool admin password
  - change the Talent Pool settings: the AI provider and API keys, the mailbox, S3 storage, folders, the database connection and the login rules. They're saved to the backend's `.env`. Secrets are never shown, only whether they're set. Settings read at startup are marked as needing a restart. Test buttons check the mailbox, S3, the database and the AI key.
- Fetch, extract and retrying failed S3 uploads stay open to any logged-in user.

**Frontend**

- A Talent Pool login screen is shown before the Talent Pool and Pipeline screens.
- A Talent Pool admin area holds the admin password prompt, user management and admin password change, styled like the CRM's admin area.

## Frontend

Phase 1 adds two CRM screens: the Talent Pool and a Pipeline screen for running fetch and extract. It also adds the Talent Pool login and admin screens described above.

### Talent Pool list

- Styled like the demand-letter list.
- **Columns:** Name, Trade (current/last job title), Passport No., Experience (years), Age, Location, Received On, View CV.
- Flagged rows show a **Needs review** badge.
- Filtering, search, sorting and paging run on the server, sized for 40,000+ rows growing by about 2,000 a month.
- A **Download Excel** button gets the current candidate Excel.

### Filters

- Trade (type to search)
- Experience range (min–max years). Candidates with unknown experience are included.
- Age range. Candidates with unknown age are included. The age is worked out from the date of birth when it's known, so it doesn't go stale.
- Location (type to search)
- Skills (type to search; every skill entered must be present)
- Received date range
- Source mailbox (including "Manual upload")
- Needs review
- Free-text search across all fields

### Candidate detail

- Opens from a row, either in a side panel or a new tab.
- **CV viewer:** shows the latest CV inline, streamed from S3. A Download button is available.
- **All CV versions** for the candidate are listed with their received dates. Any of them can be opened or downloaded.
- **Extracted fields can be edited** by any user, including passport number, email and phone. Each save updates Postgres, writes the edit history and regenerates the Excel. Editing a passport number, email or phone re-runs the matching rules against everyone else. A match only raises a review flag; an edit never merges people.
- **Edit history** shows who changed which field, when, and from what to what.
- **Review section** for open flags: Same person → merge / Different people for duplicates; Keep my edit for fields a newer CV replaced. A replaced-edit flag, or a passport match with a different name, can also be closed with **Accept** when the new values are fine.

### Manual upload

- An **Upload CV** button in the Talent Pool.
- One upload is one candidate. The upload screen says so clearly.
- One upload can contain several files. They're merged into one PDF, the same way the mail flow merges attachments.
- The upload runs the full pipeline: convert/merge → extract → rename → S3 → Postgres → matching → Excel. Progress is shown, and the new candidate appears in the list when it finishes.
- The source is recorded as "Manual upload". There are no mail details.

### Delete (Talent Pool admin only)

- The **Delete** button is visible only while the user has Talent Pool admin unlocked, and the backend enforces the same rule.
- It asks for confirmation first.
- Deleting a candidate removes:
  - the candidate record, all their CV records, edit history and flags from Postgres
  - all their CV files from S3
  - their local files on the client's machine
  - their rows in every job's shortlist and job-fit files
- The candidate's rows drop out of the regenerated Excel.
- Their file hashes are removed from `processed_files.json` and `attachments_manifest.json`. If the same person mails a CV again later, they come back as a **new candidate**.

### Pipeline screen

- **Run buttons:** Fetch mail, Extract CVs, and Run both. These use the existing `/api/fetch`, `/api/extract` and `/api/run`.
- **Live progress:** a progress bar (done / total) and the file currently being processed, polled from `/api/tasks/{id}`.
- **Already running:** if a run is in progress (the API returns 409), the buttons are disabled and the screen shows that run's progress.
- **Last run summary:** mails scanned, CVs saved, duplicates skipped, CVs extracted, CVs failed, and when the run finished.
- **Status counts:** CV files on disk, extracted OK, candidates in Postgres, S3 uploads failed.
- **Failed files list:** file name, error message and time, from `/api/status`.
- **Retry failed S3 uploads:** a button that re-uploads every CV whose S3 upload failed, with progress shown, then updates the S3 status in Postgres and the Excel. It needs a new backend endpoint, run as a background task like fetch and extract.
- **Read-only settings:** the connected mailbox and folder, and the LLM provider in use, from `/api/config`.

## Out of scope and open questions

**Parked for later phases**

- **Jobs module:** JD → requirements → criteria → shortlist → job fit, with CVs read from S3 and the shortlist reading from Postgres instead of Excel
- **Select and match:** sending chosen Talent Pool candidates to JD matching
- **Role applied for:** a column and its extraction (the file name's "Trade" is currently the last job title)
- **Date-range fetch** in the API and on the Pipeline screen
- **Detecting re-sent Word CVs:** each conversion to PDF may produce a different file hash, so an identical Word CV sent again may not be caught as a duplicate
- **Single sign-on with the CRM**, and roles beyond user and admin
- **Moving Postgres to Amazon RDS**, and **archiving old CVs** in S3 Glacier
- **Moving fetch and extract to a server**

**Decided after the first draft**

- Retry failed S3 uploads is in scope: added to the Pipeline screen. No open questions remain.
- Talent Pool authentication and admin is in scope: `cv_pipeline` stays a separate service, the CRM frontend calls it directly, and it has its own login and admin. Added as its own section, and moved out of the parked list.
