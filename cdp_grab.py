"""用 Chromium + DevTools Protocol 抓视频流。

为什么不用 Playwright：
- Termux/Android 装不了 Playwright（它只发 x86/arm64 的桌面构建，Android 上没有对应 bundle）
- 但 Termux 有 chromium 包，它自带 --remote-debugging-port
- CDP 是标准 websocket 协议，自己发几条命令就行

这套代码在 Windows 和 Termux 上都能跑，只要能找到 chromium 可执行文件。
"""
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request

try:
    import websocket  # websocket-client
except ImportError:  # pragma: no cover
    websocket = None

# 移动 UA 是关键：实测桌面 UA 在手机壳里会被识别成功壳页（bodyLen 1304、零视频请求），
# 换 iPhone UA 后同一个页面就能正常吐出 douyinvod 流
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
      "Mobile/15E148 Safari/604.1")

# 这些 URL 片段说明是播放器素材，不是正片
NOISE = ("uuu_", "sprite", ".m3u8", "cover", "avatar", ".jpg", ".png",
         ".webp", "favicon", "logo")


def _playwright_chromium():
    """Playwright 下载的 chromium 是完整浏览器，一样能开
    --remote-debugging-port，可以当系统 chromium 用。

    为什么需要这个兜底：服务器上经常只装了 Playwright、没装 apt 的
    chromium。之前 find_chromium 只查 PATH，结果 Playwright 明明装好了
    却报「没找到 chromium」——抖音直接不能用。"""
    base = os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright")
    if not os.path.isdir(base):
        return ""
    import glob
    for pat in ("chromium-*/chrome-linux/chrome",
                "chromium-*/chrome-linux64/chrome",
                "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
                "chromium-*/chrome-win/chrome.exe"):
        hits = sorted(glob.glob(os.path.join(base, pat)))
        if hits:
            return hits[-1]        # 版本号最大的那个
    return ""


