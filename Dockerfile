FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
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
    && go install github.com/go-delve/delve/cmd/dlv@latest \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY debug_service ./debug_service

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "debug_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
