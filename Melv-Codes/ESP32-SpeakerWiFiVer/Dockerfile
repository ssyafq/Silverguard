# Dockerfile for deploying SilverGuard wifi_server.py to Google Cloud Run or Railway.
# Build from the repository root:
#   gcloud run deploy silverguard-voice --source . --region asia-southeast1 --allow-unauthenticated --memory 2Gi
FROM python:3.11-slim

# libsndfile1 is required by soundfile; libportaudio2 is required by sounddevice on Linux
RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY basestation ./basestation
COPY Melv-Codes/ESP32-SpeakerWiFiVer/wifi_server.py ./Melv-Codes/ESP32-SpeakerWiFiVer/wifi_server.py

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["python", "Melv-Codes/ESP32-SpeakerWiFiVer/wifi_server.py"]
