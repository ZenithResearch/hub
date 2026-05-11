FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch && \
    pip install --no-cache-dir fastapi uvicorn[standard] openai-whisper

COPY services/__init__.py /app/services/__init__.py
COPY services/stt_http/ /app/services/stt_http/

EXPOSE 8765

CMD ["uvicorn", "services.stt_http.main:app", "--host", "0.0.0.0", "--port", "8765"]
