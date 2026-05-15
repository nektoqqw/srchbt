"""Убрать фон у logo.png; белые глаза внутри контура не трогать."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCAL = ROOT / "miniapp" / "static" / "img" / "logo_source.png"
LOGO = ROOT / "miniapp" / "static" / "img" / "logo.png"


def _is_green_body(r: int, g: int, b: int) -> bool:
    return g > r + 12 and g > b + 12 and g >= 70


def _is_dark_line(r: int, g: int, b: int) -> bool:
    """Контур и зрачки — стена для заливки фона."""
    return r < 95 and g < 95 and b < 95


def _is_white_bg(r: int, g: int, b: int) -> bool:
    return r >= 232 and g >= 232 and b >= 232


def _can_flood_through(r: int, g: int, b: int) -> bool:
    """По фону и светлому антиалиасу снаружи персонажа."""
    if _is_green_body(r, g, b) or _is_dark_line(r, g, b):
        return False
    return r >= 210 and g >= 210 and b >= 210


def remove_background_keep_eyes(img: Image.Image) -> Image.Image:
    """
    Заливка от краёв только через «фоновые» пиксели.
    До глаз не доходит: их окружает зелёный контур и тёмная обводка.
    """
    out = img.convert("RGBA")
    w, h = out.size
    px = out.load()
    seen: set[tuple[int, int]] = set()
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
        if not _can_flood_through(r, g, b):
            continue
        seen.add((x, y))
        px[x, y] = (255, 255, 255, 0)
        q.append((x + 1, y))
        q.append((x - 1, y))
        q.append((x, y + 1))
        q.append((x, y - 1))

    # подчистить белое явно за пределами силуэта (уголки)
    xs: list[int] = []
    ys: list[int] = []
    for y in range(h):
        for x in range(w):
            r, g, b, _a = px[x, y]
            if _is_green_body(r, g, b):
                xs.append(x)
                ys.append(y)
    if xs:
        pad = 8
        min_x = max(0, min(xs) - pad)
        max_x = min(w - 1, max(xs) + pad)
        min_y = max(0, min(ys) - pad)
        max_y = min(h - 1, max(ys) + pad)
        for y in range(h):
            for x in range(w):
                if (x, y) in seen:
                    continue
                r, g, b, a = px[x, y]
                outside = x < min_x or x > max_x or y < min_y or y > max_y
                if outside and _is_white_bg(r, g, b):
                    px[x, y] = (255, 255, 255, 0)

    return out


def main() -> None:
    if not SOURCE_LOCAL.is_file():
        raise SystemExit(f"source not found: {SOURCE_LOCAL}")
    out = remove_background_keep_eyes(Image.open(SOURCE_LOCAL))
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
