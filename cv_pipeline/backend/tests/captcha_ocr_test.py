from pathlib import Path
import easyocr

IMAGE_PATH = Path(r"C:\Users\HP\Desktop\captcha.png")

if not IMAGE_PATH.is_file():
    raise FileNotFoundError(f"Image not found: {IMAGE_PATH}")

# English-language OCR; use CPU to avoid requiring a GPU.
reader = easyocr.Reader(["en"], gpu=False)

results = reader.readtext(str(IMAGE_PATH))

if not results:
    print("No text detected.")
else:
    for box, text, confidence in results:
        print(f"Text: {text}")
        print(f"Confidence: {confidence:.3f}")
        print("-" * 30)
