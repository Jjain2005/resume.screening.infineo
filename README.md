# Fixed-Role Resume Screening — Version 1 (local only)

No LLM, no API key, no network. Select one of the 14 fixed internship roles, upload resumes, get a
score, requirement-level evidence and a Select / Manual Review / Reject recommendation.

## Run
```bash
cd fixed_role_resume_screening_v1
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
brew install tesseract          # Mac (OCR for scanned PDFs/images). Linux: sudo apt install tesseract-ocr
streamlit run app.py
```
Without Tesseract, text PDFs/DOCX still work; scanned pages are flagged and sent to Manual Review.

Self-test (builds sample text/two-column/scanned/DOCX/blank resumes): `python tests/test_pipeline.py`
Batch without UI: `python run_cli.py "AI Engineer Intern" path/to/resumes/ results.csv`

## How extraction works (src/resume_reader.py)
- **PDF**: every page via PyMuPDF; two-column/sidebar layouts are re-ordered (left column then right); hyperlinks
  (GitHub/LinkedIn) are added. A page is OCR'd (Tesseract, 250 dpi) when it has almost no text, is mostly an image,
  or its text layer is garbled. OCR confidence is recorded.
- **DOCX**: paragraphs and tables in document order, merged cells de-duplicated, text boxes, headers/footers,
  and OCR of embedded images if the file is mostly pictures.
- **Images (PNG/JPG)** via OCR, **TXT** directly. Legacy `.doc` is rejected with a clear message.
- **Warnings → forced Manual Review**: unreadable/blank page, password-protected PDF, garbled text, very little text,
  low OCR confidence, OCR needed but unavailable.
- The app shows the full extracted text per file, page by page — check it before trusting a score.

## How evaluation works (src/evaluator.py, src/role_config.py, src/concepts.py)
1. **Basic requirements first.** Each role has a fixed list of basic requirements (`role_config.py`).
   A requirement is met when ANY of its listed alternatives is found ANYWHERE in the whole resume
   (e.g. "any of: Python, Java, JavaScript, Node.js, PHP"). Lines with a single item need that item.
   **Basic % = requirements met / total requirements.**
2. **Decision uses Basic % only:** >= 60% Select · 40-59% Manual Review · < 40% Reject
   (never auto-reject if the resume describes role-relevant project/experience work; sliders change the numbers).
   Any extraction warning forces Manual Review.
3. **Good-to-have = bonus only.** Normalised: bonus = 10 x min(1, found / target) (target = 25% of the list, min 3).
   Final score = Basic % + bonus (max 100) for ranking. A bonus can never turn a failing basic score into Select.
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

**Other countries (same 6 roles).** The candidate's country is judged from the **contact/address area only**: the header
(up to the first section heading), an "Address:/Location:" value, and lines under a lone "Address" label. Projects,
certificates, jobs and the summary are ignored, so "Deloitte Australia" in a certificate or "UK curriculum" in a job does not reject
an Indian candidate.
- contact area shows India (the word or a +91 phone) and no other country -> accepted (then skills decide)
- contact area shows another country (name, USA/UK/UAE, or a non-+91 phone) and no India -> **REJECT**
- contact area shows both -> **MANUAL REVIEW**
Other countries found elsewhere are only listed in the Report tab as information. Lists: `OTHER_COUNTRY_NAMES` /
`OTHER_COUNTRY_ABBREVIATIONS` in `src/role_config.py`. Test: `python tests/test_other_country.py`.

### Optional: Google Places country confirmation (India-only roles)
Runs only when the simple rule fails (no "India", no +91). It sends ONLY address/location text (never name, e-mail,
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

## Important limits
- The reference PDF does not define approved minimum criteria or thresholds. The 60/40 thresholds, the grouping of
  requirements in `role_config.py` and the bonus size are **provisional**; calibrate on human-labelled resumes.
- Without an LLM this cannot truly understand a project description; it relies on terms, aliases and statement
  structure. "Not found" never proves a skill is absent. Don't use Reject decisions without human spot-checks.

## Calibrating
Upload 10–20 anonymized resumes for ONE role (start with AI Engineer Intern), set "Your decision" per candidate,
download the CSV, and compare `system_decision` vs `human_decision`. Inspect false rejections first; fix by adding
aliases in `concepts.py` or adjusting groups in `role_config.py`.
