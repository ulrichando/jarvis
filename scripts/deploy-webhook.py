#!/usr/bin/env python3
"""
JARVIS Deploy Webhook — listens for Forgejo push events and redeploys.

Run on CT104:
    python3 scripts/deploy-webhook.py

Then add a Forgejo webhook:
    URL: http://<CT104-IP>:9000/deploy
    Secret: set DEPLOY_SECRET env var (same on both sides)
    Events: Push

To run as a service, add to docker-compose.yml or run with:
    nohup python3 scripts/deploy-webhook.py > /tmp/jarvis-webhook.log 2>&1 &
"""

import hashlib
import hmac
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRET = os.environ.get("DEPLOY_SECRET", "").encode()
DEPLOY_SCRIPT = os.path.join(os.path.dirname(__file__), "ct104-deploy.sh")
PORT = 9000


class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silence access log

    def do_POST(self):
        if self.path != "/deploy":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # Verify Forgejo HMAC signature if secret is set
        if SECRET:
            sig = self.headers.get("X-Gitea-Signature", "")
            expected = hmac.new(SECRET, body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                print("[webhook] Invalid signature — rejected")
                self.send_response(403)
                self.end_headers()
                return

        try:
            payload = json.loads(body)
            ref = payload.get("ref", "")
        except Exception:
            ref = ""

        # Only deploy on push to master
        if ref not in ("refs/heads/master", "refs/heads/main", ""):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"skipped (not master)")
            return

        print(f"[webhook] Push to {ref} — triggering deploy")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"deploying")

        # Run deploy in background so webhook returns immediately
        subprocess.Popen(
            ["bash", DEPLOY_SCRIPT],
            stdout=open("/tmp/jarvis-deploy.log", "a"),
            stderr=subprocess.STDOUT,
        )


if __name__ == "__main__":
    print(f"[webhook] Listening on port {PORT}")
    print(f"[webhook] Deploy script: {DEPLOY_SCRIPT}")
    print(f"[webhook] Secret: {'set' if SECRET else 'NOT SET (open)'}")
    HTTPServer(("0.0.0.0", PORT), WebhookHandler).serve_forever()
