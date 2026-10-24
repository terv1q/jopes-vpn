"""Rebuild the welcome GIF with the established Heading Now composition.

Run with Python and Pillow installed; the font remains a local Windows asset.
"""

from __future__ import annotations

import argparse
import json
import math
from hashlib import sha256
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
WIDTH, HEIGHT = 1280, 640
TEXT = "JOPES VPN"
SCALE = 2
TOTAL_MS = 7200
FRAME_MS = 40


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def smooth(value: float) -> float:
    value = clamp(value)
    return value * value * (3.0 - 2.0 * value)


def back_out(value: float) -> float:
    value = clamp(value) - 1.0
    return 1.0 + 2.35 * value**3 + 1.35 * value**2


def glyphs(font_path: Path) -> list[tuple[Image.Image, float]]:
    font = ImageFont.truetype(str(font_path), 280 * SCALE)
    widths = [font.getlength(letter) / SCALE for letter in TEXT]
    tracking = 7.0
    total = sum(widths) + tracking * (len(TEXT) - 1)
    x = (WIDTH - total) / 2.0
    result = []
    for letter, width in zip(TEXT, widths):
        if letter != " ":
            box = font.getbbox(letter)
            mask = Image.new("L", (box[2] - box[0] + 16, box[3] - box[1] + 16), 0)
            ImageDraw.Draw(mask).text((8 - box[0], 8 - box[1]), letter, font=font, fill=255)
            mask = mask.crop(mask.getbbox())
            result.append((mask, x + width / 2.0))
        x += width + tracking
    return result


def pose(t: float, index: int) -> tuple[float, float, float]:
    # Alternate directions; 45 ms stagger and a sharp, bounded spring entrance.
    direction = 1.0 if index % 2 == 0 else -1.0
    local = t - (0.08 + index * 0.045)
    if local <= 0:
        return direction * 155.0, 0.86, 0.0
    if local < 0.42:
        progress = local / 0.42
        return direction * 155.0 * (1.0 - back_out(progress)), 0.90 + 0.10 * back_out(progress), smooth(progress / 0.28)
    # A single brief travelling pulse, returning every letter to its baseline.
    wave = (t - 2.55 - index * 0.065) / 0.38
    if 0 < wave < 1:
        return -25.0 * math.sin(math.pi * wave)**2, 1.0, 1.0
    # Reverse-order exit; opacity reaches zero while the complete glyph is in frame.
    exit_progress = (t - 6.32 - (7 - index) * 0.045) / 0.38
    if exit_progress > 0:
        progress = clamp(exit_progress)
        return -direction * 170.0 * progress**2, 1.0 - 0.10 * progress, 1.0 - smooth(progress)
    return 0.0, 1.0, 1.0


def render(t: float, letters: list[tuple[Image.Image, float]]) -> Image.Image:
    canvas = Image.new("L", (WIDTH * SCALE, HEIGHT * SCALE), 0)
    for index, (original, center_x) in enumerate(letters):
        dy, scale, opacity = pose(t, index)
        if opacity <= 0:
            continue
        mask = original.resize((round(original.width * scale), round(original.height * scale)), Image.Resampling.LANCZOS)
        if opacity < 1:
            mask = mask.point([round(v * opacity) for v in range(256)])
        x = round(center_x * SCALE - mask.width / 2)
        y = round((HEIGHT / 2 + dy) * SCALE - mask.height / 2)
        assert x >= 0 and y >= 0 and x + mask.width <= canvas.width and y + mask.height <= canvas.height, "Clipped glyph"
        canvas.paste(mask, (x, y))
    return canvas.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/HeadingNowTrial-56Bold.ttf"))
    parser.add_argument("--output", type=Path, default=ROOT / "jopesvpn/assets/banners/start-welcome.gif")
    args = parser.parse_args()
    letters = glyphs(args.font)
    palette = [component for v in range(256) for component in (v, v, v)]
    frames = []
    for ms in range(0, TOTAL_MS, FRAME_MS):
        frame = render(ms / 1000, letters).convert("P")
        frame.putpalette(palette)
        frames.append(frame)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(args.output, save_all=True, append_images=frames[1:], duration=FRAME_MS, loop=0, disposal=2, optimize=False)
    directory = Path(__file__).resolve().parent
    frames[round(1.2 * 1000 / FRAME_MS)].convert("RGB").save(directory / "preview.png")
    samples = [0, .12, .24, .40, .56, .72, .88, 1.20, 2.60, 2.72, 2.84, 2.96, 3.12, 3.28, 4.20, 6.32, 6.44, 6.56, 6.72, 7.12]
    sheet = Image.new("RGB", (4 * 384, 5 * 216), (22, 22, 22))
    draw = ImageDraw.Draw(sheet)
    for index, stamp in enumerate(samples):
        x, y = index % 4 * 384, index // 4 * 216
        frame = frames[round(stamp * 1000 / FRAME_MS)].convert("RGB")
        sheet.paste(frame.resize((384, 192), Image.Resampling.LANCZOS), (x, y + 24))
        draw.text((x + 10, y + 5), f"{stamp:.2f}s", fill="white")
    sheet.save(directory / "contact-sheet.png")
    (directory / "preview.html").write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Jopes VPN</title><style>html,body{margin:0;background:#000;height:100%}body{display:grid;place-items:center}img{width:min(100%,1280px);height:auto}</style><img src="../../../jopesvpn/assets/banners/start-welcome.gif" alt="JOPES VPN">', encoding="utf-8")
    print(json.dumps({"gif":str(args.output),"sha256":sha256(args.output.read_bytes()).hexdigest(),"font":str(args.font),"duration_ms":TOTAL_MS,"generated_frames":len(frames)}))


if __name__ == "__main__":
    main()
