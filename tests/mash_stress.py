import sys, os, random
sys.path.insert(0, "/Users/samuelmittman/humantype-app")
from AppKit import *; from Foundation import *; from Quartz import *
import objc, faulthandler; faulthandler.dump_traceback_later(120, exit=True)
import humantype as ht, humantype_ui as ui
res = []
def check(n, ok, info=""): res.append(ok); print(("PASS " if ok else "FAIL ") + n, info, flush=True)
out = []
class KB:
    def __init__(self, *a): pass
    def type(self, ch): out.append(ch)
    def press(self, k):
        if k == ht.Key.backspace and out: out.pop()
        elif k == ht.Key.enter: out.append("\n")
        elif k == ht.Key.tab: out.append("\t")
    def release(self, k): pass
ht._CTypesKeyboard = KB
class FakeApp:
    @classmethod
    def sharedApplication(cls): return cls
    @classmethod
    def isActive(cls): return False
TEXT = ("The quick brown fox jumps over the lazy dog. Pack my box with five dozen liquor jugs! "
        "How vexingly quick daft zebras jump; sphinx of black quartz, judge my vow.\nNew line here.")
class T(NSObject):
    def go_(self, _):
        c = self.c = ui.AppController.alloc().init()
        NSApplication.sharedApplication().setDelegate_(c); c.applicationDidFinishLaunching_(None); _cb = c._tap_callback; c._teardown_global_tap(); c._tap_callback = _cb; c._tap = 'isolated'; c._install_global_tap = lambda: None  # ignore the real keyboard
        ui.NSApplication = FakeApp
        c._sync_dnd_remap = lambda force_off=False: None
        bad = 0; total = 0; finished_runs = 0
        for realism in ("Minimal", "Natural", "Pro"):
            for trial in range(3):
                c._realism_name = realism
                del out[:]
                c._run_typing(TEXT, 5); c._kbd_locked = True
                presses = 0
                while c._typing_gen is not None and presses < 2000:
                    before = c._kb_counter.count
                    c.mashStep_(None)
                    presses += 1; total += 1
                    if c._typing_gen is not None and c._kb_counter.count - before != 1:
                        bad += 1
                finished_runs += 1 if c._typing_gen is None else 0
                ok_text = "".join(out) == TEXT
                if not ok_text: print("  mismatch", realism, repr("".join(out)[-40:]))
                res.append(ok_text)
                c._kbd_locked = False
        check(f"every press typed exactly one keystroke ({total} presses, 9 runs incl. typos)", bad == 0, f"{bad} bad")
        check("every run finished with the exact text", finished_runs == 9 and all(res[-9:]))
        # through the real key path (tap → queued mashStep), rapid presses
        del out[:]
        c._realism_name = "Pro"; c._run_typing(TEXT, 99); c._kbd_locked = True
        self.c0 = c._kb_counter.count
        for i in range(60):
            e = CGEventCreateKeyboardEvent(None, random.choice([0, 1, 2, 35, 40]), True)
            CGEventSetIntegerValueField(e, kCGEventSourceUnixProcessID, 0)
            r = c._tap_callback(None, kCGEventKeyDown, e, None)
            if r is not None: bad += 1
        self.performSelector_withObject_afterDelay_(b"a:", None, 0.8)
    def a_(self, _):
        c = self.c
        check("60 rapid real key presses → 60 keystrokes, none leaked", c._kb_counter.count - self.c0 == 60, str(c._kb_counter.count - self.c0))
        c.state.stop()
        print(f"{sum(res)}/{len(res)} passed", flush=True); os._exit(0)
app = NSApplication.sharedApplication(); app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
t = T.alloc().init(); t.performSelector_withObject_afterDelay_(b"go:", None, 0.3); app.run()
