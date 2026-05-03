FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/usr/lib/llvm-19/lib/python3.13/site-packages" \
    PATH="/root/go/bin:${PATH}" \
    GOFLAGS="-buildvcs=false"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        clang \
        curl \
        default-jdk \
        golang-go \
        lldb \
        nodejs \
        npm \
    && ln -s /usr/lib/llvm-19/lib/liblldb.so.1 /usr/lib/llvm-19/lib/liblldb.so \
    && ln -s /usr/lib/llvm-19/bin/lldb-server /usr/bin/lldb-server-19.1.7 \
    && go install github.com/go-delve/delve/cmd/dlv@latest \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY debug_service ./debug_service

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "debug_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
