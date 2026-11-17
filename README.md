# Fixed-Role Resume Screening — Version 1 (local only)

No LLM, no API key, no network. Select one of the 18 fixed internship roles, upload resumes, get a
score, requirement-level evidence and a Select / Manual Review / Reject recommendation.

## Run
```bash
cd fixed_role_resume_screening_v1
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```
RapidOCR is installed with the Python requirements and runs locally for scanned PDFs, images, and embedded DOCX images.
Tesseract is optional; when installed, it is preferred and provides OCR confidence measurements. On macOS, install it
with `brew install tesseract`; on Linux, use `sudo apt install tesseract-ocr`.

Self-test (builds sample text/two-column/scanned/DOCX/blank resumes): `python tests/test_pipeline.py`
Batch without UI: `python run_cli.py "AI Engineer Intern" path/to/resumes/ results.csv`

## How extraction works (src/resume_reader.py)
- **PDF**: every page via PyMuPDF; two-column/sidebar layouts are re-ordered (left column then right); hyperlinks
  (GitHub/LinkedIn) are added. A page is OCR'd (250 dpi) when it has almost no text, is mostly an image,
  or its text layer is garbled. OCR confidence is recorded by either engine.
- **DOCX**: paragraphs and tables in document order, merged cells de-duplicated, text boxes, headers/footers,
  and OCR of embedded images if the file is mostly pictures.
- **Images (PNG/JPG)** via RapidOCR or Tesseract, **TXT** directly. Legacy `.doc` is rejected with a clear message.
- **Warnings → forced Manual Review**: unreadable/blank page, password-protected PDF, garbled text, very little text,
  low OCR confidence, OCR needed but unavailable or failed.
- The app shows the full extracted text per file, page by page — check it before trusting a score.

## How evaluation works (src/evaluator.py, src/role_config.py, src/concepts.py)
1. **Basic requirements first.** Each role has a fixed list of basic requirements (`role_config.py`).
   A requirement is met when ANY of its listed alternatives is found ANYWHERE in the whole resume
   (e.g. "any of: Python, Java, JavaScript, Node.js, PHP"). Lines with a single item need that item.
   **Basic % = requirements met / total requirements.**
2. **Decision floor:** Reject if Basic % is below 30% or fewer than two Basic requirements are matched.
3. **Score:** Good-to-have % = matched good-to-have items / total good-to-have items. Score =
   Basic % + 0.25 × Good-to-have %. Select at score >= 45; otherwise Reject. Percentages and score are
   calculated before display rounding. Extraction warnings force Manual Review.
4. **Equivalent wording counts** (`concepts.py`, bidirectional): LLM = Large Language Models, RAG = Retrieval-Augmented
   Generation, FAISS/Pinecone/Chroma = Vector Databases, OpenAI API/function calling = LLM APIs, etc.
5. **Evidence level** is shown for every requirement: demonstrated (in a project/experience or with an action verb),
   coursework/certificate, mentioned, or listed only. It does not change Basic %, but the report counts how many
   basics are "only listed" so you can spot keyword stuffing.

## India-only roles
For Graphic Design, Social Media, Content Creation, Sales & Marketing, AI Video Creation and Prompt Engineering interns
the resume must contain the word "India" or a phone number starting with +91 anywhere in the text; otherwise it is
REJECTED regardless of skills. Edit the list in `src/role_config.py` (`INDIA_ONLY_ROLES`). Extraction warnings still
force Manual Review first, because the text may be incomplete.

### Optional: Google Places country confirmation in the app (India-only roles)
In the Streamlit app, this runs only when the simple rule fails (no "India", no +91). It sends ONLY address/location text (never name, e-mail,
phone, URLs or the resume) to Google: Text Search with field mask `places.id` (IDs Only SKU), then Place Details with
`addressComponents` (Essentials SKU). Result IN = pass; another country, nothing found, or no readable address = REJECT.
If Google cannot be reached or the key is rejected, the resume goes to MANUAL REVIEW (not reject).
Setup: create a Google Maps Platform key, enable "Places API (New)", then open the `.env` file in the project folder
and paste the key after `GOOGLE_MAPS_API_KEY=` (no quotes, no spaces). Save and refresh the browser. Empty key = simple
rule only. Keep `.env` private (never upload or share it). The sidebar only shows whether a key was found.
Free monthly caps and prices change: check Google's pricing page. Tests use a fake transport: `python tests/test_places.py`.

