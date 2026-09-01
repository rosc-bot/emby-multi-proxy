#!/bin/bash
# Emby Multi-Site Reverse Proxy Panel - 一键安装脚本
set -e

echo "=== 1/4 安装系统依赖 (Nginx + Python3) ==="
apt update -y
apt install -y nginx python3 curl

echo "=== 2/4 部署 Emby 管理面板 ==="
mkdir -p /opt/emby-panel /var/cache/nginx/emby
chown -R www-data:www-data /var/cache/nginx/emby

curl -fsSL https://raw.githubusercontent.com/rosc-bot/emby-multi-proxy/main/panel.py -o /opt/emby-panel/panel.py

cat <<'EOF_SERVICE' > /etc/systemd/system/emby-panel.service
[Unit]
Description=Emby Reverse Proxy Web Panel
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/emby-panel
ExecStart=/usr/bin/python3 /opt/emby-panel/panel.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF_SERVICE

systemctl daemon-reload
systemctl enable --now emby-panel.service

echo "=== 3/4 初始化 Nginx 反代配置 ==="
cd /opt/emby-panel
python3 -c "import panel; panel.apply_nginx_config(panel.load_sites())" || true
nginx -t && systemctl reload nginx || systemctl restart nginx

echo "=== 4/4 部署完成 ==="
SERVER_IP=$(curl -s4 ifconfig.me || curl -s4 icanhazip.com || echo "YOUR_SERVER_IP")
echo "--------------------------------------------------------"
echo "🎉 Emby 多站点反代面板已成功部署！"
echo "👉 管理后台地址: http://${SERVER_IP}:35087/panel/"
echo "👉 默认反代端口: 35087"
echo "--------------------------------------------------------"
