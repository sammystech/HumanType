import sys, os, time
sys.path.insert(0, "/Users/samuelmittman/humantype-app")
from AppKit import *; from Foundation import *; from Quartz import *
import objc, faulthandler; faulthandler.dump_traceback_later(40, exit=True)
import humantype as ht, humantype_ui as ui
res = []
def check(n, ok, info=""): res.append(ok); print(("PASS " if ok else "FAIL ") + n, info, flush=True)
d = NSUserDefaults.standardUserDefaults()
for k in ("start_stop", "stop", "pause", "lock", "type_paste"):
    d.removeObjectForKey_(k + "_kc"); d.removeObjectForKey_(k + "_mods")
class FakeEvent:
    def __init__(s, kc, mods): s.kc, s.mods = kc, mods
    def keyCode(s): return s.kc
    def modifierFlags(s): return s.mods
class T(NSObject):
    def go_(self, _):
        c = self.c = ui.AppController.alloc().init()
        NSApplication.sharedApplication().setDelegate_(c); c.applicationDidFinishLaunching_(None); _cb = c._tap_callback; c._teardown_global_tap(); c._tap_callback = _cb; c._tap = 'isolated'; c._install_global_tap = lambda: None  # ignore the real keyboard
        L = ui.binding_label
        check("fresh install: Start/Stop = ⌘S", L(c.start_stop_binding) == "⌘S", L(c.start_stop_binding))
        check("fresh install: Stop = ⌘S", L(c.stop_binding) == "⌘S", L(c.stop_binding))
        check("fresh install: Lock = F6", L(c.lock_binding) == "F6", L(c.lock_binding))
        check("fresh install: Pause = backtick", c.pause_binding == (50, 0))
        check("fresh install: Type clipboard = ⌥⌘V", L(c.type_paste_binding) == "⌥⌘V", L(c.type_paste_binding))
        check("Settings rows show them", str(c.start_stop_rec.title()) == "⌘S" and str(c.stop_rec.title()) == "⌘S" and str(c.lock_rec.title()) == "F6")
        # ⌘S while idle starts, while typing stops (start_stop checked before stop)
        e = CGEventCreateKeyboardEvent(None, 1, True); CGEventSetFlags(e, kCGEventFlagMaskCommand)
        CGEventSetIntegerValueField(e, kCGEventSourceUnixProcessID, 0)
        c._run_typing("hello there", 99)
        r = c._tap_callback(None, kCGEventKeyDown, e, None)
        check("⌘S while running stops it", r is None and c.state.stopped)
        c._finish(False)
        # recorder: Stop may reuse ⌘S, Lock may not (real key events through the app)
        self.step = 0
        c._record("stop", c.stop_rec); self.post(1, NSEventModifierFlagCommand)
        self.performSelector_withObject_afterDelay_(b"r1:", None, 0.4)
    @objc.python_method
    def post(self, kc, mods):
        ev = NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
            NSEventTypeKeyDown, (0, 0), mods, 0, self.c.window.windowNumber(), None, "s", "s", False, kc)
        NSApplication.sharedApplication().postEvent_atStart_(ev, False)
    def r1_(self, _):
        c = self.c
        check("recorder lets Stop share ⌘S", c.stop_binding == (1, kCGEventFlagMaskCommand), str(c.stop_binding))
        c._record("lock", c.lock_rec); self.post(1, NSEventModifierFlagCommand)
        self.performSelector_withObject_afterDelay_(b"r2:", None, 0.4)
    def r2_(self, _):
        c = self.c
        check("recorder refuses ⌘S for Lock", c.lock_binding == (97, 0), str(c.lock_binding))
        d = NSUserDefaults.standardUserDefaults()
        for k in ("stop", "lock"):
            d.removeObjectForKey_(k + "_kc"); d.removeObjectForKey_(k + "_mods")
        # permission step, macOS never reporting trusted until restart
        c._is_trusted = lambda: False
        c._tap = None; c._install_global_tap = lambda: None
        c._open_accessibility_settings = lambda: None; c._prompt_accessibility = lambda: None
        c.showTutorial_(None); ob = self.ob = c._onboarding; ob.next_(None)
        check("permission step shown", ob.current() == "permission")
        check("Next is never greyed out", ob._primary.isEnabled())
        ob.permAction_(None)    # user clicks Open Settings
        self.t0 = time.time()
        self.performSelector_withObject_afterDelay_(b"a:", None, 5.5)
    def a_(self, _):
        ob = self.ob
        check("after Settings, offers Reopen even though macOS says not trusted", ob._perm_state == "reopen" and str(ob._perm_btn.title()) == "Reopen HumanType", ob._perm_state)
        check("Next still clickable", ob._primary.isEnabled())
        ob.next_(None)
        check("Next moves on", ob.current() == "paste", ob.current())
        ob._close(); d.removeObjectForKey_("ht_onboarded")
        print(f"{sum(res)}/{len(res)} passed", flush=True); os._exit(0)
app = NSApplication.sharedApplication(); app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
t = T.alloc().init(); t.performSelector_withObject_afterDelay_(b"go:", None, 0.3); app.run()
