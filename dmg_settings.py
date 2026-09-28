# dmgbuild settings for the HumanType installer window.
#   dmgbuild -s dmg_settings.py -D app=dist/HumanType.app HumanType dist/HumanType.dmg
#
# Opens as a fixed-size window: the app on the left, an Applications shortcut
# on the right, and a background (tools/make_dmg_background.py) with the arrow
# between them. Icon centres must match APP_ICON / APPS_ICON in that script.
# The in-app updater mounts this same DMG and copies HumanType.app from its root.

import os.path

app = defines.get("app", "dist/HumanType.app")  # noqa: F821 (injected by dmgbuild)

format = "UDZO"
files = [app]
symlinks = {"Applications": "/Applications"}
icon = "HumanType.icns"                                  # the mounted volume's icon
background = "assets/dmg-background.tiff"

window_rect = ((200, 140), (660, 420))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
icon_size = 128
text_size = 13
icon_locations = {
    os.path.basename(app): (180, 205),
    "Applications": (480, 205),
}
# No hide_extensions: it writes com.apple.FinderInfo onto the bundle, which
# fails `codesign --verify --strict`, the check the in-app updater runs before
# installing. (Finder hides ".app" by default anyway.)
