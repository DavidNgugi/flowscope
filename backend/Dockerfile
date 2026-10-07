FROM python:3.13-slim

ARG DENO_VERSION=2.3.7

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    unzip \
    && case "$(dpkg --print-architecture)" in \
        amd64) DENO_ARCH=x86_64 ;; \
        arm64) DENO_ARCH=aarch64 ;; \
        *) echo "Unsupported architecture for Deno" >&2; exit 1 ;; \
    esac \
    && curl -fsSLo /tmp/deno.zip \
        "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${DENO_ARCH}-unknown-linux-gnu.zip" \
    && unzip /tmp/deno.zip -d /usr/local/bin \
    && chmod 0755 /usr/local/bin/deno \
    && rm /tmp/deno.zip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /app/data/media

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
