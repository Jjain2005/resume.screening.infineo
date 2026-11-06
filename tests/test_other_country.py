"""India-only roles: the candidate's country is judged from the CONTACT/ADDRESS area only.  python tests/test_other_country.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.evaluator import evaluate, classify
from src.role_config import INDIA_ONLY_ROLES

BODY = "SKILLS\nCanva, Figma, Photoshop\nPROJECTS\nDesigned poster, logo and banner for social media campaign.\n"
ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print("PASS" if cond else "FAIL", name)

def decide(head, role="Graphic Design Intern", extra="", warnings=None):
    ev = evaluate(head + BODY + extra, role); return classify(ev, warnings or [])[0], ev

d, _ = decide("Amit\nNagpur, India\n+91 98765 43210\n"); check("Indian resume is selected", d == "SELECT")

# foreign contact area, no India -> REJECT for every India-only role
for label, head in [("country name in address", "Jane\nLondon, United Kingdom\n"), ("USA in address", "Raj\nNew Jersey, USA\n"),
                    ("+1 phone", "Raj\n+1 212 555 0100\nSeattle\n"), ("Nepal", "Ram\nKathmandu, Nepal\n")]:
    for role in INDIA_ONLY_ROLES:
        d, ev = decide(head, role); check(f"{label} / {role} => REJECT", d == "REJECT" and ev["other_country"]["rejected"])

# foreign word only OUTSIDE the contact area, India address -> accepted (the Atul Sharma / Devi cases)
d, ev = decide("Atul Sharma\nGurugram, Haryana, India | +91 7206365276\n", extra="CERTIFICATIONS\nData Analytics (Deloitte Australia)\n")
check("Indian address + 'Australia' in a certificate => not rejected", d == "SELECT" and "Australia" in ev["other_country"]["elsewhere"])
d, ev = decide("Devi\nChennai, India\n", extra="EXPERIENCE\nCurriculum Designer (UK curriculum), worked with USA clients\n")
check("Indian address + 'UK'/'USA' in job text => not rejected", d == "SELECT")
d, ev = decide("Atul Sharma\nGurugram, Haryana, India | +91 7206365276\nSUMMARY Built apps for Canada based clients.\n")
check("foreign word in the summary line below the header => not rejected", d == "SELECT")

# one-line header (all text on one line) still only looks at the header part
d, ev = decide("RAVI K Pune, Maharashtra, India +91 9876543210 PROFESSIONAL SUMMARY Worked for a client in Germany. ")
check("single-line resume: text after the heading is ignored", d == "SELECT")

# address in a two-column layout: label alone, value below
d, ev = decide("PRATEEK\nAddress\nSwaroop Nagar colony,\nBiaora, India\n", extra="Certificate from Canada\n")
check("'Address' label with value below, India => accepted", d == "SELECT")

# UK header, India only mentioned later ("client in India") -> still REJECT
d, ev = decide("Jane\nLondon, United Kingdom\n+44 7700 900123\n", extra="PROJECTS\nWorked with a client team based in India\n")
check("UK contact area + 'India' only in a project => REJECT", d == "REJECT")

# conflict: contact area shows India and another country -> MANUAL REVIEW
d, ev = decide("Raj\nMumbai, India\nAlso at: Toronto, Canada\n")
check("contact area shows India AND Canada => MANUAL REVIEW", d == "MANUAL REVIEW" and ev["other_country"]["conflict"])

# foreign-only contact area is rejected even when an extraction warning exists
d, _ = decide("Jane\nUSA\n", warnings=["Very little text"]); check("foreign contact area is rejected even with a warning", d == "REJECT")

ev = evaluate("John\nNew York, USA\nPython\n", "Python Developer Intern"); check("rule not applied to non India-only roles", not ev["other_country"]["rejected"])
d, _ = decide("Amit\nPune, India\n+91 9876543210\n", extra="C++ 11, 10+ 2 years, focus on us\n"); check("no false positives (C++, 10+, 'us')", d == "SELECT")
print("\nALL PASSED" if ok else "\nSOME FAILED"); sys.exit(0 if ok else 1)
