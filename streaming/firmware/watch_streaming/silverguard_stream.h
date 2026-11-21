// silverguard_stream.h: the watch's streaming code (Wi-Fi → Cloud Run → Firestore).
//
// WHAT THIS FILE DOES
//   Sends the watch's activity CHANGES (still ↔ moving), confirmed falls and a once-a-minute
//   heartbeat to the SilverGuard ingest service (streaming/server/main.py) over HTTPS.
//   It is a drop-in: copy this file + your stream_config.h next to a sketch and call 3 functions:
//
//     setup():          sgstream::begin();
//     runModels():      sgstream::onHar(har);                           // every HAR result (0 still, 1 moving)
//     confirmPending(): sgstream::sendFall(pend_score, pend_peak, turn); // only when a fall is confirmed
//
//   An identical copy lives in firmware/stream_demo/ and firmware/watch_streaming/ (Arduino
//   needs it in the sketch's own folder). Edit one, copy it to the other.
//
// HOW IT WORKS (see streaming/README.md)
//   - onHar() debounces: a new class must hold for STREAM_CONFIRM_WINDOWS results in a row before
//     it counts as a change, so flicker near the still/moving boundary sends nothing.
//   - All networking runs in its own FreeRTOS task. loop() only drops messages into a queue and
//     never waits on Wi-Fi or TLS, so the 50 Hz sampling and fall detection are not stalled.
//   - Messages wait in the queue while Wi-Fi is down and go out in order once it is back.
//   - Every message carries boot_id + seq. The server uses them as the document id, so a retry
//     after a lost reply never creates a duplicate.
//   - Log lines start with "# stream:" and never contain the fall markers watch_bridge.py looks for.

#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include "stream_config.h"

#ifndef STREAM_CONFIRM_WINDOWS
#define STREAM_CONFIRM_WINDOWS 6          // 6 x 0.5 s = 3 s of the same class before it is a change
#endif
#ifndef STREAM_HEARTBEAT_MS
#define STREAM_HEARTBEAT_MS    60000      // 1 heartbeat a minute
#endif
#ifndef STREAM_QUEUE_LEN
#define STREAM_QUEUE_LEN       32
#endif
#ifndef STREAM_DETECTOR
#define STREAM_DETECTOR        "har_v0.5"
#endif
#ifndef STREAM_FALL_DETECTOR
#define STREAM_FALL_DETECTOR   "fall_rf30_v0.5"
#endif
#ifndef STREAM_LOG
#define STREAM_LOG             1          // 0 = silent
#endif

