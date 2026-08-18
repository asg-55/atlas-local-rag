from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


SCALE = 4
SIZE = 256


def scaled(points):
    return [(x * SCALE, y * SCALE) for x, y in points]


def create_mark() -> Image.Image:
    canvas = Image.new("RGBA", (SIZE * SCALE, SIZE * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    margin = 12 * SCALE
    draw.rounded_rectangle(
        (margin, margin, (SIZE * SCALE) - margin, (SIZE * SCALE) - margin),
        radius=45 * SCALE,
        fill=(236, 236, 236, 255),
    )
    outer = scaled([(128, 62), (194, 128), (128, 194), (62, 128)])
    draw.line(outer + [outer[0]], fill=(23, 23, 23, 255), width=12 * SCALE, joint="curve")
    draw.polygon(
        scaled([(128, 108), (148, 128), (128, 148), (108, 128)]),
        fill=(23, 23, 23, 255),
    )
    return canvas.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def main() -> None:
    assets = Path(__file__).resolve().parent / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    mark = create_mark()
    mark.save(assets / "atlas-mark.png", optimize=True)
    mark.save(
        assets / "atlas.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()

