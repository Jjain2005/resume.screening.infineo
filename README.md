# Infineo Resume Screening System

An automated, **LLM-free** resume screening pipeline for internship applications. Candidates submit via a Google Form → resumes land in Google Drive → Apps Script calls a Cloud Run service → results appear in two Google Sheet tabs with a Select / Manual Review / Reject decision.

> [!TIP]
> **Adding or changing a role / JD?** Go to [Section 8](#8-adding-a-new-role--jd). It is written so an HR admin *or* a coding agent can follow it end to end: where the role name goes, how to build the keyword lists, when to extend the keyword taxonomy, and how to get the keywords approved by the person who asked for the role before anything goes live.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [How the Screening Engine Works](#2-how-the-screening-engine-works)
3. [Project Structure](#3-project-structure)
4. [Running Locally (Streamlit UI)](#4-running-locally-streamlit-ui)
5. [Running the Sheet Test CLI](#5-running-the-sheet-test-cli)
6. [Cloud Run Service (Already Deployed)](#6-cloud-run-service-already-deployed)
7. [Apps Script Setup](#7-apps-script-setup)
8. [Adding a New Role / JD](#8-adding-a-new-role--jd)
9. [Country & Geography Rules](#9-country--geography-rules)
10. [Calibrating the System](#10-calibrating-the-system)
11. [Troubleshooting](#11-troubleshooting)
12. [Important Limits & Warnings](#12-important-limits--warnings)

---

## 1. System Overview

```
Google Form submission
        │
        ▼
Google Sheet (Form responses tab)
  - Candidate name, role(s), city, resume Drive link
        │
        ▼
Apps Script trigger (on form submit)
  - Reads row from Sheet
  - Downloads resume from Drive (as base64)
  - Calls Cloud Run /screen endpoint with SCREENING_TOKEN
        │
        ▼
Cloud Run service (asia-south1)
  - Extracts text from PDF / DOCX / image / TXT
  - Runs scoring engine (src/)
  - Returns decision + score
        │
        ▼
Apps Script writes the result back to the "Intern Hiring" tab
  - "Application Screening" column: Shortlisted (SELECT) or Rejected (REJECT)
  - SELECT also triggers inviteShortlistedCandidate() -> interview invitation is sent
  - Every decision is also logged to a separate "Resume Screening Logs" spreadsheet
```

> [!WARNING]
> **Downstream effects of a decision (from the Apps Script).** `SELECT` automatically sends the candidate an interview invitation. `REJECT` sends nothing; the row just says *Rejected*. The service can also return `MANUAL REVIEW`, but the script is configured with `MANUAL_REVIEW_AS: 'REJECT'`, so **a Manual Review result is written as *Rejected*** and no human is alerted. This is the intended policy (decided by the project owner): keep `MANUAL_REVIEW_AS` as `'REJECT'`. Because the log spreadsheet keeps the service's original decision, HR can filter its *Decision* column for `MANUAL REVIEW` to spot-check these candidates. When a candidate has several roles, **any one SELECT shortlists them**; the script screens the first resume link in the row against the whole role cell.

**No LLM. No external AI API.** Every decision is made locally using keyword matching, synonym expansion, and scoring rules defined in this repository.

---

## 2. How the Screening Engine Works

### 2.1 Resume Extraction (`src/resume_reader.py`)

| File type | Method |
|-----------|--------|
| PDF | PyMuPDF text layer; OCR (RapidOCR or Tesseract) for scanned/image pages |
| DOCX | Paragraphs, tables, text boxes, headers/footers; OCR for embedded images |
| PNG / JPG | RapidOCR or Tesseract |
| TXT | Direct read |
| `.doc` | Rejected — convert to `.docx` or PDF first |

A page is OCR'd automatically when it has almost no text, is mostly an image, or the text layer looks garbled. Any extraction problem (blank page, password-protected PDF, low OCR confidence) generates a **warning** that forces **Manual Review**.

### 2.2 Scoring Rules (`src/evaluator.py`)

Each role is defined by three things in the code:

| Part | Purpose | Where it lives | Affects the decision? |
|------|---------|----------------|-----------------------|
| **Basic requirements** | The minimum gate. Each requirement is a *group of acceptable alternatives*; finding **any one** alternative anywhere in the resume satisfies that requirement. | `RULES` in `src/role_config.py` | ✅ Yes |
| **Good-to-have** | Scored but not individually required. Each term is checked on its own. | `good_to_have` in `role_requirements.json` | ✅ Yes (bonus) |
| **`essential` list and `note`** | `essential` is loaded but **not used by any scoring or UI code** (see warning below). `note` is shown in the Streamlit UI as guidance for the human reviewer. | `role_requirements.json` | ❌ No |

> [!WARNING]
> **The `essential` list in `role_requirements.json` does not drive anything.** The decision uses `RULES` (basic groups) and `good_to_have`; the Streamlit "Fixed requirements" panel is also built from `RULES`. The `essential` key must still **exist** for every role (`load_roles()` reads it and will crash if it is missing), so fill it in as a human-readable summary of the Basic groups, but **editing `essential` alone changes nothing**. All real gate changes go in `RULES`.

**Decision formula** (constants at the top of `evaluator.py`: `BASIC_FLOOR_PCT = 30`, `MIN_BASIC_MATCHED = 2`, `SCORE_PASS_LINE = 45`, `GOOD_TO_HAVE_WEIGHT = 0.25`):

```
Basic %  = groups satisfied / total groups × 100
Score    = Basic % + 0.25 × Good-to-have %

Checked in this order (first match wins):
1. REJECT         resume shows a blocked country (Pakistan / Bangladesh)       [every role]
2. MANUAL REVIEW  any extraction warning
3. MANUAL REVIEW  India-only role and the country check errored / conflicted
4. REJECT         India-only role and no "India" / +91 found
5. REJECT         Basic % < 30  OR  fewer than 2 groups matched
6. SELECT         Score ≥ 45
7. REJECT         otherwise
```

(`MANUAL REVIEW` is produced by steps 2 and 3. In the sheet it is intentionally written as *Rejected* (`MANUAL_REVIEW_AS = 'REJECT'` in the Apps Script); see Section 7.)

### 2.3 Synonym / Alias Expansion — the "keyword taxonomy" (`src/concepts.py`)

`concepts.py` holds `CLASSES`: a list of lists, where each inner list is **one concept** and every entry counts as evidence for every other entry. Examples:

- `LLM` = `Large Language Model` = `GPT` = `ChatGPT` = `Gemini` = `Claude`
- `RAG` = `Retrieval-Augmented Generation` = `Chat with PDF` = `RAG Pipeline`
- `FAISS` = `Pinecone` = `Chroma` = `ChromaDB` = `Vector Databases`

You never list every spelling in a role's keywords. Add one alias to the right class and every role using that keyword benefits.

**Exactly how a requirement term is resolved** (this matters when you add classes):

1. The term is normalised: lower-cased, `&` → `and`, dots between characters removed (`node.js` → `nodejs`, `A.I.` → `ai`), other punctuation → spaces. `+` and `#` are kept (`c++`, `c#`).
2. A trailing plural `s` is tolerated both when looking up the term and when matching resume text (`LLMs` finds the `llm` class).
3. **A class whose *first* entry equals the term wins.** If none has it first, *every* class that contains the term as any entry is merged. This is why the first entry in a class matters: it is the class "name", and it keeps narrow terms narrow. For example `Claude` resolves only to `[claude, anthropic]`, not to the broader `llm` class that merely lists `claude`.
4. If the term is in **no** class, it matches only itself (whole-word match).
5. Matching is on word boundaries, so `ai` will not match inside `maintain`.

### 2.4 Evidence Levels

Each matched requirement is tagged with its evidence quality:

| Label | Meaning | Example |
|-------|---------|---------|
| `demonstrated` | Found in a project/experience bullet, or a sentence with an action verb | *"Built a RAG pipeline using LangChain"* |
| `coursework/certificate` | Found in education or certs section | *"Certified in Prompt Engineering"* |
| `mentioned` | Found in summary or other sections | *"Interested in AI"* |
| `listed only` | Found in a skills list with no context | *"Skills: Python, LLM"* |

Evidence level does **not** change the score, but `listed only` is shown so you can spot keyword stuffing.

---

## 3. Project Structure

This is what the repository contains:

```
resume.screening.infineo-main/
├── app.py                  # Streamlit UI (local screening)
├── run_cli.py              # Batch CLI (no UI): python run_cli.py "<Role>" <folder> [out.csv]
├── sheet_test.py           # Sheet + Drive integration test runner; holds FORM_TO_ROLE and map_role()
├── role_requirements.json  # good_to_have, note (and the unused "essential") per role
├── requirements.txt
├── .gitignore
├── src/
│   ├── __init__.py
│   ├── concepts.py         # CLASSES (synonym/alias equivalence classes) + ACTION_VERBS
│   ├── evaluator.py        # Scoring engine (do NOT edit when adding a role)
│   ├── places_check.py     # Google Places country confirmation (India-only roles)
│   ├── resume_reader.py    # PDF / DOCX / image extraction
│   └── role_config.py      # RULES, INDIA_ONLY_ROLES, BLOCKED_COUNTRIES, load_roles()
└── tests/
    ├── test_pipeline.py    # End-to-end pipeline self-test (no network)
    ├── test_places.py      # Places API test (fake transport)
    └── test_sheet_test.py  # Sheet runner test (fake Google, no network)
```

> [!NOTE]
> **Deployment files are not in this repository.** `Dockerfile`, `server.py` and `screening_service.py` (used in [Section 6](#6-cloud-run-service-already-deployed)) come from a separate "Cloud Run upload package" that is unzipped on top of this project before deploying, and the Apps Script lives inside the Google Sheet (Section 7). The Apps Script has been reviewed and has **no role list or mapping**: it sends the raw role cell text to the service. Roles are matched by name: the role name in the code is the same as the Google Form option text, so no extra mapping is needed (see [Section 8, Step 2](#step-2-confirm-where-the-role-is-registered)).

**Where a role is registered:**

| What | File | Required? |
|------|------|-----------|
| Role name + `essential`, `good_to_have`, `note` | `role_requirements.json` | ✅ always |
| Basic requirement groups (the gate) | `src/role_config.py` → `RULES` | ✅ always |
| India-only flag | `src/role_config.py` → `INDIA_ONLY_ROLES` | only if India-only |
| Synonyms / aliases | `src/concepts.py` → `CLASSES` | only if new terms need aliases |
| Form option text → role name | `sheet_test.py` → `FORM_TO_ROLE` | only if the form text ≠ role name |
| Regression test for the role | `tests/test_pipeline.py` | recommended |

---

## 4. Running Locally (Streamlit UI)

```bash
cd resume.screening.infineo-main
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

- Select a role from the sidebar.
- Upload one or more resumes (PDF, DOCX, PNG, JPG, TXT).
- Click **Extract & evaluate**.
- Download results as CSV.

**Batch run without the UI:**

```bash
python run_cli.py "AI Engineer Intern" path/to/resumes/ results.csv
python run_cli.py            # no arguments: prints usage and the list of roles that loaded
```

**Optional — Google Maps key (for India-only roles):**
Create a `.env` file in the project folder:
```
GOOGLE_MAPS_API_KEY=your_key_here
```
Leave it blank or omit the file to use the simple India/+91 rule only.

> [!CAUTION]
> Never commit `.env`, `credentials.json`, or `token.json` to Git. They are already listed in `.gitignore`.

---

## 5. Running the Sheet Test CLI

Reads rows from your Google Sheet, downloads resumes from Drive, screens them, and prints a table.

**One-time Google setup:**
1. In Google Cloud Console → APIs & Services → Library: enable **Google Sheets API** and **Google Drive API**.
2. OAuth consent screen → External → add your account as a Test user.
3. Credentials → Create → OAuth client ID → Desktop app → download JSON → rename to `credentials.json` → place in project root.
4. First run opens a browser for consent; a `token.json` is saved for future runs.

```bash
python sheet_test.py --sheet "<sheet URL or ID>" --from 361 --to 380
python sheet_test.py --sheet "<sheet URL or ID>" --from 361 --to 380 --verbose --tab "Form responses 1"
```

Optional flags: `--no-places`, `--no-color`, `--out results.csv`, `--service-account key.json`.

### Form option → role mapping

The sheet's role column can hold several options per candidate (comma-separated). Each option is matched to a role by `map_role()` in `sheet_test.py`:

1. The option text is normalised: curly quotes straightened, the text `(limited seats available)` removed, whitespace collapsed, lower-cased.
2. If the result is a key in `FORM_TO_ROLE`, that role is used.
3. Otherwise, if it equals a role name from `role_requirements.json` (case-insensitive), that role is used. **No `FORM_TO_ROLE` row is needed in this case.**
4. Otherwise the option is **skipped** — no criteria are defined, and no error is raised.

Current `FORM_TO_ROLE` entries where the form text differs from the role name:

| Form option | Mapped role |
|-------------|-------------|
| Website Development Intern | Full Stack Developer Intern |
| Software Testing (QA) Intern | Software Testing Intern |
| Video Creation Intern | AI Video Creation Intern |
| Game Development Intern | Game Developer Intern |
| Curriculum Development Intern | Curriculum Developer Intern |

(The dict also contains rows whose text already equals the role name, e.g. `devops intern`; those are harmless but unnecessary.)

> [!NOTE]
> Roles that exist in the Streamlit app but have **no form option** with a matching text (for example *AI Engineer Intern*, *Backend Developer Intern*, *HR Intern*) are only reachable from the sheet if the form offers an option with that exact name or a `FORM_TO_ROLE` row points to them.

---

## 6. Cloud Run Service (Already Deployed)

The screening service is **already deployed**; the one-time GCP setup (project, billing, enabling services, generating the token) is done and is not repeated here. Day to day you only ever **redeploy** after a code change, which is described in [Section 8, Step 8](#step-8-deploy-to-cloud-run).

| Setting | Value |
|---------|-------|
| Service name | `resume-screening` |
| Region | `asia-south1` |
| Service URL | `https://resume-screening-595055277622.asia-south1.run.app` |
| Resources | `--memory 2Gi --cpu 2 --concurrency 1 --max-instances 3 --min-instances 0 --timeout 120` |
| Auth | Public URL, protected by the `SCREENING_TOKEN` environment variable (sent as a Bearer token by Apps Script) |

The upload package (`Dockerfile`, `server.py`, `screening_service.py`) is merged into the project folder before each deploy. The URL and token do not change when you redeploy, as long as `--set-env-vars SCREENING_TOKEN=$TOKEN` is passed every time.

> [!NOTE]
> **If the token is ever lost or leaked:** generate a new one with `openssl rand -hex 32`, redeploy with it, and update `SCREEN_SERVICE_TOKEN` in the Apps Script properties (Section 7.2). The token and the Apps Script property must match.

### Verify the service

```bash
# Health check (may take a few seconds on the first call: cold start)
curl https://YOUR-SERVICE-URL/healthz
# Expected: {"ok":true}

# Auth check (should refuse requests without a token)
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://YOUR-SERVICE-URL/screen \
  -H "Content-Type: application/json" -d '{"file_b64":"AA=="}'
# Expected: 401
```

### Deployment troubleshooting

| Error message | Likely cause | Fix |
|---------------|-------------|-----|
| "Permission denied" during build | Signed in to a different Google account than the project owner | Use the same Google account for Cloud Console and Cloud Shell |
| Deploy succeeds but `curl` returns 403 | Organisation policy blocks public Cloud Run services | Contact your Workspace admin, or use IAP/authenticated access |
| Build fails while installing packages | Dockerfile or requirements issue | Copy the last 20 lines of the error and investigate |
| `ls` doesn't show `Dockerfile` | Wrong folder after unzip, or the upload package wasn't merged in | `cd` into the correct subfolder; re-copy the package files |
| `401` from `/screen` after a deploy | `--set-env-vars SCREENING_TOKEN=$TOKEN` was omitted, so the token was wiped | Redeploy with the saved token |

---

## 7. Apps Script Setup

The Apps Script (a `.gs`/`.js` file bound to the Google Sheet, alongside `FormSubmitHandler.js`) calls the Cloud Run service whenever a form response arrives and writes the result back into the sheet.

### 7.1 What it does

- **Trigger:** `scrHandleFormSubmit` runs on every form submission and screens that row immediately. `scrRetryPending` runs every 15 minutes and picks up any row whose **Application Screening** cell is still empty.
- **What it sends to the service:** the resume file from the Drive link (first link in the cell only), the **raw role cell text** (`role_cell`), the city, the sheet name and row. It does **not** map, filter or validate roles, so **adding a role requires no Apps Script change.**
- **What it writes:** `Shortlisted` for SELECT, `Rejected` for REJECT, into the *Application Screening* column of the tab named in `SCREEN_FORM_TAB`. These labels must match the column's dropdown options exactly (`LABEL_SELECT`, `LABEL_REJECT` in `SCR_CFG`).
- **Several roles in one cell:** if any role returns SELECT the candidate is *Shortlisted*; otherwise *Rejected*. If the service returns `ERROR` for any role, the row is treated as failed and retried.
- **On SELECT:** calls `inviteShortlistedCandidate(sheet, row)` (defined in `FormSubmitHandler.js`), which sends the interview invitation. If that fails, it is logged as `INVITE_FAILED` and does not stop the loop.
- **Manual Review:** `MANUAL_REVIEW_AS` is `'REJECT'`, so Manual Review is written as *Rejected*. This is the intended policy; do not change it to `'SELECT'`, which would invite candidates with extraction problems or unconfirmed country. The *Resume Screening Logs* sheet records the service's original decision (`MANUAL REVIEW`), so periodically filter its *Decision* column to review those candidates by hand.
- **Failures:** a row with no usable decision is retried up to `MAX_ATTEMPTS` (4) times; after that the cell is left blank and an email is sent to `SCREEN_NOTIFY_EMAIL` (or the script owner) and to the Pumble errors channel. A `401` or `503` from the service stops the run and emails immediately.
- **Logging:** every result is appended to a separate "Resume Screening Logs" spreadsheet (one row per role) if `SCREEN_LOG_SHEET_ID` is set.

### 7.2 Script properties

In Apps Script: gear icon (Project Settings) → **Script properties**.

| Property | Required | Value |
|----------|----------|-------|
| `SCREEN_SERVICE_URL` | ✅ | `https://resume-screening-595055277622.asia-south1.run.app` |
| `SCREEN_SERVICE_TOKEN` | ✅ | your 64-character token (paste only when your screen is private) |
| `SCREEN_FORM_TAB` | ✅ | `Intern Hiring` (exact tab name) |
| `SCREEN_NOTIFY_EMAIL` | optional | who gets "gave up" / "service refused" emails; defaults to the script owner |
| `SCREEN_LOG_SHEET_ID` | optional | set automatically by `scrCreateLogSheet`; if missing, logging is skipped |
| `SCREEN_ATTEMPTS` | automatic | retry counter maintained by the script; do not edit |

Rules for the URL: starts with `https://`, **no `/screen` at the end** (the code appends it), no quotes or spaces.

**Sheet requirements.** The header row of the `SCREEN_FORM_TAB` tab must contain columns whose names include: `Timestamp`, `Full Name`, `internship role`, `Resume`, and exactly `Application Screening`. `Email Address` and `City` are used when present. Header matching is case-insensitive and partial, so renaming a form question can break it.

**Other code the script depends on:** `inviteShortlistedCandidate` and `postErrorAlert` must exist elsewhere in the same Apps Script project; `scr_safe_` is defined in this file and should be deleted here if another file already defines it.

### 7.3 Run setup (once)

1. Open Apps Script **from the sheet** (Extensions → Apps Script).
2. Optionally run **`scrCreateLogSheet`** to create the log spreadsheet.
3. Run **`scrAuthorize`** and grant permissions (if it says "app isn't verified": **Advanced → Go to … (unsafe) → Allow**).
4. Run **`scrSetup`**. Log should end with `Setup done for tab "Intern Hiring".`

> [!WARNING]
> `scrSetup` **deletes** existing triggers for `scrHandleFormSubmit`, `scrRetryPending`, `scrAddMenu` **and the old pipeline `handleFormSubmit` trigger**, then creates three new ones (form submit, 15-minute retry, on-open menu). Running it on the wrong sheet replaces that sheet's triggers.

> [!CAUTION]
> Do first-time setup and tests on the **test copy** of the sheet, not the real company sheet. A SELECT on a real row sends a real interview invitation.

### 7.4 Verify

- Reload the sheet; a **Screening** menu appears with: *Screen selected rows (redo)*, *Process blank rows now*, *Test connection*.
- **Screening → Test connection** should say `Connection OK (token accepted).`
- Optionally run `scrTestLog` to confirm the log spreadsheet is writable (it adds a `TEST` row).

**Re-screening a row:** select cells in up to 25 rows → *Screen selected rows (redo)*. This clears the result and attempt counter and screens again. A redo that ends in SELECT does not resend the invitation if the candidate already has a Booking Ref.

### 7.5 Apps Script Error Reference

| Error message | Cause | Fix |
|---------------|-------|-----|
| `Set SCREEN_SERVICE_URL and SCREEN_SERVICE_TOKEN…` | A property is missing or misspelled | Check the property names exactly in Project Settings |
| `Set SCREEN_FORM_TAB (= Intern Hiring)…` / `SCREEN_FORM_TAB "…" does not exist` | Property missing, or tab name differs | Set it to the exact tab name |
| `Could not find column(s)…` | A required header is missing (`Timestamp`, `Full Name`, `internship role`, `Resume`, `Application Screening`) | Fix the header names in the sheet |
| `service refused (HTTP 401/503): check token` | Token doesn't match `SCREENING_TOKEN` on Cloud Run | Update the script property or redeploy with the saved token |
| `service not reachable or too slow` | Cold start or timeout | Usually self-heals on the next 15-minute retry |
| `no usable decision from service` | Service returned `ERROR`, or nothing usable (for example it did not recognise the role) | Check the service logs and the "Resume Screening Logs" sheet |
| Row cell stays blank, "gave up on row N" email | 4 failed attempts | Fix the cause, then *Screen selected rows (redo)* |
| `Cannot read properties of null` | Script is standalone, not bound to a sheet | Reopen via Extensions → Apps Script from the sheet |
| `Authorization is required` | Permissions not granted | Run `scrAuthorize`, then `scrSetup` |
| `Exceeded maximum number of triggers` | Too many triggers | Remove old triggers in the Apps Script dashboard |
| `SyntaxError` or `has already been declared` | Paste cut off, or `scr_safe_` defined twice | Paste the full script fresh; delete duplicate definitions |
| Generic "An error occurred" with no detail | Popup blocked, multiple Google accounts | Allow popups on script.google.com; try Incognito with one account |

---

## 8. Adding a New Role / JD

This section is a **playbook** for two kinds of reader:

- **An HR admin** adding a role by hand.
- **A coding agent** (Claude Code, Cursor, Copilot, …) told *"add the X Intern role to the screener."* If you are an agent: read this whole section first, follow the steps in order, and obey the [rules and stop conditions](#88-rules-for-coding-agents).

### 8.0 The big picture

Adding a role is **not just a code change**. The keywords decide who gets selected, so the person who owns the role must approve them before they go live.

| Step | What happens | Who |
|------|--------------|-----|
| 1 | [Collect the inputs](#step-1-collect-the-inputs) | HR / agent |
| 2 | [Confirm where the role is registered](#step-2-confirm-where-the-role-is-registered) | Agent / HR |
| 3 | [Draft the keywords](#step-3-draft-the-keywords) | Agent drafts, **requester decides** |
| 4 | [Register the role in code](#step-4-register-the-role-in-code) | Agent / HR |
| 5 | [Extend the keyword taxonomy (only if needed)](#step-5-extend-the-keyword-taxonomy-only-if-needed) | Agent / HR |
| 6 | [Approval](#step-6-approval-required) | **The person who asked for the role** |
| 7 | [Test locally](#step-7-test-locally) | Agent / HR |
| 8 | [Deploy to Cloud Run](#step-8-deploy-to-cloud-run) | HR / tech team |
| 9 | [Post-launch audit](#step-9-post-launch-audit) | HR + requester |

### Files that can change (and files that must not)

| File | Change? | What to edit |
|------|---------|--------------|
| `role_requirements.json` | ✅ always | Add the role: `essential`, `good_to_have`, `note` |
| `src/role_config.py` | ✅ always | Add the role to `RULES`; add to `INDIA_ONLY_ROLES` only if the requester says so |
| `src/concepts.py` | ⚠️ only if needed | Add / extend synonym classes for terms not already covered |
| `sheet_test.py` | ⚠️ only if needed | Add a `FORM_TO_ROLE` row if the form option text ≠ role name |
| `tests/test_pipeline.py` | ✅ recommended | Add a sample-resume check for the new role |
| `src/evaluator.py` | ⛔ never | Scoring logic — not part of adding a role |
| `.env`, `credentials.json`, `token.json` | ⛔ never | Secrets — never read, edit, print, or commit |

> [!CAUTION]
> **Add the role to `role_requirements.json` and `RULES` together, in the same change.** `load_roles()` loops over every role in the JSON and reads `RULES[name]`. If a role is in the JSON but **not** in `RULES`, it raises `KeyError` and **the whole app, the CLI, and the sheet runner fail for every role**, not just the new one. (The reverse — a role in `RULES` but not in the JSON — is silently ignored, so the role never appears anywhere.) Run the [consistency check in Step 4](#4e-run-the-consistency-check) before you do anything else.

---

### Step 1: Collect the inputs

Do not start editing until you have all of these. If any are missing, **ask a human** (see [8.8](#88-rules-for-coding-agents)).

| Input | Description | Example |
|-------|-------------|---------|
| **Role name** | Exact, canonical name; becomes the key everywhere | `Data Analyst Intern` |
| **Google Form option text** | The exact wording candidates select (may differ from the role name) | `Data Analytics Intern` |
| **The JD** | Full text: responsibilities, required skills, nice-to-haves | *(paste)* |
| **India-only?** | Must candidates be India-based? Business decision — ask the person, don't assume | Yes / No |
| **Flexibility note** | One sentence on what the reviewer should tolerate | *"SQL or Python is enough; BI tools are a bonus."* |

> [!IMPORTANT]
> **Role name rules.** The role name in the code must be **identical to the option text in the Google Form** (case and extra spaces don't matter; a trailing `(limited seats available)` is ignored). This is the convention that makes everything work without any mapping. Use one spelling, character-for-character, in the JSON key, the `RULES` key, the `INDIA_ONLY_ROLES` entry and the right-hand side of `FORM_TO_ROLE`. Existing roles all end in `Intern` — follow that style. Names with special characters (`Sales & Marketing Intern`, `Founder's Associate Intern`) work, but copy them exactly.

---

### Step 2: Confirm the name is new and where the role is registered

First make sure the name is new:

```bash
grep -rn "Data Analyst Intern" --include="*.py" --include="*.json" .
```

If it already exists you are **modifying** a role — see [8.9](#89-changing-keywords-of-an-existing-role).

Then look at how an existing role is wired up to see every touch-point in the repo:

```bash
grep -rn "Game Developer Intern" --include="*.py" --include="*.json" .
```

In this repository that shows `role_requirements.json`, `src/role_config.py`, `sheet_test.py` (`FORM_TO_ROLE`) and `tests/`. Those are the only places a role is registered here.

**Naming convention — no extra mapping needed.** Because the role name in the code equals the Google Form option text, the Apps Script (which just passes the form text through) and the screening service resolve the role by name. Nothing outside this repository needs to change when you add a role, other than redeploying (Step 8).

The only exception is a role whose form option text is *deliberately* different from its role name (for example *Website Development Intern* → *Full Stack Developer Intern*). That needs a `FORM_TO_ROLE` row (Step 4d). Avoid this for new roles: name the form option and the role the same.

### Step 3: Draft the keywords

You need **Basic requirement groups** (the gate), a **good-to-have list**, an `essential` summary and a `note`. Write them down in plain language first — as a table in the PR or a message to the requester — before touching code.

#### 3.1 What each list means

| Concept | Meaning | Where it goes |
|---------|---------|---------------|
| **Basic requirement group** | One must-have capability, expressed as a list of acceptable alternatives. The candidate satisfies the group by showing **any one** alternative. | `RULES[role]` — each inner list is one group |
| **Good-to-have keywords** | Bonus skills, tools, certifications. Each is scored on its own; none is required. | `role_requirements.json` → `good_to_have` |
| **Essential summary** | A flat, human-readable restatement of the Basic groups. Not used by the code, but the key must exist. | `role_requirements.json` → `essential` |
| **Note** | One sentence of reviewer guidance, shown in the UI. | `role_requirements.json` → `note` |

#### 3.2 How to derive them from the JD

1. **Hard requirements** ("must have", "required", "proficient in", core duties) → Basic groups.
2. **Soft requirements** ("preferred", "bonus", "nice to have", extra tools) → good-to-have.
3. **Group genuine alternatives together.** If the JD says "Python or Java", or several tools do the same job, they are **one** group. Existing examples: `["Unity", "Unreal Engine"]`, `["AWS", "Azure", "Google Cloud", "GCP"]`.
4. **Different capabilities → different groups.** "Programming" and "Data visualisation" are separate groups.
5. **Use short standard terms**, not sentences (`SQL`, not `ability to write SQL queries`). Matching is keyword-based.
6. **Don't list spelling variants or obvious synonyms** (`ChatGPT` / `GPT` / `LLM`) — the taxonomy handles those (Step 5).
7. **Never invent requirements the JD doesn't support.** If the JD is vague, ask the requester.

#### 3.3 Sizing the groups — this controls how strict the role is

The number of groups (N) sets the percentage each group is worth, so it directly changes how hard it is to be selected. Existing roles use **3 to 10 groups** (e.g. Graphic Design 3, Software Testing 5, AI Engineer 8, DevOps 10) and **10 to 21 good-to-have terms**.

| Groups (N) | Matches 2 | Matches 3 | Matches 4 |
|-----------|-----------|-----------|-----------|
| 4 | 50% → **Select** even with 0 good-to-have | 75% → Select | 100% → Select |
| 5 | 40% → needs ≥ 20% good-to-have | 60% → Select | 80% → Select |
| 6 | 33% → needs ≥ 47% good-to-have | 50% → Select | 67% → Select |
| 8 | 25% → **Reject** (below the 30% floor) | 37.5% → needs ≥ 30% good-to-have | 50% → Select |
| 10 | 20% → **Reject** | 30% → needs ≥ 60% good-to-have | 40% → needs ≥ 20% good-to-have |

Guidance:
- Only add a group if the requester would genuinely reject someone who lacks it.
- More groups = stricter. With 8+ groups, ask whether some should be good-to-have instead.
- A role with very few groups (3–4) selects people who match just two of them; make sure those two are meaningful.
- Good-to-have is worth at most 25 points, so it can tip a borderline candidate but never rescue a weak one.

#### 3.4 Output of this step: a keyword draft

Produce this table (it goes to the requester in Step 6):

```
Role: Data Analyst Intern
Form option text: Data Analytics Intern
India-only: Yes / No (requester to confirm)

BASIC REQUIREMENT GROUPS  (candidate satisfies a group by showing ANY one alternative)
  G1  Programming / data tooling : Python | SQL | Excel
  G2  Analysis technique         : Data Analysis | Statistical Analysis | EDA
  G3  Visualisation              : Data Visualisation | Dashboard | Tableau | Power BI | Matplotlib
  G4  Databases                  : SQL | MySQL | PostgreSQL | BigQuery

GOOD-TO-HAVE: Pandas, NumPy, Seaborn, Google Sheets, Regression, Machine Learning, Looker, ...

NOTE: SQL, Python and Excel are alternatives for data work; one complete data project is enough.
```

(Illustrative example only — the real lists must come from the real JD and be approved by the requester.)

---

### Step 4: Register the role in code

#### 4a. `role_requirements.json`

Add a new top-level key, exactly the role name from Step 1. All three keys below are used by the loader: `essential` and `good_to_have` are **required** (missing → `KeyError`), `note` is optional.

```json
"Data Analyst Intern": {
  "essential": ["Python", "SQL", "Excel", "Data Analysis", "Data Visualisation", "Statistical Analysis"],
  "good_to_have": ["Pandas", "NumPy", "Seaborn", "Google Sheets", "Regression", "Machine Learning", "Looker"],
  "note": "SQL, Python, and Excel are alternatives for data work. One complete data project is sufficient."
}
```

Mind the JSON syntax: a comma between roles, no trailing comma after the last one, double quotes only.

#### 4b. `src/role_config.py` → `RULES`

This is the **gate**. Each inner list is one Basic group.

```python
"Data Analyst Intern": [
    ["Python", "SQL", "Excel"],
    ["Data Analysis", "Statistical Analysis", "EDA"],
    ["Data Visualisation", "Dashboard", "Tableau", "Power BI", "Matplotlib"],
    ["SQL", "MySQL", "PostgreSQL", "BigQuery"],
],
```

Notes:
- Each group is checked independently, so the same term can appear in two groups (as `SQL` does above), but then one `SQL` mention satisfies **both** — avoid this unless intended.
- Keep `essential` in `role_requirements.json` as a readable summary of these groups. It is not enforced, so drift between the two is not an error, but a stale `essential` list misleads reviewers.

#### 4c. `INDIA_ONLY_ROLES` (only if the requester confirmed)

```python
INDIA_ONLY_ROLES = {
    "Graphic Design Intern", "Social Media Intern", "Content Creation Intern",
    "Sales & Marketing Intern", "AI Video Creation Intern", "Prompt Engineering Intern",
    "Data Analyst Intern",   # ← only if the requester said India-only
}
```

If you add a role here, also update the list in [Section 9](#9-country--geography-rules). India-only roles **reject** resumes with no "India" / `+91` (see Section 9 for the Places fallback), so confirm this is intended.

#### 4d. `sheet_test.py` → `FORM_TO_ROLE` (only if the form text differs)

**Normally skip this step.** If the form option text equals the role name (the convention), `map_role()` matches it automatically. Add a row only if the two deliberately differ:

```python
FORM_TO_ROLE = {
    ...
    "data analytics intern": "Data Analyst Intern",   # key: form text, lower-case, straight quotes
}
```

Also confirm the option exists in the Google Form. If it isn't mapped and doesn't match a role name, `map_role()` **silently skips it** (no error, no result row).

#### 4e. Run the consistency check

From the project root, right after editing:

```bash
python3 - <<'EOF'
import json
from src.role_config import RULES, INDIA_ONLY_ROLES
data = json.load(open("role_requirements.json", encoding="utf-8"))
problems = []
for role, d in data.items():
    if role not in RULES:
        problems.append(f"'{role}' is in role_requirements.json but NOT in RULES -> app crashes with KeyError")
    for key in ("essential", "good_to_have"):
        if not d.get(key):
            problems.append(f"'{role}' has no '{key}' list")
for role in RULES:
    if role not in data:
        problems.append(f"'{role}' is in RULES but NOT in role_requirements.json -> role is silently ignored")
for role in INDIA_ONLY_ROLES:
    if role not in data:
        problems.append(f"'{role}' is in INDIA_ONLY_ROLES but is not a known role")
print("\n".join(problems) if problems else f"OK - {len(data)} roles consistent")
EOF
```

If the JSON is invalid or a name is mistyped you will see an error or a problem line here. Currently it prints `OK - 18 roles consistent`.

---

### Step 5: Extend the keyword taxonomy (only if needed)

`src/concepts.py` makes `ChatGPT`, `GPT` and `LLM` count as the same thing so role lists stay short. Read [Section 2.3](#23-synonym--alias-expansion--the-keyword-taxonomy-srcconceptspy) first — the lookup rules there decide what happens when you add a class.

#### 5.1 Check whether each keyword is already covered

For **every** keyword in your Basic groups and good-to-have list:

```bash
grep -n -i "tableau" src/concepts.py
```

- **Found in a class** → nothing to do.
- **Found, but a common phrasing is missing** → add it to that existing class.
- **Not found, and the term has real-world aliases** (abbreviations, product renames, hyphen/space variants) → add a new class.
- **Not found, and it has no aliases** (e.g. `Excel`) → no class needed; it matches itself.

You can also see exactly what a term expands to:

```bash
python3 -c "from src.evaluator import forms_for; print(forms_for('Tableau'))"
```

#### 5.2 How to add a class

Append inside `CLASSES`, near the related section (`# ---- AI / LLM ----`, `# ---- Testing ----`, etc.):

```python
["tableau", "tableau desktop", "tableau public", "tableau server"],
["power bi", "powerbi", "microsoft power bi"],
["eda", "exploratory data analysis", "data exploration", "exploratory analysis"],
```

Rules:
- Entries lower-case, written the way they will look **after normalisation** (no dots: `nodejs`-style is fine, `node.js` is normalised to the same thing).
- **Put the canonical term first.** The first entry is the class name, and a class whose first entry equals a requirement term is the one used for that term.
- One concept per list. Spelling variants, abbreviations and plurals-with-different-stems belong together; unrelated tools do not. (A plain trailing `s` is already handled automatically.)
- No change to `evaluator.py` is needed.

#### 5.3 Blast radius — taxonomy changes affect every role

> [!WARNING]
> Classes are shared by **all** roles and matching is two-way. A bad alias makes *every* role using either term match more easily.

| Pitfall | Example | Why it's bad |
|---------|---------|--------------|
| Over-generic term | adding `ai`, `data`, `analysis` to an unrelated class | Matches almost every resume |
| Over-merging distinct tools | `java` with `javascript`, or `matplotlib` with `tableau` when roles treat them separately | Candidate is credited for the wrong skill |
| Term in a second class | `sql` already has a class; adding it to another | If it isn't first in either, **all** classes containing it are merged for that term — silently widening it |
| Short tokens | `r`, `go`, `c`, `bi` | Word-boundary matching still matches them as standalone words in unrelated text; test with real resumes |

If you changed `concepts.py`, re-test **other roles that use the changed terms** (Step 7.3).

#### 5.4 Output of this step

A short note for the reviewer, e.g. *"Added 2 new classes (tableau, power bi); extended 1 class (eda); no other changes"* — or *"No taxonomy changes: all keywords already covered."*

---

### Step 6: Approval (required)

**No role goes live until the person who asked for it has approved the keywords.** You do not need a named owner or reviewer: the approver is simply **the person you are working with** (the *requester*), who knows what a good candidate looks like. HR or the agent *proposes*; the requester *decides*.

**How to ask:** show the requester the review packet (6.1) in the conversation and ask directly, for example: *"Here are the proposed keywords. Do you approve them, or what should I change?"* Do not continue to testing sign-off or deployment until they answer with an explicit yes.

> [!IMPORTANT]
> **Tell the requester what a decision does.** A **SELECT** automatically sends the candidate an interview invitation. A **REJECT** (and, by default, a **MANUAL REVIEW**) is written as *Rejected* with no email and no human alert. Over-permissive keywords therefore invite weak candidates automatically, and over-strict ones silently reject good ones. That is why the sample results in the review packet matter.

#### 6.1 What to show the requester

Copy this template, fill it in, and paste it into the conversation (or PR description / email if you work that way):

```
ROLE KEYWORD REVIEW — <Role name>
Requested by: <name>      Date: <date>

1. BASIC REQUIREMENTS (must-haves; a candidate must satisfy at least 2 groups and 30%)
   G1 <capability> : <alt 1> | <alt 2> | <alt 3>
   G2 <capability> : ...
   Total groups: <N>

2. GOOD-TO-HAVE (bonus, not required)
   <keyword>, <keyword>, ...

3. NOTE FOR REVIEWER (shown in the UI)
   <one sentence>

4. SYNONYMS ADDED / CHANGED
   <e.g. "tableau ≈ tableau desktop ≈ tableau public"> or "None"

5. GEOGRAPHY
   India-only: Yes / No
   (Pakistan / Bangladesh resumes are rejected for every role.)

6. HOW STRICT IS THIS?  (from Step 7 test results)
   Strong sample resume   -> <decision, score>
   Borderline sample      -> <decision, score>
   Weak / unrelated       -> <decision, score>

7. QUESTIONS FOR YOU
   - Would you reject someone lacking every alternative in any Basic group?
   - Is anything in Good-to-have actually mandatory?
   - Which tools/terms do your candidates commonly use that are missing?
   - Is there a term that would wrongly let weak candidates through?
   - India-only: confirm Yes / No.
```

#### 6.2 What the requester should check

| Question | Why it matters |
|----------|----------------|
| Would you reject a candidate who lacks *every* alternative in a Basic group? | If no, the group is too strict — make it good-to-have |
| Is a must-have missing? | Missing groups let weak candidates through |
| Are the alternatives in each group truly interchangeable? | Otherwise someone is credited for the wrong skill |
| Are common terms missing? | Prevents false rejections |
| Is the India-only decision right? | It auto-rejects resumes with no India signal |
| Do the sample results match your own judgement? | The best sanity check there is |

#### 6.3 Record the approval

Keep the approval **in writing**, with the change (the chat reply, PR comment, or email). Minimum record:

```
Approved by: <name of the person who approved>   Date: <date>
Role: <role name>   Keywords approved: <PR link / commit hash / pasted final list>
Conditions (if any): <e.g. "re-review after the first 20 candidates">
```

If the requester asks for changes, go back to Step 3, update, and re-send. Approval applies to the **final** list, not an earlier draft.

---

### Step 7: Test locally

#### 7.1 Run the app

```bash
source .venv/bin/activate
streamlit run app.py
```

1. Confirm the role appears in the sidebar dropdown. (If the app fails to start with `KeyError`, the role is in the JSON but not in `RULES` — see Step 4e.)
2. Check the "Fixed requirements" panel: the numbered Basic requirements must match what you drafted (`any of: …` for groups), and the good-to-have list and note should be right.

#### 7.2 Test with real resumes

Use 4–5 anonymised resumes — real ones where possible.

| Sample | Expected |
|--------|----------|
| Clearly strong match | **Select** |
| Borderline | A judgement call — show the requester |
| Clearly weak or unrelated | **Reject** |
| Strong skills but unusual phrasing (e.g. "built dashboards" instead of "data visualisation") | Should still match; if not, fix the keywords/taxonomy |
| (India-only roles) a non-India resume | Reject, as designed |

For any wrong result, read the "Extracted text" tab and the "not found" list to see *why*, then fix keywords, groups or taxonomy — **not** the scoring code. For a quick check on a single text file without the UI:

```bash
python3 - <<'EOF'
from src.evaluator import evaluate, classify
from src.role_config import load_roles
roles = load_roles()
ev = evaluate(open("sample_resume.txt", encoding="utf-8").read(), "Data Analyst Intern", roles)
print(classify(ev))
print("Basic:", ev["basic_met"], "/", ev["basic_total"], " Score:", round(ev["score"], 1))
print("Missing basic groups:", ev["missing"])
EOF
```

#### 7.3 Run the automated tests

```bash
pip install -r requirements.txt        # includes pymupdf and the OCR engine (rapidocr)
python tests/test_pipeline.py
python tests/test_sheet_test.py
python tests/test_places.py
```

Each prints `ALL PASSED` when healthy. Notes:

- `test_pipeline.py` needs `pymupdf` and an OCR engine. Without an OCR engine it prints `SKIP scanned tests` and the two checks *"PNG: OCR used…"* and *"DOCX: OCR used on embedded image"* **fail** — that is an environment problem, not caused by your change.
- `test_pipeline.py` contains a hard-coded table `expected_new_roles` with the number of Basic groups for four roles (Founder's Associate 10, DevOps 10, Game Developer 7, Curriculum Developer 10), and sample resumes for each. **If you change the group count of one of those roles, update that table.**
- **Add a sample-resume check for your new role** by copying the pattern used for the four roles there (a short strong resume that must `SELECT`, ideally a weak one that must `REJECT`).
- If you edited `concepts.py`, also run 2–3 resumes for **existing roles that share the changed terms** and confirm their decisions didn't change unexpectedly.

---

### Step 8: Deploy to Cloud Run

**Only after approval (Step 6) and passing tests (Step 7).** The keywords live inside the container image, so any change needs a redeploy.

> [!NOTE]
> **The Service URL and `SCREENING_TOKEN` do NOT change on redeployment.** The Apps Script properties (`SCREEN_SERVICE_URL`, `SCREEN_SERVICE_TOKEN`) never need updating. Only the container image is rebuilt.

**On your local computer** — make sure the Cloud Run upload package files (`Dockerfile`, `server.py`, `screening_service.py`) are in the project folder, then zip it, **excluding** `.env`, `credentials.json`, `token.json`, and `downloads/`.

**In Cloud Shell** — open the ⋮ menu → Upload → choose your zip, then run:

```bash
cd ~
unzip -o 'resume-screening-gcp-upload.zip'
cd resume-screening
read -s TOKEN      # paste your saved token, press Enter — nothing shows, that is normal
echo ${#TOKEN}     # must print 64
gcloud run deploy resume-screening \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --memory 2Gi --cpu 2 \
  --concurrency 1 --max-instances 3 --min-instances 0 \
  --timeout 120 \
  --set-env-vars SCREENING_TOKEN=$TOKEN
```

> [!IMPORTANT]
> Always pass `--set-env-vars SCREENING_TOKEN=$TOKEN` on every deploy. If you omit it, the token is wiped and Apps Script returns 401 until you redeploy with it.

Verify:

```bash
curl https://YOUR-SERVICE-URL/healthz
# Expected: {"ok":true}
```

Then, **in the test copy of the sheet**, run **Screening → Test connection** and screen one test row for the new role (*Screen selected rows (redo)*) to confirm it is recognised end to end. Do not test on the real sheet: a SELECT sends a real interview invitation. Make sure the **Google Form actually offers** the new option before announcing the role.

| Item | Changes on redeploy? |
|------|---------|
| Service URL | ❌ Same |
| `SCREENING_TOKEN` | ❌ Same, as long as you pass it |
| Apps Script properties | ❌ Never need touching |
| Billing / GCP project setup | ❌ Already done |
| Time to redeploy | ✅ ~3 minutes |

---

### Step 9: Post-launch audit

For the **first 10–20 real candidates** of the new role:

1. Compare system decisions with a human's (see [Section 10](#10-calibrating-the-system)) and share the result with the requester.
2. Look first at **false rejections** — good candidates the system rejected.
3. Fix by adding phrasing to `concepts.py` or widening a group in `RULES`.
4. Any keyword change made here goes through **Steps 6–8 again** (requester approval → test → redeploy).

---

### 8.8 Rules for coding agents

**Do**
- Follow Steps 1–9 in order and show your work: the keyword draft (3.4), the diff, the consistency check output, and the test results.
- Derive keywords from the JD you were given and say which JD line each Basic group came from.
- Use the identical role-name string everywhere.
- Edit `role_requirements.json` and `RULES` together, then run the Step 4e check.
- Prepare the review packet (6.1), show it to the requester, and **stop** until they approve.

**Do not**
- Do not edit `src/evaluator.py`, its constants (30 / 2 / 45 / 0.25), or `BLOCKED_COUNTRIES` as part of adding a role.
- Do not read, print or commit `.env`, `credentials.json`, `token.json` or `SCREENING_TOKEN`.
- Do not add generic words to `concepts.py` classes to make a test pass.
- Do not tune keywords so one specific resume passes — fix phrasing gaps, not individual outcomes.
- Do not deploy, or say a role is live, before the requester has approved.
- Do not assume or record approval yourself — wait for the requester's explicit yes.

**Stop and ask a human when**
- The role name, form option text or JD is missing or ambiguous.
- India-only status is not stated.
- Tests fail and the cause isn't clearly your change.
- A taxonomy change would alter how existing roles behave.
- You are about to deploy.

### 8.9 Changing keywords of an existing role

Same process, shorter: draft the change → show the requester a **before/after** of the keyword lists (and any taxonomy change) → get written approval → re-test on the same resume set used before → redeploy → audit. If you change the group count of one of the four roles listed in `tests/test_pipeline.py`, update `expected_new_roles` there. Never edit a live role without owner sign-off.

### 8.10 Quick checklist

**Inputs**
- [ ] Exact role name (same as the form option text), JD, India-only decision and reviewer note collected
- [ ] Confirmed the role name doesn't already exist

**Build**
- [ ] Role name in the code is identical to the Google Form option text (no Apps Script or service change needed)
- [ ] Drafted Basic groups, good-to-have, essential summary and note — each traceable to the JD
- [ ] Added the role to `role_requirements.json` **and** `RULES` together
- [ ] Added to `INDIA_ONLY_ROLES` only if the requester confirmed (and updated Section 9)
- [ ] Checked every keyword against `src/concepts.py`; added/extended classes only where needed
- [ ] Added a `FORM_TO_ROLE` row only if the form text ≠ role name; the option exists in the Google Form
- [ ] Step 4e consistency check prints `OK`

**Review**
- [ ] Sent the requester the review packet (6.1)
- [ ] Requester approved the **final** list, in writing (6.3)

**Verify & ship**
- [ ] Role shows in the Streamlit dropdown with the right requirements panel
- [ ] Tested strong / borderline / weak / unusual-phrasing resumes
- [ ] Added a sample check to `tests/test_pipeline.py`; all three test files pass
- [ ] If `concepts.py` changed: re-checked 2–3 existing roles
- [ ] Redeployed with `--set-env-vars SCREENING_TOKEN=$TOKEN`
- [ ] `/healthz` OK; **Screening → Test connection** OK; one test row screened for the new role
- [ ] Audit of the first 10–20 candidates scheduled with the requester

---

## 9. Country & Geography Rules

### India-only roles

For these roles the resume **must** contain the word "India" or a phone number starting with `+91`, otherwise the candidate is rejected regardless of skills:

- Graphic Design Intern
- Social Media Intern
- Content Creation Intern
- Sales & Marketing Intern
- AI Video Creation Intern
- Prompt Engineering Intern

Edit the set `INDIA_ONLY_ROLES` in `src/role_config.py` to add or remove roles, and keep this list in sync. Whether a role is India-only is a **business decision for the requester** — confirm it in [Section 8, Step 6](#step-6-approval-required).

**Optional Google Places fallback:** When the simple India/+91 check fails, the app can send only the candidate's address/location text (never name, email, or phone) to Google Places to confirm the country. Enable this by setting `GOOGLE_MAPS_API_KEY` in `.env`. If the API is unreachable, the key is invalid, or the result conflicts, the resume goes to Manual Review (never auto-rejected for that reason). In the sheet runner (`sheet_test.py`) the **City column** is also used to confirm the country.

### Blocked countries (every role)

A resume that mentions **Pakistan** or **Bangladesh** (the word, "Pakistani", "Bangladeshi", or a phone starting with `+92` / `+880`) is **rejected for every role**, regardless of skills and even when extraction warnings exist. Edit `BLOCKED_COUNTRIES` in `src/role_config.py`.

---

## 10. Calibrating the System

After deploying, run the system on 10–20 anonymised resumes for one role (start with AI Engineer Intern). Set your own decision per candidate, download the CSV, and compare `system_decision` vs `human_decision`.

**False rejections** are more important to investigate than false selections. When the system rejects a good candidate:

1. Open their extracted text — confirm the text was extracted cleanly.
2. Look at which Basic requirements were `not found`. Are the terms on the resume but just phrased differently?
3. If yes → add the phrasing to the right class in `src/concepts.py`.
4. If the groupings are too strict → widen a group in `src/role_config.py` by adding an alternative.

After any change to `concepts.py` or `role_config.py`, re-run on the same set of resumes to confirm improvement.

> [!IMPORTANT]
> Any keyword, group or taxonomy change made while calibrating is a change to a role's requirements. Get it approved by the requester and redeploy, following [Section 8](#8-adding-a-new-role--jd) (see 8.9).

---

## 11. Troubleshooting

### Local Streamlit / CLI

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError` | Activate the venv: `source .venv/bin/activate`, then `pip install -r requirements.txt` |
| OCR not working | Install Tesseract: `brew install tesseract` (macOS) or `sudo apt install tesseract-ocr` (Linux); `rapidocr` is installed by `requirements.txt` |
| Score seems wrong | Check the "Extracted text" tab — confirm the text was read correctly before trusting the score |
| `KeyError: '<Role name>'` on startup (app, CLI or sheet runner) | The role is in `role_requirements.json` but not in `RULES` (or spelled differently). Run the Step 4e check |
| Role not in dropdown | It's in `RULES` but not in the JSON, or the JSON is invalid. Run the Step 4e check |
| `KeyError: 'essential'` / `'good_to_have'` | That role in the JSON is missing one of the two required lists |
| New role works locally but the candidate's *Application Screening* cell stays blank | Form option not mapped and not equal to the role name (`map_role()` skips it, so no usable decision comes back and the script retries 4 times, then emails); or the service wasn't redeployed. Check the "Resume Screening Logs" sheet |
| Everyone is rejected for a new role | Too many / too strict groups (see the sizing table, Step 3.3), or typos in keywords |
| Everyone is selected for a new role | Groups too broad, or a generic alias was added to `concepts.py` |
| `test_pipeline.py` shows `SOME CHECKS FAILED` with OCR checks | No OCR engine installed in this environment — install `rapidocr` or Tesseract |

### Cloud Run

| Issue | Fix |
|-------|-----|
| `{"ok":true}` on `/healthz` but `/screen` returns 500 | Check Cloud Run logs in the GCP Console (a startup `KeyError` from a bad role entry would also surface here) |
| `401` from `/screen` with correct token | Token mismatch — redeploy with the same token you have saved |
| Cold start takes > 10 seconds | Normal — the first call after a period of inactivity wakes the container |
| Cost exceeds budget | Reduce `--max-instances`; check Cloud Run metrics for unexpected traffic |

### Apps Script

See the [Apps Script Error Reference](#75-apps-script-error-reference) above.

---

## 12. Important Limits & Warnings

> [!WARNING]
> This system matches keywords and their aliases. It **cannot truly understand** a project description the way a human (or an LLM) can. "Not found" in a report means no matching wording was detected — it does not prove the skill is absent.

> [!IMPORTANT]
> Never use Reject decisions as final without human spot-checks, especially for borderline scores (45–60 range) and for Manual Review cases.

> [!NOTE]
> The scoring thresholds (30% Basic floor, minimum 2 groups, 45 pass line, 0.25 Good-to-have weight) are starting points. Calibrate them against your own reviewed resumes before using in production.

> [!CAUTION]
> Keep `.env`, `credentials.json`, `token.json` and `SCREENING_TOKEN` private at all times. Do not paste the token into chat, emails or shared documents. Do not open Script properties while your screen is shared.

---

*Built for Infineo internship screening. Maintained by the tech team.*

