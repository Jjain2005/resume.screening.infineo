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

_cache = {}   # query -> {"code": "IN", "name": "India"}  (successful lookups only)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(r"(https?://\S+|www\.\S+|\b[\w-]+\.(?:com|in|io|org|net|co)\b\S*)", re.I)
_PHONE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
_LABEL = re.compile(r"^\s*(?:current |present |permanent |home |residential )?"
                    r"(address|location|city|country|residence|resides in|based in|lives in)\s*[:\-–]?\s*", re.I)
_LABELLED = re.compile(r"^\s*(?:current |present |permanent |home |residential )?"
                       r"(address|location|city|country|residence|resides in|based in|lives in)\b", re.I)
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
    det = transport("GET", DETAILS_URL.format(id=places[0]["id"]), {**hdr, "X-Goog-FieldMask": "addressComponents"})
    for comp in (det or {}).get("addressComponents", []):
        if "country" in comp.get("types", []):
            res = {"code": (comp.get("shortText") or "").upper(), "name": comp.get("longText") or comp.get("shortText")}
            _cache[query] = res
            return res
    _cache[query] = {}
    return {}


def _clean_segment(seg, name):
    seg = _EMAIL.sub(" ", seg)
    seg = _URL.sub(" ", seg)
    seg = _PHONE.sub(" ", seg)
    seg = _LABEL.sub("", seg)
    seg = re.sub(r"\s+", " ", seg).strip(" ,;:-–|")
    if name and seg.lower() == name.lower():
        return ""
    return seg


# Words that label a contact field but are not a place, and words that make a line a sentence or an education line.
_FIELD_LABELS = {"contact us", "contact", "contact me", "contact details", "contact information", "get in touch",
                 "phone", "mobile", "email", "e mail", "portfolio", "linkedin", "github", "website", "skill",
                 "skills", "language", "languages", "address", "location", "personal details", "about me"}
_SENTENCE_WORDS = {"i", "am", "my", "is", "are", "was", "with", "about", "through", "for", "to", "by", "and",
                   "the", "a", "an", "of"}
_EDU_WORDS = re.compile(r"\b(school|college|university|institute|academy|vidyalaya|vidyalay|polytechnic|"
                        r"iit|nit|iiit)\b", re.I)
_PIN = re.compile(r"\b\d{6}\b")


def _place_like(seg):
    """True when the text looks like place text (a short address fragment), not a sentence, a label or a skill."""
    from .evaluator import norm                                    # local import avoids a circular import
    words = seg.split()
    n = norm(seg)
    if not words or len(words) > 8 or n in _FIELD_LABELS:
        return False
    if re.search(r"[.!?]\s+[A-Za-z]", seg):                       # two sentences glued together
        return False
    if len(words) > 2 and sum(len(w) == 1 and w.isalpha() for w in words) > len(words) / 2:
        return False                                              # spaced-out title like "G R A P H I C"
    if len(set(n.split()) & _SENTENCE_WORDS) >= 2 or "i" in n.split():
        return False                                              # prose such as "I am a creative ..."
    return True


def _has_place_shape(raw, allow_bracket=True):
    """Address fragments usually contain a comma, a 6-digit PIN code or (under an Address label) a bracketed state
    code such as "(MP)". The fallback for lines near the top does NOT accept brackets: job lines such as
    "Curriculum Designer (UK curriculum)" must never be sent to Google."""
    return "," in raw or bool(_PIN.search(raw)) or (allow_bracket and "(" in raw)


def location_queries(text, name="", max_queries=4, include_top=True):
    """Place-text candidates from the resume, most reliable first:
      1. the value on the same line as an Address/Location label ("Address: Nagpur, Maharashtra"),
      2. the lines that follow a label standing alone on its own line (two-column / sidebar layouts, where the
         value can be a few lines below), joined when an address line ends with a comma,
      3. contact-style lines near the top (only lines that look like an address: comma, bracket or PIN code).
    Names, e-mails, phones, URLs, sentences, section titles, field labels and education lines are never sent."""
    from .evaluator import detect_header, forms_for, norm   # local import avoids a circular import
    lines = [l.strip() for l in text.splitlines() if l.strip() and not _PAGE.fullmatch(l.strip())
             and not l.strip().lower().startswith("links:")]
    queries, seen = [], set()

    def add(seg):
        """Clean and test one piece of place text; True when the query limit has been reached."""
        seg = _clean_segment(seg, name)
        n = norm(seg)
        if (len(seg) < 3 or len(seg) > 90 or not re.search(r"[A-Za-z]{3}", seg) or detect_header(seg)
                or n in seen or not _place_like(seg)):
            return False
        parts = [norm(p) for p in seg.split(",") if p.strip()]   # skip skill/tool lists and statements
        if any(forms_for(p)[0] != p or len(forms_for(p)) > 1 for p in parts if p):
            return False
        seen.add(n)
        queries.append(seg)
        return len(queries) >= max_queries

    # 1 + 2: Address/Location-labelled lines
    for i, line in enumerate(lines):
        if not _LABELLED.match(line):
            continue
        same_line = _clean_segment(line, name)
        if same_line:                                   # the value is on the label's own line
            for seg in re.split(r"[|•·●▪■]", line):
                if add(seg):
                    return queries
            continue
        joined, cur, prev_comma = [], "", False         # label alone on its line: look at the next lines
        for j in range(i + 1, min(len(lines), i + 9)):
            raw = lines[j]
            if detect_header(raw.rstrip(":")) or norm(raw) in _FIELD_LABELS or _LABELLED.match(raw):
                break
            cleaned = _clean_segment(raw, name)
            usable = (cleaned and not _EDU_WORDS.search(cleaned) and _place_like(cleaned)
                      and (_has_place_shape(raw) or j == i + 1 or prev_comma))
            if usable and prev_comma and cur:
                cur += ", " + cleaned                   # "Swaroop Nagar colony," + "Biaora, (MP)"
            elif usable:
                if cur:
                    joined.append(cur)
                cur = cleaned
            else:
                if cur:
                    joined.append(cur)
                cur = ""
            prev_comma = bool(usable and raw.rstrip().endswith(","))
        if cur:
            joined.append(cur)
        for seg in sorted(joined, key=lambda x: -x.count(",")):   # fullest address first
            if add(seg):
                return queries

    if not include_top:                 # caller wants labelled address lines only
        return queries

    # 3: contact-style lines near the top
    top = lines[:15]
    if top and re.fullmatch(r"[A-Za-z .'-]{2,40}", top[0]) and not _LABELLED.match(top[0]):
        top = top[1:]                    # first line that is just letters = the candidate's name; never send it
    for line in top:
        for seg in re.split(r"[|•·●▪■]", line):
            if _has_place_shape(seg, allow_bracket=False) and add(seg):
                return queries
    return queries


def make_checker(api_key, transport=None, max_queries=4):
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
            tried.append(dict(query=q, country=res.get("code", ""), error=""))
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
