"""End-to-end checks. Run from the project folder:  python tests/test_pipeline.py
Builds sample resumes (text PDF, two-column PDF, scanned PDF, DOCX with a table, unrelated resume) and
asserts extraction + evaluation behave. Needs Tesseract for the scanned-PDF check."""
import io, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pymupdf
from src.resume_reader import extract_bytes, tesseract_available
from src.evaluator import evaluate, classify
from src.role_config import load_roles

ROLES = load_roles()


def pdf_from_lines(lines, x=50):
    doc = pymupdf.open(); page = doc.new_page(); y = 60
    for l in lines:
        page.insert_text((x, y), l, fontsize=10); y += 14
    return doc


STRONG = ["Asha Rao", "asha@example.com", "SKILLS", "Python, LLMs, RAG, FAISS, ChatGPT API, LangChain, FastAPI",
          "PROJECTS",
          "- Built a RAG chatbot using OpenAI API and FAISS embeddings for document question answering.",
          "- Developed a semantic search engine over 10k PDFs using sentence transformers and Pinecone.",
          "- Implemented prompt engineering techniques and function calling for an AI agent.",
          "- Deployed a Flask REST API serving a fine-tuned Hugging Face model.",
          "EDUCATION", "B.Tech Computer Science, 2026"] + ["Additional coursework in machine learning and NLP."] * 4
WEAK = ["Ravi Kumar", "Final year BCom student", "SKILLS", "Tally, MS Office, Communication",
        "PROJECTS", "- Developed a project report on inventory management for a retail shop.",
        "- Built a good rapport with customers during internship and created monthly sales sheets.",
        "- Implemented new filing process for the office documents in the department.",
        "EDUCATION", "B.Com, 2026"] + ["Hobbies include cricket, reading and travelling to new places."] * 4


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    return cond


