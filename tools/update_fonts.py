"""Refresh palette/google-fonts.js from Google's own catalogue.

    python tools/update_fonts.py

Keeps every family that covers Latin, most popular first, with its category,
so the palette can offer them all without an API key or a network call of
its own.
"""

import datetime
import json
import os
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "palette", "google-fonts.js")


def main():
    with urllib.request.urlopen("https://fonts.google.com/metadata/fonts", timeout=60) as reply:
        text = reply.read().decode("utf-8")
    if text.startswith(")]}'"):
        text = text[4:]
    rows = []
    for family in json.loads(text)["familyMetadataList"]:
        if "latin" not in family.get("subsets", []):
            continue
        category = (family.get("category") or "").lower().replace(" ", "-")
        rows.append((family.get("popularity", 99999), family["family"], category))
    rows.sort()
    fonts = [[name, category] for _, name, category in rows]
    with open(OUT, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("// Every Google Fonts family that covers Latin, most popular first, with its\n"
                     "// category. Taken from fonts.google.com/metadata/fonts on %s;\n"
                     "// re-run tools/update_fonts.py to refresh it.\n" % datetime.date.today())
        handle.write("const GOOGLE_FONTS = " + json.dumps(fonts, separators=(",", ":")) + ";\n")
    print("%d families written to %s" % (len(fonts), OUT))


if __name__ == "__main__":
    main()
