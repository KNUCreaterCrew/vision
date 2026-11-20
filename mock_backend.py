"""Loopback-only development receiver for the existing metadata API contract."""

import argparse
from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from urllib.parse import unquote, urlsplit

from zeroframe.transport import validate_report


DEVELOPMENT_KEY = "local-development-only"
MAX_BODY = 1024 * 1024


def make_server(*, port=8765, api_key=DEVELOPMENT_KEY, on_report=None):
    class Receiver(BaseHTTPRequestHandler):
        def reply(self, status, value):
            data = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self.connection.settimeout(3)
            path = re.fullmatch(r"/api/v1/rooms/([^/]+)/seats/status", urlsplit(self.path).path)
            if path is None:
                self.reply(404, {"ok": False})
                return
            supplied = self.headers.get("X-Edge-Api-Key", "")
            if not hmac.compare_digest(supplied.encode("utf-8"), api_key.encode("utf-8")):
                self.reply(401, {"ok": False})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY or self.headers.get_content_type() != "application/json":
                    raise ValueError("Invalid body")
                report = json.loads(self.rfile.read(length))
                validate_report(report)
                if report["room_id"] != unquote(path[1]):
                    raise ValueError("Room mismatch")
            except (ValueError, TypeError, OSError):
                self.reply(400, {"ok": False})
                return
            if on_report is not None:
                on_report(report)
            else:
                print(json.dumps(report, ensure_ascii=False), flush=True)
            self.reply(200, {"ok": True, "updated_count": len(report["seats"]),
                             "server_time": datetime.now(timezone.utc).isoformat(timespec="milliseconds")})

        def log_message(self, *args):
            # Do not dump headers, API keys, or invalid/untrusted request bodies.
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Receiver)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    with make_server(port=args.port, api_key=os.environ.get("EDGE_NODE_SECRET_KEY", DEVELOPMENT_KEY)) as server:
        print(f"Local metadata receiver: http://127.0.0.1:{server.server_port}", flush=True)
        print("Ctrl+C to stop. Reports are printed; headers and keys are not logged.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
