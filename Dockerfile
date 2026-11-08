FROM python:3.11-slim

# ffmpeg 用于转码/裁切/打码
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py cdp_grab.py cdp_cli.py app.js index.html ./

ENV VIDEOWM_MEDIA=/data/media \
    VIDEOWM_PORT=8848 \
    VIDEOWM_FFMPEG=/usr/bin/ffmpeg \
    VIDEOWM_FFPROBE=/usr/bin/ffprobe \
    VIDEOWM_YTDLP=/usr/local/bin/yt-dlp \
    PYTHONUNBUFFERED=1

VOLUME ["/data/media"]
EXPOSE 8848

CMD ["python", "-u", "server.py"]
