"""End-to-end checks. Run from the project folder:  python tests/test_pipeline.py
Builds sample resumes (text PDF, two-column PDF, scanned PDF, DOCX with a table, unrelated resume) and
asserts extraction + evaluation behave. Needs Tesseract for the scanned-PDF check."""
import io, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pymupdf
from src.resume_reader import extract_bytes, ocr_available
from src.evaluator import evaluate, classify, GOOD_TO_HAVE_WEIGHT, SCORE_PASS_LINE
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
    def decision_case(basic_met, basic_total, good_pct=0):
        basic_pct = 100 * basic_met / basic_total
        return dict(basic_met=basic_met, basic_total=basic_total, basic_pct=basic_pct,
                    good_pct=good_pct, score=basic_pct + GOOD_TO_HAVE_WEIGHT * good_pct,
                    blocked={"rejected": False}, india_only=False, india={"passed": True, "error": False})

    ok &= check("Basic floor rejects below 30%",
                classify(decision_case(2, 10, 100))[0] == "REJECT")
    ok &= check("Basic floor rejects fewer than two matched requirements",
                classify(decision_case(1, 2, 100))[0] == "REJECT")
    ok &= check("score exactly 45 selects",
                classify(decision_case(3, 10, 60))[0] == "SELECT")
    ok &= check("score below 45 rejects",
                classify(decision_case(3, 10, 59.9))[0] == "REJECT")
    ok &= check("extraction warnings still force manual review",
                classify(decision_case(10, 10, 100), ["OCR warning"])[0] == "MANUAL REVIEW")
    expected_new_roles = {
        "Founder's Associate Intern": 10,
        "DevOps Intern": 10,
        "Game Developer Intern": 7,
        "Curriculum Developer Intern": 10,
    }
    ok &= check("four new roles load with expected grouped basic requirements",
                all(role in ROLES and len(ROLES[role]["basic"]) == count
                    for role, count in expected_new_roles.items()))
    new_role_samples = {
        "Founder's Associate Intern": (
            "EXPERIENCE\n- Led business development research and competitor analysis for a startup, "
            "prepared business strategy and business analysis in Excel and Google Sheets, made a PowerPoint "
            "presentation, managed the project, communicated findings, and solved operational problems."
        ),
        "DevOps Intern": (
            "SKILLS\nLinux, GitHub, Docker, CI/CD, AWS, Bash, networking, Python, Kubernetes\n"
            "PROJECTS\n- Deployed a Dockerized app to AWS with a continuous integration and delivery pipeline."
        ),
        "Game Developer Intern": (
            "SKILLS\nUnity, C#, game development, game design, 2D game development, gameplay programming, OOP\n"
            "PROJECTS\n- Built a playable 2D Unity game in C# with gameplay programming and object-oriented design."
        ),
        "Curriculum Developer Intern": (
            "SKILLS\nCurriculum Development, Content Development, Instructional Design, Lesson Planning, "
            "Educational Content, Research, Learning Objectives, Content Writing, Subject Knowledge, PowerPoint\n"
            "PROJECTS\n- Developed a curriculum with lesson plans and educational content aligned to learning objectives."
        ),
    }
    new_role_results = {
        role: evaluate(text, role, ROLES)
        for role, text in new_role_samples.items()
    }
    ok &= check("new role examples satisfy their required alternatives",
                all(ev["basic_pct"] >= 60 for ev in new_role_results.values()))
    game_ev = new_role_results["Game Developer Intern"]
    ok &= check("game role accepts Unity and C# without requiring Unreal Engine or C++",
                game_ev["basic_pct"] == 100 and not any(
                    term in game_ev["missing"] for term in ("Unity / Unreal Engine", "C# / C++")))
    devops_ev = new_role_results["DevOps Intern"]
    ok &= check("DevOps role recognizes CI/CD and AWS alternatives",
                devops_ev["rows"][3]["found"] and devops_ev["rows"][4]["found"])

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
    if ocr_available():
        src = pdf_from_lines(STRONG); out = pymupdf.open()
        for p in src:
            pix = p.get_pixmap(dpi=150); pg = out.new_page(width=p.rect.width, height=p.rect.height)
            pg.insert_image(pg.rect, pixmap=pix)
        ex = extract_bytes("scanned.pdf", out.tobytes())
        ev = evaluate(ex["text"], "AI Engineer Intern", ROLES); dec, _r = classify(ev, ex["warnings"])
        print("scanned:", ev["basic_pct"], ev["score"], dec, ex["warnings"], ex["notes"])
        ok &= check("scanned PDF: OCR used and text recovered",
                    ex["pages"][0]["used_ocr"] and "Python" in ex["text"])
        ok &= check("scanned PDF: OCR recovers a strong resume score", ev["basic_pct"] >= 60)
    else:
        print("SKIP scanned tests (no OCR engine installed)")
    # 4b standalone image OCR uses the available OCR engine
    image_doc = pdf_from_lines(["OCR IMAGE TEST: Python and Docker"])
    pix = image_doc[0].get_pixmap(dpi=180)
    image_buf = io.BytesIO()
    from PIL import Image
    Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(image_buf, format="PNG")
    image_ex = extract_bytes("ocr-test.png", image_buf.getvalue())
    ok &= check("PNG: OCR used and text recovered",
                bool(image_ex["pages"]) and image_ex["pages"][0]["used_ocr"]
                and "Python" in image_ex["text"])
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
    # 6b DOCX containing a pasted image and little native text
    image_docx = Document()
    image_docx.add_paragraph("Scan")
    image_docx.add_picture(io.BytesIO(image_buf.getvalue()))
    image_docx_buf = io.BytesIO()
    image_docx.save(image_docx_buf)
    image_docx_ex = extract_bytes("embedded-image.docx", image_docx_buf.getvalue())
    ok &= check("DOCX: OCR used on embedded image",
                "OCR IMAGE TEST" in image_docx_ex["text"] and
                any("OCR used on images embedded" in note for note in image_docx_ex["notes"]))
    # 7 Good-to-have percentage contributes exactly one quarter of its value to score.
    t = "SKILLS\nPython, LLM, RAG, Prompt Engineering\nGood extras: FastAPI, Flask, LangChain, Hugging Face, Claude, ChatGPT, Gemini, JSON\n" + "Interests: reading and travel. " * 12
    ev = evaluate(t, "AI Engineer Intern", ROLES); dec, _r = classify(ev, [])
    print("weighted score:", ev["basic_pct"], ev["good_pct"], ev["score"], dec)
    ok &= check("good-to-have contributes 0.25 × its exact match percentage",
                ev["good_pct"] > 0 and ev["score"] == ev["basic_pct"] + 0.25 * ev["good_pct"])
    ok &= check("score uses unrounded Basic and Good-to-have percentages",
                ev["score"] == ev["basic_pct"] + GOOD_TO_HAVE_WEIGHT * ev["good_pct"])
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
