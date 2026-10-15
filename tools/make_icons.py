"""Draw the toolbar icons into resources/.

    python tools/make_icons.py

Follows what fusion-utp/tools/make_icons.py measured off Fusion's own icons:
one file per size, theme and display density, filling the frame, pale faces
with one light-blue accent, drawn at 8x and reduced with LANCZOS.

The picture: an upright cylinder with a blue "T" sunk into its front.
"""

import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources")
THEMES = {
    "": {"face": (236, 236, 236), "shade": (190, 190, 190), "ink": (90, 90, 90)},
    "-dark": {"face": (120, 120, 120), "shade": (90, 90, 90), "ink": (220, 220, 220)},
}
ACCENT = (80, 160, 230)


def draw(size, colours):
    s = size * 8
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = max(8, s // 24)            # outline width
    left, right = w // 2, s - w // 2 - 1
    cap = s // 5                   # height of the elliptical ends
    top, bottom = cap // 2 + w // 2, s - cap // 2 - w // 2 - 1
    # Body, then the shaded right side, then the ends.
    d.rectangle([left, top, right, bottom], fill=colours["face"])
    d.rectangle([s * 3 // 4, top, right, bottom], fill=colours["shade"])
    d.ellipse([left, bottom - cap // 2, right, bottom + cap // 2], fill=colours["face"], outline=colours["ink"], width=w)
    d.rectangle([left + w, top, right - w, bottom], fill=colours["face"])
    d.rectangle([s * 3 // 4, top, right - w, bottom], fill=colours["shade"])
    d.line([left + w // 2, top, left + w // 2, bottom], fill=colours["ink"], width=w)
    d.line([right - w // 2, top, right - w // 2, bottom], fill=colours["ink"], width=w)
    d.ellipse([left, top - cap // 2, right, top + cap // 2], fill=colours["face"], outline=colours["ink"], width=w)
    # The sunk letter.
    bar = s // 7
    d.rectangle([s * 22 // 100, s * 36 // 100, s * 72 // 100, s * 36 // 100 + bar], fill=ACCENT)
    d.rectangle([s * 47 // 100 - bar // 2, s * 36 // 100, s * 47 // 100 + bar // 2, s * 84 // 100], fill=ACCENT)
    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    for base in (16, 32, 64):
        for theme, colours in THEMES.items():
            draw(base, colours).save(os.path.join(OUT, "%dx%d%s.png" % (base, base, theme)))
            draw(base * 2, colours).save(os.path.join(OUT, "%dx%d%s@2x.png" % (base, base, theme)))


if __name__ == "__main__":
    main()
