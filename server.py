#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
视频去水印 Web 服务 —— 手机浏览器访问，电脑端跑 yt-dlp + ffmpeg

启动： python server.py
访问： http://<本机局域网IP>:8848
"""
import os
import re
import sys
import json
import time
import glob
import uuid
import hmac
import random
import shutil
import socket
import hashlib
import threading
import collections
import subprocess
import mimetypes
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote, quote, parse_qs

# ---------------- 配置 ----------------
BASE = os.path.dirname(os.path.abspath(__file__))
IS_WIN = os.name == "nt"


def find_bin(names, fallbacks):
    """环境变量 > PATH > 已知安装位置，三选一"""
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for p in fallbacks:
        if p and os.path.isfile(p):
            return p
    return ""


YTDLP = os.environ.get("VIDEOWM_YTDLP") or find_bin(
    ["yt-dlp", "yt-dlp.exe"],
    [r"E:\mysoft\conda3\Scripts\yt-dlp.exe"])

FFMPEG = os.environ.get("VIDEOWM_FFMPEG") or find_bin(
    ["ffmpeg"], [r"E:\mysoft\ffmpeg\bin\ffmpeg.exe"])

FFPROBE = os.environ.get("VIDEOWM_FFPROBE") or find_bin(
    ["ffprobe"],
    [os.path.join(os.path.dirname(FFMPEG), "ffprobe.exe") if FFMPEG else ""])

FFBIN = os.path.dirname(FFMPEG) if FFMPEG else ""

# 媒体目录：环境变量 > Windows 旧目录（保持已有文件可用） > 本目录下 media/
_LEGACY_MEDIA = r"E:\testsoft\Lumii-data\outputs\video-downloads\web"
MEDIA = (os.environ.get("VIDEOWM_MEDIA")
         or (_LEGACY_MEDIA if os.path.isdir(_LEGACY_MEDIA) else os.path.join(BASE, "media")))
MEDIA = os.path.abspath(os.path.expanduser(MEDIA))

PORT = int(os.environ.get("VIDEOWM_PORT") or 8848)

# 产物保留时长（分钟）。0 = 不自动清理。
# 默认改为 0：成片会进 Lumii 产物台账，TTL 清盘后点开只剩「文件不存在」。
# 真要自动清盘再设 VIDEOWM_TTL_MIN（例如 60）。
TTL_MIN = int(os.environ.get("VIDEOWM_TTL_MIN") or 0)
# 扫描间隔，默认 5 分钟；调小方便验证过期逻辑
CLEAN_EVERY = int(os.environ.get("VIDEOWM_CLEAN_EVERY") or 300)
ACTIVE_STATES = {"queued", "downloading", "merging", "transcoding", "cropping"}

# ── 安全 ────────────────────────────────────────────────
# 访问码：环境变量 > .token 文件 > 随机生成并落盘
# 落盘是为了重启后不变，否则每次重启手机都得重新扫码
def _load_token():
    t = os.environ.get("VIDEOWM_TOKEN")
    if t:
        return t.strip()
    f = os.path.join(BASE, ".token")
    try:
        if os.path.isfile(f):
            v = open(f, encoding="utf-8").read().strip()
            if v:
                return v
    except Exception:
        pass
    v = "".join(random.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(8))
    try:
        with open(f, "w", encoding="utf-8") as fp:
            fp.write(v)
        os.chmod(f, 0o600)
    except Exception:
        pass
    return v


TOKEN = _load_token()
COOKIE_NAME = "vwm_auth"
SESSION_DAYS = int(os.environ.get("VIDEOWM_SESSION_DAYS") or 90)
# 反代前缀，手机端是 /videowm
BASE_PATH = (os.environ.get("VIDEOWM_BASE") or "").rstrip("/")

# 单个视频上限
MAX_DURATION = int(os.environ.get("VIDEOWM_MAX_DURATION") or 1800)      # 秒
MAX_SIZE = int(os.environ.get("VIDEOWM_MAX_SIZE") or 500) * 1024 * 1024  # 字节

# 频率限制（按 IP）
REQ_PER_MIN = int(os.environ.get("VIDEOWM_REQ_PER_MIN") or 60)
JOB_PER_HOUR = int(os.environ.get("VIDEOWM_JOB_PER_HOUR") or 10)
JOB_PER_DAY = int(os.environ.get("VIDEOWM_JOB_PER_DAY") or 40)

# 并发上限，别让一台手机同时干几件重活
SEM_DOWNLOAD = threading.Semaphore(2)
SEM_FFMPEG = threading.Semaphore(2)

# 只放开主流视频平台，堵掉 SSRF（防止有人让你手机去访问内网）
ALLOW_HOSTS = (
    "douyin.com", "iesdouyin.com", "douyinvod.com",
    "bilibili.com", "b23.tv", "bili2233.cn", "bilibili.tv", "hdslb.com",
    "xiaohongshu.com", "xhslink.com", "xhslink.cn",
    "kuaishou.com", "kuaishouapp.com", "chenzhongtech.com", "gifshow.com",
    "tiktok.com", "vm.tiktok.com", "muscdn.com",
    "weibo.com", "weibo.cn", "weibocdn.com",
    "zhihu.com", "zhimg.com",
    "youtube.com", "youtu.be", "googlevideo.com",
    "ixigua.com", "toutiao.com", "snssdk.com",
    "acfun.cn", "huya.com", "douyu.com", "yy.com",
    "qq.com", "weixin.qq.com", "weishi.qq.com",
    "xiaoyuzhoufm.com", "dedao.cn", "ximalaya.com",
    "163.com", "music.163.com", "y.qq.com", "kugou.com", "kuwo.cn",
    "instagram.com", "twitter.com", "x.com",
)

os.makedirs(MEDIA, exist_ok=True)

TASKS = {}

# ── 解析结果缓存：同一条链接别反复解析下载 ──
# 抖音/B站这类要开浏览器或走一堆接口，重复解析纯属浪费。
CACHE_TTL = 24 * 3600          # 24 小时后失效
CACHE_LOCK = threading.Lock()
_CACHE_PATH = os.path.join(MEDIA, ".parse-cache.json") if "MEDIA" in globals() else ""


def _cache_path():
    global _CACHE_PATH
    if not _CACHE_PATH:
        _CACHE_PATH = os.path.join(MEDIA, ".parse-cache.json")
    return _CACHE_PATH


def _cache_load():
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _cache_save(d):
    try:
        os.makedirs(os.path.dirname(_cache_path()), exist_ok=True)
        tmp = _cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, _cache_path())
    except Exception as e:
        log("cache save fail", str(e)[:80])


def cache_key(url):
    """同一条视频的不同写法要落到同一个 key 上：
    短链先展开、抖音统一成 www.douyin.com/video/<id>，再取摘要。"""
    u = url
    try:
        if is_douyin(u):
            u = normalize_douyin(u)
        m = re.search(r"(?:video|note|share/video)/(\d{8,})", u) or             re.search(r"(?:/video/|/explore/|/detail/)([A-Za-z0-9]{6,})", u)
        if m:
            host = (urlparse(u).hostname or "").replace("www.", "")
            return "%s:%s" % (host, m.group(1))
    except Exception:
        pass
    return hashlib.md5(u.encode("utf-8", "replace")).hexdigest()[:20]


def cache_get(key):
    with CACHE_LOCK:
        d = _cache_load()
    it = d.get(key)
    if not it:
        return None
    if time.time() - (it.get("ts") or 0) > CACHE_TTL:
        return None
    f = it.get("file") or ""
    if not f or not os.path.isfile(os.path.join(MEDIA, f)):
        return None                  # 文件已经不在了，缓存作废
    return it


def cache_put(tid):
    t = TASKS.get(tid) or {}
    url = t.get("url") or ""
    if not url or not t.get("file"):
        return
    key = cache_key(url)
    with CACHE_LOCK:
        d = _cache_load()
        d[key] = {
            "task": tid, "file": t.get("file"), "title": t.get("title") or "",
            "duration": t.get("duration") or 0, "width": t.get("width") or 0,
            "height": t.get("height") or 0, "uploader": t.get("uploader") or "",
            "extractor": t.get("extractor") or "", "created": t.get("created") or 0,
            "audio_only": bool(t.get("audio_only")),
            "ts": time.time(),
        }
        # 顺手清掉过期的
        now = time.time()
        d = {k: v for k, v in d.items() if now - (v.get("ts") or 0) <= CACHE_TTL * 2}
        _cache_save(d)
    log("cache put", key, t.get("file"))

LOCK = threading.Lock()


def log(*a):
    print("[%s]" % time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------- 鉴权 ----------------
def session_value():
    ts = int(time.time())
    sig = hmac.new(TOKEN.encode(), str(ts).encode(), hashlib.sha256).hexdigest()[:32]
    return "%d.%s" % (ts, sig)


def session_valid(v):
    if not v or "." not in v:
        return False
    try:
        ts_s, sig = v.split(".", 1)
        ts = int(ts_s)
    except Exception:
        return False
    if abs(time.time() - ts) > SESSION_DAYS * 86400:
        return False
    exp = hmac.new(TOKEN.encode(), ts_s.encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(sig, exp)


def dl_sig(name, exp):
    return hmac.new(TOKEN.encode(), ("dl|%s|%d" % (name, exp)).encode(),
                    hashlib.sha256).hexdigest()[:24]


def dl_link(name):
    """带签名的下载地址：复制出去在有效期内谁都能下，不用登录。"""
    exp = int(time.time()) + max(300, TTL_MIN * 60)
    return "/download/%s?s=%s&e=%d" % (quote(name), dl_sig(name, exp), exp)


def dl_sig_ok(name, s, e):
    try:
        e = int(e)
    except Exception:
        return False
    if time.time() > e:
        return False
    return hmac.compare_digest(s or "", dl_sig(name, e))


# ---------------- 限流 ----------------
RL_LOCK = threading.Lock()
REQ_BUCKET = collections.defaultdict(collections.deque)
JOB_HOUR = collections.defaultdict(collections.deque)
JOB_DAY = collections.defaultdict(collections.deque)


def _hit(bucket, key, window, limit):
    now = time.time()
    with RL_LOCK:
        dq = bucket[key]
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


def rate_request(ip):
    return _hit(REQ_BUCKET, ip, 60, REQ_PER_MIN)


def rate_job(ip):
    if not _hit(JOB_HOUR, ip, 3600, JOB_PER_HOUR):
        return "请求太频繁（每小时 %d 个任务上限），歇一会儿再来" % JOB_PER_HOUR
    if not _hit(JOB_DAY, ip, 86400, JOB_PER_DAY):
        return "今天用得够多了（%d 个任务上限），明天再来" % JOB_PER_DAY
    return None


# ---------------- 域名白名单 ----------------
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def url_allowed(url):
    try:
        p = urlparse(url)
    except Exception:
        return "链接格式不对"
    if p.scheme not in ("http", "https"):
        return "只支持 http/https 链接"
    host = (p.hostname or "").lower().strip(".")
    if not host:
        return "链接格式不对"
    if IPV4_RE.match(host) or ":" in host:
        return "不支持直接填 IP 地址"
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        return "不支持本地地址"
    for d in ALLOW_HOSTS:
        if host == d or host.endswith("." + d):
            return None
    return "这个站点不在支持列表里（只放开主流视频平台）"


def login_page(msg):
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#08090b"><title>需要访问码</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%%;background:#08090b;color:#e8eaed;
  font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;display:grid;place-items:center;padding:24px}
.box{max-width:340px;width:100%%;text-align:center}
.mark{width:46px;height:46px;margin:0 auto 20px;border:1.5px solid #00e08a;border-radius:3px;
  display:grid;place-items:center;box-shadow:0 0 18px rgba(0,224,138,.25)}
.mark i{width:14px;height:14px;border:2px solid #00e08a;border-right-color:transparent;
  border-bottom-color:transparent;transform:rotate(-45deg)}
h1{font-size:17px;font-weight:600;margin:0 0 8px;letter-spacing:.04em}
p{font-size:13.5px;color:#7d858f;line-height:1.65;margin:0 0 22px}
form{display:flex;gap:8px}
input{flex:1;min-width:0;background:#101215;border:1px solid #22262c;border-radius:3px;
  padding:13px;color:#e8eaed;font-size:16px;font-family:ui-monospace,Menlo,monospace;
  letter-spacing:.18em;text-align:center;outline:none}
input:focus{border-color:#00e08a}
button{padding:13px 20px;border:none;border-radius:3px;background:#00e08a;color:#00170e;
  font-size:14px;font-weight:600;font-family:inherit}
.msg{margin-top:16px;font-size:12.5px;color:#ff8a94;line-height:1.6}
.foot{position:fixed;bottom:22px;left:0;right:0;text-align:center;
  font-family:ui-monospace,Menlo,monospace;font-size:9.5px;letter-spacing:.14em;color:#4a5259}
</style></head>
<body>
<div class="box">
  <div class="mark"><i></i></div>
  <h1>需要访问码</h1>
  <p>%s<br>扫电脑终端上的二维码最快，也可以手输 8 位访问码。</p>
  <form method="get" action="./">
    <input name="k" autocomplete="off" autocapitalize="off" autocorrect="off"
           spellcheck="false" maxlength="32" placeholder="访问码">
    <button type="submit">进入</button>
  </form>
  <div class="msg">%s</div>
</div>
<div class="foot">LOCAL · PRIVATE</div>
</body></html>""" % (msg, "")


