import sys, os
sys.path.insert(0, "/Users/samuelmittman/humantype-app")
from AppKit import *; from Foundation import *
import humantype as ht, humantype_ui as ui
res = []
def check(n, ok, info=""): res.append(ok); print(("PASS " if ok else "FAIL ") + n, info, flush=True)
class NullKB:
    typed = []
    def __init__(self, *a): pass
    def type(self, ch): NullKB.typed.append(ch)
    def press(self, k): pass
    def release(self, k): pass
ht._CTypesKeyboard = NullKB
class FakeApp:
    active = True
    @classmethod
    def sharedApplication(cls): return cls
    @classmethod
    def isActive(cls): return cls.active
class T(NSObject):
    def go_(self, _):
        c = self.c = ui.AppController.alloc().init()
        NSApplication.sharedApplication().setDelegate_(c); c.applicationDidFinishLaunching_(None); _cb = c._tap_callback; c._teardown_global_tap(); c._tap_callback = _cb; c._tap = 'isolated'; c._install_global_tap = lambda: None  # ignore the real keyboard
        c.window.makeKeyAndOrderFront_(None)
        c._is_trusted = lambda: True
        c._countdown_secs = 1; c._apply_wpm(200)
        ui.NSApplication = FakeApp
        c.text_view.setString_("hi there")
        c.start_(None)
        check("window stays visible after Start", bool(c.window.isVisible()))
        check("window floats above other apps", c.window.level() == NSFloatingWindowLevel)
        check("pause/stop enabled", c.pause_btn.isEnabled() and c.stop_btn.isEnabled())
        self.step = 0
        self.performSelector_withObject_afterDelay_(b"a:", None, 1.6)
    def a_(self, _):
        c = self.c
        check("HumanType frontmost: nothing typed", NullKB.typed == [])
        check("status asks to click target", "Click where" in str(c.status.stringValue()), str(c.status.stringValue()))
        FakeApp.active = False
        self.performSelector_withObject_afterDelay_(b"b:", None, 0.4)
    def b_(self, _):
        c = self.c
        n = len(NullKB.typed)
        check("typing proceeds once another app has focus", n > 0, str(n))
        c.togglePause_(None)
        self.n = len(NullKB.typed)
        self.performSelector_withObject_afterDelay_(b"c:", None, 0.5)
    def c_(self, _):
        c = self.c
        check("Pause button stops keystrokes", len(NullKB.typed) == self.n)
        c.togglePause_(None)
        self.performSelector_withObject_afterDelay_(b"d:", None, 2.0)
    def d_(self, _):
        c = self.c
        check("resumed and finished", c._typing_gen is None and "".join(NullKB.typed).replace("\b","") != "", repr("".join(NullKB.typed)))
        check("window back to normal level", c.window.level() == NSNormalWindowLevel)
        check("window still open after finish", bool(c.window.isVisible()))
        # stop button path
        FakeApp.active = False
        c.text_view.setString_("a much longer text to stop midway through it")
        c.start_(None)
        self.performSelector_withObject_afterDelay_(b"e:", None, 1.5)
    def e_(self, _):
        c = self.c
        c.stop_(None)
        self.performSelector_withObject_afterDelay_(b"f:", None, 0.5)
    def f_(self, _):
        c = self.c
        check("Stop button ends the run", c._typing_gen is None and str(c.status.stringValue()) == "Stopped.", str(c.status.stringValue()))
        for k in ("ht_wpm",): NSUserDefaults.standardUserDefaults().removeObjectForKey_(k)
        print(f"{sum(res)}/{len(res)} passed", flush=True); os._exit(0)
app = NSApplication.sharedApplication(); app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
t = T.alloc().init(); t.performSelector_withObject_afterDelay_(b"go:", None, 0.3); app.run()
