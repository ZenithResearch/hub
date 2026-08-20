FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7 AS builder

RUN python -m pip install --no-cache-dir --prefix=/install \
    fastapi==0.141.1 \
    httpx==0.28.1 \
    pydantic==2.13.4 \
    uvicorn==0.52.3

FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=65532:65532 services/hypha_admin_broker /app/services/hypha_admin_broker
COPY --chown=65532:65532 scripts/provision_matrix_admins.py /app/scripts/provision_matrix_admins.py
COPY --chown=65532:65532 scripts/bootstrap_hypha_admin_broker_authority.py /app/scripts/bootstrap_hypha_admin_broker_authority.py

USER 65532:65532
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=4 \
    CMD ["python", "-c", "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8080/_hypha/admin/v1/health', timeout=2); assert r.status == 200"]
ENTRYPOINT ["python", "-m", "uvicorn", "services.hypha_admin_broker.main:runtime_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--no-server-header"]
