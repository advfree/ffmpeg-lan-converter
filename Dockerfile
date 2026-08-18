FROM ubuntu:24.04

LABEL org.opencontainers.image.title="FFmpeg LAN Converter" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.licenses="MIT"

ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=10888 MEDIA_ROOT=/media DATA_ROOT=/data
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg python3 ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --home-dir /app ffmpegweb

WORKDIR /app
COPY --chown=ffmpegweb:ffmpegweb app.py /app/app.py
COPY --chown=ffmpegweb:ffmpegweb static /app/static
RUN mkdir -p /data /media /root/data \
    && chown -R ffmpegweb:ffmpegweb /data /media \
    && chmod 711 /root /root/data

USER ffmpegweb
EXPOSE 10888
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10888/healthz', timeout=3)"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/app/app.py"]
