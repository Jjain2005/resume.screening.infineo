"""Places-check tests with a FAKE Google transport (no network, no API key needed).
Run from the project folder:  python tests/test_places.py"""
import sys
import urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import places_check
from src.places_check import make_checker, location_queries
from src.evaluator import evaluate, classify
from src.role_config import load_roles

ROLES = load_roles()
BODY = ("SKILLS\nCanva, Photoshop, Figma, Instagram, Content Creation, Copywriting, Digital Marketing, Video Editing, Reels\n"
        "PROJECTS\n- Designed posters, logos and banners in Canva and Photoshop for a college fest.\n"
        + "Interests: reading and travel. " * 12)
CALLS = []


def fake(country_by_text):
    """transport: place text -> (id) -> country code; unknown text -> no result."""
    def transport(method, url, headers, body=None, timeout=10):
        CALLS.append((method, url, headers.get("X-Goog-FieldMask"), body))
        if method == "POST":
            q = body["textQuery"]
            for key in country_by_text:
                if key.lower() in q.lower():
                    return {"places": [{"id": "ID::" + key}]}
            return {}
        key = url.split("/places/")[1].split("?")[0].replace("ID::", "")
        code, name = country_by_text[key]
        return {"addressComponents": [{"types": ["locality"], "longText": "x", "shortText": "x"},
                                      {"types": ["country", "political"], "longText": name, "shortText": code}]}
    return transport


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    return cond


def run(head, transport, role="Graphic Design Intern"):
    places_check._cache.clear(); CALLS.clear()
    ev = evaluate(head + BODY, role, ROLES, make_checker("KEY", transport))
    return ev, classify(ev, [])[0]


