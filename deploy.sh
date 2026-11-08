#!/usr/bin/env bash
# 一键部署到 Linux 服务器（systemd 用户级服务）
#
# 用法：sudo mkdir -p /opt/video-downloader-web && sudo chown $USER /opt/video-downloader-web
#       cd /opt/video-downloader-web && bash deploy.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "==> 检查依赖"
command -v python3 >/dev/null || { echo "缺 python3"; exit 1; }
command -v ffmpeg  >/dev/null || { echo "缺 ffmpeg：apt install ffmpeg"; exit 1; }

echo "==> 建虚拟环境"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

echo "==> 生成访问码"
if [ ! -f .token ]; then
  head -c 9 /dev/urandom | base64 | tr -d '/+=' | head -c 12 > .token
  echo "    访问码已写入 .token：$(cat .token)"
else
  echo "    沿用已有访问码：$(cat .token)"
fi

echo "==> 写启动脚本"
cat > start.sh <<'INNER'
#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VIDEOWM_MEDIA="$DIR/media"
export VIDEOWM_FFMPEG="$(command -v ffmpeg)"
export VIDEOWM_FFPROBE="$(command -v ffprobe)"
export VIDEOWM_YTDLP="$DIR/.venv/bin/yt-dlp"
export VIDEOWM_PORT="${VIDEOWM_PORT:-8848}"
export VIDEOWM_BASE="${VIDEOWM_BASE:-/}"
export VIDEOWM_TOKEN="${VIDEOWM_TOKEN:-$(cat "$DIR/.token")}"
export VIDEOWM_TTL_MIN="${VIDEOWM_TTL_MIN:-0}"
mkdir -p "$VIDEOWM_MEDIA"
cd "$DIR" || exit 1
exec "$DIR/.venv/bin/python" -u server.py
INNER
chmod +x start.sh

echo "==> 装 systemd 用户服务"
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/videowm.service <<INNER
[Unit]
Description=Video de-watermark web service
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$DIR/start.sh
Restart=always
RestartSec=3
KillMode=control-group

[Install]
WantedBy=default.target
INNER

systemctl --user daemon-reload
systemctl --user enable --now videowm
sleep 3
systemctl --user --no-pager status videowm | head -8

echo
echo "==> 完成。访问 http://127.0.0.1:8848/?k=$(cat .token)"
echo "    公网映射请另配 nginx 反代或内网穿透"
