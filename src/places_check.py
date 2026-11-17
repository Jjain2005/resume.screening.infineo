"""Optional Google Places confirmation of the candidate's country (India-only roles).

Used ONLY when the simple rule fails (no "India" and no +91 phone number in the resume) and an API key is set.
  1. Pick the location text out of the resume: Address/Location-labelled lines and the contact lines at the top.
     Names, e-mails, phone numbers and URLs are removed first - only place text is sent to Google, never the resume.
  2. Places API (New) Text Search with field mask `places.id`        -> "IDs Only" SKU (listed as unlimited free)
  3. Places API (New) Place Details with field mask `addressComponents` -> Essentials SKU (free monthly cap;
     70,000 at India pricing, 10,000 at global pricing)  -> the country code.
Results are cached in memory per query, so Streamlit reruns do not call Google again. Errors are never cached.
Uses only the standard library (no new dependency).
"""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def get_api_key(env_file=None):
    """Google Maps key from the project's .env file (GOOGLE_MAPS_API_KEY=...), else from the environment.
    Read on every call, so editing .env takes effect on the next Streamlit rerun / browser refresh."""
    path = Path(env_file) if env_file else ENV_FILE
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "GOOGLE_MAPS_API_KEY":
                v = v.strip().strip('"').strip("'")
                if v:
                    return v
    except OSError:
        pass
    return os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()


SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{id}?languageCode=en"

# A resolved result only counts as the candidate's location if Google says it is a geographic place
# (city, region, address...). Businesses/institutions (e.g. what "Graphic Designer" or "IIT Bombay" return) are ignored.
GEO_TYPES = {"locality", "sublocality", "sublocality_level_1", "neighborhood", "colloquial_area", "postal_town",
             "administrative_area_level_1", "administrative_area_level_2", "administrative_area_level_3",
             "country", "postal_code", "street_address", "route", "premise", "subpremise", "political"}

_cache = {}   # query -> {"code": "IN", "name": "India"}  (successful lookups only)
_city_cache = {}  # normalized city -> [{"code": "IN", "name": "India"}, ...]

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(r"(https?://\S+|www\.\S+|\b[\w-]+\.(?:com|in|io|org|net|co)\b\S*)", re.I)
_PHONE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
_LABEL = re.compile(r"^\s*(?:current |present |permanent |home |residential )?"
                    r"(address|location|city|country|residence|resides in|based in|lives in)\s*[:\-–]?\s*", re.I)
_LABELLED = re.compile(r"^\s*(?:current |present |permanent |home |residential )?"
                       r"(address|location|city|country|residence|resides in|based in|lives in)\b", re.I)
_LABEL_ONLY = re.compile(r"^(e-?mail|phone|mobile|contact|linkedin|github|behance|portfolio|website|web|dob|"
                         r"date of birth|nationality|languages?|gender|summary|profile|objective)$", re.I)
_JOB_TITLE = re.compile(r"\b(designer|developer|engineer|intern(ship)?|manager|student|analyst|writer|marketer|"
                        r"executive|specialist|fresher|trainee|associate|consultant|freelancer?|creator|editor|"
                        r"architect|scientist|tester|recruiter|coordinator)\b", re.I)
_PAGE = re.compile(r"\[Page \d+\]")


