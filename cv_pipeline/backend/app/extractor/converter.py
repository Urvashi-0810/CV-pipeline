"""
How a file is turned into something the LLM can read.

Two modes, chosen with CV_PARSER in .env:

  CV_PARSER=llm      (default) - the LLM parses the resume itself:
        PDF / images  -> the original file is sent straight to Claude/Gemini (both read PDFs
                         and images natively, including scans and multi-column layouts)
        DOCX          -> LLMs can't open Word files, so its raw text is pulled out with
                         python-docx (no layout/OCR models) and sent as text
        DOC           -> converted to DOCX with LibreOffice first (if installed)
     No Docling, no ML models to download.

  CV_PARSER=docling  - Docling converts every file to Markdown first (OCR, layout models),
                       the LLM gets text. Needs:  pip install -r requirements-docling.txt

In BOTH modes all CV fields are extracted by the LLM - this module only decides what to send.

`extract_text()` is used for job descriptions (plain text needed for grounding checks).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from app.core.config import IMAGE_EXTENSIONS, settings
from app.core.logger import get_logger

log = get_logger("converter")

MIN_USEFUL_CHARS = 200   # less text than this = probably a scan where text extraction failed


def has_enough_text(text: str) -> bool:
    return len((text or "").replace("<!-- image -->", "").strip()) >= MIN_USEFUL_CHARS


def llm_reads_file(path: Path) -> bool:
    """File types Claude/Gemini accept as a document/image input."""
    ext = path.suffix.lower()
    return ext == ".pdf" or ext in IMAGE_EXTENSIONS


# ======================================================================
#  Main entry for CVs: what should the LLM receive?
# ======================================================================
def prepare_for_llm(path: Path) -> tuple[str, Optional[Path], str]:
    """
    Returns (text, file, method):
        text   - text to send ('' if the file itself is sent)
        file   - file to attach for the LLM to read itself (None if text is enough)
        method - short label stored in Excel ("llm-file", "docx-text", "docling", "docling+file")
    """
    mode = settings.cv_parser
    if mode == "docling":
        return _prepare_docling(path)

    # ---- LLM mode
    if llm_reads_file(path):
        return "", path, "llm-file"
    text = extract_word_text(path)
    if not text.strip():
        raise RuntimeError("Word file contains no text")
    return text, None, "docx-text"


# ======================================================================
#  Plain text helpers (no ML models)
# ======================================================================
def extract_word_text(path: Path) -> str:
    """DOCX (or DOC via LibreOffice) -> text, including tables, headers and text boxes in body."""
    with tempfile.TemporaryDirectory() as tmp:
        src = _doc_to_docx(path, Path(tmp)) if path.suffix.lower() == ".doc" else path
        from docx import Document
        try:
            doc = Document(str(src))
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Could not open Word file: {e}") from e
        parts: list[str] = []
        for section in doc.sections:                         # headers often hold name/contact
            parts += [p.text for p in section.header.paragraphs if p.text.strip()]
        parts += [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:                             # many CV templates are tables
            for row in table.rows:
                cells = []
                for c in row.cells:
                    t = c.text.strip()
                    if t and t not in cells:                 # merged cells repeat their text
                        cells.append(t)
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts).strip()


def extract_pdf_text(path: Path) -> str:
    """Text layer of a digital PDF (empty for scans)."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    try:
        return "\n\n".join(page.get_textpage().get_text_range() for page in pdf).strip()
    finally:
        pdf.close()


def extract_text(path: Path) -> str:
    """Any supported document -> plain text. Used for JOB DESCRIPTIONS."""
    ext = path.suffix.lower()
    if ext in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore")
    if ext in (".docx", ".doc"):
        return extract_word_text(path)
    if ext == ".pdf":
        text = extract_pdf_text(path)
        if not text.strip() and settings.cv_parser == "docling":
            text = convert_to_markdown(path)
        if not text.strip():
            raise RuntimeError("This PDF has no text layer (scanned). Paste the JD as text instead.")
        return text
    raise RuntimeError(f"Unsupported JD file type {ext} - use .pdf, .docx, .txt or .md")


def _doc_to_docx(path: Path, out_dir: Path) -> Path:
    """Old Word .doc -> .docx with LibreOffice (if installed)."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError(".doc file needs LibreOffice installed (or re-save the CV as .docx/.pdf)")
    subprocess.run(
        [soffice, "--headless", "--convert-to", "docx", "--outdir", str(out_dir), str(path)],
        check=True, capture_output=True, timeout=120,
    )
    return out_dir / (path.stem + ".docx")


# ======================================================================
#  Docling mode (optional)
# ======================================================================
_converters: dict[bool, object] = {}   # Docling models are heavy -> build once per OCR mode


def _ocr_enabled() -> bool:
    return os.getenv("DOCLING_OCR", "true").lower() in ("1", "true", "yes")


def _get_converter(ocr: bool):
    if ocr not in _converters:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
        except ImportError as e:
            raise RuntimeError("CV_PARSER=docling but Docling is not installed: "
                               "pip install -r requirements-docling.txt  (or set CV_PARSER=llm)") from e
        opts = PdfPipelineOptions()
        opts.do_ocr = ocr
        opts.do_table_structure = True
        log.info("Loading Docling (ocr=%s) - first run downloads models, can take a minute...", ocr)
        _converters[ocr] = DocumentConverter(format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
        })
    return _converters[ocr]


def _docling(src: Path, ocr: bool) -> str:
    result = _get_converter(ocr).convert(str(src))
    return (result.document.export_to_markdown() or "").strip()


def convert_to_markdown(path: Path) -> str:
    """Docling: file -> Markdown. OCR failure -> retry without OCR -> (PDF) plain text layer."""
    with tempfile.TemporaryDirectory() as tmp:
        src = _doc_to_docx(path, Path(tmp)) if path.suffix.lower() == ".doc" else path
        if _ocr_enabled():
            try:
                return _docling(src, ocr=True)
            except RuntimeError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("Docling with OCR failed on %s (%s) - retrying without OCR", path.name, str(e)[:150])
        try:
            return _docling(src, ocr=False)
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            if src.suffix.lower() != ".pdf":
                raise
            log.warning("Docling failed on %s (%s) - using plain PDF text layer", path.name, str(e)[:150])
            return extract_pdf_text(src)


def _prepare_docling(path: Path) -> tuple[str, Optional[Path], str]:
    text = ""
    try:
        text = convert_to_markdown(path)
    except Exception as e:  # noqa: BLE001
        if not llm_reads_file(path):
            raise RuntimeError(f"Could not read file: {e}") from e
        log.warning("Docling failed on %s (%s) -> sending the file to the LLM", path.name, e)
    if has_enough_text(text):
        return text, None, "docling"
    if llm_reads_file(path):
        log.info("Little/no text in %s -> sending the file to the LLM", path.name)
        return text, path, "docling+file"
    raise RuntimeError("No readable text in document")
