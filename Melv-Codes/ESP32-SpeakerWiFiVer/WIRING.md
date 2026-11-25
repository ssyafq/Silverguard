# ESP32-SpeakerWiFiVer — Cloud Run / Railway (Different Wi-Fi) & Local Hotspot Guide

Supports **two modes** from the same sketch:
1. **Cloud Mode (Different Wi-Fi networks — e.g. Watch at home, Caregiver at hospital):** set `CLOUD_SERVER_HOST` in `wifi_config.h` to your Google Cloud Run or Railway domain.
2. **Local Hotspot Mode (Same Wi-Fi):** leave `CLOUD_SERVER_HOST ""` and run `python -m basestation.voice.watch_bridge --host <ip>`.

---

## 1. Pinout (ESP32-C3)

| Module | Module Pin | ESP32-C3 Pin | Notes |
|---|---|---|---|
| **MPU-6050 (I2C)** | `SDA` / `SCL` | **GPIO 1** / **GPIO 0** | `VCC` → 3.3V, `GND` → GND |
| **MAX98357A (Speaker)** | `BCLK` / `LRC` / `DIN` | **GPIO 4** / **GPIO 5** / **GPIO 6** | `VIN` → 5V (or LiPo), `GND` → GND |
| **INMP441 (Mic)** | `SCK` / `WS` / `SD` | **GPIO 4** / **GPIO 5** / **GPIO 7** | `VDD` → **3.3V ONLY**, `L/R` → **GND**, `GND` → GND |
| **Cancel Button** | `BOOT` | **GPIO 9** | Cancels offline alarm on the watch |

---

## 2. Deploy `wifi_server.py` to Google Cloud Run (Option 1)

1. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), then log in and select your project:
   ```powershell
   gcloud auth login
   gcloud config set project YOUR_GCP_PROJECT_ID
   ```
2. Copy the Dockerfile to the repo root temporarily (or build with `--source`) and deploy from `C:\Users\melvi\Silverguard`:
   ```powershell
   Copy-Item Melv-Codes\ESP32-SpeakerWiFiVer\Dockerfile Dockerfile
   gcloud run deploy silverguard-voice `
     --source . `
     --region asia-southeast1 `
     --allow-unauthenticated `
     --memory 2Gi `
     --set-env-vars ANTHROPIC_API_KEY=your_key,SUPABASE_URL=your_url,SUPABASE_SECRET_KEY=your_key,TELEGRAM_BOT_TOKEN=your_token,TELEGRAM_CHAT_ID=your_id
   Remove-Item Dockerfile
   ```
3. Cloud Run will print a Service URL like `https://silverguard-voice-xxxx-as.a.run.app`.
4. Verify it is live by opening `https://silverguard-voice-xxxx-as.a.run.app/health` in your browser (should show `{"status": "ok"}`).

---

## 3. Configure `wifi_config.h` & Flash the Watch

1. Copy [wifi_config.example.h](./wifi_config.example.h) to **`wifi_config.h`** in `Melv-Codes/ESP32-SpeakerWiFiVer/`:
   ```cpp
   #define WIFI_SSID          "YourHome2.4GHzWiFi"
   #define WIFI_PASSWORD      "YourHomePassword"
   #define CLOUD_SERVER_HOST  "silverguard-voice-xxxx-as.a.run.app"  // NO https://
   #define CLOUD_SERVER_PORT  443
   #define CLOUD_USE_HTTPS    1
   ```
2. Upload [ESP32-SpeakerWiFiVer.ino](./ESP32-SpeakerWiFiVer.ino) to the ESP32-C3.
3. Open Serial Monitor (`115200` baud) and type **`t`** to trigger a live cloud voice check-in over HTTPS right away!
   - The watch will speak *"Are you okay?"* from Cloud Run, record your reply on the INMP441, send it to Cloud Run, play back the answer, and update your dashboard + Telegram — from any Wi-Fi network in the world.