def default_transport(method, url, headers, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def lookup_country(query, api_key, transport=None):
    """Return {"code","name"} for the place text, {} when Google finds nothing, raises on API/network errors."""
    if query in _cache:
        return _cache[query]
    transport = transport or default_transport
    hdr = {"Content-Type": "application/json", "X-Goog-Api-Key": api_key}
    found = transport("POST", SEARCH_URL, {**hdr, "X-Goog-FieldMask": "places.id"},
                      {"textQuery": query, "maxResultCount": 1, "languageCode": "en"})
    places = (found or {}).get("places") or []
    if not places:
        _cache[query] = {}               # "Google found nothing" is cached too (errors are not)
        return {}
    det = transport("GET", DETAILS_URL.format(id=places[0]["id"]),
                    {**hdr, "X-Goog-FieldMask": "addressComponents,types"})
    geo = bool(GEO_TYPES & set((det or {}).get("types", [])))
    for comp in (det or {}).get("addressComponents", []):
        if "country" in comp.get("types", []):
            res = {"code": (comp.get("shortText") or "").upper(), "name": comp.get("longText") or comp.get("shortText"),
                   "geo": geo}
            _cache[query] = res
            return res
    _cache[query] = {}
    return {}


def lookup_city_countries(city, api_key, transport=None):
    """Return distinct countries Google associates with a city name; cache successful empty/results by city."""
    key = re.sub(r"\s+", " ", city).strip().casefold()
    if not key:
        return []
    if key in _city_cache:
        return _city_cache[key]
    transport = transport or default_transport
    hdr = {"Content-Type": "application/json", "X-Goog-Api-Key": api_key}
    found = transport("POST", SEARCH_URL, {**hdr, "X-Goog-FieldMask": "places.id"},
                      {"textQuery": city.strip(), "maxResultCount": 10, "languageCode": "en"})
    places = (found or {}).get("places") or []
    countries = {}
    for place in places:
        det = transport("GET", DETAILS_URL.format(id=place["id"]),
                        {**hdr, "X-Goog-FieldMask": "addressComponents,types"})
        if not (GEO_TYPES & set((det or {}).get("types", []))):
            continue
        for comp in (det or {}).get("addressComponents", []):
            if "country" in comp.get("types", []):
                code = (comp.get("shortText") or "").upper()
                if code:
                    countries[code] = comp.get("longText") or comp.get("shortText") or code
                break
    result = [{"code": code, "name": countries[code]} for code in sorted(countries)]
    _city_cache[key] = result
    return result


def _clean_segment(seg, name):
    seg = _EMAIL.sub(" ", seg)
    seg = _URL.sub(" ", seg)
    seg = _PHONE.sub(" ", seg)
    seg = _LABEL.sub("", seg)
    seg = re.sub(r"\s+", " ", seg).strip(" ,;:-–|")
    if name and seg.lower() == name.lower():
        return ""
    return seg


def location_queries(text, name="", max_queries=6):
    """Place-text candidates from the resume, most reliable first (labelled lines, then top contact lines)."""
    from .evaluator import detect_header, forms_for, norm   # local import avoids a circular import
    lines = [l.strip() for l in text.splitlines() if l.strip() and not _PAGE.fullmatch(l.strip())
             and not l.strip().lower().startswith("links:")]
    labelled = [l for l in lines if _LABELLED.match(l)]
    top = lines[:15]
    if top and re.fullmatch(r"[A-Za-z .'-]{2,40}", top[0]) and not _LABELLED.match(top[0]):
        top = top[1:]                    # first line that is just letters = the candidate's name; never send it
    queries, seen = [], set()
    for line in labelled + top:
        for seg in re.split(r"[|•·●▪■]", line):
            seg = _clean_segment(seg, name)
            n = norm(seg)
            if (len(seg) < 3 or _LABEL_ONLY.match(seg) or _JOB_TITLE.search(seg) or len(seg) > 90 or len(seg.split()) > 12 or not re.search(r"[A-Za-z]{3}", seg)
                    or detect_header(seg) or n in seen):
                continue
            # skip skill/tool lists and statements: any comma-part that is a known resume term
            parts = [norm(p) for p in seg.split(",") if p.strip()]
            if any(forms_for(p)[0] != p or len(forms_for(p)) > 1 for p in parts if p):
                continue
            seen.add(n)
            queries.append(seg)
            if len(queries) >= max_queries:
                return queries
    return queries


def make_checker(api_key, transport=None, max_queries=6):
    """Return check(text, name="") -> {status, country_code, evidence, tried}.
    status: "india" (every resolved place is in India) | "not_india" | "conflict" | "no_location" | "error"."""
    def check(text, name=""):
        queries = location_queries(text, name, max_queries)
        if not queries:
            return dict(status="no_location", country_code="", tried=[],
                        evidence="no readable address/location text found, so the country could not be confirmed")
        tried, countries, errors = [], [], []
        for q in queries:
            try:
                res = lookup_country(q, api_key, transport)
            except urllib.error.HTTPError as exc:
                errors.append(f"Google returned HTTP {exc.code} ({'bad/blocked API key or API not enabled' if exc.code in (400, 403) else 'request failed'})")
                tried.append(dict(query=q, country="", error=errors[-1]))
                break                       # same key for every query - stop on an API-level error
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                tried.append(dict(query=q, country="", error=errors[-1]))
                break
            if res.get("code") and not res.get("geo", True):
                tried.append(dict(query=q, country="", error="", ignored="not a place (business/institution)"))
                continue
            tried.append(dict(query=q, country=res.get("code", ""), error="", ignored=""))
            if res.get("code"):
                countries.append((q, res))
        if errors and not countries:
            return dict(status="error", country_code="", tried=tried,
                        evidence="Google Places check could not run (" + errors[0] + ")")
        codes = {r["code"] for _, r in countries}
        if codes == {"IN"}:
            return dict(status="india", country_code="IN", tried=tried,
                        evidence=f'Google Places confirmed India for "{countries[0][0]}"')
        if "IN" in codes:       # some place text says India, some says another country -> a human decides
            listing = "; ".join(f'"{q}" -> {r["name"]} ({r["code"]})' for q, r in countries)
            return dict(status="conflict", country_code="", tried=tried,
                        evidence="Google Places gave conflicting countries (" + listing + ")")
        if countries:
            q, res = countries[0]
            return dict(status="not_india", country_code=res["code"], tried=tried,
                        evidence=f'Google Places places "{q}" in {res["name"]} ({res["code"]}), not India')
        return dict(status="no_location", country_code="", tried=tried,
                    evidence="Google Places found no country for the location text (" +
                             "; ".join(f'"{t["query"]}"' for t in tried) + ")")
    return check