LOGIN_HINT = ""


def sub_env():
    """yt-dlp 合并/转码需要 ffmpeg，客户端进程 PATH 里可能没有，显式注入"""
    e = os.environ.copy()
    if FFBIN:
        e["PATH"] = FFBIN + os.pathsep + e.get("PATH", "")
    e["PYTHONIOENCODING"] = "utf-8"
    return e


def run(cmd, timeout=180):
    return subprocess.run(cmd, capture_output=True, env=sub_env(),
                          timeout=timeout, encoding="utf-8", errors="replace")


def task_set(tid, **kw):
    with LOCK:
        TASKS.setdefault(tid, {}).update(kw)


def cleanup_worker():
    """每 5 分钟扫一遍，删掉超过 TTL 的产物。
    TTL_MIN<=0 时不清理（成片要进 Lumii 产物台账）。
    正在下载/转码/裁剪的任务跳过，别把干活中的文件删了。"""
    every = max(5, CLEAN_EVERY)
    while True:
        time.sleep(every)
        if TTL_MIN <= 0:
            continue
        ttl = TTL_MIN * 60
        try:
            now = time.time()
            with LOCK:
                active = {tid for tid, t in TASKS.items()
                          if t.get("status") in ACTIVE_STATES}
            removed = 0
            for p in glob.glob(os.path.join(MEDIA, "*")):
                if not os.path.isfile(p):
                    continue
                name = os.path.basename(p)
                if name.endswith((".part", ".ytdl")):
                    continue
                tid = name.split(".")[0]
                if tid in active:
                    continue
                try:
                    if now - os.path.getmtime(p) > ttl:
                        os.remove(p)
                        removed += 1
                        with LOCK:
                            TASKS.pop(tid, None)
                except Exception:
                    pass
            if removed:
                log("cleanup: 过期清理 %d 个文件（TTL %d 分钟）" % (removed, TTL_MIN))
        except Exception as e:
            log("cleanup error", e)


