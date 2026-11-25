// wifi_config.example.h: TEMPLATE for ESP32-SpeakerWiFiVer Wi-Fi & Cloud Server settings.
// Copy it to "wifi_config.h" in this folder and fill in your values. wifi_config.h is git-ignored.
// The hotspot / router must be 2.4 GHz (the ESP32-C3 has no 5 GHz radio).
#pragma once

#define WIFI_SSID          "your-home-wifi-name"
#define WIFI_PASSWORD      "your-home-wifi-password"

// Option 1 (Across different networks): your Google Cloud Run or Railway hostname (NO "https://")
//   e.g. "silverguard-voice-xxxx-as.a.run.app" or "silverguard-production.up.railway.app"
// Leave as "" to use local same-Wi-Fi mode (watch_bridge --host <ip> on TCP port 3333).
#define CLOUD_SERVER_HOST  ""
#define CLOUD_SERVER_PORT  443
#define CLOUD_USE_HTTPS    1    // 1 = Cloud Run / Railway HTTPS (443), 0 = local HTTP (8000)
