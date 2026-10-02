"""
End-to-end extraction test with the LLM mocked:
CV files in folder -> converter -> (mock) LLM -> Excel rows, skip on rerun, both providers.
Needs sample CVs; they are generated on the fly (reportlab + python-docx).
"""
import shutil

import pytest

import mock_llm
from app.core.config import settings
from app.extractor import cv_extractor
from app.storage.excel_store import load_candidates

mock_llm.start()

CV_TEXT = """Rahul Sharma  rahul.sharma@example.com  +91 98765 43210  Pune
Senior Software Engineer, Infosys - Jan 2021 to Present. Software Engineer, TCS - 2016 to 2020.
Skills: Python, Django, AWS, Docker. Education: B.E. Computer Engineering, University of Pune 2016.
Languages: English, Hindi. Certifications: AWS Solutions Architect. """ * 2


@pytest.fixture(autouse=True)
def clean():
    for d in (settings.cv_folder, settings.state_folder, settings.output_excel.parent):
        shutil.rmtree(d, ignore_errors=True)
    settings.ensure_dirs()
    yield


def make_pdf(path):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path))
    y = 800
    for i in range(0, len(CV_TEXT), 90):
        c.drawString(30, y, CV_TEXT[i:i + 90]); y -= 15
    c.save()


def make_docx(path):
    from docx import Document
    d = Document(); d.add_paragraph(CV_TEXT); d.save(path)


def test_extract_to_excel_and_skip_on_rerun():
    make_pdf(settings.cv_folder / "rahul.pdf")
    make_docx(settings.cv_folder / "rahul2.docx")
    (settings.cv_folder / "ignore.txt").write_text("not a cv")

    r = cv_extractor.run_extraction()
    assert r.found == 2 and r.extracted == 2 and r.failed == 0, r.errors
    df = load_candidates()
    assert len(df) == 2
    assert df.iloc[0]["Full Name"] == "Rahul Sharma"
    assert df.iloc[0]["Age"] == 32 or df.iloc[0]["Age"] == 33       # computed from DOB in Python
    assert "Python" in df.iloc[0]["Skills"] and df.iloc[0]["Skills"].count("Python") == 1

    r = cv_extractor.run_extraction()                                 # second run: nothing to do
    assert r.extracted == 0 and r.skipped_already_done == 2

    r = cv_extractor.run_extraction(provider="gemini", force=True)    # force + other provider
    assert r.extracted == 2
    df = load_candidates()
    assert len(df) == 2 and set(df["LLM"]) == {"gemini"}              # rows replaced, not duplicated


def test_image_cv_is_extracted():
    """Image CV: Docling OCR text if available, otherwise LLM vision fallback."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1000, 400), "white")
    ImageDraw.Draw(img).text((20, 20), "Rahul Sharma - Python developer", fill="black")
    img.save(settings.cv_folder / "scan.png")
    r = cv_extractor.run_extraction()
    assert r.extracted == 1, r.errors
    assert load_candidates().iloc[0]["Extraction Method"] in ("text", "vision")


def test_bad_file_does_not_stop_batch():
    make_pdf(settings.cv_folder / "good.pdf")
    (settings.cv_folder / "broken.docx").write_bytes(b"this is not a docx")
    r = cv_extractor.run_extraction()
    assert r.extracted == 1 and r.failed == 1 and "broken.docx" in r.errors
