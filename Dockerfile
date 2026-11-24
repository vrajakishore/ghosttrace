# GhostTrace prototype: one small image, standard library only.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GT_DB=/data/ghosttrace.db

WORKDIR /app
COPY config ./config
COPY services ./services

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /data /app/docs/results \
 && chown -R app:app /data /app/docs
USER app

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health', timeout=2)" || exit 1

CMD ["python", "-m", "services.api"]
