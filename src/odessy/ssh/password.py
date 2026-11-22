"""Session-only SSH password broker. No credential is written to disk or argv."""
from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Broker(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address

    def handle_error(self, request, address):
        pass  # Never emit credential-bearing request diagnostics.


class PasswordLease:
    def __init__(self, password: str):
        if not isinstance(password, str) or not 1 <= len(password) <= 1024 or any(c in password for c in "\r\n\x00"):
            raise ValueError('Password must be 1–1024 characters without line breaks')
        self.password = password
        self.token = secrets.token_urlsafe(32)
        self.expires = time.monotonic() + 8 * 60 * 60
        lease = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if (self.path != '/password' or time.monotonic() >= lease.expires or
                        not lease.password or not hmac.compare_digest(
                            self.headers.get('Authorization', ''), 'Bearer ' + lease.token)):
                    self.send_error(403)
                    return
                payload = lease.password.encode('utf-8')
                self.send_response(200)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = _Broker(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}/password'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self._closed = threading.Lock()
        self._expiry = threading.Timer(8 * 60 * 60, self.close)
        self._expiry.daemon = True
        self._expiry.start()

    @property
    def active(self) -> bool:
        return bool(self.password) and time.monotonic() < self.expires

    def environment(self, helper: str) -> dict[str, str]:
        if not self.active:
            raise ValueError('SSH password expired; enter it again')
        return {**os.environ, 'SSH_ASKPASS': helper, 'SSH_ASKPASS_REQUIRE': 'force',
                'DISPLAY': 'odessy:0', 'ODESSY_SSH_ASKPASS': '1',
                'ODESSY_SSH_PASSWORD_URL': self.url, 'ODESSY_SSH_PASSWORD_TOKEN': self.token}

    def close(self):
        with self._closed:
            self.password = ''
            self.token = ''
            self._expiry.cancel()
            if self.thread.is_alive():
                self.server.shutdown()
                self.thread.join(timeout=3)
            self.server.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