def task_get(tid):
    with LOCK:
        t = TASKS.get(tid)
        if t:
            return dict(t)
    # 服务重启后内存表空了，但文件还在——从磁盘恢复，别让用户白等一场
    if tid and re.fullmatch(r"[A-Za-z0-9]{8,24}", tid):
        p = os.path.join(MEDIA, tid + ".mp4")
        if os.path.isfile(p):
            info = probe(p)
            return {"status": "done", "percent": 100.0, "file": tid + ".mp4",
                    "size": os.path.getsize(p), "duration": info.get("duration") or 0,
                    "width": info.get("width") or 0, "height": info.get("height") or 0}
    return {}


def probe(path):
    """返回 {duration,width,height,vcodec,acodec}"""
    try:
        p = run([FFPROBE, "-v", "error", "-show_entries",
                 "format=duration:stream=codec_type,codec_name,width,height",
                 "-of", "json", path], timeout=60)
        data = json.loads(p.stdout or "{}")
    except Exception as e:
        log("probe fail", e)
        return {}
    info = {"duration": 0.0, "width": 0, "height": 0, "vcodec": "", "acodec": ""}
    try:
        info["duration"] = float((data.get("format") or {}).get("duration") or 0)
    except Exception:
        pass
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video" and not info["vcodec"]:
            info["vcodec"] = s.get("codec_name") or ""
            info["width"] = int(s.get("width") or 0)
            info["height"] = int(s.get("height") or 0)
        elif s.get("codec_type") == "audio" and not info["acodec"]:
            info["acodec"] = s.get("codec_name") or ""
    return info


def find_output(tid):
    """下载完找实际产物，排除 .part/.ytdl"""
    cands = []
    for p in glob.glob(os.path.join(MEDIA, tid + ".*")):
        if p.endswith((".part", ".ytdl", ".json", ".info")):
            continue
        if os.path.isfile(p):
            cands.append(p)
    if not cands:
        return None
    cands.sort(key=lambda p: os.path.getsize(p), reverse=True)
    return cands[0]


# ---------------- 解析 ----------------
# 国内平台必须直连：走系统代理会被判定为异常流量
# （小红书会直接跳登录页，B站/抖音也容易风控）
DOMESTIC = (
    "douyin.com", "iesdouyin.com", "douyinvod.com", "ixigua.com", "toutiao.com",
    "bilibili.com", "b23.tv", "bili2233.cn", "hdslb.com",
    "xiaohongshu.com", "xhslink.com", "xhslink.cn",
    "kuaishou.com", "chenzhongtech.com", "gifshow.com",
    "weibo.com", "weibo.cn", "zhihu.com", "qq.com", "weixin.qq.com",
    "acfun.cn", "huya.com", "douyu.com", "ximalaya.com", "dedao.cn",
)


def is_domestic(url):
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == d or host.endswith("." + d) for d in DOMESTIC)


def net_args(url):
    """国内站直连（绕开系统代理），国外站不干预"""
    return ["--proxy", ""] if is_domestic(url) else []


# 短链域名：直接喂给 yt-dlp 容易触发风控（小红书会跳登录页）
SHORT_LINKS = ("xhslink.com", "xhslink.cn", "v.douyin.com", "b23.tv", "vm.tiktok.com")
# 短链展开用手机 UA：小红书对桌面 UA 会直接 302 到登录页
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
             "Mobile/15E148 Safari/604.1")
UA = MOBILE_UA
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")


def is_douyin(url):
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == d or host.endswith("." + d) for d in DOUYIN_HOSTS)


