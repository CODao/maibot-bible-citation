"""Draw the project's original icon. Optional development dependency: Pillow.

Run: python tools/generate_icon.py
This uses geometric primitives only; no game artwork, fonts or external assets.
"""
from pathlib import Path
from PIL import Image, ImageDraw

SCALE = 4
image = Image.new('RGB', (256 * SCALE, 256 * SCALE), '#123D46')
draw = ImageDraw.Draw(image)

def points(items):
    return [(x * SCALE, y * SCALE) for x, y in items]

def polygon(items, fill):
    draw.polygon(points(items), fill=fill)

def line(items, fill, width=4):
    draw.line(points(items), fill=fill, width=width * SCALE, joint='curve')

# Two open covers, warm paper, centre fold and a gold page marker.
polygon([(32,70),(118,81),(128,93),(138,81),(224,70),(224,198),(139,207),(128,214),(117,207),(32,198)], '#5AA6AA')
polygon([(40,56),(113,64),(128,78),(128,198),(111,187),(40,180)], '#FAF3DB')
polygon([(128,78),(143,64),(216,56),(216,180),(145,187),(128,198)], '#E6E3CC')
line([(128,81),(128,195)], '#B0BBAD', 3)
for offset in (0, 22, 44):
    line([(57,94+offset),(104,100+offset)], '#537B79', 4)
    line([(151,100+offset),(199,94+offset)], '#537B79', 4)
polygon([(166,61),(182,59),(182,111),(174,104),(166,113)], '#D8AA4C')
path = Path(__file__).resolve().parents[1] / 'assets' / 'bible-book.png'
image.resize((256, 256), Image.Resampling.LANCZOS).save(path, optimize=True)
