import sys, os
sys.path.insert(0, "/Users/samuelmittman/humantype-app")
from AppKit import *; from Foundation import *; from Quartz import *
import objc, faulthandler; faulthandler.dump_traceback_later(40, exit=True)
import humantype as ht, humantype_ui as ui
res = []
def check(n, ok, info=""): res.append(ok); print(("PASS " if ok else "FAIL ") + n, info, flush=True)
C, A, M = kCGEventFlagMaskControl, kCGEventFlagMaskAlternate, kCGEventFlagMaskCommand
def flags(f):
    e = CGEventCreate(None); CGEventSetType(e, kCGEventFlagsChanged); CGEventSetFlags(e, f)
    CGEventSetIntegerValueField(e, kCGEventSourceUnixProcessID, 0); return e
def key(kc, f):
    e = CGEventCreateKeyboardEvent(None, kc, True); CGEventSetFlags(e, f)
    CGEventSetIntegerValueField(e, kCGEventSourceUnixProcessID, 0); return e
class T(NSObject):
    def go_(self, _):
        c = self.c = ui.AppController.alloc().init()
        NSApplication.sharedApplication().setDelegate_(c); c.applicationDidFinishLaunching_(None); _cb = c._tap_callback; c._teardown_global_tap(); c._tap_callback = _cb; c._tap = 'isolated'; c._install_global_tap = lambda: None  # ignore the real keyboard
        self.fired = 0
        def fake(sender=None): self.fired += 1
        c.selfDestruct_ = fake
        self.cb = lambda e, t: c._tap_callback(None, t, e, None)
        check("default is hold ⌃⌥⌘", ui.binding_label(c.destruct_binding) == "⌃⌥⌘" and str(c.destruct_rec.title()) == "⌃⌥⌘")
        r = self.cb(flags(C | A | M), kCGEventFlagsChanged)
        check("modifier change is passed through, never swallowed", r is not None)
        self.performSelector_withObject_afterDelay_(b"a:", None, 0.6)
    @objc.python_method
    def release(self): self.cb(flags(0), kCGEventFlagsChanged)
    def a_(self, _):
        check("held ⌃⌥⌘ alone → self-destruct", self.fired == 1, str(self.fired))
        self.release()
        self.cb(flags(C | A | M), kCGEventFlagsChanged); self.cb(key(40, C | A | M), kCGEventKeyDown)
        self.performSelector_withObject_afterDelay_(b"b:", None, 0.6)
    def b_(self, _):
        check("⌃⌥⌘ + a letter (another app's shortcut) → nothing", self.fired == 1, str(self.fired))
        self.release()
        self.cb(flags(C | A), kCGEventFlagsChanged)
        self.performSelector_withObject_afterDelay_(b"c:", None, 0.6)
    def c_(self, _):
        check("only ⌃⌥ held → nothing", self.fired == 1, str(self.fired))
        self.release()
        self.cb(flags(C | A | M), kCGEventFlagsChanged)
        self.performSelector_withObject_afterDelay_(b"c2:", 0.15, 0.15)
    def c2_(self, _):
        self.release()      # let go before 0.4s
        self.performSelector_withObject_afterDelay_(b"d:", None, 0.5)
    def d_(self, _):
        check("quick tap of ⌃⌥⌘ (let go early) → nothing", self.fired == 1, str(self.fired))
        # the real action: wipes text + draft, stops, unlocks, quits
        c = self.c; del c.selfDestruct_
        c._tap = None; c._tap_source = None
        class FakeApp:
            quit = False
            @classmethod
            def sharedApplication(cls): return cls
            @classmethod
            def terminate_(cls, s): cls.quit = True
            @classmethod
            def isActive(cls): return False
        ui.NSApplication = FakeApp
        d = NSUserDefaults.standardUserDefaults(); d.setObject_forKey_("secret draft", "draft_text")
        c.text_view.setString_("secret text"); c._run_typing("secret text", 99); c._kbd_locked = True
        c.selfDestruct_(None)
        check("text box wiped", str(c.text_view.string()) == "")
        check("saved draft wiped", d.stringForKey_("draft_text") is None)
        check("run stopped, keyboard unlocked", c._typing_gen is None and not c._kbd_locked)
        check("app quits, skipping install-on-quit", FakeApp.quit and c._relaunching)
        print(f"{sum(res)}/{len(res)} passed", flush=True); os._exit(0)
app = NSApplication.sharedApplication(); app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
t = T.alloc().init(); t.performSelector_withObject_afterDelay_(b"go:", None, 0.3); app.run()