def _http_probe_size(url):
    """探一下文件总大小（Range 0-0，不整体下载）"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA_DESKTOP, "Referer": "https://www.douyin.com/",
        "Range": "bytes=0-0"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as r:
            cr = r.headers.get("Content-Range") or ""
            if "/" in cr:
                return int(cr.rsplit("/", 1)[1])
    except Exception:
        pass
    return 0


def normalize_douyin(url):
    """抖音统一成 www.douyin.com/video/<id>。
    实测：iesdouyin.com/share/... 那套页面在移动 UA 下拿不到流，
    而 www.douyin.com/video/<id> 能稳定出 douyinvod 地址。"""
    m = re.search(r"(?:video|note|share/video)/(\d{8,})", url)
    if not m:
        m = re.search(r"/(\d{16,})", url)
    if m:
        return "https://www.douyin.com/video/%s" % m.group(1)
    return url

def browser_extract(url):
    """通用兜底：无头浏览器打开页面，从网络响应里捞视频流地址。
    yt-dlp 的抖音 extractor 缺 a_bogus 签名（源码里就是 TODO），西瓜/快手这类也可能需要。

    两条实现路径：
      · Windows：Playwright（浏览器内核自带，启动快）
      · 手机 Termux：装不了 Playwright，改走 chromium + CDP（见 cdp_grab.py）
    """
    if is_douyin(url):
        url = normalize_douyin(url)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        # 没有 Playwright 就退回 CDP —— 手机上走这条
        try:
            import cdp_grab
        except ImportError:
            return None, "这个平台需要浏览器环境，但没找到可用的浏览器组件"
        if not cdp_grab.find_chromium():
            return None, ("这个平台需要浏览器。手机端执行：pkg install x11-repo && "
                          "pkg install chromium；电脑/服务器端执行：pip install playwright && "
                          "playwright install chromium；或 apt install chromium")
        # 用独立子进程跑（daemon 进程内起 chromium 会踩坑，子进程隔离后稳定）
        cli = os.path.join(BASE, "cdp_cli.py")
        media, title, meta = [], "", {}
        # 抖音风控是波动的，单次失败很常见；重试两次能显著提高成功率
        for attempt in range(3):
            try:
                r = subprocess.run([sys.executable, cli, url], capture_output=True,
                                   timeout=120, encoding="utf-8", errors="replace")
                data = json.loads((r.stdout or "{}").strip() or "{}")
                media = data.get("media") or []
                title = data.get("title") or ""
                meta = data.get("meta") or {}
                if data.get("error") and not media:
                    meta["error"] = data["error"]
            except Exception as e:
                meta = {"error": "子进程调用失败 %s" % type(e).__name__}
            if media:
                log("cdp ok on attempt", attempt + 1)
                break
            if attempt < 2:
                log("cdp retry", attempt + 1, "empty")
                time.sleep(2)
        diag = meta.get("diag") or {}
        log("cdp", url[:50], "media=", len(media), "diag=", diag,
            "err=", meta.get("error", ""))
        if meta.get("error") and not media:
            return None, "浏览器抓流失败：%s (diag=%s)" % (meta["error"], diag)
        if not media:
            return None, "页面没有给出视频地址（诊断：%s）" % (diag,)
        best, best_size = media[0], 0
        for u in media[:4]:
            sz = _http_probe_size(u)
            if sz > best_size:
                best, best_size = u, sz
        m = re.search(r"/(?:video|note)/(\d+)", url) or re.search(r"/(\d{10,})", url)
        dur = meta.get("dur") or 0
        try:
            dur = float(dur)
        except Exception:
            dur = 0.0
        log("cdp grab", url[:50], len(media), "stream(s)")
        return {
            "id": m.group(1) if m else uuid.uuid4().hex[:12],
            "title": (title or "").strip() or "视频",
            "duration": dur,
            "width": int(meta.get("w") or 0),
            "height": int(meta.get("h") or 0),
            "uploader": "",
            "thumbnail": "",
            "extractor": "BrowserCDP",
            "webpage": url,
            "_direct": best,
        }, None

    media = []
    info = {}

    def on_resp(r):
        try:
            ct = (r.headers.get("content-type") or "").lower()
            u = r.url
            if not (ct.startswith("video/") or ".mp4" in u.lower()
                    or "douyinvod" in u or "/video/tos/" in u):
                return
            if any(x in u for x in ("uuu_", "sprite", ".m3u8", ".ts?",
                                    "cover", "avatar", ".jpg", ".png")):
                return
            media.append(u)
        except Exception:
            pass

    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, args=["--mute-audio", "--no-sandbox"])
            # 移动 UA + 移动视口。实测：桌面 UA 在这个页面上会拿到 2 秒的
            # 预览片（hevc / 222KB），移动 UA 才吐 douyinvod 的正片地址。
            # 移动 UA 千万别配桌面分辨率，那个组合本身就是特征。
            mobile = is_douyin(url)
            ctx = b.new_context(
                user_agent=(MOBILE_UA if mobile else UA_DESKTOP), locale="zh-CN",
                viewport=({"width": 390, "height": 844, "isMobile": True,
                           "device_scale_factor": 3, "has_touch": True}
                          if mobile else {"width": 1280, "height": 800}))
            page = ctx.new_page()
            page.on("response", on_resp)
            # 播放一下，逼出真实视频流请求
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=40000)
            except Exception:
                pass
            try:
                page.wait_for_selector("video", timeout=15000)
                page.evaluate("() => { const v = document.querySelector('video');"
                              " if (v) { v.muted = true; v.play && v.play().catch(()=>{}); } }")
            except Exception:
                pass
            # 一抓到视频流就返回，不再死等固定秒数（实测能省一半时间）
            for _ in range(50):                  # 最多再等 20 秒
                if media:
                    page.wait_for_timeout(600)   # 留点时间吐第 2、3 个地址
                    break
                page.wait_for_timeout(400)
            info = page.evaluate(
                "() => { const v = document.querySelector('video');"
                " return { title: (document.title||'').replace(/\s*-\s*(抖音|西瓜视频|快手)$/,''),"
                "  src: v ? (v.src||v.currentSrc||'') : '',"
                "  w: v ? v.videoWidth : 0, h: v ? v.videoHeight : 0,"
                "  dur: v ? v.duration : 0 }; }")
            b.close()
    except Exception as e:
        log("browser fail", type(e).__name__, str(e)[:120])
        return None, "页面打不开（%s），稍后再试" % type(e).__name__

    seen, uniq = set(), []
    for u in media:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    if not uniq and str(info.get("src", "")).startswith("http"):
        uniq = [info["src"]]
    if not uniq:
        return None, "页面没有给出视频地址，可能被风控或需要登录"

    # 挑体积最大的（通常最高清）
    best, best_size = uniq[0], 0
    for u in uniq[:4]:
        sz = _http_probe_size(u)
        if sz > best_size:
            best, best_size = u, sz

    m = re.search(r"/(?:video|note)/(\d+)", url) or re.search(r"/(\d{10,})", url)
    try:
        dur = float(info.get("dur")) if info.get("dur") and str(info.get("dur")) != "nan" else 0.0
    except Exception:
        dur = 0.0

    return {
        "id": m.group(1) if m else uuid.uuid4().hex[:12],
        "title": (info.get("title") or "").strip() or "视频",
        "duration": dur,
        "width": int(info.get("w") or 0),
        "height": int(info.get("h") or 0),
        "uploader": "",
        "thumbnail": "",
        "extractor": "Browser",
        "webpage": url,
        "_direct": best,
    }, None


BROWSER_FALLBACK_HOSTS = ("douyin.com", "iesdouyin.com", "ixigua.com",
                          "kuaishou.com", "chenzhongtech.com", "gifshow.com")


def browser_fallback_ok(url):
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == d or host.endswith("." + d) for d in BROWSER_FALLBACK_HOSTS)


def fetch_direct(url, dest, referer=""):
    """带 Referer 把直链存成本地文件，返回字节数"""
    if not referer:
        try:
            p0 = urlparse(url)
            referer = "%s://%s/" % (p0.scheme, p0.netloc)
        except Exception:
            referer = "https://www.douyin.com/"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA_DESKTOP, "Referer": referer,
        "Accept": "*/*"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    total = 0
    with opener.open(req, timeout=60) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
    return total


def expand_short(url):
    """短链先展开成真实地址，再交给 yt-dlp。
    小红书实测：短链直连 yt-dlp 会 302 到登录页，展开后正常。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return url
    if not any(host == d or host.endswith("." + d) for d in SHORT_LINKS):
        return url
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(url, headers={"User-Agent": MOBILE_UA})
        with opener.open(req, timeout=20) as r:
            final = r.geturl()
        if final and "login" not in final and final != url:
            log("expand", url[:50], "->", final[:80])
            return normalize_xhs(final)
    except Exception as e:
        log("expand fail", type(e).__name__)
    return url


