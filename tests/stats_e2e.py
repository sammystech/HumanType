import sys, os, json, time, subprocess, tempfile, urllib.request
sys.path.insert(0, "/Users/samuelmittman/humantype-app")
TMPD = tempfile.mkdtemp()
for f in ("server.py", "dashboard.html"):
    subprocess.run(["cp", f"/Users/samuelmittman/humantype-app/stats/{f}", TMPD])
srv = subprocess.Popen([sys.executable, f"{TMPD}/server.py"], env=dict(os.environ, HT_INGEST_PORT="18792",
                       HT_DASH_PORT="18793", HT_DASH_HOST="127.0.0.1"))
for _ in range(50):                      # wait until the test server answers
    try:
        urllib.request.urlopen("http://127.0.0.1:18793/api/summary", timeout=1); break
    except Exception:
        time.sleep(0.1)
os.environ["HUMANTYPE_STATS_URL"] = "http://127.0.0.1:18792/ht/api/stats"
from AppKit import *; from Foundation import *; from Quartz import *
import objc, faulthandler; faulthandler.dump_traceback_later(60, exit=True)
import humantype as ht, humantype_ui as ui
res = []
def check(n, ok, info=""): res.append(ok); print(("PASS " if ok else "FAIL ") + n, info, flush=True)
d = NSUserDefaults.standardUserDefaults()
for k in ("ht_stats_choice", "ht_stats_queue", "ht_stats_install", "ht_stats_last_open"):
    d.removeObjectForKey_(k)
class KB:
    def __init__(s, *a): pass
    def type(s, ch): pass
    def press(s, k): pass
    def release(s, k): pass
ht._CTypesKeyboard = KB
class FakeApp:
    @classmethod
    def sharedApplication(cls): return cls
    @classmethod
    def isActive(cls): return False
def summary(): return json.load(urllib.request.urlopen("http://127.0.0.1:18793/api/summary"))
class T(NSObject):
    def go_(self, _):
        c = self.c = ui.AppController.alloc().init()
        NSApplication.sharedApplication().setDelegate_(c); c.applicationDidFinishLaunching_(None)
        _cb = c._tap_callback; c._teardown_global_tap(); c._tap_callback = _cb; c._tap = "isolated"
        ui.NSApplication = FakeApp
        c._is_trusted = lambda: True; c._apply_wpm(200); c._realism_name = "Natural"
        c._stat({"kind": "open"})
        check("nothing is recorded before the user says yes", not d.stringForKey_("ht_stats_queue"))
        c._set_stats_choice(True)
        check("Settings switch + menu follow the choice", c._st_stats.state() == 1 and c.stats_menu_item.state() == 1)
        self.text = "Hello there, this is a stats test run. It types forty-odd characters!"
        c._run_typing(self.text, 0)
        self.wait(b"a:", 0.5)
    @objc.python_method
    def wait(self, sel, t): self.performSelector_withObject_afterDelay_(sel, None, t)
    def a_(self, _):
        if self.c._typing_gen is not None: return self.wait(b"a:", 0.5)
        self.wait(b"b:", 1.5)                     # let the batch send
    def b_(self, _):
        s = summary(); t = s["totals"]
        print("server totals:", t, flush=True)
        check("the Mac shows up on the dashboard", t["macs"] == 1 and len(s["macs"]) == 1)
        check("the run was counted with real letters", t["runs"] == 1 and t["letters"] >= sum(ch.isalpha() for ch in self.text) - 1, str(t["letters"]))
        check("real speed + smoothness measured", t["wpm_real"] is not None and t["on_time"] is not None and t["lag_avg"] is not None)
        check("hardware described (chip, model)", bool(s["macs"][0]["chip"]) and bool(s["macs"][0]["model"]), str(s["macs"][0]))
        check("queue emptied after sending", json.loads(d.stringForKey_("ht_stats_queue") or "[]") == [])
        raw = open(f"{TMPD}/events.jsonl").read()
        check("none of the typed text is in the stored data", "stats test run" not in raw and "Hello" not in raw)
        check("install ID is shortened on the server", all(len(json.loads(l)["install"]) == 8 for l in raw.splitlines()))
        self.c._set_stats_choice(False)
        self.c._stat({"kind": "open"})
        check("turning it off stops recording and clears the queue", not d.stringForKey_("ht_stats_queue"))
        for k in ("ht_stats_choice", "ht_stats_queue", "ht_stats_install", "ht_stats_last_open", "ht_wpm"):
            d.removeObjectForKey_(k)
        srv.terminate()
        print(f"{sum(res)}/{len(res)} passed", flush=True); os._exit(0)
app = NSApplication.sharedApplication(); app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
t = T.alloc().init(); t.performSelector_withObject_afterDelay_(b"go:", None, 0.3); app.run()
