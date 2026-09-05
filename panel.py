#!/usr/bin/env python3
"""Emby Multi Proxy management panel."""

from __future__ import annotations

import argparse
import hmac
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from core import ConfigError, Site, Store, nginx_test, render_nginx

VERSION = "2.0.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 35187
DEFAULT_DATA_DIR = Path("/var/lib/emby-multi-proxy")
DEFAULT_NGINX_INCLUDE = Path("/etc/emby-multi-proxy/sites.conf")
DEFAULT_TOKEN_FILE = Path("/etc/emby-multi-proxy/admin.token")
MAX_BODY = 1024 * 1024

def _read_token(path: Path) -> str:
    env_token = os.environ.get("EMBY_PANEL_TOKEN", "").strip()
    if env_token:
        return env_token
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise SystemExit(f"Admin token not found: {path}") from exc
    if len(token) < 20:
        raise SystemExit("Admin token is too short; use at least 20 characters")
    return token


PANEL_HTML = Path(__file__).with_name("panel.html").read_text(encoding="utf-8").replace("__VERSION__", VERSION)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "EmbyMultiProxy/" + VERSION
    sys_version = ""

    @property
    def app(self) -> "AppServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args: Any) -> None:
        message = fmt % args
        print(f"{self.address_string()} - {message}", flush=True)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/", "/panel", "/panel/"}:
            self._html(PANEL_HTML)
            return
        if path == "/healthz":
            self._json(HTTPStatus.OK, {"ok": True, "version": VERSION})
            return
        if not path.startswith("/api/"):
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._authorized():
            return

        try:
            if path == "/api/sites":
                sites = self.app.store.load()
                ok, detail = nginx_test()
                self._json(HTTPStatus.OK, {"sites": [asdict(s) for s in sites], "nginx_ok": ok, "nginx_detail": detail})
            elif path == "/api/export":
                sites = self.app.store.load()
                self._json(HTTPStatus.OK, {"version": 1, "exported_at": datetime.now(timezone.utc).isoformat(), "sites": [asdict(s) for s in sites]})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except (ConfigError, json.JSONDecodeError, OSError) as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/") or not self._authorized():
            return
        try:
            if path == "/api/reload":
                ok, detail = self.app.store.regenerate()
                if ok:
                    self._json(HTTPStatus.OK, {"ok": True, "message": "Nginx 配置校验通过并已平滑重载", "detail": detail})
                else:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": detail})
                return
            if path == "/api/import":
                body = self._body_json()
                raw_sites = body.get("sites") if isinstance(body, dict) else None
                if not isinstance(raw_sites, list):
                    raise ConfigError("sites 必须是数组")
                sites = [Site.from_dict(item) for item in raw_sites]
                self.app.store.save(sites)
                self._json(HTTPStatus.OK, {"ok": True, "count": len(sites)})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except (ConfigError, json.JSONDecodeError, OSError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_PUT(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/sites/") or not self._authorized():
            if path.startswith("/api/"):
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            target_id = path.removeprefix("/api/sites/")
            body = self._body_json()
            site = Site.from_dict(body)
            sites = self.app.store.load()
            idx = next((i for i, s in enumerate(sites) if s.id == target_id), None)
            if idx is None:
                if target_id != site.id:
                    raise ConfigError("URL 中的站点 id 与请求数据不一致")
                sites.append(site)
            else:
                if site.id != target_id:
                    raise ConfigError("编辑时不允许修改站点 id")
                sites[idx] = site
            self.app.store.save(sites)
            self._json(HTTPStatus.OK, {"ok": True, "site": asdict(site)})
        except (ConfigError, json.JSONDecodeError, OSError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_DELETE(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/sites/") or not self._authorized():
            if path.startswith("/api/"):
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            target_id = path.removeprefix("/api/sites/")
            sites = self.app.store.load()
            new_sites = [s for s in sites if s.id != target_id]
            if len(new_sites) == len(sites):
                self._json(HTTPStatus.NOT_FOUND, {"error": "站点不存在"})
                return
            self.app.store.save(new_sites)
            self._json(HTTPStatus.OK, {"ok": True})
        except (ConfigError, json.JSONDecodeError, OSError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        supplied = auth[7:] if auth.startswith("Bearer ") else ""
        if supplied and hmac.compare_digest(supplied, self.app.token):
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Bearer realm="emby-multi-proxy"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        body = b'{"error":"unauthorized"}'
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def _body_json(self) -> Any:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > MAX_BODY:
            raise ConfigError("请求体为空或超过 1 MiB")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, status: int, data: Any) -> None:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _html(self, data: str) -> None:
        payload = data.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], store: Store, token: str):
        super().__init__(address, handler)
        self.store = store
        self.token = token


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Emby Multi Proxy management panel")
    p.add_argument("--host", default=os.environ.get("EMBY_PANEL_HOST", DEFAULT_HOST))
    p.add_argument("--port", type=int, default=int(os.environ.get("EMBY_PANEL_PORT", DEFAULT_PORT)))
    p.add_argument("--data-dir", type=Path, default=Path(os.environ.get("EMBY_PANEL_DATA_DIR", str(DEFAULT_DATA_DIR))))
    p.add_argument("--nginx-include", type=Path, default=Path(os.environ.get("EMBY_NGINX_INCLUDE", str(DEFAULT_NGINX_INCLUDE))))
    p.add_argument("--token-file", type=Path, default=Path(os.environ.get("EMBY_PANEL_TOKEN_FILE", str(DEFAULT_TOKEN_FILE))))
    p.add_argument("--check", action="store_true", help="validate sites.json and print generated nginx config")
    p.add_argument("--version", action="version", version=VERSION)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    store = Store(args.data_dir, args.nginx_include)
    if args.check:
        print(render_nginx(store.load()))
        return 0
    token = _read_token(args.token_file)
    server = AppServer((args.host, args.port), AppHandler, store, token)
    print(f"Emby Multi Proxy v{VERSION} listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