def main():
    ok = True
    tr = fake({"Nagpur": ("IN", "India"), "Toronto": ("CA", "Canada")})
    ev, dec = run("Priya Singh\npriya@example.com | 98765 43210\nNagpur, Maharashtra 440001\n", tr)
    ok &= check("city-only Indian address, no 'India'/+91 -> Places says IN -> not rejected", dec != "REJECT" and ev["india"]["method"] == "places")
    ev, dec = run("Priya Singh\npriya@example.com\nAddress: Toronto, Ontario\n", tr)
    ok &= check("Places says Canada -> REJECT", dec == "REJECT" and "Canada" in ev["india"]["evidence"])
    ev, dec = run("Priya Singh\npriya@example.com\n", tr)
    ok &= check("no readable address and no India/+91 -> REJECT (no API call)", dec == "REJECT" and not CALLS)
    ev, dec = run("Priya Singh\nAddress: Atlantis Lane\n", tr)
    ok &= check("Google finds nothing -> REJECT", dec == "REJECT")
    ev, dec = run("Priya Singh\nPune, India\n", tr)
    ok &= check("simple rule passes -> Google never called", dec != "REJECT" and not CALLS)
    ev, dec = run("Priya Singh\nNagpur\n", tr, role="Python Developer Intern")
    ok &= check("role not India-only -> Google never called", not CALLS and not ev["india_only"])
    tr2 = fake({"Toronto": ("CA", "Canada"), "Bombay": ("IN", "India")})
    ev, dec = run("John Smith\nAddress: Toronto, Ontario\nB.Tech, IIT Bombay\n", tr2)
    ok &= check("address abroad + Indian university line -> NOT passed (Manual Review, never Select)",
                dec == "MANUAL REVIEW" and not ev["india"]["passed"])
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 403, "forbidden", {}, None)
    ev, dec = run("Priya Singh\nAddress: Nagpur\n", boom)
    ok &= check("API error (403) -> MANUAL REVIEW, not reject", dec == "MANUAL REVIEW" and ev["india"]["error"])
    def offline(*a, **k):
        raise OSError("network unreachable")
    ev, dec = run("Priya Singh\nAddress: Nagpur\n", offline)
    ok &= check("no internet -> MANUAL REVIEW", dec == "MANUAL REVIEW")
    # privacy: only place text is sent
    run("Priya Singh\npriya@example.com | +1 415 555 0100 | linkedin.com/in/priya\nNagpur, Maharashtra\n", tr)
    sent = " ".join(str(c[3]) for c in CALLS if c[3])
    ok &= check("name, e-mail, phone and URL are NOT sent to Google",
                not any(x in sent for x in ("Priya", "example.com", "415", "linkedin")))
    ok &= check("field masks are places.id then addressComponents",
                [c[2] for c in CALLS][:2] == ["places.id", "addressComponents"])
    # skill lists are not mistaken for places
    q = location_queries("Priya Singh\nSKILLS\nPython, Java, SQL\nCanva, Photoshop\nPune, Maharashtra\n", "Priya Singh")
    ok &= check("skill lists are not used as place queries", q == ["Pune, Maharashtra"])
    # caching
    places_check._cache.clear(); CALLS.clear()
    chk = make_checker("KEY", tr)
    chk("Priya\nAddress: Nagpur\n"); n = len(CALLS); chk("Priya\nAddress: Nagpur\n")
    ok &= check("repeat check uses cache (no new Google calls)", len(CALLS) == n)
    import os, tempfile
    from src.places_check import get_api_key
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / ".env"
        f.write_text("# comment\nGOOGLE_MAPS_API_KEY=\n"); os.environ.pop("GOOGLE_MAPS_API_KEY", None)
        ok &= check(".env with empty key -> no key", get_api_key(f) == "")
        f.write_text('# comment\nGOOGLE_MAPS_API_KEY="abc123"\n')
        ok &= check(".env key is read (quotes stripped)", get_api_key(f) == "abc123")
        f.write_text("GOOGLE_MAPS_API_KEY=xyz\n")
        ok &= check(".env key without quotes is read", get_api_key(f) == "xyz")
        ok &= check("missing .env file -> no key, no crash", get_api_key(Path(d) / "nope") == "")
    q = location_queries("Priya\nNagpur, Maharashtra\n", "")
    ok &= check("one-word first line (the name) is not sent to Google", q == ["Nagpur, Maharashtra"])
    # two-column resume: "Address" label alone, value several lines below, junk lines around it
    col = ("PRATEEK NAMDEV\nG R A P H I C D E S I G N E R\nAbout Me\nContact Us\n"
           "I am a creative and detail-oriented Graphic\nDesigner with hands-on exposure to poster\n"
           "appealing designs. Passionate about turning\nPhone\n8224834844\nEmail\nPrateeknamdev167@gmail.\ncom\n"
           "Portfolio\nhttps://bison-ty2ram.my.canva.site/\nEducation\nSecondary Education- 2022\nAddress\n"
           "International Public School, Biaora\nSwaroop Nagar colony,\nBiaora, (MP)\nCompleted 10th\n"
           "Higher Secondary Education- 2024\nSkill\nPoster & Template Design\nCanva, Photoshop, Illustrator\n")
    q = location_queries(col, "PRATEEK NAMDEV")
    ok &= check("two-column resume: address found below a lone 'Address' label and joined", "Swaroop Nagar colony, Biaora, (MP)" in q and q[0] == "Swaroop Nagar colony, Biaora, (MP)")
    ok &= check("two-column resume: no 'Contact Us', sentences or 'Email' sent", not any(w in " ".join(q) for w in ("Contact", "creative", "Email", "Designer")))
    tr3 = fake({"Biaora": ("IN", "India")})
    places_check._cache.clear(); CALLS.clear()
    ev = evaluate(col + BODY, "Graphic Design Intern", ROLES, make_checker("KEY", tr3))
    ok &= check("two-column resume: Places confirms India -> passes India check", ev["india"]["passed"] and ev["india"]["method"] == "places")
    sent = " ".join(str(c[3]) for c in CALLS if c[3])
    ok &= check("two-column resume: only the address text is sent", "Biaora" in sent and "creative" not in sent and "PRATEEK" not in sent.upper() and "8224834844" not in sent)
    q = location_queries("DEVI N M\nnmdevi234@gmail.com\nWORK EXPERIENCE\nEdoo Mate - Curriculum Designer (UK curriculum)\nData Analytics (Deloitte Australia)\n", "DEVI N M")
    ok &= check("job/certificate lines with brackets are never sent to Google", q == [])
    print("\nALL PASSED" if ok else "\nSOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
