# 🎬 Emby Multi-Site Reverse Proxy Panel (Emby 多站点反向代理管理面板)

> 🚀 **专为 NAT VPS、低配 Linux 服务器量身定制的超轻量 Emby / Jellyfin 多站点反代系统**。
> 内存仅占 **15MB**，单端口支持无限多站点子路径分流，内置 Web 可视化管理面板、一键平滑重载 Nginx、流媒体切片极速直通优化。

---

## ✨ 核心特性

- 🖥️ **可视化 Web 管理面板**：支持通过浏览器增删改查 Emby 站点，无需手动编辑 Nginx 配置文件。
- ⚡ **单端口子路径分流 (Path Proxy)**：支持在单个公网端口（如 `35087`）下通过 `/emby1/`、`/emby2/` 等子路径反代无限多个不同的 Emby / Jellyfin 服务器。
- 🔄 **自动平滑重载 (Zero-Downtime Reload)**：每次在面板保存站点，后台自动执行 `nginx -t` 校验语法并执行 `systemctl reload nginx`，连接不中断。
- 🚀 **流媒体高码率流水线加速**：
  - 针对 HLS、TS、MP4、MKV 视频流媒体数据流配置了 `16MB` 内存级高速流水线缓冲；
  - 开启 `sendfile`、`tcp_nodelay`、`tcp_nopush` 与内核 BBR 拥塞控制；
  - 消除磁盘 IO 瓶颈，小切片秒级直推客户端。
- 🖼️ **边缘静态资源高速缓存**：内置 300MB 磁盘/内存索引缓存池，海报封面、Logo、字幕自动缓存，秒开防刷。
- 🛡️ **SNI 伪装与 Header 补全**：自动透传 `X-Forwarded-Proto: https`、SNI 域名伪装及 WebSocket 长连接，完美突破 Cloudflare 403 阻断。
- 🩺 **原生接口兜底**：内置针对 Hills、Infuse、VidHub、Yamby、SenPlayer 等客户端的探测与空数组兜底规则。

---

## 📦 一键安装与部署

适用于所有 **Debian / Ubuntu** 服务器（包括 512M / 1G NAT VPS）：

```bash
curl -fsSL https://raw.githubusercontent.com/rosc-bot/emby-multi-proxy/main/install.sh | bash
```

---

## 🛠️ 手动部署步骤

### 1. 安装基础依赖
```bash
apt update && apt install -y nginx python3
systemctl enable --now nginx
```

### 2. 下载并启动面板服务
```bash
mkdir -p /opt/emby-panel
curl -fsSL https://raw.githubusercontent.com/rosc-bot/emby-multi-proxy/main/panel.py -o /opt/emby-panel/panel.py

cat <<'EOF' > /etc/systemd/system/emby-panel.service
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
EOF

systemctl daemon-reload
systemctl enable --now emby-panel.service
```

### 3. 打开 Web 管理面板
在浏览器访问：
```text
http://YOUR_SERVER_IP:35087/panel/
```
*(端口可在面板设置或代码中自由指定)*

---

## 📱 客户端连接示例

| 站点名称 | 源站地址 | 路径前缀 | 客户端连接填写地址 |
|---|---|---|---|
| **默认根站点** | `https://emby1.example.com` | *(留空)* | `http://IP:35087` |
| **Emby 演示服 1** | `https://emby1.example.com` | `emby1` | `http://IP:35087/emby1/` |
| **Emby 演示服 2** | `https://emby2.example.com` | `emby2` | `http://IP:35087/emby2/` |

---

## 📄 开源协议

本项目基于 [MIT License](LICENSE) 开源。
