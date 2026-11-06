import sys, os
sys.path.insert(0, "/Users/samuelmittman/humantype-app")
from AppKit import *; from Foundation import *; from Quartz import *
import objc, faulthandler; faulthandler.dump_traceback_later(60, exit=True)
import humantype as ht, humantype_ui as ui
res = []
def check(n, ok, info=""): res.append(ok); print(("PASS " if ok else "FAIL ") + n, info, flush=True)
typed = []
NSUserDefaults.standardUserDefaults().removeObjectForKey_('ht_mash_mode')
class KB:
    def __init__(self, *a): pass
    def type(self, ch): typed.append(ch)
    def press(self, k): typed.append("<%s>" % k)
    def release(self, k): pass
ht._CTypesKeyboard = KB
class FakeApp:
    @classmethod
    def sharedApplication(cls): return cls
    @classmethod
    def isActive(cls): return False
def ev(kc, mods=0):
    e = CGEventCreateKeyboardEvent(None, kc, True); CGEventSetFlags(e, mods)
    CGEventSetIntegerValueField(e, kCGEventSourceUnixProcessID, 0); return e
class T(NSObject):
    def go_(self, _):
        c = self.c = ui.AppController.alloc().init()
        NSApplication.sharedApplication().setDelegate_(c); c.applicationDidFinishLaunching_(None); _cb = c._tap_callback; c._teardown_global_tap(); c._tap_callback = _cb; c._tap = 'isolated'; c._install_global_tap = lambda: None  # ignore the real keyboard
        ui.NSApplication = FakeApp
        c._is_trusted = lambda: True; c._apply_wpm(200)
        c.lock_binding = (97, 0); c._sync_dnd_remap = lambda force_off=False: None
        self.cb = lambda kc, m=0: c._tap_callback(None, kCGEventKeyDown, ev(kc, m), None)
        check("key-by-key is on by default", c._mash_mode and c._st_mash.state() == 1)
        c._run_typing("Hello world, this is key by key.", 99)     # long countdown
        check("F6 locks during the countdown", self.cb(97) is None and c._kbd_locked)
        self.w(b"a:", 0.2)
    @objc.python_method
    def w(self, sel, t): self.performSelector_withObject_afterDelay_(sel, None, t)
    def a_(self, _):
        c = self.c
        for _ in range(5): self.cb(35)            # press P five times
        self.w(b"b:", 0.3)
    def b_(self, _):
        c = self.c
        check("each key press types exactly one keystroke", len(typed) == 5, repr(typed))
        check("pressing a key skipped the countdown", c._countdown_left == 0)
        self.n = len(typed); self.w(b"c:", 1.2)
    def c_(self, _):
        c = self.c
        check("nothing types between presses", len(typed) == self.n, f"{len(typed)} vs {self.n}")
        self.cb(97)                               # unlock
        self.w(b"d:", 0.3)
    def d_(self, _):
        c = self.c
        check("unlocking pauses the run", not c._kbd_locked and c.state.paused)
        check("keys type normally again (not swallowed)", self.cb(35) is not None)
        self.n = len(typed); self.w(b"e:", 1.0)
    def e_(self, _):
        c = self.c
        check("paused: HumanType types nothing", len(typed) == self.n)
        self.cb(97); self.w(b"f:", 0.2)           # lock again
    def f_(self, _):
        c = self.c
        for _ in range(3): self.cb(1)
        self.w(b"g:", 0.3)
    def g_(self, _):
        c = self.c
        check("locking again carries on key by key", len(typed) == self.n + 3 and not c.state.paused, f"{len(typed)} vs {self.n + 3}")
        self.cb(1, kCGEventFlagMaskCommand)       # ⌘S stops (instant)
        self.w(b"h:", 0.3)
    def h_(self, _):
        c = self.c
        check("⌘S stops at once", c._typing_gen is None, str(c._typing_gen))
        if c._kbd_locked: self.cb(97)
        # setting off: the old lock (keys swallowed, typing carries on by itself)
        c._st_mash.setState_(0); c.settingsMashToggled_(c._st_mash)
        del typed[:]
        c._run_typing("abcdefghij klmnop", 0); self.cb(97)
        self.w(b"i:", 3.5)
    def i_(self, _):
        c = self.c
        check("setting off: lock just blocks keys", self.cb(35) is None)
        check("setting off: typing carries on by itself", len(typed) > 0, str(len(typed)))
        if c.state is not None: c.state.stop()
        NSUserDefaults.standardUserDefaults().removeObjectForKey_("ht_mash_mode")
        if c._kbd_locked: self.cb(97)
        print(f"{sum(res)}/{len(res)} passed", flush=True); os._exit(0)
app = NSApplication.sharedApplication(); app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
t = T.alloc().init(); t.performSelector_withObject_afterDelay_(b"go:", None, 0.3); app.run()