## Blocked countries (every role)
A resume showing **Pakistan** or **Bangladesh** is REJECTED for every role, regardless of skills: the resume contains
the country word (or "Pakistani"/"Bangladeshi", e.g. in a Nationality line) or a phone number starting with +92 / +880.
Edit `BLOCKED_COUNTRIES` in `src/role_config.py`. This rule runs BEFORE the extraction-warning rule: a country that was actually found is
positive evidence, so unreadable pages elsewhere cannot undo it.

## Test straight from the Google Sheet (sheet_test.py)
Reads candidate rows from the Form-responses sheet (Sheets API), downloads each resume from Drive (Drive API), screens it
with the existing code, and prints a table (also saved as `sheet_test_results.csv`).

One-time Google setup (same Google Cloud project as your Places key is fine):
1. APIs & Services -> Library: enable **Google Sheets API** and **Google Drive API**.
2. APIs & Services -> OAuth consent screen: type External, add your own Google account under **Test users**.
3. APIs & Services -> Credentials -> Create credentials -> **OAuth client ID** -> Application type **Desktop app** -> download the JSON,
   rename it `credentials.json` and put it in the project folder (an API key alone cannot read private sheets/files).
4. First run opens a browser: log in with an account that can open the sheet AND the resume files (e.g. the form owner), click
   Allow (read-only). If it says "Google hasn't verified this app": Advanced -> Continue. A `token.json` is saved for next time.

Run (row numbers are the real sheet row numbers; row 1 is the header):
```bash
python sheet_test.py --sheet "<sheet URL or ID>" --from 361 --to 380
python sheet_test.py --sheet "<sheet URL or ID>" --from 361 --to 380 --verbose --tab "Form responses 1"
```
Optional: put `SHEET_ID=...` in `.env` to skip `--sheet`. Flags: `--no-places`, `--no-color`, `--out file.csv`,
`--service-account key.json` (instead of the browser login; share the sheet and files with that account).

Columns are found by header text (`Full Name`, `...internship role...`, `...Resume...`). Form role -> our role mapping is the
`FORM_TO_ROLE` dict at the top of `sheet_test.py` (Website Development -> Full Stack, Software Testing (QA) -> Software Testing,
Video Creation -> AI Video Creation, Game Development -> Game Developer, Curriculum Development -> Curriculum Developer).
Founder's Associate, DevOps, Game Developer, and Curriculum Developer now have screening criteria.
A candidate who picked several roles gets one line per role. Missing link / no permission / file not found are shown as ERROR
(never silently counted as a reject). Downloads are cached in `downloads/`.
Tests (fake Google, no network): `python tests/test_sheet_test.py`

### India-only country check in the sheet runner
`sheet_test.py` reads the candidate's `City` column and sends only that city value to Google Places; it does not search
resume address/contact lines. Results are cached in memory by city for the duration of the run. The Places text search
requests up to 10 matching places and checks their country details.

- A single country result passes only for India; a single non-India country rejects.
- Multiple countries or no matching places use the resume fallback: the resume must contain "India" or a +91 phone number.
- A blank or missing City column uses the same resume fallback.
- A Google/API error results in Manual Review. `--no-places` or no API key uses the resume fallback.
- The terminal output and CSV include `City (sheet)` / `city_sheet` and Google's result / `city_google`.
- The Pakistan/Bangladesh rejection rule still applies to every role, including India-only roles.

## Important limits
- The basic requirement groupings in `role_config.py` and current decision thresholds should still be calibrated
  against human-reviewed resumes.
- Without an LLM this cannot truly understand a project description; it relies on terms, aliases and statement
  structure. "Not found" never proves a skill is absent. Don't use Reject decisions without human spot-checks.

## Calibrating
Upload 10–20 anonymized resumes for ONE role (start with AI Engineer Intern), set "Your decision" per candidate,
download the CSV, and compare `system_decision` vs `human_decision`. Inspect false rejections first; fix by adding
aliases in `concepts.py` or adjusting groups in `role_config.py`.
