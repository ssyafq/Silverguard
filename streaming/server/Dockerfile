# Dockerfile: the recipe Google Cloud Run uses to build the ingest service's container.
#
# A container is a sealed box with our code and everything it needs, so it runs the same on
# Google's machines as on ours. `gcloud run deploy --source .` (see deploy.ps1) sends this
# folder to Cloud Build, which follows these steps, then Cloud Run starts the result whenever
# a request arrives.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
# Cloud Run tells the container which port to listen on in $PORT (8080 by default).
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
