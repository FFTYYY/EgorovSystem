"""Turn the transparent chibi head into the PWA icon set (static/icons/).

Usage: python make_icons.py head.png OUT_DIR   (needs pillow, numpy, scipy)
- sticker: a dark outline plus an outer ring in the UI's paper colour, so it
  stands out on dark home screens
- icon-*.png: transparent ("any"), maskable-*.png and apple-touch-icon.png: on
  the reading-room green with a soft glow, the head inside the safe zone
"""
import os
import sys

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

DARK = (38, 30, 34)
PAPER = (231, 221, 199)
S = 1024


def clean(im):
    """Drop stray semi-transparent specks that are not attached to the head."""
    a = np.asarray(im.getchannel("A")).astype(np.float32) / 255
    lab, n = ndimage.label(a > 0.05)
    if n > 1:
        sizes = ndimage.sum(np.ones_like(a), lab, range(1, n + 1))
        keep = np.zeros(n + 1, bool)
        keep[1:] = sizes > sizes.max() * 0.01
        a = a * ndimage.binary_dilation(keep[lab], iterations=2)
    im = im.copy()
    im.putalpha(Image.fromarray((a * 255).astype(np.uint8)))
    return im


def ring(alpha, radius):
    """Anti-aliased dilation of a hard mask by `radius` pixels."""
    dist = ndimage.distance_transform_edt(~(alpha > 0.5))
    return np.clip(radius + 0.5 - dist, 0, 1)


def sticker(im, dark=9, paper=16):
    pad = dark + paper + 8
    canvas = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad))
    canvas.alpha_composite(im, (pad, pad))
    a = np.asarray(canvas.getchannel("A")).astype(np.float32) / 255
    layers = []
    for color, radius in ((PAPER, dark + paper), (DARK, dark)):
        layer = np.zeros(a.shape + (4,), np.uint8)
        layer[..., :3] = color
        layer[..., 3] = (np.maximum(ring(a, radius), a) * 255).astype(np.uint8)
        layers.append(Image.fromarray(layer, "RGBA"))
    out = layers[0]
    out.alpha_composite(layers[1])
    out.alpha_composite(canvas)
    return out.crop(out.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox())


def fit(art, box):
    r = min(box / art.width, box / art.height)
    return art.resize((round(art.width * r), round(art.height * r)), Image.LANCZOS)


def ground():
    """The app's dark green with a faint brass glow near the top."""
    y, x = np.mgrid[0:S, 0:S].astype(np.float32) / S
    d = np.sqrt(((x - 0.5) / 0.9) ** 2 + ((y - 0.18) / 0.7) ** 2)
    t = np.clip(d, 0, 1)[..., None]
    top, mid, edge = np.array([34, 42, 34]), np.array([21, 26, 22]), np.array([16, 20, 17])
    rgb = np.where(t < 0.55, top + (mid - top) * (t / 0.55), mid + (edge - mid) * ((t - 0.55) / 0.45))
    return Image.fromarray(rgb.astype(np.uint8), "RGB").convert("RGBA")


def on_ground(art, fraction):
    tile = ground()
    f = fit(art, round(S * fraction))
    pos = ((S - f.width) // 2, (S - f.height) // 2)
    shadow = Image.new("RGBA", (S, S))
    black = Image.new("RGBA", f.size, (0, 0, 0, 150))
    shadow.paste(black, (pos[0], pos[1] + round(S * 0.012)), f.getchannel("A"))
    tile.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(S * 0.012)))
    tile.alpha_composite(f, pos)
    return tile.convert("RGB")


def main(src, out):
    os.makedirs(out, exist_ok=True)
    art = sticker(clean(Image.open(src).convert("RGBA")))
    art.save(os.path.join(out, "sticker.png"))
    icon = Image.new("RGBA", (S, S))
    f = fit(art, round(S * 0.94))
    icon.alpha_composite(f, ((S - f.width) // 2, (S - f.height) // 2))
    for size in (512, 192):
        icon.resize((size, size), Image.LANCZOS).save(os.path.join(out, f"icon-{size}.png"), optimize=True)
    # maskable: launchers crop to as little as a centred circle of 80% diameter
    mask = on_ground(art, 0.66)
    for size in (512, 192):
        mask.resize((size, size), Image.LANCZOS).save(os.path.join(out, f"maskable-{size}.png"), optimize=True)
    # iOS rounds the corners only a little and never shows transparency
    on_ground(art, 0.84).resize((180, 180), Image.LANCZOS).save(os.path.join(out, "apple-touch-icon.png"), optimize=True)
    for size in (64, 32):
        icon.resize((size, size), Image.LANCZOS).save(os.path.join(out, f"favicon-{size}.png"), optimize=True)
    print("wrote", sorted(os.listdir(out)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
