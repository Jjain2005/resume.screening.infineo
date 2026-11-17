"""Test runner: Google Sheet rows -> Drive resumes -> existing screening -> terminal table + CSV.

  python sheet_test.py --sheet <SHEET_ID_or_URL> --from 361 --to 380 [--tab "Form responses 1"] [--verbose]

Reads Full Name / internship role(s) / resume link from the sheet (Sheets API), downloads each resume (Drive API),
and runs the existing screening (src/) once per mapped role. Nothing in src/ is changed by this script.
"""
import argparse
import csv
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly",
          "https://www.googleapis.com/auth/drive.readonly"]

# ---- form option (normalised) -> our role. Edit here. Options not listed here but equal to one of our 18 role names
# ---- names map to themselves; anything else is "skipped" (no criteria defined).
FORM_TO_ROLE = {
    "website development intern": "Full Stack Developer Intern",
    "software testing (qa) intern": "Software Testing Intern",
    "video creation intern": "AI Video Creation Intern",
    "automation intern": "Automation Intern",
    "prompt engineering intern": "Prompt Engineering Intern",
    "content creation intern": "Content Creation Intern",
    "social media intern": "Social Media Intern",
    "founder's associate intern": "Founder's Associate Intern",
    "devops intern": "DevOps Intern",
    "game development intern": "Game Developer Intern",
    "game developer intern": "Game Developer Intern",
    "curriculum development intern": "Curriculum Developer Intern",
    "curriculum developer intern": "Curriculum Developer Intern",
}

MIME_EXT = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt", "image/png": "png", "image/jpeg": "jpg",
}
GOOGLE_DOC = "application/vnd.google-apps.document"


