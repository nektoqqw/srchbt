"""Убрать белый фон снаружи персонажа; глаза и тело не трогать."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCAL = ROOT / "miniapp" / "static" / "img" / "logo_source.png"
LOGO = ROOT / "miniapp" / "static" / "img" / "logo.png"


def _is_green_body(r: int, g: int, b: int) -> bool:
    """Зелёное тело/контур Om Nom."""
    return g > r + 12 and g > b + 12 and g >= 70


def _is_white(r: int, g: int, b: int) -> bool:
    return r >= 235 and g >= 235 and b >= 235


def remove_background_keep_eyes(img: Image.Image) -> Image.Image:
    """
    Прозрачным делаем только белый фон ВНЕ bbox зелёного тела.
    Белые глаза внутри силуэта остаются нетронутыми.
    """
    src = img.convert("RGBA")
    w, h = src.size
    px = src.load()

    xs: list[int] = []
    ys: list[int] = []
    for y in range(h):
        for x in range(w):
            r, g, b, _a = px[x, y]
            if _is_green_body(r, g, b):
                xs.append(x)
                ys.append(y)

    if not xs:
        return src

    pad = 12
    min_x = max(0, min(xs) - pad)
    max_x = min(w - 1, max(xs) + pad)
    min_y = max(0, min(ys) - pad)
    max_y = min(h - 1, max(ys) + pad)

    out = src.copy()
    opx = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = opx[x, y]
            inside = min_x <= x <= max_x and min_y <= y <= max_y
            if not inside and _is_white(r, g, b):
                opx[x, y] = (255, 255, 255, 0)
            elif not inside and r >= 220 and g >= 220 and b >= 220:
                # антиалиас у края холста
                opx[x, y] = (r, g, b, 0)

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
