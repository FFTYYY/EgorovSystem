"""Lower the saturation of the blue hair only, leaving skin, eyes and the brass key alone.

Usage: python tone.py head-raw.png 0.82 head.png
"""
import sys
import numpy as np
from PIL import Image


def tone(src, factor, dst=None):
    im = Image.open(src).convert("RGBA")
    rgb = np.asarray(im.convert("RGB")).astype(np.float32) / 255
    hsv = np.asarray(im.convert("RGB").convert("HSV")).astype(np.float32)
    h, s, v = hsv[..., 0] * 360 / 255, hsv[..., 1] / 255, hsv[..., 2] / 255
    blue = (h > 195) & (h < 260) & (s > 0.25)
    # feather the mask by hue distance so edges do not band
    w = np.clip(1 - np.abs(h - 228) / 40, 0, 1) * blue
    s2 = s * (1 - (1 - factor) * w)
    out = Image.fromarray(np.dstack([hsv[..., 0], s2 * 255, hsv[..., 2]]).astype(np.uint8), "HSV").convert("RGB")
    res = out.convert("RGBA")
    res.putalpha(im.getchannel("A"))
    if dst:
        res.save(dst)
    return res


if __name__ == "__main__":
    tone(sys.argv[1], float(sys.argv[2]), sys.argv[3])
