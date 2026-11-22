"""Render the OpenRAR icon (PNG + multi-size ICO) with Pillow."""
import os
import sys

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "openrar", "resources")


def render(size: int, accent=((79, 70, 229), (14, 165, 233))) -> Image.Image:
    s = 4 * size                      # supersample for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    # vertical gradient tile
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    (r1, g1, b1), (r2, g2, b2) = accent
    for y in range(s):
        t = y / (s - 1)
        gd.line([(0, y), (s, y)], fill=(int(r1 + (r2 - r1) * t), int(g1 + (g2 - g1) * t), int(b1 + (b2 - b1) * t), 255))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([s * 0.04, s * 0.04, s * 0.96, s * 0.96], radius=s * 0.22, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    w = max(2, int(s * 0.055))
    white = (255, 255, 255, 255)
    # box body + lid
    d.rounded_rectangle([s * 0.24, s * 0.40, s * 0.76, s * 0.78], radius=s * 0.05, outline=white, width=w)
    d.rounded_rectangle([s * 0.19, s * 0.27, s * 0.81, s * 0.42], radius=s * 0.04, fill=white)
    # zipper strip down the middle
    zx0, zx1 = s * 0.455, s * 0.545
    y = s * 0.44
    step = s * 0.06
    i = 0
    while y + step * 0.6 < s * 0.74:
        x0 = zx0 if i % 2 == 0 else s * 0.5
        d.rectangle([x0, y, x0 + (zx1 - zx0) / 2, y + step * 0.55], fill=white)
        y += step * 0.75
        i += 1
    # zipper pull
    d.rounded_rectangle([s * 0.43, s * 0.30, s * 0.57, s * 0.39], radius=s * 0.02, fill=accent[0] + (255,))
    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    big = render(256)
    big.save(os.path.join(OUT, "icon.png"))
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    imgs = [render(n) for n in sizes]
    imgs[-1].save(os.path.join(OUT, "icon.ico"), sizes=[(n, n) for n in sizes], append_images=imgs[:-1])
    sfx = [render(n, ((234, 88, 12), (245, 158, 11))) for n in sizes]
    sfx[-1].save(os.path.join(OUT, "sfx.ico"), sizes=[(n, n) for n in sizes], append_images=sfx[:-1])
    arc = [render(n, ((100, 116, 139), (71, 85, 105))) for n in sizes]
    arc[-1].save(os.path.join(OUT, "archive.ico"), sizes=[(n, n) for n in sizes], append_images=arc[:-1])
    print("icons written to", os.path.abspath(OUT))


if __name__ == "__main__":
    sys.exit(main())
