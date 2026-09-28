#!/usr/bin/env python3
"""Render the DMG window background (assets/dmg-background.tiff).

The installer window shows HumanType.app on the left and an Applications
shortcut on the right; this draws the soft aurora, a heading, the arrow between
the two icons and the instruction line. It renders at 1x and 2x and packs both
into one multi-resolution TIFF, which is what Finder expects for Retina.

Icon centres here must match icon_locations in dmg_settings.py.
    python3 tools/make_dmg_background.py
"""

import os
import subprocess
import sys

from AppKit import (
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSFontWeightBold,
    NSFontWeightMedium,
    NSForegroundColorAttributeName,
    NSGradient,
    NSGraphicsContext,
    NSLineCapStyleRound,
    NSLineJoinStyleRound,
    NSMakePoint,
    NSMakeRect,
    NSPNGFileType,
    NSRectFill,
    NSString,
)

W, H = 660, 420                      # window content size, points
APP_ICON = (180, 205)                # icon centres, from the top-left
APPS_ICON = (480, 205)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "dmg-background.tiff")


def rgb(r, g, b, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, a)


def text(s, size, weight, color, cy):
    """Draw `s` centred horizontally, its line centred on `cy` (from the top)."""
    attrs = {NSFontAttributeName: NSFont.systemFontOfSize_weight_(size, weight),
             NSForegroundColorAttributeName: color}
    ns = NSString.stringWithString_(s)
    sz = ns.sizeWithAttributes_(attrs)
    ns.drawAtPoint_withAttributes_(
        NSMakePoint((W - sz.width) / 2, H - cy - sz.height / 2), attrs)


def draw():
    rgb(247, 248, 252).set()
    NSRectFill(NSMakeRect(0, 0, W, H))
    # Aurora: the same palette as the app's backdrop, kept pastel so Finder's
    # icon labels stay readable on top of it.
    for (x, y, r, c) in ((70, 40, 330, rgb(10, 132, 255, 0.30)),
                         (640, 150, 300, rgb(175, 82, 222, 0.26)),
                         (230, 430, 300, rgb(48, 176, 199, 0.26)),
                         (560, 440, 240, rgb(255, 55, 95, 0.20))):
        g = NSGradient.alloc().initWithStartingColor_endingColor_(c, c.colorWithAlphaComponent_(0.0))
        center = NSMakePoint(x, H - y)
        g.drawFromCenter_radius_toCenter_radius_options_(center, 0, center, r, 0)

    text("Install HumanType", 24, NSFontWeightBold, rgb(28, 28, 32), 58)

    # Arrow from the app to Applications, in a frosted capsule.
    y = H - APP_ICON[1]
    x0, x1 = APP_ICON[0] + 92, APPS_ICON[0] - 92
    capsule = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x0 - 14, y - 22, (x1 - x0) + 28, 44), 22, 22)
    rgb(255, 255, 255, 0.55).set()
    capsule.fill()
    rgb(255, 255, 255, 0.9).set()
    capsule.setLineWidth_(1.0)
    capsule.stroke()
    arrow = NSBezierPath.bezierPath()
    arrow.moveToPoint_(NSMakePoint(x0 + 4, y))
    arrow.lineToPoint_(NSMakePoint(x1 - 4, y))
    arrow.moveToPoint_(NSMakePoint(x1 - 18, y + 12))
    arrow.lineToPoint_(NSMakePoint(x1 - 4, y))
    arrow.lineToPoint_(NSMakePoint(x1 - 18, y - 12))
    arrow.setLineWidth_(4.5)
    arrow.setLineCapStyle_(NSLineCapStyleRound)
    arrow.setLineJoinStyle_(NSLineJoinStyleRound)
    rgb(10, 132, 255, 0.95).set()
    arrow.stroke()

    text("Drag HumanType onto the Applications folder", 14, NSFontWeightMedium,
         rgb(60, 60, 67, 0.85), 350)


def render(scale, path):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, W * scale, H * scale, 8, 4, True, False, "NSDeviceRGBColorSpace", 0, 0)
    rep.setSize_((W, H))                 # draw in points; the rep holds pixels
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(
        NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    draw()
    NSGraphicsContext.restoreGraphicsState()
    rep.representationUsingType_properties_(NSPNGFileType, {}).writeToFile_atomically_(path, True)


def main():
    tmp = os.path.join(ROOT, "build", "dmg-bg")
    os.makedirs(tmp, exist_ok=True)
    one, two = os.path.join(tmp, "bg.png"), os.path.join(tmp, "bg@2x.png")
    render(1, one)
    render(2, two)
    subprocess.run(["tiffutil", "-cathidpicheck", one, two, "-out", OUT],
                   check=True, capture_output=True)
    print(f"wrote {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
