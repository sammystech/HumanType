import sys, os
sys.path.insert(0, "/Users/samuelmittman/humantype-app")
from AppKit import *; from Foundation import *
import humantype_ui as ui
res = []
def check(n, ok, info=""): res.append(ok); print(("PASS " if ok else "FAIL ") + n, info, flush=True)
d = NSUserDefaults.standardUserDefaults()
d.removeObjectForKey_("ht_shortcuts_version")
old = {"start_stop": (1, 786432), "stop": (12, 786432), "lock": (37, 786432),
       "type_paste": (9, 786432), "pause": (48, 0)}          # an older user's saved shortcuts
for k, (kc, m) in old.items():
    d.setInteger_forKey_(kc, k + "_kc"); d.setInteger_forKey_(m, k + "_mods")
c = ui.AppController.alloc().init()
L = ui.binding_label
check("old saved shortcuts replaced on update",
      [L(b) for b in (c.start_stop_binding, c.pause_binding, c.stop_binding, c.lock_binding, c.type_paste_binding, c.destruct_binding)]
      == ["⌘S", "` (backtick)", "⌘S", "F6", "⌥⌘V", "⌃⌥⌘"],
      str([L(b) for b in (c.start_stop_binding, c.pause_binding, c.stop_binding, c.lock_binding, c.type_paste_binding, c.destruct_binding)]))
# a shortcut recorded after the update is kept on the next launch
c._save_binding("pause", (50, 524288))
c2 = ui.AppController.alloc().init()
check("shortcuts set after the update are kept", c2.pause_binding == (50, 524288))
for k in list(old) + ["destruct"]:
    d.removeObjectForKey_(k + "_kc"); d.removeObjectForKey_(k + "_mods")
d.removeObjectForKey_("ht_shortcuts_version")
print(f"{sum(res)}/{len(res)} passed", flush=True)
