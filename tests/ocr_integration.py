"""Manual OCR smoke test used in Docker verification."""

from pathlib import Path
import tempfile

from PIL import Image, ImageDraw, ImageFont

from rag_assistant.parsers import _ocr_image_blocks, parse_pdf


def main() -> None:
    image = Image.new("RGB", (900, 440), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    font = ImageFont.truetype(str(font_path), 30)
    xs = [20, 500, 880]
    ys = [20, 120, 220, 320, 420]
    for x in xs:
        draw.line((x, ys[0], x, ys[-1]), fill="black", width=3)
    for y in ys:
        draw.line((xs[0], y, xs[-1], y), fill="black", width=3)
    values = [
        ("Параметр", "Значение"),
        ("Давление", "5 МПа"),
        ("Температура", "120 °C"),
        ("Период ТО", "500 часов"),
    ]
    for row, (left, right) in enumerate(values):
        draw.text((40, ys[row] + 30), left, font=font, fill="black")
        draw.text((520, ys[row] + 30), right, font=font, fill="black")
    blocks = _ocr_image_blocks(image, "тестовая страница")
    combined = "\n".join(block.text for block in blocks)
    print(combined)
    print("block_types", [block.block_type for block in blocks])
    assert "Давлен" in combined, combined
    assert "5" in combined and "МПа" in combined, combined
    assert "500" in combined, combined
    assert any(block.block_type == "ocr_table" and " | " in block.text for block in blocks), blocks
    with tempfile.TemporaryDirectory() as folder:
        pdf_path = Path(folder) / "scanned-table.pdf"
        image.save(pdf_path, "PDF", resolution=170)
        pdf_blocks = parse_pdf(pdf_path)
        pdf_text = "\n".join(block.text for block in pdf_blocks)
        print("pdf_block_types", [block.block_type for block in pdf_blocks])
        assert "Давлен" in pdf_text and "500" in pdf_text, pdf_text
        assert any(block.block_type == "ocr_table" for block in pdf_blocks), pdf_blocks


if __name__ == "__main__":
    main()
