// wifi_config.example.h: TEMPLATE for the espwatch Wi-Fi settings.
// Copy it to "wifi_config.h" in this folder and fill in your hotspot. wifi_config.h is git-ignored
// because it holds the real password. Without wifi_config.h the sketch sends over USB instead.
// The hotspot must be 2.4 GHz (the ESP32-S3 has no 5 GHz radio).
#pragma once

#define WIFI_SSID      "your-hotspot-name"
#define WIFI_PASSWORD  "your-hotspot-password"
