# Local HTTP relay to timestamp.apple.com for codesign --timestamp=URL (see release.sh).
import http.server, urllib.request, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        req = urllib.request.Request("http://timestamp.apple.com" + self.path, data=body,
                                     headers={"Content-Type": self.headers.get("Content-Type", "application/timestamp-query")})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        self.send_response(200); self.send_header("Content-Type", "application/timestamp-reply")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
