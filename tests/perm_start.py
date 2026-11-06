import sys, os
sys.path.insert(0, "/Users/samuelmittman/humantype-app")
from AppKit import *; from Foundation import *
import objc, faulthandler; faulthandler.dump_traceback_later(60, exit=True)
import humantype as ht, humantype_ui as ui
res = []
def check(n, ok, info=""): res.append(ok); print(("PASS " if ok else "FAIL ") + n, info, flush=True)
class KB:
    def __init__(s, *a): pass
    def type(s, ch): pass
    def press(s, k): pass
    def release(s, k): pass
ht._CTypesKeyboard = KB
calls = []
ui.subprocess.run = lambda args, **kw: calls.append(list(args))
class T(NSObject):
    def go_(self, _):
        c = self.c = ui.AppController.alloc().init()
        NSApplication.sharedApplication().setDelegate_(c); c.applicationDidFinishLaunching_(None)
        _cb = c._tap_callback; c._teardown_global_tap(); c._tap_callback = _cb
        c._prompt_accessibility = lambda: calls.append("prompt")
        c._request_post_access = lambda: calls.append("request")
        c._open_accessibility_settings = lambda: calls.append("settings")
        c._permission_help = lambda: calls.append("help")
        c._countdown_secs = 30
        # 1) macOS says "not trusted" but the tap installs → just start, no prompt
        c._is_trusted = lambda: False
        c._tap = None; c._install_global_tap = lambda: setattr(c, "_tap", "ok")
        c.text_view.setString_("hello"); c.start_(None)
        check("granted but misreported: starts with no prompt", c._typing_gen is not None and not calls)
        c.state.stop(); c._finish(False)
        # 2) truly blocked: one normal ask, then it starts by itself once allowed
        c._tap = None; c._install_global_tap = lambda: None
        self.allowed = False
        c._is_trusted = lambda: self.allowed
        c.start_(None)
        check("blocked: asks once (prompt + Settings), doesn't start", c._typing_gen is None and calls == ["prompt", "request", "settings"], str(calls))
        self.allowed = True
        self.performSelector_withObject_afterDelay_(b"a:", None, 1.6)
    def a_(self, _):
        c = self.c
        check("switch turned on → typing starts by itself", c._typing_gen is not None)
        c.state.stop(); c._finish(False)
        # 3) pressing Start again while still blocked → the fix dialog, not a prompt loop
        del calls[:]; self.allowed = False
        c.start_(None); c.start_(None)          # press Start twice while blocked
        check("second Start while blocked → Fix permission dialog", calls == ["prompt", "request", "settings", "help"], str(calls))
        c._perm_wait_token += 1                 # stop the first press's waiting
        # 4) the fix: clears HumanType's own stale entries, asks again, carries on
        del calls[:]
        c._fix_permission(start_when_ready=True)
        resets = [x for x in calls if isinstance(x, list)]
        check("fix resets only HumanType's Accessibility + PostEvent entries",
              resets == [["/usr/bin/tccutil", "reset", "Accessibility", str(NSBundle.mainBundle().bundleIdentifier() or "com.humantype.app")],
                         ["/usr/bin/tccutil", "reset", "PostEvent", str(NSBundle.mainBundle().bundleIdentifier() or "com.humantype.app")]], str(resets))
        check("fix asks again and opens Settings", calls[-3:] == ["prompt", "request", "settings"])
        self.allowed = True
        self.performSelector_withObject_afterDelay_(b"b:", None, 1.6)
    def b_(self, _):
        c = self.c
        check("after the fix, typing starts by itself", c._typing_gen is not None)
        c.state.stop(); c._finish(False)
        # 5) tour: reopened already and still blocked → Fix permission
        self.allowed = False; c._tap = None; c._just_relaunched = True
        c.showTutorial_(None); ob = c._onboarding; ob.next_(None)
        ob._settings_opened_at = 1
        self.performSelector_withObject_afterDelay_(b"d:", None, 1.4)
    def d_(self, _):
        c = self.c; ob = c._onboarding
        check("tour after a reopen: button is Fix permission", str(ob._perm_btn.title()) == "Fix permission", str(ob._perm_btn.title()))
        del calls[:]; ob.permAction_(None)
        check("tour Fix permission clears the stale entry", any(isinstance(x, list) and "tccutil" in x[0] for x in calls))
        ob._close(); NSUserDefaults.standardUserDefaults().removeObjectForKey_("ht_onboarded")
        print(f"{sum(res)}/{len(res)} passed", flush=True); os._exit(0)
app = NSApplication.sharedApplication(); app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
t = T.alloc().init(); t.performSelector_withObject_afterDelay_(b"go:", None, 0.3); app.run()
