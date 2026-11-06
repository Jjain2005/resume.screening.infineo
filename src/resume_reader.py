"""Resume text extraction: PDF (text layer + page-level OCR), DOCX (ordered, tables, text boxes,
headers/footers, image OCR), images and TXT. Everything is local; no network or API is used.

Every reader returns a dict:
  text, pages[{page, text, used_ocr, ocr_conf, chars, image_coverage}], warnings[], notes[]
`warnings` mean "a human must look at this resume" (the screening decision is forced to MANUAL REVIEW).
`notes` are informational only (e.g. "OCR was used on page 2").
"""
import io
import re
from functools import lru_cache
from pathlib import Path

import pymupdf
from PIL import Image

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None

MIN_DOC_CHARS = 250          # fewer readable characters than this => warn
PAGE_OCR_MIN_CHARS = 80      # page with fewer letters/digits than this is treated as scanned
IMAGE_HEAVY = 0.5            # fraction of page covered by images
LOW_OCR_CONF = 55            # mean Tesseract word confidence below this => warn
OCR_DPI = 250

_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)


# --------------------------------------------------------------------------- helpers
@lru_cache(maxsize=1)
def tesseract_available() -> bool:
    if pytesseract is None:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def clean_text(text: str) -> str:
    for k, v in _LIGATURES.items():
        text = text.replace(k, v)
    text = text.translate(_ZERO_WIDTH).replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def alnum_count(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


def looks_garbled(text: str) -> bool:
    """Detect broken font encodings: replacement chars, (cid:NN) tokens, private-use glyphs."""
    if not text:
        return False
    bad = text.count("\ufffd") + len(re.findall(r"\(cid:\d+\)", text)) * 6
    bad += sum(1 for ch in text if 0xE000 <= ord(ch) <= 0xF8FF)
    return bad / max(1, len(text)) > 0.05


def ocr_image(img: Image.Image, lang: str = "eng"):
    """Return (text, mean_confidence). Raises if Tesseract is unavailable."""
    img = img.convert("RGB")
    data = pytesseract.image_to_data(img, lang=lang, config="--psm 3",
                                     output_type=pytesseract.Output.DICT)
    lines, confs, order = {}, [], []
    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        if not word:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(word)
        if conf >= 0:
            confs.append(conf)
    text = "\n".join(" ".join(lines[k]) for k in order)
    return text, (sum(confs) / len(confs) if confs else 0.0)


# --------------------------------------------------------------------------- PDF
def _image_coverage(page) -> float:
    area = page.rect.width * page.rect.height
    if area <= 0:
        return 0.0
    covered = 0.0
    try:
        for info in page.get_image_info():
            r = pymupdf.Rect(info["bbox"]) & page.rect
            covered += max(0, r.width) * max(0, r.height)
    except Exception:
        return 0.0
    return min(1.0, covered / area)


def _order_blocks(blocks, width):
    """Reading order for text blocks. Handles two-column and sidebar layouts: full-width blocks
    (headers) split the page into bands; inside a band, a left column is read before the right one."""
    blocks = sorted(blocks, key=lambda b: (round(b[1]), b[0]))
    bands, cur = [], []
    for b in blocks:
        if (b[2] - b[0]) > 0.62 * width:
            if cur:
                bands.append(("cols", cur))
                cur = []
            bands.append(("span", [b]))
        else:
            cur.append(b)
    if cur:
        bands.append(("cols", cur))
    ordered = []
    for kind, bl in bands:
        if kind == "span":
            ordered.extend(bl)
            continue
        mid = width / 2
        left = [b for b in bl if (b[0] + b[2]) / 2 < mid]
        right = [b for b in bl if (b[0] + b[2]) / 2 >= mid]
        two_col = (len(left) >= 2 and len(right) >= 2
                   and max(b[2] for b in left) <= min(b[0] for b in right) + 3)
        if two_col:
            ordered.extend(sorted(left, key=lambda b: (b[1], b[0])))
            ordered.extend(sorted(right, key=lambda b: (b[1], b[0])))
        else:
            ordered.extend(sorted(bl, key=lambda b: (round(b[1]), b[0])))
    return ordered


def _native_page_text(page) -> str:
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    text = "\n".join(b[4].strip() for b in _order_blocks(blocks, page.rect.width))
    links = []
    try:
        for ln in page.get_links():
            uri = ln.get("uri")
            if uri and uri not in links:
                links.append(uri)
    except Exception:
        pass
    if links:
        text += "\nLinks: " + " ".join(links)
    return text


def read_pdf(data: bytes, lang: str = "eng") -> dict:
    warnings, notes, pages = [], [], []
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        return dict(text="", pages=[], warnings=[f"PDF could not be opened: {exc}"], notes=[])
    if doc.needs_pass and not doc.authenticate(""):
        return dict(text="", pages=[], warnings=["PDF is password-protected and could not be read."], notes=[])

    ocr_ok = tesseract_available()
    ocr_warned = False
    for i, page in enumerate(doc, start=1):
        native = clean_text(_native_page_text(page))
        cov = _image_coverage(page)
        n_chars = alnum_count(native)
        garbled = looks_garbled(native)
        needs_ocr = n_chars < PAGE_OCR_MIN_CHARS or garbled or (cov > IMAGE_HEAVY and n_chars < 500)
        text, used_ocr, conf = native, False, None
        if needs_ocr:
            if not ocr_ok:
                if not ocr_warned:
                    warnings.append("Tesseract OCR is not installed, but page(s) look scanned or image-based "
                                    "(brew install tesseract / apt install tesseract-ocr).")
                    ocr_warned = True
                warnings.append(f"Page {i}: needs OCR but OCR is unavailable.")
            else:
                try:
                    pix = page.get_pixmap(dpi=OCR_DPI, alpha=False)
                    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    o_text, conf = ocr_image(img, lang)
                    o_text = clean_text(o_text)
                    if alnum_count(o_text) > n_chars * 1.15 or garbled:
                        text, used_ocr = o_text, True
                        notes.append(f"Page {i}: OCR used (confidence {conf:.0f}).")
                        if conf < LOW_OCR_CONF:
                            warnings.append(f"Page {i}: low OCR confidence ({conf:.0f}); text may be inaccurate.")
                except Exception as exc:
                    warnings.append(f"Page {i}: OCR failed ({exc}).")
        if garbled and not used_ocr:
            warnings.append(f"Page {i}: text layer looks garbled (broken font encoding).")
        if alnum_count(text) == 0:
            warnings.append(f"Page {i}: no readable text could be extracted.")
        pages.append(dict(page=i, text=text, used_ocr=used_ocr,
                          ocr_conf=None if conf is None else round(conf, 1),
                          chars=len(text), image_coverage=round(cov, 2)))
    doc.close()
    full = "\n\n".join(f"[Page {p['page']}]\n{p['text']}" for p in pages)
    if alnum_count(full) < MIN_DOC_CHARS:
        warnings.append(f"Very little text extracted ({alnum_count(full)} characters); the resume may be unreadable.")
    return dict(text=full, pages=pages, warnings=warnings, notes=notes)


# --------------------------------------------------------------------------- DOCX
def _docx_table_lines(table, lines, seen_tc=None):
    for row in table.rows:
        cells, seen = [], set()
        for cell in row.cells:
            if id(cell._tc) in seen:          # merged cells repeat the same underlying element
                continue
            seen.add(id(cell._tc))
            t = " ".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
            if t:
                cells.append(t)
            for nested in cell.tables:
                _docx_table_lines(nested, lines)
        if cells:
            lines.append(" | ".join(cells))


def read_docx(data: bytes, lang: str = "eng") -> dict:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    warnings, notes = [], []
    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:
        return dict(text="", pages=[], warnings=[f"DOCX could not be opened: {exc}"], notes=[])

    lines = []
    for child in doc.element.body.iterchildren():          # keeps paragraph/table order
        if child.tag == qn("w:p"):
            t = Paragraph(child, doc).text.strip()
            if t:
                lines.append(t)
        elif child.tag == qn("w:tbl"):
            _docx_table_lines(Table(child, doc), lines)

    # text boxes (common in designed resumes) - the XML often holds each box twice, so de-duplicate
    seen = set(lines)
    for box in doc.element.body.iter(qn("w:txbxContent")):
        for p in box.iter(qn("w:p")):
            t = "".join(x.text or "" for x in p.iter(qn("w:t"))).strip()
            if t and t not in seen:
                seen.add(t)
                lines.append(t)
    # headers / footers often hold name, email, links
    for sec in doc.sections:
        for part in (sec.header, sec.footer):
            for p in part.paragraphs:
                t = p.text.strip()
                if t and t not in seen:
                    seen.add(t)
                    lines.append(t)

    text = clean_text("\n".join(lines))
    # DOCX made of pasted images -> OCR them
    if alnum_count(text) < MIN_DOC_CHARS:
        imgs = [r.target_part.blob for r in doc.part.rels.values()
                if "image" in r.reltype and hasattr(r, "target_part")]
        if imgs:
            if not tesseract_available():
                warnings.append("DOCX contains images and little text, but Tesseract OCR is not installed.")
            else:
                ocr_parts = []
                for blob in imgs:
                    try:
                        t, conf = ocr_image(Image.open(io.BytesIO(blob)), lang)
                        if alnum_count(t) > 20:
                            ocr_parts.append(t)
                            if conf < LOW_OCR_CONF:
                                warnings.append(f"Low OCR confidence ({conf:.0f}) on an image inside the DOCX.")
                    except Exception:
                        continue
                if ocr_parts:
                    text = clean_text(text + "\n" + "\n".join(ocr_parts))
                    notes.append("OCR used on images embedded in the DOCX.")
    if alnum_count(text) < MIN_DOC_CHARS:
        warnings.append(f"Very little text extracted ({alnum_count(text)} characters); the resume may be unreadable.")
    return dict(text=text, pages=[dict(page=1, text=text, used_ocr=False, ocr_conf=None,
                                       chars=len(text), image_coverage=0.0)],
                warnings=warnings, notes=notes)


# --------------------------------------------------------------------------- images / txt
def read_image(data: bytes, lang: str = "eng") -> dict:
    if not tesseract_available():
        return dict(text="", pages=[], warnings=["Image resumes need Tesseract OCR, which is not installed."], notes=[])
    try:
        text, conf = ocr_image(Image.open(io.BytesIO(data)), lang)
    except Exception as exc:
        return dict(text="", pages=[], warnings=[f"Image could not be read: {exc}"], notes=[])
    text = clean_text(text)
    warnings = []
    if conf < LOW_OCR_CONF:
        warnings.append(f"Low OCR confidence ({conf:.0f}); text may be inaccurate.")
    if alnum_count(text) < MIN_DOC_CHARS:
        warnings.append(f"Very little text extracted ({alnum_count(text)} characters).")
    return dict(text=text, pages=[dict(page=1, text=text, used_ocr=True, ocr_conf=round(conf, 1),
                                       chars=len(text), image_coverage=1.0)],
                warnings=warnings, notes=[f"OCR used (confidence {conf:.0f})."])


def read_txt(data: bytes) -> dict:
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = clean_text(data.decode(enc))
            break
        except Exception:
            continue
    warnings = []
    if alnum_count(text) < MIN_DOC_CHARS:
        warnings.append(f"Very little text ({alnum_count(text)} characters).")
    return dict(text=text, pages=[dict(page=1, text=text, used_ocr=False, ocr_conf=None,
                                       chars=len(text), image_coverage=0.0)], warnings=warnings, notes=[])


SUPPORTED = ["pdf", "docx", "png", "jpg", "jpeg", "txt"]


def extract_bytes(name: str, data: bytes, lang: str = "eng") -> dict:
    suffix = Path(name).suffix.lower().lstrip(".")
    try:
        if suffix == "pdf":
            res = read_pdf(data, lang)
        elif suffix == "docx":
            res = read_docx(data, lang)
        elif suffix in ("png", "jpg", "jpeg"):
            res = read_image(data, lang)
        elif suffix == "txt":
            res = read_txt(data)
        elif suffix == "doc":
            res = dict(text="", pages=[], warnings=["Legacy .doc is not supported; save it as DOCX or PDF."], notes=[])
        else:
            res = dict(text="", pages=[], warnings=[f"Unsupported file type .{suffix}."], notes=[])
    except Exception as exc:  # never crash the batch on one bad file
        res = dict(text="", pages=[], warnings=[f"Unexpected extraction error: {exc}"], notes=[])
    res["filename"] = name
    return res
