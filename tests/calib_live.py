import sys, os
sys.path.insert(0, "/Users/samuelmittman/humantype-app")
from AppKit import *; from Foundation import *
import objc, faulthandler; faulthandler.dump_traceback_later(60, exit=True)
import humantype_ui as ui
d = NSUserDefaults.standardUserDefaults(); d.removeObjectForKey_("ht_calibration")
class T(NSObject):
    def go_(self, _):
        c = self.c = ui.AppController.alloc().init()
        NSApplication.sharedApplication().setDelegate_(c); c.applicationDidFinishLaunching_(None); _cb = c._tap_callback; c._teardown_global_tap(); c._tap_callback = _cb; c._tap = 'isolated'
        c.showTabCalibrate_(None)
        self.i = 0; self.step_()
    def step_(self, _=None):
        c = self.c
        if self.i < 130:
            kc = 51 if self.i in (30, 70, 71) else 0
            ev = NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                NSEventTypeKeyDown, (0, 0), 0, 0, c.window.windowNumber(), None, "a", "a", False, kc)
            NSApplication.sharedApplication().postEvent_atStart_(ev, False)
            self.i += 1
            self.performSelector_withObject_afterDelay_(b"step:", None, 0.012)
            return
        self.performSelector_withObject_afterDelay_(b"done:", None, 0.5)
    def done_(self, _):
        c = self.c
        n = len(c._calib_events)
        print(("PASS" if n == 130 else "FAIL"), "real key events recorded while on Calibrate:", n, flush=True)
        print(("PASS" if c._calibration() else "FAIL"), "calibration saved after 120 keys:", c._calibration(), flush=True)
        c._show_tab("type", animate=False)
        print(("PASS" if c._calib_monitor is None else "FAIL"), "stops listening when you leave the page", flush=True)
        d.removeObjectForKey_("ht_calibration"); d.removeObjectForKey_("ht_realism"); d.removeObjectForKey_("ht_wpm")
        os._exit(0)
app = NSApplication.sharedApplication(); app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
t = T.alloc().init(); t.performSelector_withObject_afterDelay_(b"go:", None, 0.3); app.run()
