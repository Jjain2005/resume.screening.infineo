"""Tests for sheet_test.py with a FAKE Google client (no network). Run: python tests/test_sheet_test.py"""
import contextlib, csv, io, sys, tempfile
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pymupdf
import sheet_test as st
from src.role_config import load_roles

ROLES = list(load_roles())
HEADERS = ["Timestamp", "Email address", "Full Name"] + [f"col{i}" for i in range(9)] + [
    "Which internship role are you applying for? (Select all that apply)",
    "What skills make you a strong candidate?", "Supporting Material Resume/CV Upload (PDF Only)", "City"]
FILL = "Interests include reading, travelling and music. " * 8


def pdf(lines):
    d = pymupdf.open(); p = d.new_page(); y = 50
    for l in lines + [FILL[i:i + 90] for i in range(0, len(FILL), 90)]:
        p.insert_text((40, y), l, fontsize=9); y += 12
    return d.tobytes()


FULLSTACK = pdf(["Rajendra Tiwari", "Pune, India", "SKILLS", "HTML, CSS, JavaScript, React.js, Node.js, REST APIs, MongoDB, CRUD",
                 "PROJECTS", "- Built a full stack app with React.js, Node.js, Express.js and MongoDB using REST APIs and CRUD operations.",
                 "- Developed responsive HTML and CSS pages with JavaScript and DOM manipulation."])
PROMPT = pdf(["Vibha Rawariya", "Delhi, India", "SKILLS", "Prompt Engineering, Generative AI, LLMs, ChatGPT, Claude, Token Optimization",
              "PROJECTS", "- Built prompt engineering workflows with ChatGPT and Claude, applying token optimization for LLMs in a generative AI app."])
EGYPT = pdf(["Mahetab Tarek Eid", "+20 1060543098 | Mansoura-Dahlia-Egypt", "SUMMARY", "Creative content creator and social media designer.",
             "EXPERIENCE", "- Created content and social media posts, copywriting and blog articles, with content creation for brands."])
BLOCKED_PROMPT = pdf(["Candidate", "Pune, India", "+92 300 1234567", "SKILLS",
                      "Prompt Engineering, Generative AI, LLMs, ChatGPT, Token Optimization",
                      "PROJECTS", "- Built prompt engineering workflows with ChatGPT for a generative AI app."])
_blank_doc = pymupdf.open()
_blank_doc.new_page()
UNREADABLE = _blank_doc.tobytes()


class Fake:
    def __init__(self, rows, files):
        self.rows, self.files, self.tmp = rows, files, tempfile.mkdtemp()
    def read_header(self): return HEADERS
    def read_rows(self, a, b): return [(r, self.rows[r]) for r in range(a, b + 1) if r in self.rows]
    def download(self, fid, dest, row):
        v = self.files.get(fid)
        if v is None: return None, "HTTP 404: file not found"
        p = Path(self.tmp) / f"row{row}_{fid}.pdf"; p.write_bytes(v); return p, ""


def row(email, name, roles, link, city=""):
    c = [""] * len(HEADERS); c[1], c[2], c[12], c[14], c[15] = email, name, roles, link, city; return c


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name); return cond


