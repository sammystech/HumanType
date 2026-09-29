#!/usr/bin/env python3
"""Bundle entry point for HumanType.app — launches the native menu-bar UI.

PyInstaller builds *this* file into HumanType.app. All real logic lives in
humantype.py (typing engine) and humantype_ui.py (AppKit interface)."""

import sys

if __name__ == "__main__":
    if "--perm-probe" in sys.argv:
        # Report whether THIS app bundle is Accessibility-trusted, written to a
        # file so a LaunchServices launcher (open/Finder) — under which the app
        # is its own TCC "responsible process" — can be read back. Diagnostic.
        try:
            from ApplicationServices import AXIsProcessTrusted
            result = str(bool(AXIsProcessTrusted()))
        except Exception as exc:
            result = f"error:{exc!r}"
        try:
            with open("/tmp/humantype_perm.txt", "w") as fh:
                fh.write(result)
        except Exception:
            pass
        sys.exit(0)

    if "--selftest" in sys.argv:
        # Verify every bundled dependency imports + constructs, then exit.
        import os
        import humantype
        import humantype_ui  # noqa: F401  (pulls in AppKit/Foundation/Quartz)
        humantype.build_intercept(humantype.TypingState())
        # Without bundled CA roots every HTTPS request (update checks, license
        # validation) fails on Macs that don't have Homebrew.
        if not (humantype_ui.CA_FILE and os.path.exists(humantype_ui.CA_FILE)):
            sys.exit("selftest FAIL: CA bundle missing from the app")
        print("selftest OK")
        sys.exit(0)

    import humantype_ui
    humantype_ui.main()
