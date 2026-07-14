FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY backend/ backend/
COPY bridge/ bridge/
COPY pictochat/ pictochat/
COPY server.py ./
RUN pip install --no-cache-dir .

# 8082 = web/WebSocket, 8083 = LAN-only radio-bridge/client room bus
EXPOSE 8082 8083

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8082/users/A')" || exit 1

CMD ["pictochat-server"]
