#!/usr/bin/env bash
set -Eeuo pipefail

REPO="rosc-bot/emby-multi-proxy"
BRANCH="${EMBY_PROXY_BRANCH:-main}"
PUBLIC_PORT="${EMBY_PROXY_PORT:-35087}"
PANEL_PORT="${EMBY_PANEL_PORT:-35187}"
APP_DIR="/opt/emby-multi-proxy"
ETC_DIR="/etc/emby-multi-proxy"
DATA_DIR="/var/lib/emby-multi-proxy"
CACHE_DIR="/var/cache/nginx/emby-multi-proxy"
SERVICE_USER="emby-panel"
RAW="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

log(){ printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die(){ printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请使用 root 运行安装脚本"
command -v apt-get >/dev/null 2>&1 || die "当前版本仅支持 Debian / Ubuntu（apt）"
if ! [[ "$PUBLIC_PORT" =~ ^[0-9]+$ ]] || (( PUBLIC_PORT < 1 || PUBLIC_PORT > 65535 )); then
  die "EMBY_PROXY_PORT 无效"
fi
if ! [[ "$PANEL_PORT" =~ ^[0-9]+$ ]] || (( PANEL_PORT < 1 || PANEL_PORT > 65535 )); then
  die "EMBY_PANEL_PORT 无效"
fi
[[ "$PUBLIC_PORT" != "$PANEL_PORT" ]] || die "公网端口与面板内部端口不能相同"

export DEBIAN_FRONTEND=noninteractive
log "安装系统依赖"
apt-get update -y
apt-get install -y --no-install-recommends nginx python3 sudo curl ca-certificates openssl

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  log "创建受限服务用户 ${SERVICE_USER}"
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

install -d -o root -g root -m 0755 "$APP_DIR" "$ETC_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_DIR"
install -d -o www-data -g www-data -m 0755 "$CACHE_DIR"

# Best-effort migration from the original /opt/emby-panel layout.
if [[ ! -s "$DATA_DIR/sites.json" ]]; then
  for legacy in /opt/emby-panel/sites.json /opt/emby-panel/config.json; do
    if [[ -s "$legacy" ]] && python3 - <<PY_MIGRATE
import json
from pathlib import Path
p=Path("$legacy")
data=json.loads(p.read_text(encoding="utf-8"))
assert isinstance(data, list) or (isinstance(data, dict) and isinstance(data.get("sites"), list))
PY_MIGRATE
    then
      log "发现旧版配置，迁移: $legacy"
      cp -a "$legacy" "$DATA_DIR/sites.json"
      chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR/sites.json"
      chmod 0600 "$DATA_DIR/sites.json"
      break
    fi
  done
fi

log "下载最新面板程序"
tmp_panel="$(mktemp)"
tmp_core="$(mktemp)"
tmp_html="$(mktemp)"
trap 'rm -f "$tmp_panel" "$tmp_core" "$tmp_html"' EXIT
curl -fL --retry 3 --connect-timeout 10 "${RAW}/panel.py" -o "$tmp_panel"
curl -fL --retry 3 --connect-timeout 10 "${RAW}/core.py" -o "$tmp_core"
curl -fL --retry 3 --connect-timeout 10 "${RAW}/panel.html" -o "$tmp_html"
PYTHONPATH="$(dirname "$tmp_core")" python3 -m py_compile "$tmp_panel" "$tmp_core" || die "下载的 Python 文件语法校验失败"
install -o root -g root -m 0755 "$tmp_panel" "$APP_DIR/panel.py"
install -o root -g root -m 0644 "$tmp_core" "$APP_DIR/core.py"
install -o root -g root -m 0644 "$tmp_html" "$APP_DIR/panel.html"

if [[ ! -s "$ETC_DIR/admin.token" ]]; then
  log "生成管理 Token"
  umask 077
  openssl rand -hex 32 > "$ETC_DIR/admin.token"
fi
chown "$SERVICE_USER:$SERVICE_USER" "$ETC_DIR/admin.token"
chmod 0600 "$ETC_DIR/admin.token"

if [[ ! -e "$ETC_DIR/sites.conf" ]]; then
  cat > "$ETC_DIR/sites.conf" <<'EOF'
# Generated file. It will be replaced by the panel after the first save.
location / {
    default_type application/json;
    return 404 '{"error":"no root proxy configured"}';
}
EOF
fi
chown "$SERVICE_USER:www-data" "$ETC_DIR/sites.conf"
chmod 0640 "$ETC_DIR/sites.conf"

cat > /etc/nginx/conf.d/emby-multi-proxy.conf <<EOF
# Managed by emby-multi-proxy install.sh
map \$http_upgrade \$connection_upgrade {
    default upgrade;
    ''      close;
}

proxy_cache_path ${CACHE_DIR} levels=1:2 keys_zone=emby_static:10m max_size=300m inactive=7d use_temp_path=off;

server {
    listen ${PUBLIC_PORT} default_server;
    listen [::]:${PUBLIC_PORT} default_server;
    server_name _;

    server_tokens off;
    proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
    proxy_ssl_verify_depth 4;
    client_max_body_size 0;
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 75s;

    # Panel runs only on loopback. Nginx is the only public entry point.
    location = /panel { return 308 /panel/; }
    location ^~ /panel/ {
        proxy_pass http://127.0.0.1:${PANEL_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location ^~ /api/ {
        proxy_pass http://127.0.0.1:${PANEL_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location = /healthz {
        proxy_pass http://127.0.0.1:${PANEL_PORT};
        access_log off;
    }

    # Generated site locations.
    include ${ETC_DIR}/sites.conf;
}
EOF

cat > /etc/sudoers.d/emby-multi-proxy <<'EOF'
# emby-multi-proxy may only validate and gracefully reload Nginx.
emby-panel ALL=(root) NOPASSWD: /usr/sbin/nginx -t, /usr/bin/systemctl reload nginx
EOF
chmod 0440 /etc/sudoers.d/emby-multi-proxy
visudo -cf /etc/sudoers.d/emby-multi-proxy >/dev/null || die "sudoers 配置校验失败"

cat > /etc/systemd/system/emby-multi-proxy.service <<EOF
[Unit]
Description=Emby Multi Proxy management panel
After=network-online.target nginx.service
Wants=network-online.target
Requires=nginx.service

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${APP_DIR}
ExecStart=/usr/bin/python3 ${APP_DIR}/panel.py --host 127.0.0.1 --port ${PANEL_PORT} --data-dir ${DATA_DIR} --nginx-include ${ETC_DIR}/sites.conf --token-file ${ETC_DIR}/admin.token
Restart=on-failure
RestartSec=2
TimeoutStopSec=10
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${DATA_DIR} ${ETC_DIR}/sites.conf
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
UMask=0077

[Install]
WantedBy=multi-user.target
EOF

log "校验 Nginx 配置"
nginx -t
systemctl daemon-reload
systemctl enable --now nginx
systemctl enable --now emby-multi-proxy.service
systemctl reload nginx

TOKEN="$(cat "$ETC_DIR/admin.token")"
SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
SERVER_IP="${SERVER_IP:-YOUR_SERVER_IP}"

printf '\n\033[1;36m安装/更新完成\033[0m\n'
printf '面板地址: http://%s:%s/panel/\n' "$SERVER_IP" "$PUBLIC_PORT"
printf '管理 Token: %s\n' "$TOKEN"
printf 'Token 文件: %s/admin.token\n' "$ETC_DIR"
printf '\n常用命令:\n'
printf '  systemctl status emby-multi-proxy --no-pager\n'
printf '  journalctl -u emby-multi-proxy -f\n'
printf '  nginx -t\n'
printf '\n建议：用防火墙限制 %s 端口来源，或再套 HTTPS 反代。\n' "$PUBLIC_PORT"
