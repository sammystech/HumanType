# UI harness tests

Each script drives a real `AppController` under a live NSApplication, detached
from the physical keyboard (the global tap is torn down; key events are fed to
`_tap_callback` directly), so typing on the Mac while they run can't interfere.

    for t in tests/*.py; do .venv/bin/python "$t" "$(mktemp -d)"; done

Each prints PASS/FAIL lines and `N/M passed`. They clean up the defaults they touch.
