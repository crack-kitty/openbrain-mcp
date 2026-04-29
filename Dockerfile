FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 --shell /usr/sbin/nologin openbrain

COPY --chown=openbrain:openbrain requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=openbrain:openbrain src/ ./src/
COPY --chown=openbrain:openbrain schema/ ./schema/

ENV PYTHONPATH=/app/src \
    OPENBRAIN_HOST=0.0.0.0 \
    OPENBRAIN_PORT=8080

USER openbrain

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["python", "-m", "openbrain_mcp"]
