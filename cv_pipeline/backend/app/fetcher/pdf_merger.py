from pathlib import Path
from io import BytesIO
from PIL import Image
from pypdf import PdfReader, PdfWriter


def merge_attachments_to_pdf(
    attachment_paths: list[Path],
    output_pdf: Path,
) -> Path:
    """
    Merge supported attachments into one PDF.

    Supported directly:
    - PDF
    - PNG, JPG, JPEG, TIFF, BMP images

    Other formats require conversion to PDF first.
    """

    writer = PdfWriter()
    image_buffers = []

    try:
        for file_path in attachment_paths:
            file_path = Path(file_path)
            extension = file_path.suffix.lower()

            if extension == ".pdf":
                reader = PdfReader(str(file_path))

                for page in reader.pages:
                    writer.add_page(page)

            elif extension in {
                ".png", ".jpg", ".jpeg",
                ".tif", ".tiff", ".bmp"
            }:
                image = Image.open(file_path)

                if image.mode != "RGB":
                    image = image.convert("RGB")

                buffer = BytesIO()
                image.save(buffer, format="PDF")
                buffer.seek(0)

                image_buffers.append(buffer)
                reader = PdfReader(buffer)

                for page in reader.pages:
                    writer.add_page(page)

            else:
                raise ValueError(
                    f"Unsupported attachment format: {file_path.name}. "
                    "Convert it to PDF before merging."
                )

        if not writer.pages:
            raise ValueError("No supported pages to merge.")

        output_pdf.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with output_pdf.open("wb") as output_file:
            writer.write(output_file)

        return output_pdf

    finally:
        for buffer in image_buffers:
            buffer.close()
