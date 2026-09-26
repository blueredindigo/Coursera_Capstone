"""Make the printable anchor-tag sheet: `python make_tags.py > anchor-tags.html`.

Nine AprilTags from the tag36h11 family, ids 0-8, one per spot in the plan (README §15). Each
is printed 100 mm across its black border, with a white margin around it (the detector needs
the margin). Print at 100 % ("actual size"), not "fit to page", then measure one with a ruler.
"""

import cv2

SIZE_MM = 100.0           # across the black border
SPOTS = [
    "Lounge, by the Nest (the chargers)",
    "Lounge, across the room",
    "Kitchen",
    "Hallway",
    "Bedroom 1",
    "Bedroom 2",
    "Bathroom 1, inside",
    "Bathroom 2, inside",
    "Balcony door",
]


def bits(tag_id: int) -> list[list[int]]:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    image = cv2.aruco.generateImageMarker(dictionary, tag_id, 8, borderBits=1)
    return [[1 if image[r, c] > 127 else 0 for c in range(8)] for r in range(8)]


def svg(tag_id: int) -> str:
    cell = SIZE_MM / 8
    rects = "".join(f'<rect x="{c * cell + cell:.3f}" y="{r * cell + cell:.3f}" '
                    f'width="{cell + 0.02:.3f}" height="{cell + 0.02:.3f}"/>'
                    for r, row in enumerate(bits(tag_id)) for c, v in enumerate(row) if not v)
    side = SIZE_MM + 2 * cell
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{side}mm" height="{side}mm" '
            f'viewBox="0 0 {side} {side}" shape-rendering="crispEdges">'
            f'<rect width="{side}" height="{side}" fill="#fff"/><g fill="#000">{rects}</g></svg>')


def page() -> str:
    tags = "".join(f'<figure>{svg(i)}<figcaption><b>Tag {i}</b> · {name}<br>'
                   f'<span>tag36h11 · {SIZE_MM:.0f} mm · this side up ↑</span></figcaption></figure>'
                   for i, name in enumerate(SPOTS))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Anchor Tags for the Flat</title>
<style>
@page {{ size: A4; margin: 8mm 10mm; }}
body {{ margin: 0; font: 11pt/1.35 system-ui, sans-serif; color: #111; background: #fff; }}
.intro {{ max-width: 180mm; margin: 0 auto 3mm; font-size: 10pt; }}
@media print {{ .intro {{ break-after: page; }} }}
.intro h1 {{ font-size: 16pt; margin: 0 0 2mm; }}
.intro p {{ margin: 0 0 2mm; }}
.grid {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 2mm 10mm; }}
figure {{ margin: 0; break-inside: avoid; page-break-inside: avoid; text-align: center; }}
figure svg {{ display: block; }}
figcaption {{ margin-top: 0; font-size: 9pt; line-height: 1.25; }}
figcaption span {{ color: #555; font-size: 9pt; }}
@media screen {{ body {{ padding: 16px; background: #eee; }} figure {{ background: #fff; padding: 4mm; }} }}
</style></head><body>
<div class="intro">
<h1>Anchor tags for Ah-Ah and Tee-Tee</h1>
<p><b>Print at 100 % (actual size), not "fit to page".</b> Then check one with a ruler: the black
square should be {SIZE_MM:.0f} mm across. If it isn't, tell the Nest the size you measured.</p>
<p>Stick each on a wall at its spot, upright (arrow up), flat, and with its <b>centre 20 cm
above the floor</b>: the same height for all nine, because the walkthrough uses them to find the
floor. Avoid shiny laminate, which glares in photos. Put them up <b>before</b> you film the
walkthrough, so they're in the video.</p>
</div>
<div class="grid">{tags}</div>
</body></html>
"""


if __name__ == "__main__":
    print(page())
