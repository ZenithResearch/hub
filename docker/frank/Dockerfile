FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN addgroup --system frank && adduser --system --ingroup frank --home /home/frank frank

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Install Hermes SDK and runtime dependencies
# httpx: queue/STT HTTP client
# pyyaml: config parsing
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
      "git+https://github.com/NousResearch/hermes-agent.git" \
      httpx \
      pyyaml \
      grpcio \
      jsonschema

COPY services/__init__.py /app/services/__init__.py
COPY services/frank/ /app/services/frank/
COPY services/cases/__init__.py /app/services/cases/__init__.py
COPY services/cases/contract.py /app/services/cases/contract.py
COPY libs/ /app/libs/

RUN chown -R frank:frank /app

USER frank

# HERMES_HOME and TERMINAL_CWD are injected by docker-compose at runtime.
# Do not hardcode them here — they must be overridable per environment.
ENV PYTHONPATH=/app

CMD ["python", "-m", "services.frank.main"]