def find_chromium():
    """在各平台上找 chromium 可执行文件"""
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    pw = _playwright_chromium()
    if pw:
        return pw
    candidates = [
        "/data/data/com.termux/files/usr/bin/chromium",   # Termux
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p

    return ""


def _free_port():
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _profile_dir(tag=""):
    """每次用独立 profile——固定目录会被上一个没退干净的实例锁住。
    Android 上 /tmp 不存在，必须逐个试到能写为止。"""
    cands = [
        os.environ.get("TMPDIR"),
        os.environ.get("TEMP"),
        os.path.join(os.path.expanduser("~"), ".cache"),
        "/data/data/com.termux/files/usr/tmp",
        "/tmp",
    ]
    for base in cands:
        if not base:
            continue
        try:
            d = os.path.join(base, "vwm-cdp-%s" % (tag or "default"))
            os.makedirs(d, exist_ok=True)
            # 确认真的可写
            probe = os.path.join(d, ".w")
            with open(probe, "w") as f:
                f.write("1")
            os.remove(probe)
            return d
        except Exception:
            continue
    raise RuntimeError("找不到可写的临时目录")


def _launch(binp, url, port):
    # 参数照抄现场验证成功的那一版：
    # 移动 UA 千万别再配 --window-size=1280,800 —— UA 和视口对不上会被识别成壳页
    args = [
        binp,
        "--headless=new",
        "--remote-debugging-port=%d" % port,
        "--remote-allow-origins=*",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--mute-audio",
        "--autoplay-policy=no-user-gesture-required",
        "--user-agent=" + UA,
        "--user-data-dir=" + _profile_dir(str(port)),
        url,
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def _wait_devtools(port, timeout=25.0):
    """等 devtools 端口就绪，返回 tab 列表"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/json" % port, timeout=1.5) as r:
                tabs = json.loads(r.read().decode("utf-8", "replace"))
            page = [t for t in tabs if t.get("type") == "page"]
            if page:
                return page
        except Exception:
            pass
        time.sleep(0.35)
    return []


def _is_video_url(url, content_type):
    low = url.lower()
    # 先排除素材，别被 content-type 绕过去（uuu_265.mp4 这类也是 video/mp4）
    if any(n in low for n in NOISE):
        return False
    ct = (content_type or "").lower()
    if ct.startswith("video/"):
        return True
    return (".mp4" in low or "douyinvod" in low or "/video/tos/" in low)


def grab(url, timeout=45.0, wait_video=25.0):
    """打开页面，收集视频流地址。返回 (media_list, title, meta_dict)

    实现照抄了现场验证成功的那版：只发 Network.enable / Page.enable，
    不多发 Emulation 之类的命令（多发反而收不到事件）。
    """
    binp = find_chromium()
    if not binp:
        return [], "", {"error": "没找到 chromium"}
    if websocket is None:
        return [], "", {"error": "缺 websocket-client 库"}

    port = _free_port()
    proc = None
    media, seen = [], set()
    title, meta = "", {}
    ws = None
    ev_count = 0
    try:
        proc = _launch(binp, url, port)
        tabs = _wait_devtools(port)
        if not tabs:
            return [], "", {"error": "devtools 端口没起来",
                            "diag": {"bin": binp, "port": port}}
        tab = tabs[0]
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"],
                                         timeout=5, max_size=None)

        def send(i, method, params=None):
            ws.send(json.dumps({"id": i, "method": method,
                                "params": params or {}}))

        send(1, "Network.enable")
        send(2, "Page.enable")

        end = time.time() + timeout
        hit_at = 0.0
        next_id = 20
        next_poke = time.time() + 6.0
        while time.time() < end:
            try:
                ws.settimeout(1.0)
                raw = ws.recv()
            except Exception:
                raw = None
            if raw:
                try:
                    msg = json.loads(raw)
                except Exception:
                    msg = None
                if msg:
                    ev_count += 1
                    if msg.get("method") == "Network.responseReceived":
                        r = msg.get("params", {}).get("response", {})
                        u = r.get("url", "")
                        ct = r.get("mimeType", "")
                        if _is_video_url(u, ct) and u not in seen:
                            seen.add(u)
                            media.append(u)
                            hit_at = time.time()
            # 还没出流就持续戳播放（只戳一次经常赶不上播放器初始化）
            if not media and time.time() >= next_poke:
                send(next_id, "Runtime.evaluate", {
                    "expression": (
                        "(()=>{const v=document.querySelector('video');"
                        "if(!v)return 0;v.muted=true;v.preload='auto';"
                        "v.currentTime=v.currentTime;"
                        "const p=v.play&&v.play();if(p&&p.catch)p.catch(()=>{});"
                        "return 1})()"),
                    "returnByValue": True})
                next_id += 1
                next_poke = time.time() + 4.0
            if media and time.time() - hit_at > 1.5:
                break

        send(next_id, "Runtime.evaluate", {
            "expression": (
                "(()=>{const v=document.querySelector('video');"
                "return JSON.stringify({t:(document.title||'')"
                ".replace(/\s*-\s*(抖音|西瓜视频|快手)$/,''),"
                "w:v?v.videoWidth:0,h:v?v.videoHeight:0,"
                "d:v?(isNaN(v.duration)?0:v.duration):0})})()"),
            "returnByValue": True})
        nid = next_id
        next_id += 1
        meta_end = time.time() + 5
        while time.time() < meta_end:
            try:
                ws.settimeout(0.8)
                msg = json.loads(ws.recv())
            except Exception:
                continue
            if msg.get("id") == nid:
                raw_v = (msg.get("result", {}).get("result", {}) or {}).get("value")
                if raw_v:
                    try:
                        d = json.loads(raw_v)
                        title = (d.get("t") or "").strip()
                        meta = {"w": d.get("w") or 0, "h": d.get("h") or 0,
                                "dur": d.get("d") or 0}
                    except Exception:
                        pass
                break
    except Exception as e:
        meta["error"] = "%s: %s" % (type(e).__name__, str(e)[:80])
    finally:
        try:
            if ws:
                ws.close()
        except Exception:
            pass
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    meta["diag"] = {"events": ev_count, "media": len(media)}
    return media, title, meta
