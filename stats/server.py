#!/usr/bin/env python3
"""
HumanType anonymous usage stats: collector + dashboard. Runs on the NMX PC.

Two listeners, on purpose:

* 127.0.0.1:8792  POST /ht/api/stats   Caddy publishes this one path on nmx.net, so copies of
  HumanType anywhere can send their numbers. Nothing can be read back from it.
* 127.0.0.1:8794  GET  /  /api/summary  The dashboard. Caddy publishes it on :8793 to the home
  network and Tailscale only (firewall rule "HumanType Stats Dashboard" + Caddy's remote_ip check);
  it is never behind the tunnel.

What a copy sends (only if its user said yes): a random install ID made for this, the app and
macOS versions, Mac model/chip/RAM/cores, and per-run numbers - how many characters and letters
were typed, how long it took, the speed, the mode, how many typos it made and fixed, and how
closely it kept its timing. Never any of the text, names, emails or a hardware fingerprint.

Stdlib only. Data: events.jsonl next to this file.
"""

import json
import os
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
EVENTS = os.path.join(HERE, "events.jsonl")
DASHBOARD = os.path.join(HERE, "dashboard.html")
INGEST_ADDR = ("127.0.0.1", int(os.environ.get("HT_INGEST_PORT", 8792)))
# The dashboard listens on this machine only; Caddy serves it on :8793 to the home network and
# Tailscale (Windows blocks python.exe inbound here, and Caddy re-checks the visitor's address).
DASH_ADDR = (os.environ.get("HT_DASH_HOST", "127.0.0.1"), int(os.environ.get("HT_DASH_PORT", 8794)))
LOCK = threading.Lock()

KINDS = {"open", "run", "feature"}
MODES = {"normal", "clipboard", "practice"}
FEATURES = {"self_destruct", "calibrate", "tour_done", "tour_skipped", "lock", "key_by_key",
            "type_clipboard", "snippet_saved", "stats_on", "stats_off"}
# number fields per event: name -> (min, max)
NUMBERS = {"chars": (0, 10**7), "letters": (0, 10**7), "seconds": (0, 86400), "wpm_set": (0, 400),
           "wpm_real": (0, 600), "typos": (0, 10**6), "keypresses": (0, 10**7),
           "lag_avg": (0, 5000), "lag_p95": (0, 5000), "on_time": (0, 100), "hour": (0, 23)}
FLAGS = {"finished", "key_by_key", "locked"}


def clean(value, limit, pattern=r"[^A-Za-z0-9 .,()_-]"):
    return re.sub(pattern, "", str(value or ""))[:limit]


def ingest(payload):
    install = str(payload.get("install") or "")
    events = payload.get("events")
    if not re.fullmatch(r"[0-9A-Fa-f-]{36}", install) or not isinstance(events, list) or not 0 < len(events) <= 300:
        return 400, {"error": "bad request"}
    device = {
        "install": install[:8],                       # enough to count Macs, not to follow anyone
        "app": clean(payload.get("app"), 12), "macos": clean(payload.get("macos"), 10),
        "model": re.sub(r"[^A-Za-z0-9,]", "", str(payload.get("model") or ""))[:16],
        "chip": clean(payload.get("chip"), 40),
    }
    for name, hi in (("ram_gb", 1024), ("cores", 256)):
        v = payload.get(name)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            device[name] = int(min(max(v, 0), hi))
    received = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = []
    for e in events:
        if not isinstance(e, dict) or e.get("kind") not in KINDS:
            continue
        row = dict(device, kind=e["kind"], received=received,
                   day=clean(e.get("day"), 10, r"[^0-9-]") or received[:10])
        if e["kind"] == "run":
            row["mode"] = e.get("mode") if e.get("mode") in MODES else "normal"
            row["realism"] = clean(e.get("realism"), 12)
        if e["kind"] == "feature":
            if e.get("name") not in FEATURES:
                continue
            row["name"] = e["name"]
        for name, (lo, hi) in NUMBERS.items():
            v = e.get(name)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                row[name] = round(min(max(float(v), lo), hi), 2)
        for name in FLAGS:
            if isinstance(e.get(name), bool):
                row[name] = e[name]
        lines.append(json.dumps(row))
    with LOCK:
        with open(EVENTS, "a", encoding="utf-8") as fh:
            fh.write("".join(line + "\n" for line in lines))
    return 200, {"ok": True, "stored": len(lines)}


_cache = {"at": 0, "data": None}