namespace sgstream {

enum MsgType : uint8_t { MSG_ACTIVITY, MSG_FALL, MSG_HEARTBEAT };

struct Msg {
  MsgType  type;
  int8_t   state;          // 0 still, 1 moving, -1 unknown
  float    confidence, peak_g, turn_deg;
  uint32_t seq, uptime_ms;
};

static const char* const STATE_NAME[2] = {"still", "moving"};

static QueueHandle_t   q            = nullptr;
static char            boot_id[9]   = "00000000";
static uint32_t        next_seq     = 0;
static portMUX_TYPE    seq_mux      = portMUX_INITIALIZER_UNLOCKED;
static volatile int8_t sent_state   = -1;   // last state queued (shared with the network task)
static int8_t          cand_state   = -1;   // debounce, loop task only
static int             cand_count   = 0;
static volatile uint32_t sent_ok    = 0, send_fail = 0, dropped = 0;

#if STREAM_LOG
#define SG_LOG(...) Serial.printf(__VA_ARGS__)
#else
#define SG_LOG(...) do {} while (0)
#endif

static uint32_t takeSeq() {
  portENTER_CRITICAL(&seq_mux);
  uint32_t s = next_seq++;
  portEXIT_CRITICAL(&seq_mux);
  return s;
}

// Queue a message. If the queue is full (long Wi-Fi outage) the OLDEST message is dropped:
// heartbeats are never queued while anything is waiting, so the queue only fills with changes.
static void enqueue(Msg m) {
  if (!q) return;
  m.seq = takeSeq();
  m.uptime_ms = millis();
  if (xQueueSend(q, &m, 0) != pdTRUE) {
    Msg old;
    xQueueReceive(q, &old, 0);
    dropped++;
    xQueueSend(q, &m, 0);
  }
}

static int buildJson(const Msg& m, char* out, size_t n) {
  const char* type = m.type == MSG_ACTIVITY ? "activity" : m.type == MSG_FALL ? "fall" : "heartbeat";
  int len = snprintf(out, n,
      "{\"device_id\":\"%s\",\"type\":\"%s\",\"boot_id\":\"%s\",\"seq\":%lu,\"uptime_ms\":%lu",
      STREAM_DEVICE_ID, type, boot_id, (unsigned long)m.seq, (unsigned long)m.uptime_ms);
  if (m.state >= 0)
    len += snprintf(out + len, n - len, ",\"state\":\"%s\"", STATE_NAME[m.state]);
  if (m.type == MSG_ACTIVITY)
    len += snprintf(out + len, n - len, ",\"detector\":\"%s\"", STREAM_DETECTOR);
  if (m.type == MSG_FALL)
    len += snprintf(out + len, n - len,
        ",\"confidence\":%.3f,\"peak_g\":%.2f,\"turn_deg\":%.1f,\"detector\":\"%s\"",
        m.confidence, m.peak_g, m.turn_deg, STREAM_FALL_DETECTOR);
  len += snprintf(out + len, n - len, "}");
  return len;
}

// Returns true if the message is finished with (sent, or refused for good), false to retry.
static bool post(WiFiClientSecure& tls, const Msg& m) {
  char body[320];
  buildJson(m, body, sizeof(body));
  HTTPClient http;
  http.setReuse(true);                       // keep the TLS connection: no new handshake per message
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  if (!http.begin(tls, STREAM_URL)) return false;
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", STREAM_DEVICE_KEY);
  int code = http.POST((uint8_t*)body, strlen(body));
  http.end();
  if (code >= 200 && code < 300) { sent_ok++; return true; }
  if (code == 401 || code == 422) {          // wrong key or bad data: retrying will not help
    SG_LOG("# stream: server refused seq %lu (HTTP %d), dropped. Check STREAM_DEVICE_KEY / STREAM_DEVICE_ID\n",
           (unsigned long)m.seq, code);
    send_fail++;
    return true;
  }
  SG_LOG("# stream: send failed seq %lu (%d), will retry\n", (unsigned long)m.seq, code);
  send_fail++;
  return false;
}

static void netTask(void*) {
  WiFiClientSecure tls;
#ifdef STREAM_ROOT_CA
  tls.setCACert(STREAM_ROOT_CA);
#else
  tls.setInsecure();                         // prototype only: encrypted but the server is not verified
#endif
  uint32_t last_hb = millis(), backoff = 1000;
  bool was_up = false;

  for (;;) {
    bool up = WiFi.status() == WL_CONNECTED;
    if (up != was_up) {
      if (up) SG_LOG("# stream: Wi-Fi up, IP %s\n", WiFi.localIP().toString().c_str());
      else    SG_LOG("# stream: Wi-Fi down, messages are queued\n");
      was_up = up;
    }

    if (millis() - last_hb >= STREAM_HEARTBEAT_MS) {
      last_hb = millis();
      if (uxQueueMessagesWaiting(q) == 0) {
        Msg hb = {MSG_HEARTBEAT, sent_state, 0, 0, 0, 0, 0};
        enqueue(hb);
      }
    }

    Msg m;
    if (!up || xQueuePeek(q, &m, pdMS_TO_TICKS(500)) != pdTRUE) {
      vTaskDelay(pdMS_TO_TICKS(up ? 10 : 500));
      continue;
    }
    if (post(tls, m)) {
      xQueueReceive(q, &m, 0);               // done: remove it (FIFO, so it is the one we peeked)
      backoff = 1000;
    } else {
      tls.stop();                            // fresh connection next time
      vTaskDelay(pdMS_TO_TICKS(backoff));
      backoff = min<uint32_t>(backoff * 2, 30000);
    }
  }
}

// ---------------------------------------------------------------- public API

inline void begin() {
  snprintf(boot_id, sizeof(boot_id), "%08lx", (unsigned long)esp_random());
  q = xQueueCreate(STREAM_QUEUE_LEN, sizeof(Msg));
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(STREAM_WIFI_SSID, STREAM_WIFI_PASSWORD);   // non-blocking: the task waits for it
  // ESP32-C3 has ONE core: same priority as loop() (1) so the two share the CPU by time slices.
  // A lower priority would starve if loop() never blocks.
  xTaskCreate(netTask, "sgstream", 8192, nullptr, 1, nullptr);
  SG_LOG("# stream: device %s, boot %s, posting to %s\n", STREAM_DEVICE_ID, boot_id, STREAM_URL);
}

// Call with every HAR result (0 = still, 1 = moving). Queues only debounced changes.
inline void onHar(int har) {
  if (har < 0 || har > 1) return;
  if (har == sent_state) { cand_count = 0; cand_state = -1; return; }
  if (har != cand_state) { cand_state = har; cand_count = 1; }
  else cand_count++;
  if (cand_count >= STREAM_CONFIRM_WINDOWS) {
    sent_state = har;
    cand_count = 0; cand_state = -1;
    Msg m = {MSG_ACTIVITY, (int8_t)har, 0, 0, 0, 0, 0};
    enqueue(m);
    SG_LOG("# stream: activity -> %s\n", STATE_NAME[har]);
  }
}

// Call once per CONFIRMED fall. Sent straight away (no debounce).
inline void sendFall(float score, float peak_g, float turn_deg) {
  Msg m = {MSG_FALL, sent_state, score, peak_g, turn_deg, 0, 0};
  enqueue(m);
  SG_LOG("# stream: fall queued (score %.2f)\n", score);
}

inline void printStats() {
  SG_LOG("# stream: sent %lu, failed %lu, dropped %lu, waiting %u\n",
         (unsigned long)sent_ok, (unsigned long)send_fail, (unsigned long)dropped,
         q ? (unsigned)uxQueueMessagesWaiting(q) : 0u);
}

}  // namespace sgstream
