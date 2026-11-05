#!/usr/bin/env python3
"""Build blind review folders from captured screenshots.

  python scripts/design-review/package.py --baseline DIR --new DIR --reference DIR \
      --out /tmp/blind/r01 --mapping /tmp/private/r01.json [--seed N]

Writes <out>/{A,B,C}/{sheets,full} plus states.md and brief.md. The letter
mapping goes to --mapping, which must live outside the reviewer folder.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = (1600, 900)
MOBILE_HEIGHT = 900


def _font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def sheet(desktop: Path | None, mobile: Path | None, label: str, target: Path):
    mobile_width = round(MOBILE_HEIGHT * 390 / 844)
    width, header = DESKTOP[0] + mobile_width + 60, 44
    canvas = Image.new("RGB", (width, DESKTOP[1] + header + 20), "#7f8790")
    draw = ImageDraw.Draw(canvas)
    draw.text((20, 10), label, fill="white", font=_font(22))
    for path, box, size in ((desktop, (20, header), DESKTOP), (mobile, (DESKTOP[0] + 40, header), (mobile_width, MOBILE_HEIGHT))):
        if path and path.exists():
            with Image.open(path) as image:
                canvas.paste(image.convert("RGB").resize(size, Image.LANCZOS), box)
        else:
            draw.rectangle([box, (box[0] + size[0], box[1] + size[1])], fill="#555")
            draw.text((box[0] + 20, box[1] + 20), "not available", fill="white", font=_font(20))
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, optimize=True)


def package(source: Path, target: Path, letter: str):
    states = sorted({path.name.rsplit("--", 1)[0] for path in source.glob("*--*.png")})
    (target / "full").mkdir(parents=True, exist_ok=True)
    for state in states:
        desktop, mobile = source / f"{state}--desktop.png", source / f"{state}--mobile.png"
        for path in (desktop, mobile):
            if path.exists():
                shutil.copy2(path, target / "full" / path.name)
        sheet(desktop, mobile, f"Set {letter} · {state} · left: desktop 2560×1440 (scaled) · right: mobile 390×844",
              target / "sheets" / f"{state}.png")
    return len(states)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    letters = ["A", "B", "C"]
    random.Random(args.seed).shuffle(letters)
    mapping = dict(zip(("baseline", "new", "reference"), letters))
    review = args.out
    if review.exists():
        shutil.rmtree(review)
    counts = {}
    for name, source in (("baseline", args.baseline), ("new", args.new), ("reference", args.reference)):
        counts[name] = package(source, review / mapping[name], mapping[name])
    shutil.copy2(ROOT / "docs/design-review/states.md", review / "states.md")
    shutil.copy2(ROOT / "docs/design-review/brief.md", review / "brief.md")
    args.mapping.parent.mkdir(parents=True, exist_ok=True)
    args.mapping.write_text(json.dumps({"letters": mapping, "states": counts}, indent=2))
    print(json.dumps({"review": str(review), "states": counts}))


if __name__ == "__main__":
    main()
