import sys, os
sys.path.insert(0, "/Users/samuelmittman/humantype-app")
from AppKit import *; from Foundation import *
import humantype_ui as ui
res = []
def check(n, ok, info=""): res.append(ok); print(("PASS " if ok else "FAIL ") + n, info, flush=True)
class T(NSObject):
    def go_(self, _):
        c = self.c = ui.AppController.alloc().init()
        NSApplication.sharedApplication().setDelegate_(c); c.applicationDidFinishLaunching_(None); _cb = c._tap_callback; c._teardown_global_tap(); c._tap_callback = _cb; c._tap = 'isolated'; c._install_global_tap = lambda: None  # ignore the real keyboard
        c._is_trusted = lambda: True
        c._tap = None; c._install_global_tap = lambda: None   # macOS still refusing the tap
        self.relaunched = False
        def fake_relaunch(): self.relaunched = True
        c._relaunch = fake_relaunch
        c.showTutorial_(None); self.ob = c._onboarding; self.ob.next_(None)
        self.performSelector_withObject_afterDelay_(b"a:", None, 1.3)
    def a_(self, _):
        ob = self.ob
        check("trusted but tap refused -> asks to reopen", ob._perm_state == "reopen" and str(ob._perm_btn.title()) == "Reopen HumanType")
        check("Next stays clickable", ob._primary.isEnabled())
        ob.permAction_(None)
        d = NSUserDefaults.standardUserDefaults()
        check("reopen remembers the tour step", d.stringForKey_("ht_resume_tour") == "permission" and self.relaunched)
        # simulate the relaunched app picking it up
        c2 = ui.AppController.alloc().init()
        c2._resume_step = str(d.stringForKey_("ht_resume_tour")); d.removeObjectForKey_("ht_resume_tour")
        c2._build_main_menu(); c2._build_status_item(); c2._build_window()
        c2.showTutorial_(None)
        check("relaunch resumes at the permission step", c2._onboarding.current() == "permission")
        c2._onboarding._close(); ob._close()
        d.removeObjectForKey_("ht_onboarded")
        print(f"{sum(res)}/{len(res)} passed", flush=True); os._exit(0)
app = NSApplication.sharedApplication(); app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
t = T.alloc().init(); t.performSelector_withObject_afterDelay_(b"go:", None, 0.3); app.run()
