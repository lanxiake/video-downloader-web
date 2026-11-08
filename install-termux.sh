#!/usr/bin/env bash
# 视频去水印 · Android Termux 一键安装
# 用法：
#   1) 有电脑可访问：  bash install-termux.sh http://192.168.0.9:8848
#   2) 没有电脑：      bash install-termux.sh      然后手动把三个文件拷进 ~/videowm
set -e

APP="$HOME/videowm"
MEDIA="$HOME/storage/shared/VideoWM"
SRC="${1:-}"

echo "=============================================="
echo "  视频去水印 · Termux 安装"
echo "=============================================="

echo
echo "[1/5] 安装 Python 和 ffmpeg（大概 100-200MB）"
pkg update -y >/dev/null 2>&1 || true
pkg install -y python ffmpeg

echo
echo "[2/5] 安装 yt-dlp"
# 新版 Python 是 externally-managed，必须带 --break-system-packages
pip install --break-system-packages -U yt-dlp segno 2>/dev/null \
  || pip install -U yt-dlp segno

echo
echo "[3/5] 申请存储权限（手机会弹窗，点允许）"
termux-setup-storage 2>/dev/null || true
sleep 1

echo
echo "[4/5] 创建目录"
mkdir -p "$APP" "$MEDIA"

echo
echo "[5/5] 准备程序文件"
cd "$APP"
if [ -n "$SRC" ]; then
  SRC="${SRC%/}"
  for f in server.py index.html app.js; do
    echo "  ← $f"
    curl -fsSL -o "$f" "$SRC/$f" || { echo "下载失败：$SRC/$f"; exit 1; }
  done
else
  echo "  没给下载地址。请把 server.py / index.html / app.js 放进："
  echo "  $APP"
fi

# 启动脚本
cat > "$HOME/start-videowm.sh" <<EOF
#!/usr/bin/env bash
# 启动视频去水印服务，保持屏幕常亮防止杀后台
export VIDEOWM_MEDIA="$MEDIA"
export VIDEOWM_PORT=8848
termux-wake-lock 2>/dev/null || true
cd "$APP"
trap 'termux-wake-unlock 2>/dev/null' EXIT
exec python server.py
EOF
chmod +x "$HOME/start-videowm.sh"

echo
echo "=============================================="
if [ -f "$APP/server.py" ]; then
  echo "  装好了"
  echo
  echo "  启动：  ~/start-videowm.sh"
  echo "  浏览：  手机浏览器打开 http://127.0.0.1:8848"
  echo "  产物：  $MEDIA （文件管理器里能看到）"
else
  echo "  依赖装好了，但程序文件还没到。"
  echo "  把三个文件放进 $APP 后运行 ~/start-videowm.sh"
fi
echo "=============================================="
echo
echo "提示："
echo "  · 首次 pip 装 yt-dlp 可能要 1-2 分钟"
echo "  · 手机转码比电脑慢 3-5 倍，长片耐心等"
echo "  · 想更新 yt-dlp（平台规则变了就失效）："
echo "      pip install --break-system-packages -U yt-dlp"
echo "=============================================="
