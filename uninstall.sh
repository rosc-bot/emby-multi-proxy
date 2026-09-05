#!/usr/bin/env bash
set -Eeuo pipefail

PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1
[[ ${EUID} -eq 0 ]] || { echo "请使用 root 运行" >&2; exit 1; }

systemctl disable --now emby-multi-proxy.service 2>/dev/null || true
rm -f /etc/systemd/system/emby-multi-proxy.service
rm -f /etc/sudoers.d/emby-multi-proxy
rm -f /etc/nginx/conf.d/emby-multi-proxy.conf
rm -rf /opt/emby-multi-proxy
systemctl daemon-reload
if nginx -t >/dev/null 2>&1; then
  systemctl reload nginx || true
fi

if id emby-panel >/dev/null 2>&1; then
  userdel emby-panel 2>/dev/null || true
fi

if (( PURGE )); then
  rm -rf /etc/emby-multi-proxy /var/lib/emby-multi-proxy /var/cache/nginx/emby-multi-proxy
  echo "已卸载并清除配置/数据。"
else
  echo "已卸载程序，配置仍保留在 /etc/emby-multi-proxy 和 /var/lib/emby-multi-proxy。"
  echo "如需彻底清除：bash uninstall.sh --purge"
fi
