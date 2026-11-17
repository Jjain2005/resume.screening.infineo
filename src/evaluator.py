"""Local (no LLM, no network) evidence-based evaluation against a fixed role.

Decision rule:
  1. BASIC requirements come first. Basic % = requirements satisfied / total requirements. A requirement is
     satisfied when ANY of its acceptable alternatives appears ANYWHERE in the resume (whole-resume check).
  2. Reject if Basic % < 30 or fewer than two Basic requirements are met.
  3. Score = Basic % + 0.25 x Good-to-have %. Select at score >= 45; otherwise reject.
  4. Extraction warnings and unresolved country checks still require manual review; blocked-country and India-only
     rules are applied as before.
  5. Evidence level (demonstrated in a project/experience vs only listed) is reported for every requirement.
"""
import re
import unicodedata
from functools import lru_cache

from .concepts import CLASSES, ACTION_VERBS
from .role_config import load_roles, BLOCKED_COUNTRIES

BASIC_FLOOR_PCT = 30
MIN_BASIC_MATCHED = 2
SCORE_PASS_LINE = 45
GOOD_TO_HAVE_WEIGHT = 0.25


# --------------------------------------------------------------------------- normalisation
def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower().replace("&", " and ")
    s = re.sub(r"(?<=[a-z0-9])\.(?=[a-z0-9])", "", s)      # node.js -> nodejs, A.I. -> ai
    s = re.sub(r"[^a-z0-9+#]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_FORM_TO_CLASSES, _FIRST = {}, {}
for _i, _cls in enumerate(CLASSES):
    _FIRST.setdefault(norm(_cls[0]), set()).add(_i)
    for _f in _cls:
        _FORM_TO_CLASSES.setdefault(norm(_f), set()).add(_i)


def _class_ids(key: str):
    """Classes that define the requirement term. A class whose canonical (first) form IS the term wins,
    so a narrow requirement like "Claude" is not widened by broader lists that merely mention it."""
    cands = [key] + ([key[:-1]] if key.endswith("s") and len(key) > 3 else [])
    for c in cands:
        if c in _FIRST:
            return _FIRST[c]
    for c in cands:
        if c in _FORM_TO_CLASSES:
            return _FORM_TO_CLASSES[c]
    return set()


@lru_cache(maxsize=None)
def forms_for(term: str):
    """All surface forms that count as evidence for `term` (bidirectional equivalents)."""
    key = norm(term)
    forms = {key}
    for ci in _class_ids(key):
        forms.update(norm(f) for f in CLASSES[ci])
    return tuple(sorted((f for f in forms if f), key=len, reverse=True))


@lru_cache(maxsize=None)
def _pattern(forms):
    alt = "|".join(re.escape(f) for f in forms)
    return re.compile(r"(?<![a-z0-9+#])(?:" + alt + r")s?(?![a-z0-9+#])")


# --------------------------------------------------------------------------- structure of the resume
HEADERS = {}
for _sec, _names in {
    "skills": ["skills", "technical skills", "key skills", "core competencies", "tools", "tech stack",
               "technologies", "tools and technologies", "skills and tools", "skill set", "skillset",
               "technical proficiency", "areas of expertise", "competencies", "software skills"],
    "projects": ["projects", "academic projects", "personal projects", "key projects", "project work",
                 "project experience", "selected projects", "major projects", "portfolio", "work samples",
                 "writing samples", "design projects"],
    "experience": ["experience", "work experience", "professional experience", "internship", "internships",
                   "internship experience", "employment history", "work history", "training",
                   "positions of responsibility", "volunteer experience", "volunteering", "leadership",
                   "relevant experience", "industrial training", "extracurricular activities",
                   "extracurricular", "activities"],
    "education": ["education", "academic background", "qualifications", "academic qualifications",
                  "educational qualifications", "academics", "coursework", "relevant coursework"],
    "certs": ["certifications", "certificates", "courses", "licenses and certifications", "certification",
              "online courses", "courses and certifications", "achievements and certifications"],
    "summary": ["summary", "profile", "professional summary", "objective", "career objective", "about me",
                "about", "career summary", "personal statement"],
    "other": ["achievements", "awards", "languages", "interests", "hobbies", "declaration", "references",
              "publications", "personal details", "personal information", "contact", "contact information"],
}.items():
    for _n in _names:
        HEADERS[_n] = _sec

_BULLETS = "•●▪■◦○‣∙·*-–—►▶➢➤✓✔➔"
_INLINE_SKILLS = re.compile(r"^(technical skills?|skills?|tools?|technologies|tech stack|languages?|"
                            r"programming languages?|frameworks?|libraries|databases?|platforms?)\s*[:\-–]", re.I)
_ACTION = re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(norm(v)) for v in sorted(set(ACTION_VERBS), key=len, reverse=True)) + r")(?![a-z])")


