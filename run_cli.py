"""Batch screening without the UI:  python run_cli.py "AI Engineer Intern" resumes/ [out.csv]"""
import csv, sys
from pathlib import Path
from src.resume_reader import extract_bytes, SUPPORTED
from src.evaluator import evaluate, classify, explain
from src.role_config import load_roles
from src.places_check import make_checker, get_api_key


def main():
    if len(sys.argv) < 3:
        print(__doc__); print("Roles:", ", ".join(load_roles())); return
    role, folder = sys.argv[1], Path(sys.argv[2])
    out = sys.argv[3] if len(sys.argv) > 3 else "screening_results.csv"
    roles = load_roles()
    key = get_api_key()
    places = make_checker(key) if key else None
    rows = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower().lstrip(".") not in SUPPORTED:
            continue
        ex = extract_bytes(f.name, f.read_bytes())
        ev = evaluate(ex["text"], role, roles, places)
        decision, reason = classify(ev, ex["warnings"])
        rows.append({"file": f.name, "basic_pct": ev["basic_pct"], "bonus": ev["bonus"], "final_score": ev["score"], "decision": decision,
                     "warnings": " | ".join(ex["warnings"]), "missing": "; ".join(ev["missing"]),
                     "explanation": explain(ev, decision)})
        print(f"{f.name:40s} basic {ev['basic_pct']:>5.1f}%  +{ev['bonus']:<4} final {ev['score']:>3}  {decision}")
    if rows:
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
        print("Saved", out)


if __name__ == "__main__":
    main()