# =========================================================================== pure helpers
def norm_header(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def find_columns(headers):
    """Return column indexes; email and city are optional."""
    hs = [norm_header(h) for h in headers]

    def first(*needles):
        for i, h in enumerate(hs):
            if any(n in h for n in needles):
                return i
        return None

    cols = dict(name=first("full name"), role=first("internship role"), resume=first("resume"),
                email=first("email"), city=first("city"))
    missing = [k for k in ("name", "role", "resume") if cols[k] is None]
    if missing:
        raise ValueError("Could not find column(s): " + ", ".join(missing) +
                         ". Headers found: " + " | ".join(h for h in headers if h))
    return cols


def parse_sheet_id(value):
    m = re.search(r"/spreadsheets/d/([\w-]+)", value or "")
    return m.group(1) if m else (value or "").strip()


def parse_drive_ids(cell):
    """All Drive file ids in a cell (links separated by commas/spaces/newlines)."""
    ids = []
    for chunk in re.split(r"[,\s]+", cell or ""):
        if not chunk:
            continue
        m = re.search(r"[?&]id=([\w-]+)", chunk) or re.search(r"/d/([\w-]+)", chunk)
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def split_roles(cell):
    """Split a multi-select cell on commas that are not inside parentheses."""
    return [p.strip() for p in re.split(r",\s*(?![^()]*\))", cell or "") if p.strip()]


def _norm_option(opt):
    s = opt.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = re.sub(r"\(limited seats available\)", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()


def map_role(option, valid_roles):
    """form option -> our role name, or None when no criteria exist."""
    n = _norm_option(option)
    if n in FORM_TO_ROLE:
        return FORM_TO_ROLE[n]
    for r in valid_roles:
        if r.lower() == n:
            return r
    return None


def read_env(name, env_file=None):
    path = Path(env_file) if env_file else ROOT / ".env"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == name and v.strip().strip('"').strip("'"):
                    return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return os.environ.get(name, "").strip()


# =========================================================================== Google access
class GoogleClient:
    """All real Google calls live here (so tests can inject a fake with the same three methods)."""

    def __init__(self, sheet_id, tab=None, credentials="credentials.json", token="token.json", service_account=None):
        from googleapiclient.discovery import build
        creds = self._credentials(credentials, token, service_account)
        self.sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self.drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.sheet_id = sheet_id
        self.tab = tab

    @staticmethod
    def _credentials(credentials, token, service_account):
        if service_account:
            from google.oauth2.service_account import Credentials as SA
            return SA.from_service_account_file(service_account, scopes=SCOPES)
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        creds = None
        if Path(token).exists():
            creds = Credentials.from_authorized_user_file(token, SCOPES)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path(credentials).exists():
                raise FileNotFoundError(f"{credentials} not found. See README: create an OAuth 'Desktop app' client "
                                        "in Google Cloud and save its JSON as credentials.json")
            creds = InstalledAppFlow.from_client_secrets_file(credentials, SCOPES).run_local_server(port=0)
        Path(token).write_text(creds.to_json())
        return creds

    def _tab(self):
        if not self.tab:
            meta = self.sheets.spreadsheets().get(spreadsheetId=self.sheet_id,
                                                  fields="sheets.properties.title").execute(num_retries=3)
            self.tab = meta["sheets"][0]["properties"]["title"]
        return "'" + self.tab.replace("'", "''") + "'"

    def _values(self, rng):
        res = self.sheets.spreadsheets().values().get(spreadsheetId=self.sheet_id, range=rng).execute(num_retries=3)
        return res.get("values", [])

    def read_header(self):
        rows = self._values(f"{self._tab()}!1:1")
        return rows[0] if rows else []

    def read_rows(self, a, b):
        """Rows a..b (sheet row numbers). Returns list of (row_number, cells) for rows that exist."""
        vals = self._values(f"{self._tab()}!{a}:{b}")
        return [(a + i, r) for i, r in enumerate(vals)]

    def download(self, file_id, dest_dir, row):
        """Return (path, error). Reuses an already downloaded file; never raises for per-file problems."""
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaIoBaseDownload
        dest_dir = Path(dest_dir); dest_dir.mkdir(parents=True, exist_ok=True)
        for old in dest_dir.glob(f"*_{file_id}.*"):
            if old.stat().st_size > 0:
                return old, ""
        try:
            meta = self.drive.files().get(fileId=file_id, fields="id,name,mimeType",
                                          supportsAllDrives=True).execute(num_retries=3)
            mime = meta.get("mimeType", "")
            if mime == GOOGLE_DOC:
                request = self.drive.files().export_media(fileId=file_id, mimeType="application/pdf")
                ext = "pdf"
            else:
                ext = MIME_EXT.get(mime) or (Path(meta.get("name", "")).suffix.lower().lstrip(".") or "")
                if ext == "jpeg":
                    ext = "jpg"
                if ext not in ("pdf", "docx", "txt", "png", "jpg"):
                    return None, f"unsupported file type ({mime or 'unknown'})"
                request = self.drive.files().get_media(fileId=file_id, supportsAllDrives=True)
            path = dest_dir / f"row{row}_{file_id}.{ext}"
            with open(path, "wb") as fh:
                dl = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = dl.next_chunk(num_retries=3)
            return path, ""
        except HttpError as exc:
            code = getattr(exc.resp, "status", "?")
            why = {403: "no permission (open the file with the account you logged in with)",
                   404: "file not found"}.get(code, "Drive error")
            return None, f"HTTP {code}: {why}"
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"


# =========================================================================== output
COLORS = {"SELECT": "\033[32m", "MANUAL REVIEW": "\033[33m", "REJECT": "\033[31m", "SKIPPED": "\033[90m",
          "ERROR": "\033[35m"}
RESET = "\033[0m"


def clip(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n - 1] + "…"


def fmt_line(r, color):
    dec = r["decision"]
    dec_txt = f"{dec:<13}"
    if color and dec in COLORS:
        dec_txt = COLORS[dec] + dec_txt + RESET
    if r["basic_pct"] == "":
        nums = f"{'-':>6} {'-':>6} {'-':>5}"
    else:
        nums = f"{r['basic_pct']:>5.1f}% {r['good_pct']:>5.1f}% {r['final_score']:>5.1f}"
    return (f"{r['sheet_row']:>4}  {clip(r['name'], 22):<22}  {clip(r['mapped_role'] or r['form_role'], 28):<28}  "
            f"{clip(r.get('city_sheet', ''), 16):<16}  {dec_txt}  {nums}  "
            f"{clip(r.get('city_google', ''), 30):<30}  {clip(r['note'], 45)}")


HEADER = (f"{'Row':>4}  {'Name':<22}  {'Role (mapped)':<28}  {'City (sheet)':<16}  {'Decision':<13}  "
          f"{'Basic':>6} {'Good %':>6} {'Score':>5}  {'Google found':<30}  Note")


# =========================================================================== main
def blank_result(row, name, email, form_role, mapped, decision, note, link="", local="", status="", city="",
                 city_google=""):
    return dict(sheet_row=row, name=name, email=email, form_role=form_role, mapped_role=mapped or "", decision=decision,
                basic_pct="", basic_met="", bonus="", final_score="", missing_basic="", india_check="",
                blocked_country="", warnings="", reason="", resume_link=link, local_file=str(local or ""),
                status=status or decision.lower(), human_decision="", note=note, city_sheet=city, good_pct="",
                city_google=city_google)


def run(args, client, city_lookup=None):
    from src.resume_reader import extract_bytes
    from src.evaluator import evaluate, classify
    from src.role_config import load_roles
    from src.places_check import get_api_key, lookup_city_countries

    roles = load_roles()
    key = "" if args.no_places else get_api_key()
    city_lookup = city_lookup or lookup_city_countries
    color = sys.stdout.isatty() and not args.no_color

    headers = client.read_header()
    cols = find_columns(headers)
    rows = client.read_rows(args.row_from, args.row_to)
    print(f"Sheet rows {args.row_from}-{args.row_to}: {len(rows)} row(s) read | Google Places: {'ON' if key else 'OFF'} "
          f"| columns: name='{headers[cols['name']][:30]}', role='{headers[cols['role']][:30]}', "
          f"resume='{headers[cols['resume']][:30]}', city="
          f"'{headers[cols['city']][:30] if cols['city'] is not None else 'not found'}'")
    print()
    print(HEADER)
    print("-" * 190)

    def cell(cells, i):
        return (cells[i] if i is not None and i < len(cells) else "").strip()

    results = []

    def emit(r):
        results.append(r)
        print(fmt_line(r, color))
        if args.verbose:
            for label, val in (("reason", r["reason"]), ("missing basic", r["missing_basic"]),
                               ("India check", r["india_check"]), ("warnings", r["warnings"])):
                if val:
                    print(f"{'':>6}↳ {label}: {val}")

    for row_no, cells in rows:
        name = cell(cells, cols["name"]) or "(no name)"
        email = cell(cells, cols["email"])
        role_cell = cell(cells, cols["role"])
        link_cell = cell(cells, cols["resume"])
        city_cell = cell(cells, cols["city"])
        options = split_roles(role_cell)
        mapped = [(o, map_role(o, roles)) for o in options]
        if not options:
            emit(blank_result(row_no, name, email, "", "", "SKIPPED", "no role selected",
                              link_cell, city=city_cell))
            continue
        runnable = [(o, m) for o, m in mapped if m]
        for o, m in mapped:
            if not m:
                emit(blank_result(row_no, name, email, o, "", "SKIPPED", "no criteria defined for this role",
                                  link_cell, city=city_cell))
        if not runnable:
            continue
        ids = parse_drive_ids(link_cell)
        if not ids:
            for o, m in runnable:
                emit(blank_result(row_no, name, email, o, m, "ERROR", "no resume link in the sheet row",
                                  link_cell, status="no resume link", city=city_cell))
            continue
        path, err = client.download(ids[0], args.downloads, row_no)
        if err or path is None:
            for o, m in runnable:
                emit(blank_result(row_no, name, email, o, m, "ERROR", "could not download: " + err,
                                  link_cell, status="download failed", city=city_cell))
            continue
        ex = extract_bytes(Path(path).name, Path(path).read_bytes())
        extra = f" (+{len(ids) - 1} more file(s) ignored)" if len(ids) > 1 else ""
        for o, m in runnable:
            ev = evaluate(ex["text"], m, roles)
            city_google = ""
            city_country_reject = False
            if ev["india_only"]:
                resume_india = ev["india"]
                if not city_cell:
                    city_google = "City blank; resume fallback"
                    ev["india"] = {**resume_india,
                                   "evidence": f"City column is blank; resume check {resume_india['evidence']}",
                                   "method": "resume_fallback"}
                elif not key:
                    city_google = "Not checked; resume fallback"
                    ev["india"] = {**resume_india,
                                   "evidence": f"Google Places unavailable; resume check {resume_india['evidence']}",
                                   "method": "resume_fallback"}
                else:
                    try:
                        countries = city_lookup(city_cell, key)
                    except Exception as exc:
                        city_google = f"Lookup error: {type(exc).__name__}"
                        ev["india"] = dict(passed=False,
                                           evidence=f'Google Places lookup failed for city "{city_cell}" ({type(exc).__name__}: {exc})',
                                           method="sheet_city", error=True, tried=[])
                    else:
                        country_summary = ", ".join(f"{c['name']} ({c['code']})" for c in countries)
                        if len(countries) == 1:
                            country = countries[0]
                            city_google = country_summary
                            passed = country["code"] == "IN"
                            city_country_reject = not passed
                            ev["india"] = dict(passed=passed,
                                               evidence=f'Google Places found {country["name"]} ({country["code"]}) for city "{city_cell}"',
                                               method="sheet_city", error=False, tried=[])
                        else:
                            city_google = f"Multiple: {country_summary}" if countries else "No places found"
                            fallback = "passed" if resume_india["passed"] else "failed"
                            city_reason = "multiple countries found" if countries else "Google found no place"
                            ev["india"] = {**resume_india,
                                           "evidence": f'{city_reason} for city "{city_cell}"; resume check {fallback}: {resume_india["evidence"]}',
                                           "method": "resume_fallback"}
            decision, reason = classify(ev, ex["warnings"])
            if city_country_reject and not ev["blocked"]["rejected"]:
                decision = "REJECT"
                reason = "India-only role: " + ev["india"]["evidence"] + \
                    ". Rejected based on the single non-India country found for the sheet city."
            india = ev["india"]["evidence"] if ev["india_only"] else ""
            note = (("extraction warning: " + ex["warnings"][0]) if ex["warnings"] else "")
            if ev["blocked"]["rejected"]:
                note = f"blocked country: {ev['blocked']['country']}"
            elif ev["india_only"] and not ev["india"]["passed"] and decision == "REJECT":
                note = "India check failed"
            elif ev["india_only"] and ev["india"]["passed"]:
                note = (note + "; " if note else "") + "India check ok"
            emit(dict(sheet_row=row_no, name=name, email=email, form_role=o, mapped_role=m, decision=decision,
                      basic_pct=ev["basic_pct"], basic_met=f"{ev['basic_met']}/{ev['basic_total']}", bonus=ev["bonus"],
                      good_pct=ev["good_pct"], good_matched=f"{ev['good_found']}/{ev['good_total']}",
                      final_score=ev["score"], missing_basic="; ".join(ev["missing"]), india_check=india,
                      blocked_country=ev["blocked"]["country"], warnings=" | ".join(ex["warnings"]), reason=reason,
                      resume_link=link_cell, local_file=str(path), status="screened", human_decision="",
                      city_sheet=city_cell, city_google=city_google, note=(note + extra).strip()))

    # ---- summary + CSV
    n = lambda d: sum(r["decision"] == d for r in results)
    print("-" * 190)
    print(f"Summary: {len(rows)} row(s) | {sum(r['status'] == 'screened' for r in results)} candidate-role pair(s) screened: "
          f"{n('SELECT')} SELECT, {n('MANUAL REVIEW')} MANUAL REVIEW, {n('REJECT')} REJECT | "
          f"{n('SKIPPED')} skipped | {n('ERROR')} could not be screened (no link / download problem)")
    fields = ["sheet_row", "name", "email", "form_role", "mapped_role", "decision", "basic_pct", "basic_met", "bonus",
              "good_pct", "good_matched", "final_score", "missing_basic", "city_sheet", "city_google", "india_check", "blocked_country",
              "warnings", "reason", "resume_link",
              "local_file", "status", "human_decision"]
    with open(args.out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)
    print(f"Saved {args.out}")
    return results


def parse_args(argv):
    ap = argparse.ArgumentParser(description="Screen resumes of Google Sheet rows (Sheets + Drive API).")
    ap.add_argument("--sheet", help="Sheet ID or full URL (or set SHEET_ID in .env)")
    ap.add_argument("--from", dest="row_from", type=int, required=True, help="first sheet row (>= 2)")
    ap.add_argument("--to", dest="row_to", type=int, required=True, help="last sheet row")
    ap.add_argument("--tab", help="tab name (default: first tab)")
    ap.add_argument("--credentials", default=str(ROOT / "credentials.json"))
    ap.add_argument("--token", default=str(ROOT / "token.json"))
    ap.add_argument("--service-account", help="service account JSON (instead of the browser login)")
    ap.add_argument("--downloads", default=str(ROOT / "downloads"))
    ap.add_argument("--out", default="sheet_test_results.csv")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--no-places", action="store_true", help="do not use Google Places even if a key is in .env")
    args = ap.parse_args(argv)
    if args.row_from < 2:
        ap.error("--from must be >= 2 (row 1 is the header)")
    if args.row_to < args.row_from:
        ap.error("--to must be >= --from")
    args.sheet_id = parse_sheet_id(args.sheet or read_env("SHEET_ID"))
    if not args.sheet_id:
        ap.error("give --sheet <id or url> (or put SHEET_ID=... in .env)")
    return args


def main(argv=None, client=None, city_lookup=None):
    args = parse_args(argv)
    try:
        if client is None:
            client = GoogleClient(args.sheet_id, args.tab, args.credentials, args.token, args.service_account)
        return run(args, client, city_lookup)
    except (ValueError, FileNotFoundError) as exc:
        print("ERROR:", exc)
        return None
    except Exception as exc:  # e.g. Google auth / sheet permission problems
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return None


if __name__ == "__main__":
    sys.exit(0 if main() is not None else 1)
