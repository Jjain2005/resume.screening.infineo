"""Places-check tests with a FAKE Google transport (no network, no API key needed).
Run from the project folder:  python tests/test_places.py"""
import sys
import urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import places_check
from src.places_check import make_checker, location_queries, lookup_city_countries
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
        code, name, *rest = country_by_text[key]
        types = rest[0] if rest else ["locality", "political"]
        return {"types": types, "addressComponents": [{"types": ["locality"], "longText": "x", "shortText": "x"},
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
    ev, dec = run("Priya Singh\npriya@example.com | +44 7700 900123\nNagpur, Maharashtra 440001\n", tr)
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
    BUSINESS = ["establishment", "point_of_interest", "university"]
    tr2 = fake({"Toronto": ("CA", "Canada"), "Bombay": ("IN", "India", BUSINESS)})
    ev, dec = run("John Smith\nAddress: Toronto, Ontario\nB.Tech, IIT Bombay\n", tr2)
    ok &= check("abroad address + Indian UNIVERSITY line (a business, ignored) -> REJECT", dec == "REJECT" and not ev["india"]["passed"])
    tr3 = fake({"Toronto": ("CA", "Canada"), "Pune": ("IN", "India")})
    ev, dec = run("John Smith\nAddress: Toronto, Ontario\nPermanent address: Pune, Maharashtra\n", tr3)
    ok &= check("two real places in different countries -> MANUAL REVIEW (conflict), never Select",
                dec == "MANUAL REVIEW" and not ev["india"]["passed"])
    # the Egyptian graphic designer resume: job-title lines must not be sent / must not rescue it
    tr4 = fake({"Graphic Designer": ("IN", "India", BUSINESS), "Mansoura": ("EG", "Egypt")})
    head = ("Mahetab Tarek Eid\nGraphic Designer | UI/UX Designer\n+20 1060543098 | Mansoura-Dahlia-Egypt\n"
            "Email:mahytarek8@gmail.com | Behance: https://www.behance.net/x\n")
    ev, dec = run(head, tr4)
    sent = " ".join(str(c[3]) for c in CALLS if c[3])
    ok &= check("Egyptian graphic designer resume => REJECT (not Select)", dec == "REJECT" and ev["india"]["method"] == "places")
    ok &= check("job titles / label words are not sent to Google", "Designer" not in sent and "Email" not in sent and "Mansoura" in sent)
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
    ok &= check("field masks are places.id then addressComponents,types",
                [c[2] for c in CALLS][:2] == ["places.id", "addressComponents,types"])
    # skill lists are not mistaken for places
    q = location_queries("Priya Singh\nSKILLS\nPython, Java, SQL\nCanva, Photoshop\nPune, Maharashtra\n", "Priya Singh")
    ok &= check("skill lists are not used as place queries", q == ["Pune, Maharashtra"])
    # caching
    places_check._cache.clear(); CALLS.clear()
    chk = make_checker("KEY", tr)
    chk("Priya\nAddress: Nagpur\n"); n = len(CALLS); chk("Priya\nAddress: Nagpur\n")
    ok &= check("repeat check uses cache (no new Google calls)", len(CALLS) == n)
    places_check._city_cache.clear()
    city_calls = []
    def city_transport(method, url, headers, body=None, timeout=10):
        city_calls.append((method, headers.get("X-Goog-FieldMask"), body))
        if method == "POST":
            return {"places": [{"id": "pune"}, {"id": "hyderabad-in"}, {"id": "hyderabad-pk"}]}
        place_id = url.split("/places/")[1].split("?")[0]
        country = {"pune": ("IN", "India"), "hyderabad-in": ("IN", "India"),
                   "hyderabad-pk": ("PK", "Pakistan")}[place_id]
        return {"types": ["locality", "political"], "addressComponents": [
            {"types": ["country", "political"], "longText": country[1], "shortText": country[0]}]}
    countries = lookup_city_countries("Hyderabad", "KEY", city_transport)
    before_cache = len(city_calls)
    countries_again = lookup_city_countries(" hyderabad ", "KEY", city_transport)
    ok &= check("city lookup reports all distinct countries and caches case/space variants",
                countries == [{"code": "IN", "name": "India"}, {"code": "PK", "name": "Pakistan"}] and
                countries_again == countries and len(city_calls) == before_cache == 4)
    ok &= check("city lookup sends only the city name and requests up to 10 Places",
                city_calls[0][2]["textQuery"] == "Hyderabad" and city_calls[0][2]["maxResultCount"] == 10 and
                city_calls[0][1] == "places.id")
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
    print("\nALL PASSED" if ok else "\nSOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