def main():
    ok = True
    # 1 text PDF
    d = pdf_from_lines(STRONG)
    ex = extract_bytes("strong.pdf", d.tobytes())
    ev = evaluate(ex["text"], "AI Engineer Intern", ROLES); dec, _r = classify(ev, ex["warnings"])
    print("strong:", ev["basic_pct"], ev["score"], dec, ex["warnings"])
    ok &= check("text PDF: strong AI resume => SELECT", dec == "SELECT")
    ok &= check("text PDF: LLM/RAG equivalents found (LLMs, RAG, FAISS)",
                all(any(r["requirement"] == q and r["found"] for r in ev["rows"])
                    for q in ("Large Language Models", "Retrieval-Augmented Generation", "Vector Databases", "LLM APIs")))
    # 2 unrelated
    ex = extract_bytes("weak.pdf", pdf_from_lines(WEAK).tobytes())
    ev = evaluate(ex["text"], "AI Engineer Intern", ROLES); dec, _r = classify(ev, ex["warnings"])
    print("weak:", ev["basic_pct"], ev["score"], dec, ex["warnings"])
    ok &= check("unrelated resume => REJECT", dec == "REJECT")
    # 3 two columns
    doc = pymupdf.open(); page = doc.new_page()
    left = ["SKILLS", "Python", "LLMs", "RAG", "Vector Databases (Chroma)", "Prompt Engineering"]
    right = ["EXPERIENCE", "AI Intern, Acme (2025)", "Built a retrieval augmented generation",
             "pipeline with Chroma and embeddings.", "Wrote prompts for Gemini and Claude and used the OpenAI API."]
    for i, l in enumerate(left): page.insert_text((40, 80 + 16 * i), l, fontsize=10)
    for i, l in enumerate(right): page.insert_text((320, 80 + 16 * i), l, fontsize=10)
    ex = extract_bytes("twocol.pdf", doc.tobytes())
    ok &= check("two-column: left column read before right", ex["text"].index("Prompt Engineering") < ex["text"].index("AI Intern"))
    ev = evaluate(ex["text"] + "\n" + "Additional details about the candidate education and interests. " * 6, "AI Engineer Intern", ROLES)
    ok &= check("two-column: wrapped bullet re-joined (RAG demonstrated)",
                any(r["requirement"].startswith("Retrieval") and r["status"] == "demonstrated" for r in ev["rows"]))
    # 4 scanned
    if tesseract_available():
        src = pdf_from_lines(STRONG); out = pymupdf.open()
        for p in src:
            pix = p.get_pixmap(dpi=150); pg = out.new_page(width=p.rect.width, height=p.rect.height)
            pg.insert_image(pg.rect, pixmap=pix)
        ex = extract_bytes("scanned.pdf", out.tobytes())
        ev = evaluate(ex["text"], "AI Engineer Intern", ROLES); dec, _r = classify(ev, ex["warnings"])
        print("scanned:", ev["basic_pct"], ev["score"], dec, ex["warnings"], ex["notes"])
        ok &= check("scanned PDF: OCR used", ex["pages"][0]["used_ocr"])
        ok &= check("scanned PDF: text recovered, basic >= 60%", ev["basic_pct"] >= 60)
    else:
        print("SKIP scanned test (Tesseract not installed)")
    # 5 blank / unreadable -> warning -> manual review
    blank = pymupdf.open(); blank.new_page()
    ex = extract_bytes("blank.pdf", blank.tobytes())
    ev = evaluate(ex["text"], "AI Engineer Intern", ROLES); dec, _r = classify(ev, ex["warnings"])
    ok &= check("blank PDF => warning + MANUAL REVIEW", bool(ex["warnings"]) and dec == "MANUAL REVIEW")
    # 6 DOCX with table + header
    from docx import Document
    dd = Document(); dd.add_paragraph("Meera Shah"); dd.add_paragraph("SKILLS")
    t = dd.add_table(rows=1, cols=2); t.rows[0].cells[0].text = "Languages"; t.rows[0].cells[1].text = "Python, JavaScript, SQL, Java"
    dd.add_paragraph("PROJECTS")
    for s in ["Built a REST API with Flask and PostgreSQL for a student portal with JWT authentication.",
              "Designed an object oriented inventory system in Java using data structures such as hash maps.",
              "Developed a MongoDB backed Node.js service with Express.js and Docker deployment."] * 1:
        dd.add_paragraph("• " + s)
    dd.add_paragraph("Additional experience: contributed to open source on GitHub and worked with Git daily for two years.")
    buf = io.BytesIO(); dd.save(buf)
    ex = extract_bytes("backend.docx", buf.getvalue())
    ok &= check("DOCX: table text extracted", "Python, JavaScript" in ex["text"])
    ev = evaluate(ex["text"] + "\n" + "Education: B.Tech Computer Science with coursework in databases and operating systems. " * 3,
                  "Backend Developer Intern", ROLES); dec, _r = classify(ev, ex["warnings"])
    print("backend docx:", ev["basic_pct"], ev["score"], dec, ex["warnings"])
    ok &= check("DOCX backend candidate => SELECT", dec == "SELECT")
    # 7 borderline: basics 4/8 = 50% -> MANUAL REVIEW; good-to-have alone cannot rescue
    t = "SKILLS\nPython, LLM, RAG, Prompt Engineering\nGood extras: FastAPI, Flask, LangChain, Hugging Face, Claude, ChatGPT, Gemini, JSON\n" + "Interests: reading and travel. " * 12
    ev = evaluate(t, "AI Engineer Intern", ROLES); dec, _r = classify(ev, [])
    print("borderline:", ev["basic_pct"], ev["bonus"], ev["score"], dec)
    ok &= check("good-to-have bonus cannot lift a 50% basic candidate to SELECT", dec == "MANUAL REVIEW" and ev["bonus"] > 5)
    # 8 India-only rule (six roles): "India" or +91 required, else REJECT; other roles unaffected
    body = "SKILLS\nCanva, Photoshop, Figma, Instagram, Content Creation, Copywriting, Digital Marketing, Video Editing, Reels\n" + \
           "PROJECTS\n- Designed posters, logos and banners in Canva and Photoshop for a college fest.\n" + "Interests: reading and travel. " * 12
    for label, head, want in [("no country, no +91", "Priya Singh\npriya@example.com\n", "REJECT"),
                              ("mentions India", "Priya Singh\nPune, India\n", None),
                              ("INDIA uppercase", "Priya Singh\nINDIA\n", None),
                              ("+91 phone", "Priya Singh\n+91 98765 43210\n", None),
                              ("+91 no space", "Priya Singh\n+919876543210\n", None),
                              ("other country phone", "Priya Singh\n+1 415 555 0100\nCalifornia, USA\n", "REJECT"),
                              ("Indiana is not India", "Priya Singh\nIndiana, USA\n", "REJECT")]:
        ev = evaluate(head + body, "Graphic Design Intern", ROLES); dec, _r = classify(ev, [])
        ok &= check(f"India rule [{label}] => {want or 'not rejected'}", (dec == "REJECT") if want else (dec != "REJECT"))
    ev = evaluate("Priya Singh\npriya@example.com\n" + body, "Python Developer Intern", ROLES)
    ok &= check("India rule does not apply to non-listed roles", not ev["india_only"])
    # 9 Pakistan / Bangladesh => REJECT for EVERY role (strong skills must not rescue it)
    py = "SKILLS\nPython, OOP, Data Structures, Functions, Exception Handling, File Handling\nPROJECTS\n- Built a Python file handling tool using OOP.\n" + "Interests: reading and travel. " * 12
    for label, head in [("word Pakistan", "Ali Khan\nLahore, Pakistan\n"), ("PAKISTAN uppercase", "Ali Khan\nPAKISTAN\n"),
                        ("nationality Pakistani", "Ali Khan\nNationality: Pakistani\n"), ("+92 phone", "Ali Khan\n+92 300 1234567\n"),
                        ("word Bangladesh", "Rahim\nDhaka, Bangladesh\n"), ("nationality Bangladeshi", "Rahim\nNationality: Bangladeshi\n"),
                        ("+880 phone", "Rahim\n+880 1711 123456\n")]:
        for role in ("Python Developer Intern", "Graphic Design Intern"):
            ev = evaluate(head + py, role, ROLES); dec, _r = classify(ev, [])
            ok &= check(f"blocked country [{label}] / {role} => REJECT", dec == "REJECT" and ev["blocked"]["rejected"])
    ev = evaluate("Priya\n+91 98765 43210\nPune, India\n" + py, "Python Developer Intern", ROLES); dec, _r = classify(ev, [])
    ev = evaluate("Ali Khan\nLahore, Pakistan\n" + py, "Python Developer Intern", ROLES)
    ok &= check("blocked country wins even when extraction warnings exist", classify(ev, ["Very little text extracted"])[0] == "REJECT")
    ev = evaluate("Priya\n+91 98765 43210\nPune, India\n" + py, "Python Developer Intern", ROLES); dec, _r = classify(ev, [])
    ok &= check("Indian resume not affected by blocked-country rule", not ev["blocked"]["rejected"] and dec != "REJECT")
    print("\nALL PASSED" if ok else "\nSOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
