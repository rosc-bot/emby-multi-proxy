import http.server
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
import ssl

CONFIG_FILE = "/opt/emby-panel/sites.json"
NGINX_CONF_DIR = "/etc/nginx/conf.d"
NGINX_CONF_FILE = "/etc/nginx/conf.d/emby_sites.conf"
PANEL_PORT = 18090
SECRET_KEY = "admin123"

DEFAULT_SITES = [
    {
        "id": "terminus",
        "name": "示例站点 (Demo)",
        "target": "https://emby.example.com",
        "type": "path",      # "path" or "port"
        "listen_port": 20021,
        "path_prefix": "",   # 根路径
        "enabled": True,
        "is_default": True
    }
]

def load_sites():
    if not os.path.exists(CONFIG_FILE):
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        save_sites(DEFAULT_SITES)
        return DEFAULT_SITES
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_SITES

def save_sites(sites):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(sites, f, indent=2, ensure_ascii=False)
    apply_nginx_config(sites)

def apply_nginx_config(sites):
    # 根据 sites 生成 Nginx 配置
    # 按照 listen_port 分组
    by_port = {}
    for s in sites:
        if not s.get("enabled", True):
            continue
        port = s.get("listen_port", 35087)
        if port not in by_port:
            by_port[port] = []
        by_port[port].append(s)

    conf_lines = []
    
    # 静态资源缓存池（针对图片、海报、图标）
    conf_lines.append("# Emby 静态资源边缘缓存池 (20MB 内存索引, 300MB 磁盘空间)")
    conf_lines.append("proxy_cache_path /var/cache/nginx/emby levels=1:2 keys_zone=emby_static:20m max_size=300m inactive=7d use_temp_path=off;")
    conf_lines.append("")

    for port, port_sites in by_port.items():
        conf_lines.append(f"server {{")
        conf_lines.append(f"    listen {port} reuseport;")
        conf_lines.append(f"    listen [::]:{port} reuseport;")
        conf_lines.append(f"    server_name _;")
        conf_lines.append(f"    client_max_body_size 2048M;")
        conf_lines.append(f"    sendfile on;")
        conf_lines.append(f"    tcp_nopush on;")
        conf_lines.append(f"    tcp_nodelay on;")
        conf_lines.append(f"    keepalive_timeout 120s;")
        conf_lines.append(f"    keepalive_requests 10000;")
        conf_lines.append("")
        conf_lines.append("")

        # Web 管理面板入口与 API
        conf_lines.append("    # Web 管理面板")
        conf_lines.append("    location ^~ /panel/ {")
        conf_lines.append("        proxy_pass http://127.0.0.1:18090/;")
        conf_lines.append("        proxy_set_header Host $host;")
        conf_lines.append("        proxy_set_header X-Real-IP $remote_addr;")
        conf_lines.append("    }")
        conf_lines.append("    location ^~ /api/ {")
        conf_lines.append("        proxy_pass http://127.0.0.1:18090/api/;")
        conf_lines.append("        proxy_set_header Host $host;")
        conf_lines.append("        proxy_set_header X-Real-IP $remote_addr;")
        conf_lines.append("    }")
        conf_lines.append("")

        # 区分根路径站点与子路径站点
        root_site = next((s for s in port_sites if not s.get("path_prefix")), None)
        path_sites = [s for s in port_sites if s.get("path_prefix")]

        # 处理子路径站点
        for s in path_sites:
            prefix = s.get("path_prefix").strip("/")
            target = s.get("target").rstrip("/")
            # 提取 host
            parsed = urllib.parse.urlparse(target)
            host = parsed.netloc.split(":")[0]
            scheme = parsed.scheme or "https"
            
            conf_lines.append(f"    # 站点: {s.get('name')}")

            # 针对终点站等特殊扩展接口的空数组兜底（支持双斜杠容错）
            conf_lines.append(f"    location ~* ^/{prefix}/+(emby/)?(System/Ext/ServerDomains|Sessions)$ {{")
            conf_lines.append(f"        proxy_pass {target};")
            conf_lines.append(f"        proxy_set_header Host {parsed.netloc};")
            conf_lines.append(f"        proxy_set_header X-Real-IP $remote_addr;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-Proto https;")
            if scheme == "https":
                conf_lines.append(f"        proxy_ssl_server_name on;")
                conf_lines.append(f"        proxy_ssl_name {host};")
            conf_lines.append(f"        proxy_intercept_errors on;")
            conf_lines.append(f"        error_page 404 = @empty_json_{prefix};")
            conf_lines.append(f"    }}")
            conf_lines.append(f"    location @empty_json_{prefix} {{")
            conf_lines.append(f"        default_type application/json;")
            conf_lines.append(f"        return 200 '[]';")
            conf_lines.append(f"    }}")
            conf_lines.append(f"")

            # 针对 Emby 客户端 ping 探测接口返回 200 (System/Info/Public)
            conf_lines.append(f"    location ^~ /{prefix}/emby/system/info/public {{")
            conf_lines.append(f"        proxy_pass {target}/emby/system/info/public;")
            conf_lines.append(f"        proxy_set_header Host {parsed.netloc};")
            conf_lines.append(f"        proxy_set_header X-Real-IP $remote_addr;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-Proto https;")
            if scheme == "https":
                conf_lines.append(f"        proxy_ssl_server_name on;")
                conf_lines.append(f"        proxy_ssl_name {host};")
            conf_lines.append(f"    }}")
            conf_lines.append(f"    location ^~ /{prefix}/System/Info/Public {{")
            conf_lines.append(f"        proxy_pass {target}/System/Info/Public;")
            conf_lines.append(f"        proxy_set_header Host {parsed.netloc};")
            conf_lines.append(f"        proxy_set_header X-Real-IP $remote_addr;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-Proto https;")
            if scheme == "https":
                conf_lines.append(f"        proxy_ssl_server_name on;")
                conf_lines.append(f"        proxy_ssl_name {host};")
            conf_lines.append(f"    }}")
            conf_lines.append(f"")

            # 针对视频流媒体与大文件的极致切片流水线优化 (HLS/TS/MP4/MKV/Stream)
            conf_lines.append(f"    location ~* ^/{prefix}/(emby/)?(videos|audio|sync|Items/.+/Download|Videos/.+/stream) {{")
            conf_lines.append(f"        proxy_pass {target};")
            conf_lines.append(f"        proxy_set_header Host {parsed.netloc};")
            conf_lines.append(f"        proxy_set_header X-Real-IP $remote_addr;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-Proto https;")
            conf_lines.append(f"        proxy_set_header Range $http_range;")
            conf_lines.append(f"        proxy_set_header If-Range $http_if_range;")
            conf_lines.append(f"        proxy_http_version 1.1;")
            conf_lines.append(f"        proxy_set_header Connection '';")
            if scheme == "https":
                conf_lines.append(f"        proxy_ssl_server_name on;")
                conf_lines.append(f"        proxy_ssl_name {host};")
                conf_lines.append(f"        proxy_ssl_protocols TLSv1.2 TLSv1.3;")
            conf_lines.append(f"        proxy_redirect off;")
            conf_lines.append(f"        proxy_buffering on;")
            conf_lines.append(f"        proxy_buffer_size 512k;")
            conf_lines.append(f"        proxy_buffers 16 1m;")
            conf_lines.append(f"        proxy_busy_buffers_size 2m;")
            conf_lines.append(f"        proxy_temp_file_write_size 2m;")
            conf_lines.append(f"        proxy_max_temp_file_size 0;")
            conf_lines.append(f"        proxy_read_timeout 7200s;")
            conf_lines.append(f"        proxy_send_timeout 7200s;")
            conf_lines.append(f"    }}")
            conf_lines.append(f"")

            conf_lines.append(f"    location ^~ /{prefix}/ {{")
            conf_lines.append(f"        proxy_pass {target}/;")
            conf_lines.append(f"        proxy_set_header Host {parsed.netloc};")
            conf_lines.append(f"        proxy_set_header X-Real-IP $remote_addr;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-Proto https;")
            conf_lines.append(f"        proxy_set_header Range $http_range;")
            conf_lines.append(f"        proxy_set_header If-Range $http_if_range;")
            conf_lines.append(f"        proxy_http_version 1.1;")
            conf_lines.append(f"        proxy_set_header Upgrade $http_upgrade;")
            conf_lines.append(f"        proxy_set_header Connection $http_connection;")
            if scheme == "https":
                conf_lines.append(f"        proxy_ssl_server_name on;")
                conf_lines.append(f"        proxy_ssl_name {host};")
                conf_lines.append(f"        proxy_ssl_protocols TLSv1.2 TLSv1.3;")
            conf_lines.append(f"        proxy_redirect off;")
            conf_lines.append(f"        proxy_buffering off;")
            conf_lines.append(f"        proxy_buffer_size 128k;")
            conf_lines.append(f"        proxy_buffers 4 256k;")
            conf_lines.append(f"        proxy_busy_buffers_size 256k;")
            conf_lines.append(f"        proxy_read_timeout 3600s;")
            conf_lines.append(f"        proxy_send_timeout 3600s;")
            conf_lines.append(f"    }}")
            conf_lines.append("")

        # 处理默认/根路径站点
        if root_site:
            target = root_site.get("target").rstrip("/")
            parsed = urllib.parse.urlparse(target)
            host = parsed.netloc.split(":")[0]
            scheme = parsed.scheme or "https"
            
            # 管理面板反代路径（防止外部端口受限，直接通过 /panel/ 访问管理台）
            conf_lines.append(f"    # Web 管理面板入口")
            conf_lines.append(f"    location /panel/ {{")
            conf_lines.append(f"        proxy_pass http://127.0.0.1:18090/;")
            conf_lines.append(f"        proxy_set_header Host $host;")
            conf_lines.append(f"        proxy_set_header X-Real-IP $remote_addr;")
            conf_lines.append(f"    }}")
            conf_lines.append(f"    location /api/ {{")
            conf_lines.append(f"        proxy_pass http://127.0.0.1:18090/api/;")
            conf_lines.append(f"        proxy_set_header Host $host;")
            conf_lines.append(f"        proxy_set_header X-Real-IP $remote_addr;")
            conf_lines.append(f"    }}")
            conf_lines.append("")

            # 针对终点站扩展兜底
            conf_lines.append(f"    # 根站点: {root_site.get('name')}")
            conf_lines.append(f"    location ~* ^/(emby/)?(System/Ext/ServerDomains|Sessions)$ {{")
            conf_lines.append(f"        proxy_pass {target};")
            conf_lines.append(f"        proxy_set_header Host {parsed.netloc};")
            conf_lines.append(f"        proxy_set_header X-Real-IP $remote_addr;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-Proto https;")
            if scheme == "https":
                conf_lines.append(f"        proxy_ssl_server_name on;")
                conf_lines.append(f"        proxy_ssl_name {host};")
            conf_lines.append(f"        proxy_intercept_errors on;")
            conf_lines.append(f"        error_page 404 = @empty_json_{port};")
            conf_lines.append(f"    }}")
            conf_lines.append(f"    location @empty_json_{port} {{")
            conf_lines.append(f"        default_type application/json;")
            conf_lines.append(f"        return 200 '[]';")
            conf_lines.append(f"    }}")
            conf_lines.append("")
            conf_lines.append(f"    location / {{")
            conf_lines.append(f"        proxy_pass {target};")
            conf_lines.append(f"        proxy_set_header Host {parsed.netloc};")
            conf_lines.append(f"        proxy_set_header X-Real-IP $remote_addr;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
            conf_lines.append(f"        proxy_set_header X-Forwarded-Proto https;")
            conf_lines.append(f"        proxy_set_header Range $http_range;")
            conf_lines.append(f"        proxy_set_header If-Range $http_if_range;")
            conf_lines.append(f"        proxy_http_version 1.1;")
            conf_lines.append(f"        proxy_set_header Upgrade $http_upgrade;")
            conf_lines.append(f"        proxy_set_header Connection $http_connection;")
            if scheme == "https":
                conf_lines.append(f"        proxy_ssl_server_name on;")
                conf_lines.append(f"        proxy_ssl_name {host};")
                conf_lines.append(f"        proxy_ssl_protocols TLSv1.2 TLSv1.3;")
            conf_lines.append(f"        proxy_redirect off;")
            conf_lines.append(f"        proxy_buffering off;")
            conf_lines.append(f"        proxy_buffer_size 128k;")
            conf_lines.append(f"        proxy_buffers 4 256k;")
            conf_lines.append(f"        proxy_busy_buffers_size 256k;")
            conf_lines.append(f"        proxy_read_timeout 3600s;")
            conf_lines.append(f"        proxy_send_timeout 3600s;")
            conf_lines.append(f"    }}")
        else:
            conf_lines.append("    location = / {")
            conf_lines.append("        return 302 /panel/;")
            conf_lines.append("    }")
        
        conf_lines.append("}")
        conf_lines.append("")

    # 将旧的 emby_proxy.conf 禁用或覆盖
    if os.path.exists(os.path.join(NGINX_CONF_DIR, "emby_proxy.conf")):
        try:
            os.remove(os.path.join(NGINX_CONF_DIR, "emby_proxy.conf"))
        except Exception:
            pass

    with open(NGINX_CONF_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(conf_lines))

    # 测试并重载
    res = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    if res.returncode == 0:
        subprocess.run(["systemctl", "reload", "nginx"], capture_output=True)
        return True, "Nginx 配置更新成功并已重载！"
    else:
        return False, f"Nginx 语法检查失败: {res.stderr}"

HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Emby 多站点反代管理面板</title>
<style>
:root {
  --bg: #0f172a;
  --card: #1e293b;
  --border: #334155;
  --text: #f8fafc;
  --text-muted: #94a3b8;
  --primary: #3b82f6;
  --primary-hover: #2563eb;
  --success: #10b981;
  --danger: #ef4444;
  --warning: #f59e0b;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); padding: 16px; min-height: 100vh; }
.container { max-width: 900px; margin: 0 auto; }
header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid var(--border); flex-wrap: wrap; gap: 12px; }
.title { font-size: 1.3rem; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.badge { background: #1e3a8a; color: #93c5fd; padding: 3px 8px; border-radius: 999px; font-size: 0.75rem; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 16px; }
.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.btn { background: var(--primary); color: #fff; border: none; padding: 8px 14px; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 0.88rem; transition: 0.2s; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover { background: var(--primary-hover); }
.btn-danger { background: var(--danger); }
.btn-danger:hover { background: #dc2626; }
.btn-sm { padding: 5px 10px; font-size: 0.78rem; }
.btn-outline { background: transparent; border: 1px solid var(--border); color: var(--text); }
.btn-outline:hover { background: var(--border); }
.site-item { background: #0f172a; border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
.site-info { flex: 1; min-width: 260px; }
.site-name { font-size: 1rem; font-weight: 600; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }
.site-target { font-size: 0.82rem; color: var(--text-muted); word-break: break-all; margin-bottom: 4px; }
.site-url { font-size: 0.85rem; color: #60a5fa; font-family: monospace; background: #1e293b; padding: 4px 8px; border-radius: 6px; display: inline-block; word-break: break-all; }
.site-actions { display: flex; gap: 8px; align-items: center; }
.tag { font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; font-weight: 600; }
.tag-root { background: #065f46; color: #6ee7b7; }
.tag-path { background: #831843; color: #fbcfe8; }
.status-pill { display: inline-block; width: 8px; height: 8px; border-radius: 50%; }
.status-online { background: var(--success); }
.status-offline { background: var(--danger); }

/* Modal */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: none; justify-content: center; align-items: center; padding: 16px; z-index: 99; }
.modal-overlay.active { display: flex; }
.modal { background: var(--card); border: 1px solid var(--border); border-radius: 14px; width: 100%; max-width: 480px; padding: 20px; max-height: 90vh; overflow-y: auto; }
.form-group { margin-bottom: 14px; }
.form-group label { display: block; font-size: 0.82rem; color: var(--text-muted); margin-bottom: 6px; }
.form-control { width: 100%; background: #0f172a; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; color: var(--text); font-size: 0.9rem; }
.form-control:focus { outline: none; border-color: var(--primary); }
.form-tip { font-size: 0.75rem; color: var(--text-muted); margin-top: 4px; }
.modal-footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
.toast { position: fixed; bottom: 20px; right: 20px; background: #334155; color: #fff; padding: 10px 16px; border-radius: 8px; font-size: 0.88rem; box-shadow: 0 4px 12px rgba(0,0,0,0.5); z-index: 100; transform: translateY(100px); opacity: 0; transition: 0.3s; }
.toast.show { transform: translateY(0); opacity: 1; }
</style>
</head>
<body>
<div class="container">
  <header>
    <div class="title">
      <span>🎬 Emby 多站点反代面板</span>
      <span class="badge">荷兰 NL NAT</span>
    </div>
    <button class="btn" onclick="openAddModal()">+ 添加 Emby 站点</button>
  </header>

  <div class="card">
    <div class="card-header">
      <div style="font-weight: 600; font-size: 0.95rem;">已配置的反代站点列表</div>
      <button class="btn btn-sm btn-outline" onclick="reloadAll()">🔄 重新加载 Nginx</button>
    </div>
    <div id="siteList">正在加载...</div>
  </div>

  <div class="card" style="font-size: 0.82rem; color: var(--text-muted); line-height: 1.6;">
    <div style="font-weight: 600; color: var(--text); margin-bottom: 6px;">💡 客户端连接说明：</div>
    <div>• <b>根路径站点</b>：客户端直接填写 <code>http://127.0.0.1:端口</code> 即可连接。</div>
    <div>• <b>子路径站点</b>：客户端填写 <code>http://127.0.0.1:端口/子路径/</code> （例如 <code>http://127.0.0.1:25168/myemby/</code>）。</div>
    <div>• 每次添加或修改站点后，系统会自动更新 Nginx 并平滑 reload，0 丢包。</div>
  </div>
</div>

<!-- Modal -->
<div class="modal-overlay" id="siteModal">
  <div class="modal">
    <div style="font-weight: 700; font-size: 1.1rem; margin-bottom: 16px;" id="modalTitle">添加 Emby 站点</div>
    <form id="siteForm" onsubmit="saveSite(event)">
      <input type="hidden" id="siteId">
      
      <div class="form-group">
        <label>站点名称</label>
        <input type="text" id="siteName" class="form-control" placeholder="例如：终点站 / 私有服" required>
      </div>

      <div class="form-group">
        <label>目标 Emby 源站地址 (必须包含 http:// 或 https://)</label>
        <input type="url" id="siteTarget" class="form-control" placeholder="例如：https://emby.example.com 或 https://emby.xxx.com:8443" required>
      </div>

      <div class="form-group">
        <label>监听端口 (NAT 小鸡已开放端口)</label>
        <input type="number" id="sitePort" class="form-control" value="20021" required>
        <div class="form-tip">荷兰鸡可用外部公网端口：<code>20020</code>、<code>20021</code></div>
      </div>

      <div class="form-group">
        <label>子路径前缀 (留空则为根路径)</label>
        <input type="text" id="sitePrefix" class="form-control" placeholder="例如：myemby（不要加斜杠）">
        <div class="form-tip">同一个端口下：留空的站点作为默认根目录；填写了的按 <code>/路径/</code> 分流。</div>
      </div>

      <div class="modal-footer">
        <button type="button" class="btn btn-outline" onclick="closeModal()">取消</button>
        <button type="submit" class="btn">保存并生效</button>
      </div>
    </form>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let sites = [];

function copyUrl(text, el) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => showCopied(el)).catch(() => fallbackCopy(text, el));
  } else {
    fallbackCopy(text, el);
  }
}

function fallbackCopy(text, el) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand('copy');
    showCopied(el);
  } catch (err) {
    alert('复制失败，请手动长按复制：' + text);
  }
  document.body.removeChild(ta);
}

function showCopied(el) {
  const badge = el.querySelector('.copy-badge');
  if (badge) {
    const oldText = badge.innerText;
    badge.innerText = '✅ 已复制!';
    badge.style.background = 'rgba(16, 185, 129, 0.2)';
    badge.style.color = '#10b981';
    badge.style.borderColor = 'rgba(16, 185, 129, 0.4)';
    setTimeout(() => {
      badge.innerText = oldText;
      badge.style.background = 'rgba(99,102,241,0.15)';
      badge.style.color = 'var(--primary)';
      badge.style.borderColor = 'rgba(99,102,241,0.3)';
    }, 1500);
  }
}

async function loadData() {
  try {
    const res = await fetch('/api/sites');
    sites = await res.json();
    render();
  } catch (e) {
    document.getElementById('siteList').innerHTML = '<div style="color:var(--danger)">加载失败，请刷新重试</div>';
  }
}

function render() {
  const list = document.getElementById('siteList');
  if (!sites.length) {
    list.innerHTML = '<div style="text-align:center; padding:30px; color:var(--text-muted)">暂无站点，点击右上角添加</div>';
    return;
  }
  
  const host = window.location.hostname || '127.0.0.1';
  
  list.innerHTML = sites.map((s, idx) => {
    const isRoot = !s.path_prefix;
    const accessUrl = isRoot ? `http://${host}:${s.listen_port || 35087}` : `http://${host}:${s.listen_port || 35087}/${s.path_prefix}/`;
    return `
      <div class="site-item">
        <div class="site-info">
          <div class="site-name">
            <span>${escapeHtml(s.name)}</span>
            <span class="tag ${isRoot ? 'tag-root' : 'tag-path'}">${isRoot ? '根路径' : '/' + escapeHtml(s.path_prefix)}</span>
            <span style="font-size:0.75rem; color:var(--text-muted)">端口: ${s.listen_port || 35087}</span>
          </div>
          <div class="site-target">🎯 源站: ${escapeHtml(s.target)}</div>
          <div class="site-url" style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; cursor:pointer;" onclick="copyUrl('${accessUrl}', this)" title="点击一键复制">
            <span>🔗 客户端地址: <code>${accessUrl}</code></span>
            <span class="copy-badge" style="font-size:0.75rem; background:rgba(99,102,241,0.15); color:var(--primary); padding:2px 8px; border-radius:4px; border:1px solid rgba(99,102,241,0.3); user-select:none;">📋 点击复制</span>
          </div>
        </div>
        <div class="site-actions">
          <button class="btn btn-secondary btn-sm" onclick="testUrl('${encodeURIComponent(s.target)}')">⚡ 测通</button>
          <button class="btn btn-secondary btn-sm" onclick="editSite('${s.id}')">✏️ 编辑</button>
          <button class="btn btn-danger btn-sm" onclick="deleteSite('${s.id}')">🗑️ 删除</button>
        </div>
      </div>
    `;
  }).join('');
}

function openAddModal() {
  document.getElementById('modalTitle').innerText = '添加 Emby 站点';
  document.getElementById('siteId').value = '';
  document.getElementById('siteName').value = '';
  document.getElementById('siteTarget').value = '';
  document.getElementById('sitePort').value = '20021';
  document.getElementById('sitePrefix').value = '';
  document.getElementById('siteModal').classList.add('active');
}

function editSite(idx) {
  const s = sites[idx];
  document.getElementById('modalTitle').innerText = '编辑 Emby 站点';
  document.getElementById('siteId').value = s.id;
  document.getElementById('siteName').value = s.name;
  document.getElementById('siteTarget').value = s.target;
  document.getElementById('sitePort').value = s.listen_port || 20021;
  document.getElementById('sitePrefix').value = s.path_prefix || '';
  document.getElementById('siteModal').classList.add('active');
}

function closeModal() {
  document.getElementById('siteModal').classList.remove('active');
}

async function saveSite(e) {
  e.preventDefault();
  const id = document.getElementById('siteId').value || ('site_' + Date.now());
  const name = document.getElementById('siteName').value.trim();
  const target = document.getElementById('siteTarget').value.trim();
  const port = parseInt(document.getElementById('sitePort').value) || 25168;
  const prefix = document.getElementById('sitePrefix').value.trim().replace(/^\\/+|\\/+$/g, '');

  const newSite = {
    id,
    name,
    target,
    listen_port: port,
    path_prefix: prefix,
    enabled: true
  };

  const existingIdx = sites.findIndex(s => s.id === id);
  if (existingIdx >= 0) {
    sites[existingIdx] = newSite;
  } else {
    sites.push(newSite);
  }

  showToast('正在保存并重载 Nginx...');
  closeModal();

  try {
    const res = await fetch('/api/sites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sites)
    });
    const data = await res.json();
    if (data.ok) {
      showToast('✅ 保存成功，Nginx 已平滑生效！');
      loadData();
    } else {
      showToast('❌ 保存失败: ' + (data.msg || '未知错误'));
    }
  } catch (err) {
    showToast('❌ 网络错误');
  }
}

async function deleteSite(id) {
  if (!confirm('确定要删除这个 Emby 反代站点吗？')) return;
  sites = sites.filter(s => s.id !== id);
  showToast('正在更新 Nginx...');
  try {
    const res = await fetch('/api/sites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sites)
    });
    showToast('✅ 站点已删除并重载生效');
    loadData();
  } catch (e) {
    showToast('❌ 操作失败');
  }
}

async function reloadAll() {
  showToast('正在重载 Nginx...');
  try {
    const res = await fetch('/api/reload', { method: 'POST' });
    const data = await res.json();
    showToast(data.ok ? '✅ Nginx 重载成功' : '❌ 重载失败: ' + data.msg);
  } catch (e) {
    showToast('❌ 请求失败');
  }
}

async function testUrl(targetEnc) {
  showToast('正在从荷兰鸡测试目标源站...');
  try {
    const res = await fetch('/api/test?target=' + targetEnc);
    const data = await res.json();
    if (data.ok) {
      alert(`✅ 连通正常！\nHTTP 状态码: ${data.code}\n耗时: ${data.time_ms}ms`);
    } else {
      alert(`❌ 连接失败！\n错误: ${data.error}`);
    }
  } catch (e) {
    alert('❌ 探测接口请求失败');
  }
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.innerText = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

function escapeHtml(str) {
  return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

loadData();
</script>
</body>
</html>
"""

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/sites":
            sites = load_sites()
            self.send_json(sites)
        elif parsed.path == "/api/test":
            qs = urllib.parse.parse_qs(parsed.query)
            target = qs.get("target", [""])[0]
            if not target:
                self.send_json({"ok": False, "error": "缺少 target 参数"}, 400)
                return
            import time
            start = time.time()
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            try:
                req = urllib.request.Request(target, headers={"User-Agent": "Emby/4.8.0.0"})
                with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                    dur = int((time.time() - start) * 1000)
                    self.send_json({"ok": True, "code": resp.getcode(), "time_ms": dur})
            except urllib.error.HTTPError as e:
                dur = int((time.time() - start) * 1000)
                self.send_json({"ok": True, "code": e.code, "time_ms": dur})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/sites":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                sites = json.loads(body.decode("utf-8"))
                save_sites(sites)
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"ok": False, "msg": str(e)}, 400)
        elif parsed.path == "/api/reload":
            sites = load_sites()
            ok, msg = apply_nginx_config(sites)
            self.send_json({"ok": ok, "msg": msg})
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PANEL_PORT), Handler)
    print(f"Panel running on port {PANEL_PORT}")
    server.serve_forever()
