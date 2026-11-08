"""命令行入口：抓一个页面的视频流，结果以 JSON 打到 stdout。

单独做成命令行的原因：服务是常驻 daemon，直接在它进程里起 chromium
会踩到一些莫名的问题（同样代码独立进程跑就正常）。用子进程隔离后
两边环境一致，稳定得多。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cdp_grab  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "缺少 URL"}))
        return 1
    url = sys.argv[1]
    try:
        media, title, meta = cdp_grab.grab(url, timeout=40.0)
        print(json.dumps({"media": media, "title": title, "meta": meta},
                         ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": "%s: %s" % (type(e).__name__, str(e)[:120]),
                          "media": [], "title": "", "meta": {}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
