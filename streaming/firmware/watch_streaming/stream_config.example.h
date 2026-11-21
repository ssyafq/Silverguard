// stream_config.example.h: TEMPLATE for the watch's streaming settings (Wi-Fi, server URL, key).
//
// WHAT THIS FILE DOES
//   Holds the values silverguard_stream.h needs to reach the server. Copy it to
//   "stream_config.h" in the same folder and fill it in. stream_config.h is git-ignored,
//   because it contains the real Wi-Fi password and device key, which must never be committed.
#pragma once

#define STREAM_WIFI_SSID      "your-wifi-name"
#define STREAM_WIFI_PASSWORD  "your-wifi-password"

// URL printed at the end of streaming/server/deploy.ps1, ending in /ingest
#define STREAM_URL            "https://silverguard-ingest-XXXXXXXX.asia-southeast1.run.app/ingest"

// Must match the key deploy.ps1 printed for this device ("watch-01=<key>" in Secret Manager)
#define STREAM_DEVICE_ID      "watch-01"
#define STREAM_DEVICE_KEY     "paste-the-device-key-here"

// Optional: verify the server's certificate (recommended after the prototype stage).
// Paste the PEM of Google Trust Services "GTS Root R1" (https://pki.goog/repository/).
// Leave undefined to use setInsecure(): still encrypted, but the server is not verified.
// #define STREAM_ROOT_CA "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n"

// Optional tuning (defaults shown)
// #define STREAM_CONFIRM_WINDOWS 6       // HAR results in a row before a change counts (6 x 0.5 s = 3 s)
// #define STREAM_HEARTBEAT_MS    60000
// #define STREAM_QUEUE_LEN       32
