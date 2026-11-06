import hashlib

import pandas as pd
import streamlit as st

from src.resume_reader import extract_bytes, tesseract_available, SUPPORTED
from src.evaluator import evaluate, classify, explain, DEFAULT_SELECT, DEFAULT_REVIEW
from src.role_config import load_roles
from src.places_check import make_checker, get_api_key

st.set_page_config(page_title="Fixed-Role Resume Screening | V1", page_icon="📄", layout="wide")
ROLES = load_roles()
ICON = {"SELECT": "🟢", "MANUAL REVIEW": "🟡", "REJECT": "🔴"}

st.title("Fixed-Role Resume Screening — Version 1")
st.caption("Local only (no LLM / no API). Role requirements → full resume extraction (PDF text, OCR, DOCX) → "
           "evidence matching → score → Select / Manual Review / Reject.")

with st.sidebar:
    st.header("Screening setup")
    role = st.selectbox("Select internship role", list(ROLES.keys()))
    st.subheader("Decision thresholds (on Basic %)")
    select_at = st.slider("Select if basic requirements are at least (%)", 30, 100, DEFAULT_SELECT)
    review_at = st.slider("Manual review if at least (%)", 0, select_at, min(DEFAULT_REVIEW, select_at))
    st.caption("Provisional - calibrate with human-reviewed resumes. Good-to-have skills never decide the outcome; "
               "they only add a bonus to the final score. Any extraction warning forces Manual Review.")
    st.subheader("Country check (India-only roles)")
    api_key = get_api_key()
    use_places = st.checkbox("Confirm country with Google Places", value=bool(api_key), disabled=not api_key)
    if api_key:
        st.caption("Google key found in the .env file. On: a resume without 'India'/+91 is checked by sending only its "
                   "address/location text to Google. Off: the simple 'India'/+91 rule alone decides.")
    else:
        st.caption("No key in the .env file (GOOGLE_MAPS_API_KEY), so only the simple 'India'/+91 rule is used.")
    if tesseract_available():
        st.success("OCR ready (Tesseract found)")
    else:
        st.error("Tesseract not found — scanned PDFs/images cannot be read. Install it (see README).")

cfg = ROLES[role]
places = make_checker(api_key) if (api_key and use_places) else None
with st.expander(f"Fixed requirements — {role}", expanded=True):
    c1, c2 = st.columns([3, 2])
    c1.markdown(f"**Basic requirements** — the candidate needs **{select_at}%** of these to be selected "
                f"(**{-(-select_at * len(cfg['basic']) // 100)} of {len(cfg['basic'])}**). "
                "Where a line lists alternatives, finding **any one** of them counts.")
    lines = []
    for i, g in enumerate(cfg["basic"], 1):
        lines.append(f"{i}. **{g[0]}**" if len(g) == 1 else f"{i}. **any of:** {', '.join(g)}")
    c1.markdown("\n".join(lines))
    c2.markdown("**Good-to-have (bonus only, never required)**")
    c2.write(", ".join(cfg["good_to_have"]))
    st.caption("For every role: a resume showing Pakistan or Bangladesh (the word, or a +92 / +880 phone number) is rejected.")
    if cfg.get("india_only"):
        st.warning('India-only role: a resume whose contact/address area shows another country (name, USA/UK/UAE, or a non-+91 phone) is rejected. '
                   'It must also mention "India" or have a phone number starting with +91'
                   + (', or Google Places must confirm an address in India' if places else '') + '; otherwise it is rejected.')
    st.info(cfg["note"] or "Keywords are evidence signals, not an exact-match checklist.")
    st.caption("Every requirement is searched in the WHOLE resume (skills, projects, experience, education, "
               "certificates, summary), including equivalent wording such as LLM = Large Language Models.")

uploads = st.file_uploader("Upload candidate resumes", type=SUPPORTED, accept_multiple_files=True)

if uploads and st.button("Extract & evaluate", type="primary"):
    extractions, bar = [], st.progress(0.0)
    for i, up in enumerate(uploads):
        data = up.getvalue()
        ex = extract_bytes(up.name, data)
        ex["id"] = hashlib.md5(data).hexdigest()[:8] + f"_{i}"
        extractions.append(ex)
        bar.progress((i + 1) / len(uploads))
    st.session_state["extractions"] = extractions
    bar.empty()