def normalize_xhs(url):
    """小红书：/discovery/item/ 归一成 /explore/，只留 xsec_token
    （多余分享参数不影响，但路径统一后 yt-dlp 匹配更稳）"""
    try:
        p = urlparse(url)
    except Exception:
        return url
    if "xiaohongshu.com" not in (p.hostname or ""):
        return url
    m = re.search(r"/(?:explore|discovery/item)/([0-9a-f]{24})", p.path)
    if not m:
        return url
    qs = parse_qs(p.query or "")
    tok = (qs.get("xsec_token") or [""])[0]
    if not tok:
        return url
    src = (qs.get("xsec_source") or ["app_share"])[0]
    return ("https://www.xiaohongshu.com/explore/%s?xsec_token=%s&xsec_source=%s"
            % (m.group(1), quote(tok, safe=""), quote(src, safe="")))


AUDIO_HOSTS = ("music.163.com", "y.qq.com", "kugou.com", "kuwo.cn",
               "ximalaya.com", "xiaoyuzhoufm.com", "dedao.cn")


def is_audio_only(url):
    """音频平台：能下，但没画面可裁可糊"""
    try:
        host = (urlparse(url).hostname or "").lower().replace("www.", "")
    except Exception:
        return False
    return any(host == h or host.endswith("." + h) for h in AUDIO_HOSTS)


def parse_url(url):
    """yt-dlp -J 拿元信息"""
    cmd = [YTDLP, "-J", "--no-playlist", "--no-warnings",
           "--socket-timeout", "40", "--retries", "5"] + net_args(url) + [url]
    p = run(cmd, timeout=150)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip().splitlines()
        msg = err[-1] if err else "解析失败"
        return None, msg
    try:
        info = json.loads(p.stdout)
    except Exception as e:
        return None, "解析结果不是合法 JSON: %s" % e

    # 播放列表/合集兜底取第一条
    if info.get("_type") == "playlist" and info.get("entries"):
        info = info["entries"][0] or info

    return {
        "id": info.get("id") or "",
        "title": info.get("title") or "未命名视频",
        "duration": float(info.get("duration") or 0),
        "width": int(info.get("width") or 0),
        "height": int(info.get("height") or 0),
        "uploader": info.get("uploader") or info.get("channel") or "",
        "thumbnail": info.get("thumbnail") or "",
        "extractor": info.get("extractor_key") or info.get("extractor") or "",
        "audio_only": is_audio_only(url),
        "webpage": info.get("webpage_url") or url,
    }, None


# ---------------- 下载 ----------------
PROG_RE = re.compile(r"\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.]+\w+)(?:\s+at\s+([\d.]+\w+/s))?(?:\s+ETA\s+([\d:]+))?")
MERGE_RE = re.compile(r"\[Merger\]")


def download_worker(tid, url):
    got = SEM_DOWNLOAD.acquire(timeout=180)
    if not got:
        task_set(tid, status="error", error="服务器忙，稍后再试")
        return
    try:
        _download_worker(tid, url)
    finally:
        SEM_DOWNLOAD.release()


