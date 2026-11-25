# -*- coding: utf-8 -*-
"""零依赖 HTTP 层（标准库 http.server）：把核销服务暴露成接口，用于验 AC3 的 422。

路由：
  GET  /api/v1/health
  GET  /api/v1/installments/{iid}
  GET  /api/v1/flows/{fid}
  POST /api/v1/flows/{fid}/receipts     body {"allocations":[{...}], "operator":"...", "mode":"manual"}
  POST /api/v1/match/run                body {"auto_confirm_strong": false}

状态码：201 成功核销 / 200 查询与幂等重放 / 404 资源不存在 / 422 业务校验失败 / 400 请求体非法。
可以用 `python -m pkg.server --port 8000` 单独起服务（真实 FastAPI 版接口契约见 docs/api_spec.md）。
"""

import os as _os
import sys as _sys
# 允许两种入口：`python -m pkg.server`（从包根起）与 `python pkg/server.py`（直接跑）。
# 同目录的 engine/receipt 是平铺模块，需要把本目录加进 sys.path 才能 import。
_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import engine as E
from receipt import ReceiptError, ReceiptService

RE_INST = re.compile(r"^/api/v1/installments/([A-Za-z0-9_-]+)$")
RE_FLOW = re.compile(r"^/api/v1/flows/([A-Za-z0-9_-]+)$")
RE_RECV = re.compile(r"^/api/v1/flows/([A-Za-z0-9_-]+)/receipts$")


def make_handler(svc):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return {}
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise ReceiptError(400, "请求体不是合法 JSON：%s" % e)

        def do_GET(self):
            if self.path == "/api/v1/health":
                return self._json(200, {"ok": True})
            m = RE_INST.match(self.path)
            if m:
                try:
                    return self._json(200, svc.inst_view(m.group(1)))
                except ReceiptError as e:
                    return self._json(e.code, {"detail": e.detail})
            m = RE_FLOW.match(self.path)
            if m:
                try:
                    return self._json(200, svc.flow_view(m.group(1)))
                except ReceiptError as e:
                    return self._json(e.code, {"detail": e.detail})
            return self._json(404, {"detail": "no route %s" % self.path})

        def do_POST(self):
            try:
                m = RE_RECV.match(self.path)
                if m:
                    b = self._body()
                    out = svc.apply(m.group(1), b.get("allocations"),
                                    b.get("operator") or "", b.get("mode") or "manual",
                                    b.get("idempotency_key"))
                    return self._json(200 if out.get("idempotent_replay") else 201, out)
                if self.path == "/api/v1/match/run":
                    b = self._body()
                    ac = bool(b.get("auto_confirm_strong"))
                    res = [E.match_flow(f, svc.installments, ac) for f in svc.flows.values()]
                    return self._json(200, {"count": len(res),
                                            "results": [{"flow_id": r["flow_id"],
                                                         "rule": r["rule"],
                                                         "target": r["target_installment"],
                                                         "auto_confirmed": r["auto_confirmed"]}
                                                        for r in res]})
                return self._json(404, {"detail": "no route %s" % self.path})
            except ReceiptError as e:
                return self._json(e.code, {"detail": e.detail})

    return H


def serve(port=0, flows=None):
    """启动服务，返回 (server, base_url, thread)。port=0 由系统分配空闲端口。"""
    svc = ReceiptService(flows=flows if flows is not None else E.FLOWS)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(svc))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1], t, svc


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000
    srv, base, t, _ = serve(port)
    print("listening on %s" % base)
    try:
        t.join()
    except KeyboardInterrupt:
        srv.shutdown()