extractions = st.session_state.get("extractions")
if extractions:
    # evaluation is cheap, so it is recomputed live when role / thresholds change (no re-OCR needed)
    results = []
    for ex in extractions:
        ev = evaluate(ex["text"], role, ROLES, places)
        decision, reasons = classify(ev, ex["warnings"], select_at, review_at)
        results.append(dict(ex=ex, ev=ev, decision=decision, reasons=reasons))

    st.divider()
    st.subheader(f"Results — {role}")
    m = st.columns(4)
    m[0].metric("Processed", len(results))
    m[1].metric("Select", sum(r["decision"] == "SELECT" for r in results))
    m[2].metric("Manual review", sum(r["decision"] == "MANUAL REVIEW" for r in results))
    m[3].metric("Reject", sum(r["decision"] == "REJECT" for r in results))

    table = pd.DataFrame([{
        "File": r["ex"]["filename"], "Decision": f'{ICON[r["decision"]]} {r["decision"]}',
        "Basic %": r["ev"]["basic_pct"], "Basic met": f'{r["ev"]["basic_met"]}/{r["ev"]["basic_total"]}',
        "Bonus (+)": r["ev"]["bonus"], "Final score": r["ev"]["score"],
        "Warnings": len(r["ex"]["warnings"]),
    } for r in results]).sort_values("Final score", ascending=False)
    st.dataframe(table, width="stretch", hide_index=True)

    for i, r in enumerate(results):
        ex, ev = r["ex"], r["ev"]
        with st.expander(f'{ICON[r["decision"]]} {ex["filename"]} — Basic {ev["basic_pct"]:.0f}% (+{ev["bonus"]}) = {ev["score"]} — {r["decision"]}'):
            if ex["warnings"]:
                st.warning("Extraction warnings (forces Manual Review):\n\n" + "\n".join(f"- {w}" for w in ex["warnings"]))
            tab_report, tab_evidence, tab_text = st.tabs(["Report", "Requirement evidence", "Extracted text"])
            with tab_report:
                st.write(explain(ev, r["decision"]))
                st.caption("Why this decision: " + r["reasons"])
                if ev["blocked"]["rejected"]:
                    st.markdown(f"🚫 **Blocked country: {ev['blocked']['country']}** — {ev['blocked']['evidence']}")
                if ev["india_only"] and ev["other_country"]["rejected"]:
                    st.markdown("🚫 **Other country found (India-only role)** — " + ev["other_country"]["evidence"])
                if ev["india_only"] and ev["other_country"].get("conflict"):
                    st.markdown("⚠️ **Contact area shows India and another country** — " + ev["other_country"]["evidence"])
                if ev["india_only"] and ev["other_country"].get("elsewhere"):
                    st.caption("Other countries mentioned outside the contact area (not used for the decision): "
                               + ", ".join(ev["other_country"]["elsewhere"]))
                if ev["india_only"]:
                    st.markdown(("✅ **India check passed** — " if ev["india"]["passed"] else "❌ **India check failed** — ")
                                + ev["india"]["evidence"] + (" _(via Google Places)_" if ev["india"]["method"] == "places" else ""))
                    for t in ev["india"].get("tried", []):
                        st.caption(f'Places query: "{t["query"]}" → {t["country"] or t["error"] or "no country found"}')
                k = st.columns(4)
                k[0].metric("Basic requirements", f'{ev["basic_pct"]:.0f}%', f'{ev["basic_met"]} of {ev["basic_total"]}', delta_color="off")
                k[1].metric("Good-to-have bonus", f'+{ev["bonus"]}', f'{ev["good_found"]} found', delta_color="off")
                k[2].metric("Final score", ev["score"])
                k[3].metric("Only listed (no project)", ev["listed_only"])
                st.markdown("**Basic requirements checklist**")
                for row in ev["rows"]:
                    if row["category"] != "Basic requirement":
                        continue
                    if row["found"]:
                        st.markdown(f"✅ **{row['requirement']}** — {row['status']} (matched “{row['matched_as']}”, "
                                    f"{row['section']}): _{row['evidence']}_")
                    else:
                        st.markdown(f"❌ **{row['requirement']}** — not found anywhere in the resume")
                if ev["practical"]:
                    st.markdown("**Role-relevant project / experience statements**")
                    for p in ev["practical"]:
                        st.markdown(f"- _{p['section']}_: {p['statement']}")
                if ex["notes"]:
                    st.caption(" ".join(ex["notes"]))
                c = ev["contact"]
                st.caption(f"Detected: {c['name'] or '—'} · {c['email'] or '—'} · {c['phone'] or '—'}")
                st.selectbox("Your decision (for calibration)", ["", "SELECT", "MANUAL REVIEW", "REJECT"],
                             key=f"human_{ex['id']}")
            with tab_evidence:
                st.dataframe(pd.DataFrame(ev["rows"]).drop(columns=["found"]).rename(columns={
                    "category": "Category", "requirement": "Requirement", "status": "Evidence level",
                    "matched_as": "Matched as", "section": "Resume section", "evidence": "Evidence excerpt"}),
                    width="stretch", hide_index=True)
                st.caption("'Not found' means no matching wording was found — not proof the skill is absent.")
            with tab_text:
                if ex["pages"]:
                    st.dataframe(pd.DataFrame([{"Page": p["page"], "Characters": p["chars"], "OCR used": p["used_ocr"],
                                                "OCR confidence": p["ocr_conf"], "Image coverage": p["image_coverage"]}
                                               for p in ex["pages"]]), width="stretch", hide_index=True)
                st.text_area("Everything extracted from the file — check nothing important is missing",
                             ex["text"], height=360, key=f"text_{ex['id']}")

    export = pd.DataFrame([{
        "file": r["ex"]["filename"], "role": role, "system_decision": r["decision"],
        "human_decision": st.session_state.get(f"human_{r['ex']['id']}", ""),
        "basic_pct": r["ev"]["basic_pct"], "basic_met": f'{r["ev"]["basic_met"]}/{r["ev"]["basic_total"]}',
        "bonus": r["ev"]["bonus"], "final_score": r["ev"]["score"],
        "missing_basic": "; ".join(r["ev"]["missing"]), "warnings": " | ".join(r["ex"]["warnings"]),
    } for r in results])
    st.download_button("Download results CSV (includes your decisions for calibration)",
                       export.to_csv(index=False).encode("utf-8"), "screening_results.csv", "text/csv")
else:
    st.info("Choose a role, upload resumes (PDF, DOCX, PNG/JPG, TXT), then click **Extract & evaluate**.")
