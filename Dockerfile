# Thin customer-ops sidecar image (check-only HTTP gate).
# Build from repo root:
#   docker build -t governed-sidecar:0.4.1 .
# Run (mount artifacts for key/db persistence):
#   docker run --rm -p 8080:8080 \
#     -e GOVERNANCE_REQUIRE_PERSISTED_KEY=1 \
#     -e GOVERNANCE_API_KEY=... \
#     -v "$PWD/artifacts/customer:/app/artifacts/customer" \
#     governed-sidecar:0.4.1
#
# Tests do not require Docker; this is an optional deploy shape.

FROM python:3.12-slim

WORKDIR /app

# Runtime deps only (no cloud KMS SDK).
COPY requirements.txt pyproject.toml README.md ./
COPY src ./src
COPY certified_governance_unified.py ./
COPY hais ./hais
COPY haven2 ./haven2
COPY imprint ./imprint
COPY scripts/run_sidecar.py ./scripts/run_sidecar.py

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e . \
    && mkdir -p /app/artifacts/customer

ENV GOVERNANCE_HOST=0.0.0.0 \
    GOVERNANCE_PORT=8080 \
    GOVERNANCE_DB_PATH=/app/artifacts/customer/audit.db \
    GOVERNANCE_SIGNING_KEY_PATH=/app/artifacts/customer/signing_key.pem \
    PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "scripts/run_sidecar.py"]