def detect_header(line: str):
    if len(line) > 45:
        return None
    n = norm(line)
    return HEADERS.get(n)


def build_units(text: str):
    """Split resume text into statements: [(section, statement)]. Wrapped bullet lines are re-joined."""
    units, section, cur, cur_sec = [], "other", None, None

    def flush():
        nonlocal cur, cur_sec
        if cur:
            s = " ".join(cur).strip()
            if len(s) > 1:
                units.append((cur_sec, s))
        cur, cur_sec = None, None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"\[Page \d+\]", line):
            flush()
            continue
        if line.lower().startswith("links:"):
            flush()
            units.append(("other", line))
            continue
        hdr = detect_header(line.rstrip(":"))
        if hdr:
            flush()
            section = hdr
            continue
        is_bullet = line[0] in _BULLETS or re.match(r"^\d{1,2}[.)]\s", line)
        body = line.lstrip(_BULLETS + " ") if line[0] in _BULLETS else line
        sec = section
        if section not in ("projects", "experience") and _INLINE_SKILLS.match(body):
            sec = "skills"
        if is_bullet:
            flush()
            cur, cur_sec = [body], sec
        elif cur is not None and section != "skills" and cur_sec == sec and \
                not cur[-1].endswith((".", "!", "?", ":")) and (body[0].islower() or len(cur[-1]) >= 60):
            cur.append(body)
        else:
            flush()
            cur, cur_sec = [body], sec
    flush()
    return units


def unit_strength(section: str, unit: str):
    n = norm(unit)
    if section in ("projects", "experience") or _ACTION.search(n):
        return 1.0, "demonstrated"
    if section in ("certs", "education"):
        return 0.7, "coursework/certificate"
    if section == "skills":
        return 0.4, "listed only"
    return 0.5, "mentioned"


