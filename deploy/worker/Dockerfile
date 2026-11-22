FROM ubuntu:24.04

# Avoid interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install essential compilers, runtimes, build tools, and libseccomp
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    clang \
    gcc \
    libseccomp-dev \
    libseccomp2 \
    openjdk-21-jdk-headless \
    python3 \
    python3-pip \
    python3-setuptools \
    python3-wheel \
    nodejs \
    ruby-full \
    bash \
    sqlite3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt ./
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Copy sandbox C harness and build native runner
COPY sandbox/ ./sandbox/
RUN make -C sandbox

# Copy engine and proto
COPY engine/ ./engine/
COPY proto/ ./proto/

# Secure execution user setup
RUN useradd -m -u 1000 -U pes2 && \
    mkdir -p /tmp/pes2-work && \
    chown -R pes2:pes2 /app /tmp/pes2-work

USER pes2

EXPOSE 50051

ENV PES2_HOST=0.0.0.0 \
    PES2_PORT=50051 \
    PES2_ALLOW_INTERNAL_NETWORKING=1

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python3 -c "import socket; s = socket.socket(); s.connect(('127.0.0.1', 50051)); s.close()" || exit 1

CMD ["python3", "-m", "engine.daemon.server", "--host", "0.0.0.0", "--port", "50051", "--allow-internal-network"]
