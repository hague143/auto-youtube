# Build from the repository root: docker build -f server/Dockerfile .
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg espeak-ng fonts-dejavu-core ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY server/requirements.txt .
RUN --mount=type=secret,id=studio_ca,target=/run/secrets/studio-ca.pem \
    if [ -f /run/secrets/studio-ca.pem ]; then \
      PIP_CERT=/run/secrets/studio-ca.pem pip install --no-cache-dir -r requirements.txt; \
    else pip install --no-cache-dir -r requirements.txt; fi
RUN useradd --create-home --uid 10001 studio && mkdir /data && chown studio:studio /data
COPY --chown=studio:studio server/main.py server/pipeline.py ./
COPY --chown=studio:studio web/src ./static
USER studio
ENV STUDIO_DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
