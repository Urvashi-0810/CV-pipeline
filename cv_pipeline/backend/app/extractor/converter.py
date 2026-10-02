"""
STEP 2a - Turn a CV file (PDF / DOCX / DOC / image) into clean Markdown text using Docling.

Docling handles multi-column layouts, tables, and runs OCR on scanned PDFs/images.
The Markdown is then given to the LLM (step 2b). Docling does NOT extract fields
itself - all field extraction is done by the LLM, as required.

Resilience:
  * OCR on/off is controlled by DOCLING_OCR in .env (default on).
  * If the OCR engine fails (e.g. its models can't be downloaded), we retry the same
    file with OCR off - digital PDFs still convert perfectly without OCR.
  * If there is still no usable text (a scan), the caller sends the raw PDF/image
    to the LLM's vision input instead (cv_extractor.py).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.logger import get_logger

log = get_logger("converter")

MIN_USEFUL_CHARS = 200   # less text than this = probably a scan where OCR failed/was off
OCR_ENABLED = os.getenv("DOCLING_OCR", "true").lower() in ("1", "true", "yes")

_converters: dict[bool, object] = {}   # Docling models are heavy -> build once per OCR mode, reuse


def _get_converter(ocr: bool):
    if ocr not in _converters:
        # imported lazily: docling import takes a few seconds
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

        opts = PdfPipelineOptions()
        opts.do_ocr = ocr
        opts.do_table_structure = True
        log.info("Loading Docling (ocr=%s) - first run downloads models, can take a minute...", ocr)
        _converters[ocr] = DocumentConverter(format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
        })
    return _converters[ocr]


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


def _convert(src: Path, ocr: bool) -> str:
    result = _get_converter(ocr).convert(str(src))
    return (result.document.export_to_markdown() or "").strip()


def convert_to_markdown(path: Path) -> str:
    """Return the CV content as Markdown ('' if nothing could be read)."""
    with tempfile.TemporaryDirectory() as tmp:
        src = path
        if path.suffix.lower() == ".doc":
            src = _doc_to_docx(path, Path(tmp))

        if OCR_ENABLED:
            try:
                return _convert(src, ocr=True)
            except Exception as e:  # noqa: BLE001
                log.warning("Docling with OCR failed on %s (%s) - retrying without OCR", path.name, str(e)[:150])
        try:
            return _convert(src, ocr=False)
        except Exception as e:  # noqa: BLE001
            if src.suffix.lower() != ".pdf":
                raise
            # Last resort for PDFs: plain text layer (no layout models needed, works offline)
            log.warning("Docling failed on %s (%s) - using plain PDF text layer", path.name, str(e)[:150])
            return _pdf_plain_text(src)


def _pdf_plain_text(path: Path) -> str:
    import pypdfium2 as pdfium   # installed together with docling
    pdf = pdfium.PdfDocument(str(path))
    try:
        return "\n\n".join(page.get_textpage().get_text_range() for page in pdf).strip()
    finally:
        pdf.close()


def has_enough_text(text: str) -> bool:
    return len(text.replace("<!-- image -->", "").strip()) >= MIN_USEFUL_CHARS
