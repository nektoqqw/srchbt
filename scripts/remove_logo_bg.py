"""Убрать белый фон у logo.png (flood-fill от краёв)."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "miniapp" / "static" / "img" / "logo.png"
WHITE_THRESH = 238


def _is_background(r: int, g: int, b: int) -> bool:
    return r >= WHITE_THRESH and g >= WHITE_THRESH and b >= WHITE_THRESH


def flood_remove_background(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    seen = set()
    q: deque[tuple[int, int]] = deque()

    for x in range(w):
        q.append((x, 0))
        q.append((x, h - 1))
    for y in range(h):
        q.append((0, y))
        q.append((w - 1, y))

    while q:
        x, y = q.popleft()
        if x < 0 or y < 0 or x >= w or y >= h:
            continue
        if (x, y) in seen:
            continue
        r, g, b, _a = px[x, y]
        if not _is_background(r, g, b):
            continue
        seen.add((x, y))
        px[x, y] = (r, g, b, 0)
        q.append((x + 1, y))
        q.append((x - 1, y))
        q.append((x, y + 1))
        q.append((x, y - 1))

    # мягкий край у пикселей рядом с прозрачностью
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            if r >= WHITE_THRESH - 20 and g >= WHITE_THRESH - 20 and b >= WHITE_THRESH - 20:
                neighbors_transparent = 0
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and px[nx, ny][3] == 0:
                        neighbors_transparent += 1
                if neighbors_transparent >= 2:
                    br = (r + g + b) / 3
                    fade = max(0.0, (WHITE_THRESH - br) / 40.0)
                    px[x, y] = (r, g, b, int(255 * fade))

    return img


def main() -> None:
    if not LOGO.is_file():
        raise SystemExit(f"not found: {LOGO}")
    out = flood_remove_background(Image.open(LOGO))
    bbox = out.getbbox()
    if bbox:
        out = out.crop(bbox)
    pad = 6
    padded = Image.new("RGBA", (out.width + pad * 2, out.height + pad * 2), (0, 0, 0, 0))
    padded.paste(out, (pad, pad), out)
    padded.save(LOGO, "PNG", optimize=True)
    print(f"ok: {LOGO} {padded.size}")


if __name__ == "__main__":
    main()