def _download_worker(tid, url):
    task_set(tid, status="downloading", percent=0.0, speed="", eta="", error="")

    # 抖音：浏览器已经给了直链，直接 HTTP 下载（带 Referer）
    t = task_get(tid)
    direct = t.get("direct") or ""
    if direct:
        path = os.path.join(MEDIA, tid + ".mp4")
        try:
            task_set(tid, speed="直链下载中")
            n = fetch_direct(direct, path)
            log("direct download", tid, n, "bytes")
        except Exception as e:
            task_set(tid, status="error",
                     error="抖音直链下载失败（%s），链接可能已过期，重新解析一次"
                           % type(e).__name__)
            return
        if n > MAX_SIZE:
            try:
                os.remove(path)
            except Exception:
                pass
            task_set(tid, status="error",
                     error="视频太大（超过 %d MB）" % (MAX_SIZE // 1048576))
            return
        info = probe(path)          # 浏览器给的尺寸是 0，这里用 ffprobe 补
        rel = os.path.basename(path)
        task_set(tid, status="done", percent=100.0, speed="", eta="", file=rel,
                 size=os.path.getsize(path), duration=info.get("duration") or 0,
                 width=info.get("width") or 0, height=info.get("height") or 0)
        log("download done", tid, rel, info.get("width"), "x", info.get("height"))
        cache_put(tid)
        return

    tmpl = os.path.join(MEDIA, tid + ".%(ext)s")
    cmd = [YTDLP,
           "-f", "bv*[vcodec^=avc1]+ba[ext=m4a]/b[vcodec^=avc1]/"
                 "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b",
           "--no-playlist", "--merge-output-format", "mp4",
           "--ffmpeg-location", FFBIN,
           "--newline", "--no-warnings",
           # 慢链路兜底：分片并发拉，单片超时给到 60 秒，
           # 断了多试几次。B站 CDN 实测只有 400–700KB/s，
           # 25 秒 socket 超时 + 单线程必然中途断。
           "--socket-timeout", "60",
           "--retries", "10", "--fragment-retries", "10",
           "--concurrent-fragments", "4",
           "--http-chunk-size", "10M"] + net_args(url) + [
           "-o", tmpl, url]
    log("download start", tid)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=sub_env(), encoding="utf-8", errors="replace",
                            bufsize=1)
    tail = []
    for line in proc.stdout:
        line = line.rstrip("\r\n")
        if not line:
            continue
        tail.append(line)
        if len(tail) > 25:
            tail.pop(0)
        m = PROG_RE.search(line)
        if m:
            task_set(tid, percent=float(m.group(1)),
                     speed=m.group(3) or "", eta=m.group(4) or "")
            continue
        if MERGE_RE.search(line):
            task_set(tid, status="merging", percent=100.0)
    proc.wait()
    if proc.returncode != 0:
        err = "\n".join(tail[-6:]) or "下载失败"
        task_set(tid, status="error", error=err)
        log("download fail", tid, err[-200:])
        return

    path = find_output(tid)
    if not path:
        task_set(tid, status="error", error="下载结束但没找到产物文件")
        return

    # 体积上限：拦在转码之前，别让一个大文件把存储和 CPU 都占了
    if os.path.getsize(path) > MAX_SIZE:
        try:
            os.remove(path)
        except Exception:
            pass
        task_set(tid, status="error",
                 error="视频太大（超过 %d MB），换个短一点的" % (MAX_SIZE // 1048576))
        log("too large", tid)
        return

    info = probe(path)
    vcodec = (info.get("vcodec") or "").lower()
    acoustic = (not vcodec) and bool(info.get("acodec"))
    need_trans = (not acoustic) and vcodec not in ("h264", "avc1")

    # 纯音频（网易云/QQ音乐/喜马拉雅）：容器里只有音轨。
    # 一是扩展名改成 .m4a（否则手机上是个叫 .mp4 的音频，相册当视频处理）；
    # 二是绝不能进转码分支——ffmpeg 会把它重新封装成 .mp4，白花时间还改回错扩展名。
    if acoustic:
        m4a = os.path.join(MEDIA, tid + ".m4a")
        try:
            os.replace(path, m4a)
            path = m4a
            log("audio only", tid, info.get("acodec"))
        except Exception:
            pass

    if need_trans:
        task_set(tid, status="transcoding", percent=100.0,
                 speed="转码中（%s→h264，手机兼容）" % (vcodec or "未知"), eta="")
        # 注意：输入输出不能是同一个路径，ffmpeg 会直接拒绝
        fixed = os.path.join(MEDIA, tid + "_fix.mp4")
        r = run([FFMPEG, "-y", "-i", path, "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                 "-movflags", "+faststart", fixed], timeout=3600)
        if r.returncode == 0 and os.path.exists(fixed):
            try:
                os.remove(path)
            except Exception:
                pass
            path = os.path.join(MEDIA, tid + ".mp4")
            os.replace(fixed, path)
            info = probe(path)
            log("transcode ok", tid, "->", vcodec, "=>", info.get("vcodec"))
        else:
            log("transcode fail", (r.stderr or "")[-600:])
            try:
                os.remove(fixed)
            except Exception:
                pass

    rel = os.path.basename(path)
    task_set(tid, status="done", percent=100.0, speed="", eta="", file=rel,
             size=os.path.getsize(path), duration=info.get("duration") or 0,
             width=info.get("width") or 0, height=info.get("height") or 0)
    log("download done", tid, rel, info.get("width"), "x", info.get("height"))
    cache_put(tid)


# ---------------- 裁切 ----------------
def even(n):
    n = int(n)
    return n - (n % 2)


def crop_worker(cid, src, params):
    got = SEM_FFMPEG.acquire(timeout=180)
    if not got:
        task_set(cid, status="error", error="服务器忙，稍后再试")
        return
    try:
        _crop_worker(cid, src, params)
    finally:
        SEM_FFMPEG.release()


def _crop_worker(cid, src, params):
    task_set(cid, status="cropping", percent=0.0, error="")
    mode = (params.get("mode") or "crop").strip()
    if mode not in ("crop", "blur"):
        mode = "crop"
    info = probe(src)
    W, H = info.get("width") or 0, info.get("height") or 0
    dur = info.get("duration") or 0.0
    if not W or not H:
        task_set(cid, status="error", error="读不到源视频尺寸")
        return

    x = max(0, even(min(int(params.get("x") or 0), W - 2)))
    y = max(0, even(min(int(params.get("y") or 0), H - 2)))
    w = even(min(int(params.get("w") or W), W - x))
    h = even(min(int(params.get("h") or H), H - y))
    if w < 8 or h < 8:
        task_set(cid, status="error", error="区域太小")
        return

    start = float(params.get("start") or 0)
    end = float(params.get("end") or 0)
    start = max(0.0, min(start, dur if dur else start))
    if end <= start or (dur and end > dur):
        end = dur or 0

    out = os.path.join(MEDIA, cid + ".mp4")
    cmd = [FFMPEG, "-y"]
    if start > 0.05:
        cmd += ["-ss", "%.3f" % start]
    cmd += ["-i", src]
    if end and end > start:
        cmd += ["-t", "%.3f" % (end - start)]

    if mode == "blur":
        # 保留原构图，只把水印区域糊掉：split 出两路，局部 boxblur 后盖回去
        # 注意：boxblur 半径必须严格小于区域短边的 1/4（yuv420p 色度平面限制），
        # 取到等号会报 "Invalid chroma_param radius value N, must be < N"
        radius = min(60, max(1, int(min(w, h) / 4) - 1))
        fc = ("[0:v]split[a][b];[b]crop=%d:%d:%d:%d,boxblur=%d:3[bz];"
              "[a][bz]overlay=%d:%d:format=auto" % (w, h, x, y, radius, x, y))
        cmd += ["-filter_complex", fc,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", out]
    else:
        vf = "crop=%d:%d:%d:%d" % (w, h, x, y)
        cmd += ["-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", out]

    log("process", cid, mode, "%dx%d+%d+%d" % (w, h, x, y), "%.1f-%.1f" % (start, end))
    r = run(cmd, timeout=3600)
    if r.returncode != 0 or not os.path.exists(out):
        task_set(cid, status="error", error=(r.stderr or "")[-800:] or "ffmpeg 失败")
        return
    oi = probe(out)
    task_set(cid, status="done", percent=100.0, file=os.path.basename(out),
             size=os.path.getsize(out), width=oi.get("width") or 0,
             height=oi.get("height") or 0, duration=oi.get("duration") or 0)
    log("process done", cid)


# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def client_ip(self):
        for h in ("X-Real-IP", "X-Forwarded-For"):
            v = self.headers.get(h)
            if v:
                return v.split(",")[0].strip()
        return self.client_address[0]

    def authed(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME:
                return session_valid(v)
        return False

    def _set_cookie_redirect(self, path):
        secure = ""
        if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https":
            secure = "; Secure"
        cookie = "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s" % (
            COOKIE_NAME, session_value(), SESSION_DAYS * 86400, secure)
        self.send_response(302)
        self.send_header("Set-Cookie", cookie)
        self.send_header("Location", path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if getattr(self, "_head_only", False):
            return
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    # ---- 静态文件 + Range ----
    def serve_file(self, path, download_name=None):
        if not os.path.isfile(path):
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        code = 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
                end = min(end, size - 1)
                if start > end:
                    self._send(416, "range not satisfiable", "text/plain")
                    return
                code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Access-Control-Allow-Origin", "*")
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        if download_name:
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''" + download_name)
        self.end_headers()
        if getattr(self, "_head_only", False):
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,HEAD,OPTIONS")
        self.end_headers()

    def do_HEAD(self):
        # iOS Safari 播视频前会先 HEAD 探测，不支持就放不出画面
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def do_GET(self):
        u = urlparse(self.path)
        p = unquote(u.path)
        qs = parse_qs(u.query or "")
        ttl = max(5, TTL_MIN) * 60
        ip = self.client_ip()

        # 接口限流（媒体流不计数，否则拖进度条就把配额吃光）
        if p.startswith("/api/") and not rate_request(ip):
            self._json({"ok": False, "error": "太频繁了，慢一点"}, 429)
            return

        # 扫码/带码访问：校验通过就发 cookie，然后把 k 从地址栏抹掉
        if p in ("/", "/index.html", "") and "k" in qs:
            k = (qs.get("k") or [""])[0]
            if hmac.compare_digest(k, TOKEN):
                log("auth ok", ip)
                # 用相对路径：反代到 /videowm/ 时也能正确跳回子路径
                self._set_cookie_redirect("./")
            else:
                log("auth fail", ip)
                self._send(401, login_page("访问码不对，再看一眼电脑上的二维码"),
                           "text/html; charset=utf-8")
            return

        if not self.authed():
            # 带签名的下载链接放行——复制到别的浏览器/微信里也能下
            if p.startswith("/download/"):
                name = os.path.basename(p)
                if dl_sig_ok(name, (qs.get("s") or [""])[0], (qs.get("e") or [""])[0]):
                    self.serve_file(os.path.join(MEDIA, name), download_name=name)
                    return
            self._send(401, login_page("需要先扫电脑上的二维码"), "text/html; charset=utf-8")
            return

        if p in ("/", "/index.html"):
            idx = os.path.join(BASE, "index.html")
            self.serve_file(idx)
            return

        if p.startswith("/media/"):
            name = os.path.basename(p)
            self.serve_file(os.path.join(MEDIA, name))
            return

        if p.startswith("/download/"):
            name = os.path.basename(p)
            fn = os.path.join(MEDIA, name)
            self.serve_file(fn, download_name=name)
            return

        if p.startswith("/api/progress/"):
            tid = os.path.basename(p)
            t = task_get(tid)
            if not t:
                self._json({"ok": False, "error": "任务不存在"}, 404)
                return
            resp = {k: v for k, v in t.items() if k not in ("url", "direct")}
            if resp.get("status") == "done" and resp.get("file"):
                resp["dl"] = dl_link(resp["file"])   # 带签名的下载地址
            self._json({"ok": True, **resp})
            return

        if p.startswith("/api/fileinfo/"):
            name = os.path.basename(p)
            fp = os.path.join(MEDIA, name)
            if not os.path.isfile(fp):
                self._json({"ok": False, "error": "文件不存在或已过期"}, 404)
                return
            left = ttl - (time.time() - os.path.getmtime(fp))
            self._json({"ok": True, "expiresIn": max(0, int(left)),
                        "ttl": ttl, "size": os.path.getsize(fp),
                        "dl": dl_link(name)})
            return

        if p == "/api/history":
            items = []
            for f in sorted(glob.glob(os.path.join(MEDIA, "*.mp4")),
                            key=os.path.getmtime, reverse=True)[:30]:
                items.append({"file": os.path.basename(f),
                              "size": os.path.getsize(f),
                              "mtime": int(os.path.getmtime(f))})
            self._json({"ok": True, "items": items})
            return

        if p == "/api/health":
            self._json({"ok": True, "port": PORT, "media": MEDIA})
            return

        # 同目录静态资源：只放行前端两个文件
        # （server.py / *.zip / qr.png / install-termux.sh 一律不对外，避免源码泄露）
        if p.count("/") == 1 and not p.startswith("/api"):
            name = os.path.basename(p)
            if name in ("app.js", "index.html"):
                fp = os.path.abspath(os.path.join(BASE, name))
                if fp.startswith(os.path.abspath(BASE)) and os.path.isfile(fp):
                    self.serve_file(fp)
                    return

        self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        ip = self.client_ip()

        if not self.authed():
            self._json({"ok": False, "error": "未授权，请重新扫码"}, 401)
            return
        if not rate_request(ip):
            self._json({"ok": False, "error": "太频繁了，慢一点"}, 429)
            return

        if p == "/api/parse":
            data = self._body()
            url = (data.get("url") or "").strip()
            if not url.startswith("http"):
                self._json({"ok": False, "error": "链接不合法"})
                return
            bad = url_allowed(url)
            if bad:
                log("block url", ip, url[:80], bad)
                self._json({"ok": False, "error": bad})
                return
            limited = rate_job(ip)
            if limited:
                log("rate limited", ip)
                self._json({"ok": False, "error": limited}, 429)
                return
            url = expand_short(url)
            if is_douyin(url):
                url = normalize_douyin(url)

            # 缓存命中就不再解析下载了（抖音要开浏览器，重复跑很浪费）
            _ck = cache_key(url)
            _hit = cache_get(_ck)
            if _hit:
                tid0 = _hit["task"]
                fpath = os.path.join(MEDIA, _hit["file"])
                try:
                    sz = os.path.getsize(fpath)
                except Exception:
                    sz = 0
                if sz > 0:
                    task_set(tid0, status="done", percent=100.0, speed="", eta="",
                             file=_hit["file"], size=sz, url=url,
                             title=_hit.get("title") or "", duration=_hit.get("duration") or 0,
                             width=_hit.get("width") or 0, height=_hit.get("height") or 0,
                             uploader=_hit.get("uploader") or "",
                             extractor=_hit.get("extractor") or "",
                             created=_hit.get("created") or time.time())
                    log("cache hit", _ck, _hit["file"])
                    self._json({"ok": True, "task": tid0, "cached": True,
                                "title": _hit.get("title") or "",
                                "duration": _hit.get("duration") or 0,
                                "width": _hit.get("width") or 0,
                                "height": _hit.get("height") or 0,
                                "uploader": _hit.get("uploader") or "",
                                "extractor": _hit.get("extractor") or "",
                                "audio_only": bool(_hit.get("audio_only"))
                                              or is_audio_only(url)})
                    return

            # 抖音直接走浏览器（yt-dlp 的 extractor 缺 a_bogus 签名）
            if is_douyin(url):
                info, err = browser_extract(url)
            else:
                info, err = parse_url(url)
                # yt-dlp 不行但可以靠浏览器兜底的平台
                if not info and browser_fallback_ok(url):
                    log("fallback to browser", url[:60])
                    i2, e2 = browser_extract(url)
                    if i2:
                        info, err = i2, None
            if err:
                self._json({"ok": False, "error": err})
                return
            if info["duration"] and info["duration"] > MAX_DURATION:
                self._json({"ok": False,
                            "error": "视频太长（%.0f 分钟），上限 %d 分钟"
                                     % (info["duration"] / 60.0, MAX_DURATION // 60)})
                return
            tid = uuid.uuid4().hex[:12]
            task_set(tid, status="queued", percent=0.0, url=url, title=info["title"],
                     duration=info["duration"], src_width=info["width"],
                     src_height=info["height"], uploader=info["uploader"],
                     extractor=info["extractor"], created=time.time(),
                     audio_only=bool(info.get("audio_only")),
                     direct=info.get("_direct") or "")
            threading.Thread(target=download_worker, args=(tid, url), daemon=True).start()
            self._json({"ok": True, "task": tid,
                        **{k: v for k, v in info.items() if not k.startswith("_")}})
            return

        if p == "/api/crop":
            d = self._body()
            limited = rate_job(ip)
            if limited:
                log("rate limited", ip)
                self._json({"ok": False, "error": limited}, 429)
                return
            tid = (d.get("task") or "").strip()
            t = task_get(tid)
            src = os.path.join(MEDIA, t.get("file") or "")
            if not t or t.get("status") != "done" or not os.path.isfile(src):
                self._json({"ok": False, "error": "源文件还没准备好"})
                return
            cid = "c" + uuid.uuid4().hex[:11]
            task_set(cid, status="queued", percent=0.0)
            threading.Thread(target=crop_worker, args=(cid, src, d), daemon=True).start()
            self._json({"ok": True, "task": cid})
            return

        if p == "/api/delete":
            d = self._body()
            n = os.path.basename(d.get("file") or "")
            f = os.path.join(MEDIA, n)
            if n and os.path.isfile(f):
                try:
                    os.remove(f)
                except Exception:
                    pass
            self._json({"ok": True})
            return

        self._send(404, "not found", "text/plain; charset=utf-8")


def local_ips():
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # 家用/办公局域网（192.168.x）排前面，VPN/虚拟网卡（10.x 之类）排后面
    def rank(ip):
        if ip.startswith("192.168."):
            return 0
        if ip.startswith("172."):
            return 2
        return 1
    return sorted(ips, key=rank)


def port_in_use(port):
    """Windows 上 SO_REUSEADDR 允许多进程绑同一端口，必须自己挡一道"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.6)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def is_termux():
    return "com.termux" in os.environ.get("PREFIX", "") or \
        os.path.isdir("/data/data/com.termux")


def main():
    if not YTDLP or not os.path.isfile(YTDLP):
        print("找不到 yt-dlp:", YTDLP or "(PATH 里没有)")
        print("  Windows: pip install -U yt-dlp")
        print("  Termux : pip install -U yt-dlp")
        sys.exit(1)
    if not FFMPEG or not os.path.isfile(FFMPEG):
        print("找不到 ffmpeg:", FFMPEG or "(PATH 里没有)")
        print("  Termux : pkg install ffmpeg")
        sys.exit(1)

    if port_in_use(PORT):
        print("端口 %d 已被占用——可能已经有一个服务在跑了。" % PORT)
        if IS_WIN:
            print("先关掉旧的：netstat -ano | findstr :%d   然后 taskkill /F /PID <pid>" % PORT)
        else:
            print("先关掉旧的：pkill -f server.py   或   lsof -i :%d" % PORT)
        sys.exit(2)

    ThreadingHTTPServer.allow_reuse_address = False
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=cleanup_worker, daemon=True).start()
    termux = is_termux()
    ips = [] if termux else local_ips()

    # 对外打开地址：优先 VIDEOWM_PUBLIC（外网反代），否则按本机地址拼
    public = (os.environ.get("VIDEOWM_PUBLIC") or "").rstrip("/")

    def open_url(host):
        if public:
            return public + "/?k=" + TOKEN
        return "http://%s:%d%s/?k=%s" % (host, PORT, BASE_PATH, TOKEN)

    print("=" * 58)
    print("  视频去水印 · 手机端预览与裁切")
    print("=" * 58)
    if termux:
        print("  方式:   手机本地运行（Termux）—— 不需要电脑")
        print("  打开:   %s" % open_url("127.0.0.1"))
    else:
        print("  本机:   http://127.0.0.1:%d" % PORT)
        for i, ip in enumerate(ips):
            print("  手机:   http://%s:%d%s" % (ip, PORT, "   <-- 同一 WiFi 下用这个" if i == 0 else ""))
    print("  存放:   %s" % MEDIA)
    print("  引擎:   %s" % YTDLP)
    print("          %s" % FFMPEG)
    print("  访问码: %s   ← 扫码就不用输" % TOKEN)
    print("  停止:   Ctrl+C")
    print("=" * 58)

    if termux:
        try:
            import segno
            qr = segno.make(open_url("127.0.0.1") if not public else public + "/?k=" + TOKEN)
            qr.terminal(compact=True, border=1)
            print("=" * 58)
        except Exception:
            pass
    elif ips:
        url = open_url(ips[0])
        try:
            import segno
            qr = segno.make(url)
            qr.save(os.path.join(BASE, "qr.png"), scale=6, border=2)
            print("  手机扫码登录（也存了 %s 下的 qr.png）：" % BASE)
            print("  %s\n" % url)
            qr.terminal(compact=True, border=1)
        except Exception:
            print("  装个 segno 就能在终端显示二维码： pip install segno")
            print("  手机打开：%s" % url)
        print("\n  手机连不上？多半是防火墙挡了，管理员命令行执行：")
        print('  netsh advfirewall firewall add rule name="videowm-%d" '
              'dir=in action=allow protocol=TCP localport=%d' % (PORT, PORT))
        print("=" * 58)

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
