#!/usr/bin/env python3
"""Validation, storage, and Nginx rendering for emby-multi-proxy."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SITE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
PATH_RE = re.compile(r"^[a-zA-Z0-9._~-]{1,64}$")
SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")

class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Site:
    id: str
    name: str
    upstream: str
    path: str = ""
    enabled: bool = True
    host_header: str = ""
    insecure_tls: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Site":
        if not isinstance(raw, dict):
            raise ConfigError("站点数据必须是对象")

        name = str(raw.get("name", raw.get("title", ""))).strip()
        upstream = str(raw.get("upstream", raw.get("url", raw.get("target", raw.get("origin", ""))))).strip()
        path = str(raw.get("path", raw.get("prefix", raw.get("base_path", "")))).strip().strip("/")
        site_id = str(raw.get("id", "")).strip() or _slugify(path or name)
        host_header = str(raw.get("host_header", raw.get("host", ""))).strip()
        enabled = bool(raw.get("enabled", True))
        insecure_tls = bool(raw.get("insecure_tls", False))

        if not SITE_ID_RE.fullmatch(site_id):
            raise ConfigError("id 只能包含字母、数字、下划线和连字符，长度 1-64")
        if not name or len(name) > 80:
            raise ConfigError("站点名称长度必须为 1-80")
        if path and not PATH_RE.fullmatch(path):
            raise ConfigError("路径前缀只能包含字母、数字、点、下划线、~ 和连字符")

        parts = urlsplit(upstream)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ConfigError("源站必须是完整的 http:// 或 https:// 地址")
        if parts.username or parts.password:
            raise ConfigError("源站 URL 不允许包含用户名或密码")
        if parts.query or parts.fragment:
            raise ConfigError("源站 URL 不允许包含 query 或 fragment")
        try:
            _ = parts.port
        except ValueError as exc:
            raise ConfigError("源站端口无效") from exc
        if any(c in upstream for c in "\r\n\t {};"):
            raise ConfigError("源站地址包含非法字符")
        if host_header and (len(host_header) > 253 or not _valid_host_header(host_header)):
            raise ConfigError("Host Header 格式无效")

        normalized_upstream = upstream.rstrip("/")
        return cls(
            id=site_id,
            name=name,
            upstream=normalized_upstream,
            path=path,
            enabled=enabled,
            host_header=host_header,
            insecure_tls=insecure_tls,
        )


def _slugify(value: str) -> str:
    value = SLUG_RE.sub("-", value.strip()).strip("-_")[:64]
    return value or "site"


def _valid_host_header(value: str) -> bool:
    if any(c in value for c in "\r\n\t /\\{};$"):
        return False
    host = value
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return False
        host = value[: end + 1]
        rest = value[end + 1 :]
        return not rest or (rest.startswith(":") and rest[1:].isdigit())
    if ":" in value:
        host, port = value.rsplit(":", 1)
        if not port.isdigit() or not (1 <= int(port) <= 65535):
            return False
    return bool(host) and all(ch.isalnum() or ch in ".-_" for ch in host)


def _nginx_quote(value: str) -> str:
    if any(c in value for c in "\r\n\0"):
        raise ConfigError("配置值包含换行或 NUL")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _upstream_host(site: Site) -> str:
    parts = urlsplit(site.upstream)
    if site.host_header:
        return site.host_header
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parts.port:
        default_port = 443 if parts.scheme == "https" else 80
        if parts.port != default_port:
            host = f"{host}:{parts.port}"
    return host


def _proxy_common(site: Site, indent: str = "        ", buffering: str = "off") -> str:
    host = _upstream_host(site)
    tls_name = urlsplit(site.upstream).hostname or host.split(":", 1)[0]
    tls_verify = "off" if site.insecure_tls else "on"
    lines = [
        "proxy_http_version 1.1;",
        f"proxy_set_header Host {_nginx_quote(host)};",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
        "proxy_set_header X-Forwarded-Host $host;",
        "proxy_set_header Upgrade $http_upgrade;",
        "proxy_set_header Connection $connection_upgrade;",
        "proxy_ssl_server_name on;",
        f"proxy_ssl_name {_nginx_quote(tls_name)};",
        f"proxy_ssl_verify {tls_verify};",
        "proxy_connect_timeout 10s;",
        "proxy_send_timeout 3600s;",
        "proxy_read_timeout 3600s;",
        "proxy_request_buffering off;",
        f"proxy_buffering {buffering};",
        "proxy_force_ranges on;",
        "proxy_redirect off;",
    ]
    return "\n".join(indent + line for line in lines)


def render_nginx(sites: list[Site]) -> str:
    enabled = [s for s in sites if s.enabled]
    # Prefix sites are emitted first so their static-cache regex locations win
    # before the optional root site's broader static regex.
    enabled.sort(key=lambda s: (not bool(s.path), s.path, s.id))
    roots = [s for s in enabled if not s.path]
    if len(roots) > 1:
        raise ConfigError("最多只能启用一个根站点（路径前缀留空）")

    seen_paths: set[str] = set()
    blocks: list[str] = [
        "# Generated by emby-multi-proxy. DO NOT EDIT MANUALLY.",
        f"# generated_at: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]

    for site in enabled:
        if site.path:
            if site.path in seen_paths:
                raise ConfigError(f"路径前缀重复: {site.path}")
            seen_paths.add(site.path)

        upstream = site.upstream + "/"
        static_tail = r"(?:Items/.*/Images/.*|.*\.(?:jpg|jpeg|png|webp|gif|svg|ico|srt|ass|vtt))$"
        cache_lines = [
            "    proxy_cache emby_static;",
            '    proxy_cache_key "$scheme|$proxy_host|$request_uri|$http_authorization|$http_x_emby_token";',
            "    proxy_cache_valid 200 30m;",
            "    proxy_cache_valid 404 1m;",
            "    proxy_cache_lock on;",
            "    add_header X-Proxy-Cache $upstream_cache_status always;",
        ]
        if site.path:
            prefix = f"/{site.path}/"
            escaped_path = re.escape(site.path)
            blocks.extend(
                [
                    f"# {site.name} ({site.id})",
                    f"location = /{site.path} {{ return 308 /{site.path}/; }}",
                    f"location ~* ^/{escaped_path}/{static_tail} {{",
                    f"    rewrite ^/{escaped_path}/(.*)$ /$1 break;",
                    f"    proxy_pass {_nginx_quote(site.upstream)};",
                    f"    proxy_set_header X-Forwarded-Prefix {_nginx_quote('/' + site.path)};",
                    _proxy_common(site, "    ", "on"),
                    *cache_lines,
                    "}",
                    f"location {prefix} {{",
                    f"    proxy_pass {_nginx_quote(upstream)};",
                    f"    proxy_set_header X-Forwarded-Prefix {_nginx_quote('/' + site.path)};",
                    _proxy_common(site, "    ", "off"),
                    "}",
                    "",
                ]
            )
        else:
            blocks.extend(
                [
                    f"# {site.name} ({site.id}) - root fallback",
                    f"location ~* ^/{static_tail} {{",
                    f"    proxy_pass {_nginx_quote(site.upstream)};",
                    _proxy_common(site, "    ", "on"),
                    *cache_lines,
                    "}",
                    "location / {",
                    f"    proxy_pass {_nginx_quote(upstream)};",
                    _proxy_common(site, "    ", "off"),
                    "}",
                    "",
                ]
            )

    if not roots:
        blocks.extend(
            [
                "location / {",
                "    default_type application/json;",
                '    return 404 \'{"error":"no root proxy configured"}\';',
                "}",
                "",
            ]
        )

    return "\n".join(blocks)


class Store:
    def __init__(self, data_dir: Path, nginx_include: Path):
        self.data_dir = data_dir
        self.sites_file = data_dir / "sites.json"
        self.backup_dir = data_dir / "backups"
        self.nginx_include = nginx_include
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[Site]:
        if not self.sites_file.exists():
            return []
        raw = json.loads(self.sites_file.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("sites"), list):
            raw = raw["sites"]
        if not isinstance(raw, list):
            raise ConfigError("sites.json 顶层必须是数组，或包含 sites 数组的对象")
        sites = [Site.from_dict(item) for item in raw]
        _validate_unique(sites)
        return sites

    def save(self, sites: list[Site]) -> None:
        _validate_unique(sites)
        nginx_text = render_nginx(sites)
        sites_text = json.dumps([asdict(s) for s in sites], ensure_ascii=False, indent=2) + "\n"

        old_sites = self.sites_file.read_bytes() if self.sites_file.exists() else None
        old_nginx = self.nginx_include.read_bytes() if self.nginx_include.exists() else None
        self._backup_existing(old_sites, old_nginx)

        _atomic_write(self.sites_file, sites_text.encode(), 0o600)
        _atomic_write(self.nginx_include, nginx_text.encode(), 0o640)

        ok, detail = nginx_test()
        if not ok:
            self._restore(old_sites, old_nginx)
            raise ConfigError(f"Nginx 配置校验失败，已自动回滚：{detail}")

        ok, detail = nginx_reload()
        if not ok:
            self._restore(old_sites, old_nginx)
            nginx_test()
            nginx_reload()
            raise ConfigError(f"Nginx 重载失败，已自动回滚：{detail}")

        self._prune_backups(10)

    def regenerate(self) -> tuple[bool, str]:
        sites = self.load()
        nginx_text = render_nginx(sites)
        old_nginx = self.nginx_include.read_bytes() if self.nginx_include.exists() else None
        _atomic_write(self.nginx_include, nginx_text.encode(), 0o640)
        ok, detail = nginx_test()
        if not ok:
            if old_nginx is None:
                self.nginx_include.unlink(missing_ok=True)
            else:
                _atomic_write(self.nginx_include, old_nginx, 0o640)
            return False, f"校验失败，已回滚：{detail}"
        ok, detail = nginx_reload()
        return ok, detail

    def _backup_existing(self, old_sites: bytes | None, old_nginx: bytes | None) -> None:
        if old_sites is None and old_nginx is None:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.backup_dir / stamp
        suffix = 0
        while target.exists():
            suffix += 1
            target = self.backup_dir / f"{stamp}-{suffix}"
        target.mkdir(mode=0o700)
        if old_sites is not None:
            (target / "sites.json").write_bytes(old_sites)
        if old_nginx is not None:
            (target / "sites.conf").write_bytes(old_nginx)

    def _restore(self, old_sites: bytes | None, old_nginx: bytes | None) -> None:
        if old_sites is None:
            self.sites_file.unlink(missing_ok=True)
        else:
            _atomic_write(self.sites_file, old_sites, 0o600)
        if old_nginx is None:
            self.nginx_include.unlink(missing_ok=True)
        else:
            _atomic_write(self.nginx_include, old_nginx, 0o640)

    def _prune_backups(self, keep: int) -> None:
        backups = sorted((p for p in self.backup_dir.iterdir() if p.is_dir()), reverse=True)
        for path in backups[keep:]:
            shutil.rmtree(path, ignore_errors=True)


def _validate_unique(sites: list[Site]) -> None:
    ids: set[str] = set()
    paths: set[str] = set()
    roots = 0
    for site in sites:
        if site.id in ids:
            raise ConfigError(f"站点 id 重复: {site.id}")
        ids.add(site.id)
        if site.enabled:
            if not site.path:
                roots += 1
            elif site.path in paths:
                raise ConfigError(f"路径前缀重复: {site.path}")
            paths.add(site.path)
    if roots > 1:
        raise ConfigError("最多只能启用一个根站点")


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _run(cmd: list[str], timeout: int = 15) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        )
        detail = proc.stdout.strip()[-4000:]
        return proc.returncode == 0, detail or ("ok" if proc.returncode == 0 else f"exit={proc.returncode}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def nginx_test() -> tuple[bool, str]:
    return _run(["sudo", "-n", "/usr/sbin/nginx", "-t"])


def nginx_reload() -> tuple[bool, str]:
    return _run(["sudo", "-n", "/usr/bin/systemctl", "reload", "nginx"])