def load_events():
    out = []
    with LOCK:
        if os.path.exists(EVENTS):
            with open(EVENTS, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return out


def summary():
    if time.time() - _cache["at"] < 5 and _cache["data"]:
        return _cache["data"]
    events = load_events()
    today = datetime.now().date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    week = set(days[-7:])
    runs = [e for e in events if e["kind"] == "run"]
    real = [e for e in runs if e.get("mode") != "practice"]

    def avg(values):
        values = [v for v in values if isinstance(v, (int, float))]
        return round(sum(values) / len(values), 1) if values else None

    per = defaultdict(list)
    for e in events:
        per[e["install"]].append(e)
    macs = []
    for install, evts in per.items():
        last = evts[-1]
        r = [e for e in evts if e["kind"] == "run" and e.get("mode") != "practice"]
        macs.append({
            "install": install, "model": last.get("model"), "chip": last.get("chip"),
            "ram_gb": last.get("ram_gb"), "cores": last.get("cores"), "app": last.get("app"),
            "macos": last.get("macos"), "first_seen": evts[0].get("received"),
            "last_seen": last.get("received"), "runs": len(r),
            "letters": int(sum(e.get("letters", 0) for e in r)),
            "chars": int(sum(e.get("chars", 0) for e in r)),
            "minutes": round(sum(e.get("seconds", 0) for e in r) / 60, 1),
            "wpm_real": avg(e.get("wpm_real") for e in r),
            "on_time": avg(e.get("on_time") for e in r),
            "lag_avg": avg(e.get("lag_avg") for e in r),
            "key_by_key": sum(1 for e in r if e.get("key_by_key")),
            "active_today": any(e.get("day") == days[-1] for e in evts),
        })
    macs.sort(key=lambda m: m["last_seen"] or "", reverse=True)

    daily = []
    for d in days:
        dr = [e for e in real if e.get("day") == d]
        daily.append({"day": d, "letters": int(sum(e.get("letters", 0) for e in dr)), "runs": len(dr),
                      "macs": len({e["install"] for e in events if e.get("day") == d})})

    features = Counter(e["name"] for e in events if e["kind"] == "feature")
    features["key_by_key"] = features.get("key_by_key", 0) + sum(1 for e in real if e.get("key_by_key"))
    data = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": {
            "macs": len(per),
            "active_today": sum(1 for m in macs if m["active_today"]),
            "active_7d": len({e["install"] for e in events if e.get("day") in week}),
            "runs": len(real),
            "letters": int(sum(e.get("letters", 0) for e in real)),
            "chars": int(sum(e.get("chars", 0) for e in real)),
            "typing_hours": round(sum(e.get("seconds", 0) for e in real) / 3600, 2),
            "typos": int(sum(e.get("typos", 0) for e in real)),
            "wpm_real": avg(e.get("wpm_real") for e in real),
            "wpm_set": avg(e.get("wpm_set") for e in real),
            "on_time": avg(e.get("on_time") for e in real),
            "lag_avg": avg(e.get("lag_avg") for e in real),
            "finished_pct": round(100 * sum(1 for e in real if e.get("finished")) / len(real)) if real else None,
            "practice_runs": len(runs) - len(real),
        },
        "daily": daily,
        "modes": dict(Counter(e.get("mode", "normal") for e in real)),
        "realism": dict(Counter(e.get("realism") or "?" for e in real)),
        "features": dict(features),
        "versions": dict(Counter(m["app"] or "?" for m in macs)),
        "macos": dict(Counter((m["macos"] or "?").split(".")[0] for m in macs)),
        "chips": dict(Counter(m["chip"] or "?" for m in macs)),
        "macs": macs,
    }
    _cache.update(at=time.time(), data=data)
    return data


class IngestHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if self.path.split("?")[0] != "/ht/api/stats":
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 262144:
            return self._send(413, {"error": "too large"})
        try:
            payload = json.loads(self.rfile.read(length))
        except Exception:
            return self._send(400, {"error": "bad json"})
        self._send(*ingest(payload if isinstance(payload, dict) else {}))

    def do_GET(self):
        self._send(404, {"error": "not found"})


class DashHandler(IngestHandler):
    def do_POST(self):
        self._send(405, {"error": "read only"})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/summary":
            return self._send(200, summary())
        if path in ("/", "/index.html"):
            with open(DASHBOARD, "rb") as fh:
                raw = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            return self.wfile.write(raw)
        self._send(404, {"error": "not found"})


def main():
    ingest_srv = ThreadingHTTPServer(INGEST_ADDR, IngestHandler)
    dash_srv = ThreadingHTTPServer(DASH_ADDR, DashHandler)
    threading.Thread(target=ingest_srv.serve_forever, daemon=True).start()
    print(f"collector on {INGEST_ADDR}, dashboard on {DASH_ADDR}", flush=True)
    dash_srv.serve_forever()


if __name__ == "__main__":
    main()