def main():
    ok = True
    cols = st.find_columns(HEADERS)
    ok &= check("columns found by header text",
                (cols["name"], cols["role"], cols["resume"], cols["email"], cols["city"]) == (2, 12, 14, 1, 15))
    try: st.find_columns(["a", "b"]); ok &= check("missing columns raise", False)
    except ValueError as e: ok &= check("missing columns raise with headers listed", "Headers found" in str(e))
    ok &= check("drive ids: open?id=", st.parse_drive_ids("https://drive.google.com/open?id=ABC_123-x") == ["ABC_123-x"])
    ok &= check("drive ids: /file/d/", st.parse_drive_ids("https://drive.google.com/file/d/XYZ789/view?usp=sharing") == ["XYZ789"])
    ok &= check("drive ids: uc?export=download&id=", st.parse_drive_ids("https://drive.google.com/uc?export=download&id=QQ1") == ["QQ1"])
    ok &= check("drive ids: two links", st.parse_drive_ids("https://drive.google.com/open?id=A1, https://drive.google.com/open?id=B2") == ["A1", "B2"])
    ok &= check("sheet id from URL", st.parse_sheet_id("https://docs.google.com/spreadsheets/d/1AbC-d_E/edit#gid=0") == "1AbC-d_E")
    s = "Founder's Associate Intern (limited seats available), Prompt Engineering Intern, Website Development Intern"
    ok &= check("roles split outside parentheses", st.split_roles(s) == ["Founder's Associate Intern (limited seats available)", "Prompt Engineering Intern", "Website Development Intern"])
    ok &= check("map: website dev -> Full Stack", st.map_role("Website Development Intern", ROLES) == "Full Stack Developer Intern")
    ok &= check("map: QA -> Software Testing", st.map_role("Software Testing (QA) Intern", ROLES) == "Software Testing Intern")
    ok &= check("map: video creation -> AI Video", st.map_role("Video Creation Intern", ROLES) == "AI Video Creation Intern")
    ok &= check("map: same-name role", st.map_role("Social Media Intern", ROLES) == "Social Media Intern")
    ok &= check("map: founder's associate (curly apostrophe)",
                st.map_role("Founder\u2019s Associate Intern (limited seats available)", ROLES) == "Founder's Associate Intern")
    ok &= check("map: game development form label", st.map_role("Game Development Intern", ROLES) == "Game Developer Intern")
    ok &= check("map: curriculum development form label",
                st.map_role("Curriculum Development Intern", ROLES) == "Curriculum Developer Intern")
    ok &= check("map: DevOps Intern", st.map_role("DevOps Intern", ROLES) == "DevOps Intern")

    rows = {
        361: row("a@x.com", "Rajendra Tiwari", "Website Development Intern", "https://drive.google.com/open?id=F1"),
        362: row("v@x.com", "Vibha Rawariya", "Founder\u2019s Associate Intern (limited seats available), Prompt Engineering Intern", "https://drive.google.com/open?id=F2"),
        363: row("s@x.com", "Sidhanghana", "Video Creation Intern", ""),
        364: row("m@x.com", "Mohd Adnan", "Software Testing (QA) Intern", "https://drive.google.com/open?id=GONE"),
        365: row("t@x.com", "Mahetab Tarek", "Content Creation Intern, Social Media Intern", "https://drive.google.com/open?id=F3"),
        366: row("n@x.com", "No Role", "", "https://drive.google.com/open?id=F1"),
    }
    fake = Fake(rows, {"F1": FULLSTACK, "F2": PROMPT, "F3": EGYPT})
    out = Path(fake.tmp) / "out.csv"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = st.main(["--sheet", "SID", "--from", "361", "--to", "366", "--out", str(out), "--no-places", "--no-color", "--verbose",
                       "--downloads", fake.tmp], client=fake)
    text = buf.getvalue(); print(text)
    by = {(r["sheet_row"], r["mapped_role"] or r["form_role"]): r for r in res}
    ok &= check("run returns results", res is not None and len(res) >= 8)
    ok &= check("361 Full Stack (good Indian resume) screened, basic >= 60 => SELECT", by[(361, "Full Stack Developer Intern")]["decision"] == "SELECT")
    ok &= check("362 Founder's Associate is now screened",
                by[(362, "Founder's Associate Intern")]["decision"] != "SKIPPED")
    ok &= check("362 Prompt Engineering (India) => SELECT", by[(362, "Prompt Engineering Intern")]["decision"] == "SELECT")
    ok &= check("363 no link => ERROR 'no resume link'", by[(363, "AI Video Creation Intern")]["decision"] == "ERROR" and "no resume link" in by[(363, "AI Video Creation Intern")]["note"])
    ok &= check("364 download failure reported, not rejected", by[(364, "Software Testing Intern")]["decision"] == "ERROR" and "404" in by[(364, "Software Testing Intern")]["note"])
    ok &= check("365 Egyptian resume, Content Creation (India-only) => REJECT", by[(365, "Content Creation Intern")]["decision"] == "REJECT")
    ok &= check("365 Egyptian resume, Social Media (India-only) => REJECT", by[(365, "Social Media Intern")]["decision"] == "REJECT")
    ok &= check("366 blank role => SKIPPED", any(r["sheet_row"] == 366 and r["decision"] == "SKIPPED" for r in res))
    ok &= check("summary line printed", "Summary:" in text and "REJECT" in text)
    ok &= check("verbose prints reasons", "reason:" in text and "India check" in text)
    rows_csv = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    ok &= check("CSV written with all rows and human_decision column", len(rows_csv) == len(res) and "human_decision" in rows_csv[0])
    ok &= check("CSV includes City (sheet) and Google result columns",
                "city_sheet" in rows_csv[0] and "city_google" in rows_csv[0])

    city_rows = {
        367: row("a@x.com", "Asha", "Prompt Engineering Intern", "https://drive.google.com/open?id=F2", "Pune"),
        368: row("b@x.com", "Bilal", "Prompt Engineering Intern", "https://drive.google.com/open?id=F2", "Lahore"),
        369: row("c@x.com", "Chetan", "Prompt Engineering Intern", "https://drive.google.com/open?id=F2", "Hyderabad"),
        370: row("d@x.com", "Diya", "Prompt Engineering Intern", "https://drive.google.com/open?id=F2", "Unknown"),
        371: row("e@x.com", "Esha", "Prompt Engineering Intern", "https://drive.google.com/open?id=F2"),
        372: row("f@x.com", "Farah", "Prompt Engineering Intern", "https://drive.google.com/open?id=F3", "Unlisted"),
        373: row("g@x.com", "Gita", "Prompt Engineering Intern", "https://drive.google.com/open?id=F4", "Pune"),
        374: row("h@x.com", "Heena", "Prompt Engineering Intern", "https://drive.google.com/open?id=F5", "Lahore"),
        375: row("i@x.com", "Ira", "Prompt Engineering Intern", "https://drive.google.com/open?id=F2", "ApiError"),
    }
    city_fake = Fake(city_rows, {"F2": PROMPT, "F3": EGYPT, "F4": BLOCKED_PROMPT, "F5": UNREADABLE})
    city_results = Path(city_fake.tmp) / "city_results.csv"
    cities = {"Pune": [{"code": "IN", "name": "India"}],
              "Lahore": [{"code": "PK", "name": "Pakistan"}],
              "Hyderabad": [{"code": "IN", "name": "India"}, {"code": "PK", "name": "Pakistan"}],
              "Unknown": [], "Unlisted": []}
    city_calls = []
    def fake_city_lookup(city, key):
        city_calls.append(city)
        if city == "ApiError":
            raise RuntimeError("simulated Places failure")
        return cities[city]
    with patch("src.places_check.get_api_key", return_value="KEY"):
        city_output = io.StringIO()
        with contextlib.redirect_stdout(city_output):
            city_res = st.main(["--sheet", "SID", "--from", "367", "--to", "375", "--out", str(city_results),
                                "--no-color", "--verbose", "--downloads", city_fake.tmp],
                               client=city_fake, city_lookup=fake_city_lookup)
        city_text = city_output.getvalue()
    city_by_row = {r["sheet_row"]: r for r in city_res}
    ok &= check("single India city passes", city_by_row[367]["decision"] == "SELECT" and
                city_by_row[367]["city_google"] == "India (IN)")
    ok &= check("single non-India city rejects even when resume mentions India",
                city_by_row[368]["decision"] == "REJECT" and city_by_row[368]["city_google"] == "Pakistan (PK)")
    ok &= check("ambiguous city falls back to resume rule",
                city_by_row[369]["decision"] == "SELECT" and city_by_row[369]["city_google"].startswith("Multiple:"))
    ok &= check("no Google result falls back to resume rule",
                city_by_row[370]["decision"] == "SELECT" and city_by_row[370]["city_google"] == "No places found")
    ok &= check("blank City falls back to resume rule",
                city_by_row[371]["decision"] == "SELECT" and city_by_row[371]["city_google"] == "City blank; resume fallback")
    ok &= check("no result and no India/+91 in resume rejects",
                city_by_row[372]["decision"] == "REJECT" and city_by_row[372]["city_google"] == "No places found")
    ok &= check("Pakistan phone rule still rejects even when sheet city is in India",
                city_by_row[373]["decision"] == "REJECT" and city_by_row[373]["blocked_country"] == "Pakistan")
    ok &= check("single non-India city rejects even when resume extraction warns",
                city_by_row[374]["decision"] == "REJECT" and bool(city_by_row[374]["warnings"]))
    ok &= check("Google lookup errors require Manual Review",
                city_by_row[375]["decision"] == "MANUAL REVIEW" and "Lookup error" in city_by_row[375]["city_google"])
    ok &= check("terminal includes City (sheet) and Google result",
                "City (sheet)" in city_text and "Pune" in city_text and "India (IN)" in city_text)
    city_csv = list(csv.DictReader(open(city_results, encoding="utf-8-sig")))
    ok &= check("CSV preserves sheet city and Google result",
                city_csv[0]["city_sheet"] == "Pune" and city_csv[0]["city_google"] == "India (IN)")
    ok &= check("Pakistan/Bangladesh checks remain independent and active",
                city_by_row[373]["blocked_country"] == "Pakistan")
    # argument validation
    for bad in (["--sheet", "S", "--from", "1", "--to", "5"], ["--sheet", "S", "--from", "9", "--to", "5"]):
        try:
            with contextlib.redirect_stderr(io.StringIO()): st.parse_args(bad)
            ok &= check("bad range rejected", False)
        except SystemExit:
            ok &= check("bad range rejected: " + " ".join(bad[2:]), True)
    print("\nALL PASSED" if ok else "\nSOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
