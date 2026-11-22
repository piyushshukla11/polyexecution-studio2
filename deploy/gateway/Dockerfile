FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and protobuf definitions
COPY engine/ ./engine/
COPY proto/ ./proto/

# Create unprivileged application user
RUN useradd -m -u 1000 -U pes2 && \
    chown -R pes2:pes2 /app

USER pes2

EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    PES2_GATEWAY_HOST=0.0.0.0 \
    PES2_GATEWAY_PORT=8000 \
    PES2_DAEMON_ADDR=worker-daemon:50051

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

CMD ["python", "-m", "engine.gateway.server"]
