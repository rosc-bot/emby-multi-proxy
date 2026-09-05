# 🎬 Emby Multi-Site Reverse Proxy Panel

面向 NAT VPS / 低配 Debian、Ubuntu 服务器的超轻量 Emby / Jellyfin 多站点反向代理管理面板。后端仅使用 Python 标准库，实际媒体转发由 Nginx 完成；管理服务默认只监听 `127.0.0.1`。

## ✨ v2 优化重点

- 🔐 **管理 Token 鉴权**：面板 API 不再裸奔；Token 安装时随机生成并以 `0600` 保存。
- 👤 **非 root 运行**：Python 面板使用受限 `emby-panel` 系统用户，仅通过 sudoers 获准执行 `nginx -t` 与 `systemctl reload nginx`。
- 🧯 **安全热更新**：保存配置采用原子写入，先 `nginx -t`，失败自动回滚；重载失败同样回滚。
- 🗂️ **配置备份**：自动保留最近 10 份站点与 Nginx 生成配置。
- 📤 **导入 / 导出**：可直接备份和迁移全部站点。
- ⚡ **流媒体优化**：关闭流式响应缓冲、关闭请求体缓冲、长超时、Range 支持、WebSocket、SNI、`sendfile` / `tcp_nopush` / `tcp_nodelay`。
- 🧩 **单端口多站点**：根站点 + `/emby1/`、`/emby2/` 等子路径同时存在。
- 🛡️ **输入校验**：限制站点 ID、路径前缀、源站 URL、Host Header，避免生成非法 Nginx 配置。
- 🧪 **CI**：GitHub Actions 自动进行 Python 编译检查、Shell 语法检查和 ShellCheck。

## 📦 一键安装 / 更新

> 当前脚本支持 Debian / Ubuntu，并需要 root 权限。

```bash
curl -fsSL https://raw.githubusercontent.com/rosc-bot/emby-multi-proxy/main/install.sh | bash
```

安装完成后终端会显示：

- 面板地址：`http://SERVER_IP:35087/panel/`
- 随机管理 Token
- Token 保存位置：`/etc/emby-multi-proxy/admin.token`

重复执行同一条安装命令即可更新程序，现有 Token 与站点配置会保留。

### 自定义端口

```bash
curl -fsSL https://raw.githubusercontent.com/rosc-bot/emby-multi-proxy/main/install.sh \
  | EMBY_PROXY_PORT=35087 EMBY_PANEL_PORT=35187 bash
```

`EMBY_PROXY_PORT` 是客户端和面板共用的公网入口；`EMBY_PANEL_PORT` 仅绑定回环地址，不需要在防火墙放行。

## 🖥️ 使用方式

打开 `http://服务器IP:35087/panel/`，输入安装脚本生成的 Token。

新增站点时：

| 字段 | 示例 | 说明 |
|---|---|---|
| 名称 | `Emby SG` | 仅用于面板显示 |
| ID | `emby-sg` | 唯一标识，创建后不可修改 |
| 源站 URL | `https://emby.example.com` | 必须包含 `http://` 或 `https://` |
| 路径前缀 | `emby1` | 客户端使用 `/emby1/`；留空表示根站点 |
| Host Header | `emby.example.com` | 通常留空；特殊 CDN / 源站才需要覆盖 |
| 跳过 TLS 校验 | 关闭 | 仅用于自签名源站，正常 HTTPS 不建议开启 |

一个配置例子：

| 站点 | 源站 | 路径 | 客户端地址 |
|---|---|---|---|
| 默认站 | `https://a.example.com` | 留空 | `http://IP:35087/` |
| 新加坡 | `https://b.example.com` | `sg` | `http://IP:35087/sg/` |
| 日本 | `https://c.example.com` | `jp` | `http://IP:35087/jp/` |

> 升级脚本会尝试自动迁移旧版 `/opt/emby-panel/sites.json` 或 `config.json`；旧字段 `url/target/origin`、`prefix/base_path` 也会自动兼容。

> 子路径模式依赖客户端与源站对 Base URL / 前缀的兼容性。如果某个 Emby 服务端强制生成绝对根路径 URL，建议将该站点设置为根站点或为它使用独立域名/端口。

## 🔧 文件与服务

```text
/opt/emby-multi-proxy/panel.py          # 面板程序
/opt/emby-multi-proxy/core.py           # 站点校验、存储与 Nginx 配置生成
/opt/emby-multi-proxy/panel.html        # 管理页面
/etc/emby-multi-proxy/admin.token       # 管理 Token
/etc/emby-multi-proxy/sites.conf        # 自动生成的 Nginx location
/var/lib/emby-multi-proxy/sites.json    # 站点数据
/var/lib/emby-multi-proxy/backups/      # 最近 10 份配置备份
/etc/nginx/conf.d/emby-multi-proxy.conf # Nginx 主入口
```

服务名：

```bash
systemctl status emby-multi-proxy --no-pager
journalctl -u emby-multi-proxy -f
```

校验 Nginx：

```bash
nginx -t
```

## 🗑️ 卸载

保留站点数据和 Token：

```bash
bash uninstall.sh
```

彻底清除：

```bash
bash uninstall.sh --purge
```

## 🔐 安全建议

1. 不要公开分享 `/etc/emby-multi-proxy/admin.token`。
2. 建议用防火墙仅允许自己的 IP 访问管理入口，或在前面增加 HTTPS。
3. 源站证书正常时不要开启“跳过 TLS 校验”。
4. 定期导出站点配置，或备份 `/var/lib/emby-multi-proxy/`。
5. 面板没有 shell / 任意 Nginx 配置编辑能力，避免把管理界面变成远程命令执行入口。

## 🧯 故障排查

### 面板打不开

```bash
systemctl status emby-multi-proxy --no-pager
journalctl -u emby-multi-proxy -n 100 --no-pager
ss -lntp | grep -E '35087|35187'
```

### 保存时报 Nginx 校验失败

面板会自动回滚上一版配置。继续检查：

```bash
nginx -t
journalctl -u nginx -n 100 --no-pager
```

### 源站 502 / 504

优先确认：

- 源站 URL 协议与端口是否正确；
- VPS 能否访问源站；
- HTTPS 源站证书/SNI 是否匹配；
- 某些 CDN 是否要求指定 Host Header；
- 源站是否限制代理机 IP。

### 第三方播放器子路径异常

先尝试将该站点设为根站点。如果根站点正常而子路径异常，通常是服务端或客户端没有正确处理 Base URL，而不是 Nginx 传输性能问题。

## 🔄 从旧版升级

直接重新运行一键安装命令。安装器会优先保留现有 v2 数据，并尝试从旧 `/opt/emby-panel/sites.json` 或 `/opt/emby-panel/config.json` 自动迁移。建议升级前仍先备份旧目录。

## API

管理 API 使用：

```http
Authorization: Bearer <ADMIN_TOKEN>
```

主要接口：

- `GET /api/sites`
- `PUT /api/sites/<id>`
- `DELETE /api/sites/<id>`
- `POST /api/reload`
- `GET /api/export`
- `POST /api/import`
- `GET /healthz`

## License

沿用仓库现有许可策略；若计划公开分发和接受外部贡献，建议补充明确的 `LICENSE` 文件。
