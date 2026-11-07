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

    def _stream(self, body):
        """Like llama-server with "stream": true. Cites the first card it was
        shown, invents one, adds a beyond-the-board part - and notices when the
        client hangs up."""
        import re

        parts = []
        for m in body["messages"]:
            if m["role"] != "user":
                continue
            content = m["content"]
            parts += content if isinstance(content, list) else [{"type": "text", "text": content}]
        texts = " ".join(p.get("text", "") for p in parts if isinstance(p, dict))
        images = sum(1 for p in parts if isinstance(p, dict) and p.get("type") == "image_url")
        roles = ",".join(m["role"] for m in body["messages"])
        print(f"asked: images={images} messages={len(body['messages'])} roles={roles}", flush=True)
        ids = re.findall(r"\[([0-9A-Za-z]{26})\]", texts)
        first = ids[0] if ids else "NONE"
        pieces = []
        if body.get("return_progress"):
            pieces += [("prompt_progress", {"total": 400, "cache": 0, "processed": 200}),
                       ("prompt_progress", {"total": 400, "cache": 0, "processed": 400})]
        if (body.get("chat_template_kwargs") or {}).get("enable_thinking"):
            pieces.append(("reasoning_content", "Let me look at the cards."))
        pieces += [("content", "Your card says the corner mount is best "), ("content", f"[[{first}]]. "),
                   ("content", f"Also this [[{'Z' * 26}]].\n"),
                   ("content", "Beyond your board:\nConsider wind load."),
                   ("content", "\nNEXT: Where else"), ("content", " could it go?")]
        slow = "SLOWLY" in texts
        if slow:
            pieces += [("content", " more")] * 400
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for key, text in pieces:
                chunk = ({"choices": [], "prompt_progress": text} if key == "prompt_progress"
                         else {"choices": [{"delta": {key: text}}]})
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
                if slow:
                    time.sleep(0.02)
            final = {"choices": [], "timings": {"predicted_n": 20, "predicted_per_second": 42.0}}
            self.wfile.write(f"data: {json.dumps(final)}\n\ndata: [DONE]\n\n".encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            print("client went away - stopped generating", flush=True)

    def do_POST(self):
        # read the request before refusing it, as llama-server does - answering
        # early and closing makes Windows abort the client mid-send
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.headers.get("Authorization") != f"Bearer {args.api_key}":
            return self._send(401, {"error": {"code": 401, "message": "Invalid API Key"}})
        if self.path == "/v1/embeddings":
            # like llama-server --embedding; "SLOWLY" in a text makes it take a
            # moment, so a test can catch a request in flight
            texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
            if any("SLOWLY" in t for t in texts):
                time.sleep(0.8)
            return self._send(200, {"data": [{"index": i, "embedding": [1.0, float(len(t)), 0.0, 0.0]}
                                             for i, t in enumerate(texts)]})
        if self.path == "/v1/rerank":
            # like llama-server --rerank: best first, each with its index in
            # the request; "relevance" here is the share of the query's words
            words = set(body["query"].lower().split())
            results = [{"index": i, "relevance_score": len(words & set(d.lower().split())) / len(words)}
                       for i, d in enumerate(body["documents"])]
            results.sort(key=lambda r: -r["relevance_score"])
            return self._send(200, {"results": results[:body.get("top_n", len(results))]})
        if body.get("stream"):
            return self._stream(body)
        last = body["messages"][-1]["content"]
        if not isinstance(last, list) and len(body["messages"]) > 1:
            last = body["messages"][1]["content"]     # ask's order: the cards come second
        text = ("The word is MINDBOARD, beside a red circle." if isinstance(last, list)
                else "A mind map lays out ideas and how they connect.")
        self._send(200, {
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "timings": {"prompt_n": 12, "prompt_per_second": 120.0,
                        "predicted_n": 9, "predicted_per_second": 42.0},
            "echo": {"chat_template_kwargs": body.get("chat_template_kwargs")},
        })


ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
