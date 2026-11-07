"""Stands in for llama-server in tests: the flags that matter, the endpoints
Mindboard uses, and no model. It loads for a moment (503), then answers.

Not a test module - pytest only collects test_*.py.
"""

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

p = argparse.ArgumentParser()
p.add_argument("--host", required=True)
p.add_argument("--port", type=int, required=True)
p.add_argument("--api-key", required=True)
p.add_argument("--fail", action="store_true", help="die while loading, like a model too big")
args, _rest = p.parse_known_args()

if args.fail:
    print("llama_model_load: error: cudaMalloc failed: out of memory", flush=True)
    sys.exit(1)

print("load_tensors: offloaded 65/65 layers to GPU", flush=True)
ready_at = time.time() + 0.4


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            if time.time() < ready_at:
                return self._send(503, {"error": {"code": 503, "message": "Loading model"}})
            return self._send(200, {"status": "ok"})
        self._send(404, {})

    def do_POST(self):
        if self.headers.get("Authorization") != f"Bearer {args.api_key}":
            return self._send(401, {"error": {"code": 401, "message": "Invalid API Key"}})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        last = body["messages"][-1]["content"]
        text = ("The word is MINDBOARD, beside a red circle." if isinstance(last, list)
                else "A mind map lays out ideas and how they connect.")
        self._send(200, {
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "timings": {"prompt_n": 12, "prompt_per_second": 120.0,
                        "predicted_n": 9, "predicted_per_second": 42.0},
            "echo": {"chat_template_kwargs": body.get("chat_template_kwargs")},
        })


ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