def excerpt(unit: str, pattern, width=230):
    n_unit = unit
    m = pattern.search(norm(unit))
    if len(n_unit) <= width:
        return n_unit
    # locate roughly the same place in the original text
    first = None
    if m:
        word = m.group(0).split()[0]
        first = re.search(re.escape(word), n_unit, re.I)
    start = max(0, (first.start() if first else 0) - width // 3)
    return ("…" if start else "") + n_unit[start:start + width].strip() + "…"


def find_term(units, term):
    """Best evidence for one requirement term across all statements."""
    pat = _pattern(forms_for(term))
    best = None
    for section, unit in units:
        m = pat.search(norm(unit))
        if not m:
            continue
        s, label = unit_strength(section, unit)
        if best is None or s > best["strength"]:
            best = dict(term=term, matched=m.group(0), strength=s, status=label, section=section,
                        evidence=excerpt(unit, pat))
            if s == 1.0:
                break
    return best


def eval_group(units, group, full_norm="", full_raw=""):
    """Check every alternative against the WHOLE resume. Statement-level hit gives the best evidence level;
    if wording straddles a line break the full-text fallback still finds it (as 'mentioned')."""
    best = None
    for term in group:
        hit = find_term(units, term)
        if hit is None and full_norm:
            m = _pattern(forms_for(term)).search(full_norm)
            if m:
                hit = dict(term=term, matched=m.group(0), strength=0.5, status="mentioned", section="other",
                           evidence="…" + full_norm[max(0, m.start() - 60):m.end() + 60] + "…")
        if hit and (best is None or hit["strength"] > best["strength"]):
            best = hit
    return best


# --------------------------------------------------------------------------- evaluation
def guess_contact(text: str):
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    phone = re.search(r"(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){9,11}\d", text)
    name = ""
    for line in text.splitlines()[:6]:
        line = line.strip()
        if re.fullmatch(r"\[Page \d+\]", line) or not line:
            continue
        if 1 < len(line.split()) <= 4 and re.fullmatch(r"[A-Za-z .'-]+", line) and not detect_header(line):
            name = line
        break
    return dict(name=name, email=email.group(0) if email else "", phone=phone.group(0).strip() if phone else "")


_INDIA_WORD = re.compile(r"\bindia\b", re.I)
_PLUS91 = re.compile(r"(?<![\w+])\+\s?91[\s-]?\(?\d")


def india_check(text: str):
    """Simple rule: the resume mentions "India" anywhere, or has a phone number starting with +91."""
    m = _INDIA_WORD.search(text)
    if m:
        return dict(passed=True, evidence=f'found "{m.group(0)}"')
    m = _PLUS91.search(text)
    if m:
        return dict(passed=True, evidence='phone number starting with "+91"')
    return dict(passed=False, evidence='neither "India" nor a +91 phone number found')


def blocked_country_check(text: str):
    """Reject-for-every-role rule: the resume shows a blocked country (a word, or a phone starting with its code)."""
    for country, rule in BLOCKED_COUNTRIES.items():
        for w in rule["words"]:
            m = re.search(r"\b" + re.escape(w) + r"\b", text, re.I)
            if m:
                return dict(rejected=True, country=country, evidence=f'resume contains "{m.group(0)}"')
        for code in rule["phone_codes"]:
            m = re.search(r"(?<![\w+])\+\s?" + code + r"[\s-]?\(?\d", text)
            if m:
                return dict(rejected=True, country=country, evidence=f'phone number starting with "+{code}"')
    return dict(rejected=False, country="", evidence="")


def evaluate(text: str, role: str, roles=None, places=None):
    """`places` = optional callable from places_check.make_checker, used only for India-only roles when the
    simple "India"/+91 rule fails."""
    roles = roles or load_roles()
    cfg = roles[role]
    units = build_units(text)
    india = india_check(text)
    india["method"], india["error"] = "text", False
    if cfg.get("india_only") and not india["passed"] and places is not None:
        pr = places(text, guess_contact(text)["name"])
        india = dict(passed=pr["status"] == "india", evidence=pr["evidence"], method="places",
                     error=pr["status"] in ("error", "conflict"), tried=pr["tried"])
    full_norm = norm(text)
    rows, met, listed_only = [], 0, 0

    for g in cfg["basic"]:
        hit = eval_group(units, g, full_norm, text)
        label = g[0] if len(g) == 1 else " / ".join(g)
        if hit:
            met += 1
            listed_only += hit["status"] == "listed only"
            rows.append(dict(category="Basic requirement", requirement=label, found=True, status=hit["status"],
                             matched_as=hit["matched"], section=hit["section"], evidence=hit["evidence"]))
        else:
            rows.append(dict(category="Basic requirement", requirement=label, found=False, status="not found",
                             matched_as="", section="", evidence=""))

    good_found = 0
    for term in cfg["good_to_have"]:
        hit = eval_group(units, [term], full_norm, text)
        if hit:
            good_found += 1
            rows.append(dict(category="Good-to-have", requirement=term, found=True, status=hit["status"],
                             matched_as=hit["matched"], section=hit["section"], evidence=hit["evidence"]))
        elif not hit:
            rows.append(dict(category="Good-to-have", requirement=term, found=False, status="not found",
                             matched_as="", section="", evidence=""))
    good_total = len(cfg["good_to_have"])
    good_pct = 100 * good_found / max(1, good_total)
    bonus = GOOD_TO_HAVE_WEIGHT * good_pct

    # Report role-relevant project / experience statements for human review.
    every_term = {t for g in cfg["basic"] for t in g} | set(cfg["good_to_have"])
    role_pat = _pattern(tuple(sorted({f for t in every_term for f in forms_for(t)}, key=len, reverse=True)))
    practical = []
    for section, unit in units:
        if section == "skills" or len(unit) < 25:
            continue
        n = norm(unit)
        if role_pat.search(n) and (section in ("projects", "experience") or _ACTION.search(n)):
            practical.append(dict(section=section, statement=unit[:300]))

    total = len(cfg["basic"])
    basic_pct = 100 * met / max(1, total)
    score = basic_pct + bonus
    return dict(
        role=role, basic_met=met, basic_total=total, basic_pct=basic_pct,
        bonus=bonus, good_found=good_found, good_total=good_total, good_pct=good_pct,
        score=score, listed_only=listed_only,
        practical_count=len(practical), practical=practical[:8], rows=rows,
        missing=[r["requirement"] for r in rows if r["category"] == "Basic requirement" and not r["found"]],
        contact=guess_contact(text), statements=len(units),
        india_only=bool(cfg.get("india_only")), india=india,
        blocked=blocked_country_check(text),
    )


def classify(ev, warnings=None):
    """Return (decision, reason). Extraction warnings always force MANUAL REVIEW."""
    pct = ev["basic_pct"]
    if ev["blocked"]["rejected"]:
        return "REJECT", (f'Country rule (applies to every role): {ev["blocked"]["evidence"]} - {ev["blocked"]["country"]} '
                          f'resumes are rejected regardless of skills (basic requirements were {pct:.0f}%).')
    if warnings:
        return "MANUAL REVIEW", "Extraction warning(s): a human must check the resume text before trusting the result."
    if ev.get("india_only") and not ev["india"]["passed"] and ev["india"].get("error"):
        return "MANUAL REVIEW", ("India-only role, but the country could not be confirmed: " + ev["india"]["evidence"] +
                                 ". A human must confirm the country.")
    if ev.get("india_only") and not ev["india"]["passed"]:
        return "REJECT", ('India-only role: ' + ev["india"]["evidence"] + '. Rejected regardless of skills '
                          f'(basic requirements were {pct:.0f}%).')
    if pct < BASIC_FLOOR_PCT or ev["basic_met"] < MIN_BASIC_MATCHED:
        return "REJECT", (f"Basic floor not met: {ev['basic_met']}/{ev['basic_total']} requirements "
                          f"({pct:.1f}%); requires at least {MIN_BASIC_MATCHED} matched and "
                          f"{BASIC_FLOOR_PCT}%.")
    if ev["score"] >= SCORE_PASS_LINE:
        return "SELECT", (f"Score {ev['score']:.1f} (Basic {pct:.1f}% + 0.25 × Good-to-have "
                          f"{ev['good_pct']:.1f}%) meets the {SCORE_PASS_LINE} pass line.")
    return "REJECT", (f"Score {ev['score']:.1f} (Basic {pct:.1f}% + 0.25 × Good-to-have "
                      f"{ev['good_pct']:.1f}%) is below the {SCORE_PASS_LINE} pass line.")


def explain(ev, decision):
    found = [r for r in ev["rows"] if r["category"] == "Basic requirement" and r["found"]]
    demo = [r["requirement"] for r in found if r["status"] == "demonstrated"]
    weak = [r["requirement"] for r in found if r["status"] != "demonstrated"]
    parts = [f"{decision}. Basic requirements: {ev['basic_met']} of {ev['basic_total']} met ({ev['basic_pct']:.1f}%). "
             f"Good-to-have: {ev['good_found']} of {ev['good_total']} matched ({ev['good_pct']:.1f}%), "
             f"weighted contribution +{ev['bonus']:.1f}. Final score {ev['score']:.1f}."]
    if demo:
        parts.append("Shown in project/experience work: " + ", ".join(demo) + ".")
    if weak:
        parts.append("Only listed or mentioned (weaker evidence): " + ", ".join(weak) + ".")
    if ev["missing"]:
        parts.append("Not found anywhere in the resume: " + ", ".join(ev["missing"]) +
                     " (absence of wording is not proof the skill is missing).")
    return " ".join(parts)
