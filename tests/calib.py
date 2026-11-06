import sys, os, subprocess, random
sys.path.insert(0, "/Users/samuelmittman/humantype-app")
from AppKit import *; from Foundation import *
import objc, faulthandler; faulthandler.dump_traceback_later(60, exit=True)
import humantype as ht, humantype_ui as ui
OUT = sys.argv[1]
res = []
def check(n, ok, info=""): res.append(ok); print(("PASS " if ok else "FAIL ") + n, info, flush=True)
def snap(c, n): subprocess.run(["screencapture", "-x", "-o", "-l", str(c.window.windowNumber()), f"{OUT}/{n}.png"])
d = NSUserDefaults.standardUserDefaults(); d.removeObjectForKey_("ht_calibration"); d.removeObjectForKey_("ht_realism")
class T(NSObject):
    def go_(self, _):
        c = self.c = ui.AppController.alloc().init()
        NSApplication.sharedApplication().setDelegate_(c); c.applicationDidFinishLaunching_(None); _cb = c._tap_callback; c._teardown_global_tap(); c._tap_callback = _cb; c._tap = 'isolated'
        c.window.makeKeyAndOrderFront_(None)
        check("realism has a You option", list(ui.REALISM)[-1] == "You" and c._realism_seg.segmentCount() == 4)
        c._realism_seg.setSelectedSegment_(3); c.realismChanged_(c._realism_seg)
        check("You before calibrating → opens Calibrate, keeps old choice", c._active_tab == "calibrate" and c._realism_name != "You")
        self.performSelector_withObject_afterDelay_(b"a:", None, 0.6)
    def a_(self, _):
        c = self.c
        snap(c, "cal_empty")
        # simulate a person: ~61 wpm (0.18s/key with spread), occasional pause, 7 fixes
        text = "The summer I rebuilt my grandfather's radio, I learned that patience is a skill you build one small fix at a time, and that rushing only ever makes it worse."
        t = 1000.0; typed = ""; fixes = 0
        random.seed(4)
        for i, ch in enumerate(text):
            if i in (20, 41, 60, 77, 95, 110, 130):           # 7 corrections
                t += 0.2; c._calib_events.append((t, 4)); t += 0.25; c._calib_events.append((t, 51))
            t += max(0.05, random.gauss(0.18, 0.06)) + (1.4 if i % 45 == 44 else 0)
            c._calib_events.append((t, 0)); typed += ch
        c._calib_tv.setString_(typed)
        c.calibApply_(None)
        cal = c._calibration()
        print("measured:", cal, flush=True)
        check("measures ~60 wpm", cal and 45 <= cal["wpm"] <= 75, str(cal and cal["wpm"]))
        check("counts 7 corrections", cal and cal["corrections"] == 7)
        check("avg ~0.18s/key", cal and 0.14 <= cal["avg_key"] <= 0.22)
        check("switches realism to You and sets speed", c._realism_name == "You" and c._current_wpm == cal["wpm"]
              and c._realism_seg.selectedSegment() == 3)
        check("summary shown", "matched!" in str(c._calib_status.stringValue()))
        # the engine really uses it
        class KB:
            def __init__(s, *a): pass
            def type(s, ch): pass
            def press(s, k): pass
            def release(s, k): pass
        ht._CTypesKeyboard = KB
        c._run_typing("abc", 99)
        check("typing uses your rhythm", abs(c.typist.variation - cal["variation"]) < 1e-9
              and abs(c.typist.pause_rate - cal["pause_rate"]) < 1e-9 and abs(c.typist.mistake - cal["typo"]) < 1e-9)
        c.state.stop(); c._finish(False)
        self.performSelector_withObject_afterDelay_(b"b:", None, 0.5)
    def b_(self, _):
        snap(self.c, "cal_done"); self.c._show_tab("type", animate=False)
        self.performSelector_withObject_afterDelay_(b"c:", None, 0.4)
    def c_(self, _):
        snap(self.c, "cal_typepage")
        d.removeObjectForKey_("ht_calibration"); d.removeObjectForKey_("ht_realism"); d.removeObjectForKey_("ht_wpm")
        print(f"{sum(res)}/{len(res)} passed", flush=True); os._exit(0)
app = NSApplication.sharedApplication(); app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
t = T.alloc().init(); t.performSelector_withObject_afterDelay_(b"go:", None, 0.3); app.run()
