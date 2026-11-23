"""Makes the pictures shown for events that have none of their own, in the site's street-sign style.

Run once (Pillow isn't an app dependency): uv run --with pillow python -m scripts.make_pictures
Writes app/static/pictures/<kind>.png, 1280x720.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

KINDS = {
    # kind: (label, background, text colour)
    "music": ("Live music", "#000000", "#ffffff"),
    "comedy": ("Comedy", "#ffc20e", "#000000"),
    "arts": ("Arts & culture", "#000000", "#ffffff"),
    "sports": ("Game night", "#ffc20e", "#000000"),
    "outdoors": ("Outdoors", "#000000", "#ffffff"),
    "city": ("Around the city", "#ffc20e", "#000000"),
}
WIDTH, HEIGHT, SQUARE = 1280, 720, 40
OUT = Path(__file__).parent.parent / "app" / "static" / "pictures"


def font(size: int) -> ImageFont.FreeTypeFont:
    for path in ("/System/Library/Fonts/HelveticaNeue.ttc", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(path, size, index=1)  # bold
        except OSError:
            continue
    return ImageFont.load_default(size)


def make(kind: str, label: str, background: str, ink: str) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), background)
    draw = ImageDraw.Draw(image)
    other = "#ffc20e" if background == "#000000" else "#000000"
    # The taxi checker band along the bottom, as in the email header.
    for row in range(2):
        for column in range(WIDTH // SQUARE + 1):
            if (column + row) % 2 == 0:
                top = HEIGHT - SQUARE * (2 - row)
                draw.rectangle((column * SQUARE, top, column * SQUARE + SQUARE - 1, top + SQUARE - 1), fill=other)
    # A street-sign rule above the label, then the label, large and left aligned.
    draw.rectangle((96, 236, 96 + 260, 250), fill=ink)
    draw.text((92, 280), label, font=font(132), fill=ink)
    draw.text((96, 440), "whatisupnyc", font=font(44), fill=ink)
    image.save(OUT / f"{kind}.png", optimize=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for kind, (label, background, ink) in KINDS.items():
        make(kind, label, background, ink)
        print(f"{kind}.png")
