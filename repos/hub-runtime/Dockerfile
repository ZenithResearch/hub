FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN groupadd --system worker_user \
    && useradd --system --gid worker_user --create-home --home-dir /home/worker_user worker_user

WORKDIR /opt/hub-runtime

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && mkdir -p /hub_context /hub_tools /workspace \
    && chown -R worker_user:worker_user /home/worker_user /hub_context /hub_tools /workspace

USER worker_user
WORKDIR /workspace

CMD ["python", "-m", "hub_runtime.cli"]
