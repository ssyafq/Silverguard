// SilverGuard ESP32-SpeakerWiFiVer: ESP32-C3 + MPU-6050 + INMP441 + MAX98357A over Wi-Fi
//
// Follows firmware/espwatch_prototype.ino, adapted for the ESP32-C3 wired prototype:
//   - sensor: MPU-6050 at 50 Hz (SDA = GPIO 1, SCL = GPIO 0)
//   - audio:  MAX98357A speaker (BCLK = 4, LRC = 5, DIN = 6) + INMP441 mic (SCK = 4, WS = 5, SD = 7, L/R = GND)
//   - link:   Wi-Fi TCP server on port 3333 (espwatch-c3.local) — works directly with:
//               python -m basestation.voice.watch_bridge --host <ip>
//             USB serial still works at the same time.
//   - models: HAR forest (still / moving, every 0.5 s on 2.56 s) + fall model
//             (2.5 g trigger + 30-tree forest + wrist-turn > 20 deg + pre-impact motion check)
//
// Wi-Fi: copy wifi_config.example.h to wifi_config.h and put in your 2.4 GHz hotspot.

#include <Wire.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <ESPmDNS.h>
#include <ESP_I2S.h>

#include "fall_features.h"
#include "fall_model.h"     // fall forest + FALL_TRIGGER_G, FALL_PROBA_THRESHOLD, FALL_WIN
#include "har_model.h"      // still / moving forest

#if __has_include("wifi_config.h")
  #include "wifi_config.h"
#else
  #error "Copy wifi_config.example.h to wifi_config.h and put in your Wi-Fi and cloud server settings."
#endif

#ifndef CLOUD_SERVER_HOST
  #define CLOUD_SERVER_HOST ""
#endif
#ifndef CLOUD_SERVER_PORT
  #define CLOUD_SERVER_PORT 443
#endif
#ifndef CLOUD_USE_HTTPS
  #define CLOUD_USE_HTTPS 1
#endif

const char*    DEVICE_ID      = "watch-01";
const uint32_t RECORD_SECONDS = 7;          // seconds of INMP441 audio sent per cloud check-in attempt

// ---------------- switches ----------------
#define RAW_STREAM    0     // 0 = readable "Activity:" lines (live demo), 1 = raw CSV + '# result' lines
#define HALT_ON_FALL  1     // 1 = on a fall, pause sampling until 'r' or BOOT button
#define SELF_TEST     1     // 1 = check features + models against Python at boot

#if SELF_TEST
  #include "test_vectors.h"
#endif

// ---------------- Wi-Fi link to the laptop (TCP port 3333, same as espwatch_prototype) ----------------
const uint16_t TCP_PORT = 3333;
WiFiServer server(TCP_PORT);
WiFiClient client;

class TeeOut : public Print {
 public:
  size_t write(uint8_t c) override { return write(&c, 1); }
  size_t write(const uint8_t* b, size_t n) override {
    Serial.write(b, n);
    if (client && client.connected()) client.write(b, n);
    return n;
  }
};
TeeOut Out;

// ---------------- settings & pinout ----------------
const char*    FW_VERSION   = "ESP32-SpeakerWiFiVer 0.7 (ESP32-C3 + MPU-6050 + INMP441 + MAX98357A, Wi-Fi TCP 3333)";
const int      SDA_PIN      = 1;          // MPU-6050 I2C data  (GPIO 1)
const int      SCL_PIN      = 0;          // MPU-6050 I2C clock (GPIO 0)
const int      BTN_BOOT_PIN = 9;          // ESP32-C3 BOOT button (active LOW)

// Shared I2S bus (MAX98357A speaker + INMP441 digital mic)
const int      I2S_BCLK_PIN = 4;          // MAX98357A BCLK + INMP441 SCK (GPIO 4)
const int      I2S_LRC_PIN  = 5;          // MAX98357A LRC  + INMP441 WS  (GPIO 5)
const int      I2S_DOUT_PIN = 6;          // MAX98357A DIN  (speaker data out, GPIO 6)
const int      I2S_DIN_PIN  = 7;          // INMP441   SD   (mic data in, GPIO 7)
const uint32_t AUDIO_RATE   = 16000;      // 16 kHz mono PCM (matches Whisper STT on laptop)
const int16_t  TONE_AMP     = 10000;      // alert / test tone amplitude (0..32767)

I2SClass i2s;
bool     i2s_ok = false;

const uint32_t I2C_HZ     = 400000;
const uint8_t  MPU_ADDR   = 0x68;
const uint32_t PERIOD_US  = 20000;        // 50 Hz
const uint32_t STATUS_US  = 10000000;     // status line every 10 s

const float    ACC_LSB_PER_G    = 2048.0f;
const float    GYRO_LSB_PER_DPS = 16.4f;
const int      HAR_WIN          = 128;
const int      RESULT_EVERY     = 25;
const uint32_t LOCKOUT_SAMPLES  = 5 * 50;

const float    CONFIRM_MIN_TURN_DEG = 20.0f;
const int      PRE_FROM  = -75, PRE_TO  = -25;
const int      POST_FROM =  50, POST_TO = 125;
const int      VEC_RING  = 256;

const float    NO_DIP_ABOVE_G      = 0.85f;
const float    SLOW_TURN_BELOW_DPS = 150.0f;
const int      MOTION_FROM         = -50;

// MPU-6050 registers
const uint8_t REG_SMPLRT_DIV   = 0x19;
const uint8_t REG_CONFIG       = 0x1A;
const uint8_t REG_GYRO_CONFIG  = 0x1B;
const uint8_t REG_ACCEL_CONFIG = 0x1C;
const uint8_t REG_ACCEL_XOUT_H = 0x3B;
const uint8_t REG_PWR_MGMT_1   = 0x6B;
const uint8_t REG_WHO_AM_I     = 0x75;

const uint8_t VAL_PWR_MGMT_1   = 0x01;
const uint8_t VAL_CONFIG       = 0x04;
const uint8_t VAL_SMPLRT_DIV   = 19;
const uint8_t VAL_GYRO_CONFIG  = 0x18;
const uint8_t VAL_ACCEL_CONFIG = 0x18;

// ---------------- counters & model state ----------------
uint32_t next_time    = 0;
uint32_t sample_index = 0;
uint32_t sent         = 0;
uint32_t dropped      = 0;
uint32_t read_errors  = 0;
uint32_t last_status  = 0;
uint32_t falls        = 0;
uint32_t max_model_us = 0;

float    gyro_bias[3] = {0, 0, 0};
float    am_ring[HAR_WIN];
float    gm_ring[HAR_WIN];
int      ring_pos     = 0;
int      ring_filled  = 0;
int      since_result = 0;
uint32_t lockout_until = 0;
bool     alarm_active  = false;
uint32_t last_alert_ms = 0;

float    vx[VEC_RING], vy[VEC_RING], vz[VEC_RING];
float    amh[VEC_RING], gmh[VEC_RING];
uint32_t seq = 0;

bool     pending       = false;
uint32_t pend_impact   = 0;
uint32_t pend_index    = 0;
float    pend_pre[3]   = {0, 0, 0};
bool     pend_has_pre  = false;
float    pend_score    = 0, pend_peak = 0;
float    pend_min_g    = 0, pend_max_dps = 0;

const char* ALERT_TEXT = "ALERT ALERT ALERT ALERT ALERT ALERT ALERT FALL DETECTEDDD!!!";
const char* ACTIVITY_NAME[2] = {"Still", "Moving"};

// ---------------- I2S audio helpers (MAX98357A speaker + INMP441 mic) ----------------
void initAudio() {
  i2s.setPins(I2S_BCLK_PIN, I2S_LRC_PIN, I2S_DOUT_PIN, I2S_DIN_PIN);
  i2s_ok = i2s.begin(
    I2S_MODE_STD,
    AUDIO_RATE,
    I2S_DATA_BIT_WIDTH_32BIT,
    I2S_SLOT_MODE_STEREO,
    I2S_STD_SLOT_BOTH
  );
  Out.printf("# audio I2S (BCLK=%d, LRC=%d, DOUT=%d, DIN=%d @ %lu Hz): %s\n",
             I2S_BCLK_PIN, I2S_LRC_PIN, I2S_DOUT_PIN, I2S_DIN_PIN,
             (unsigned long)AUDIO_RATE, i2s_ok ? "OK" : "FAILED");
}

void writeSilenceI2S() {
  if (!i2s_ok) return;
  int32_t zeros[128] = {0};
  i2s.write((uint8_t*)zeros, sizeof(zeros));
}

void playToneI2S(float freqHz, uint32_t durationMs, int16_t amp = TONE_AMP) {
  if (!i2s_ok) return;
  const size_t CHUNK = 128;
  int32_t stereoBuf[CHUNK * 2];
  uint32_t totalFrames = (AUDIO_RATE * durationMs) / 1000;
  uint32_t sentFrames  = 0;
  float phase    = 0.0f;
  float phaseInc = (2.0f * (float)M_PI * freqHz) / (float)AUDIO_RATE;

  while (sentFrames < totalFrames) {
    size_t n = (totalFrames - sentFrames < CHUNK) ? (totalFrames - sentFrames) : CHUNK;
    for (size_t i = 0; i < n; i++) {
      int16_t s16 = (freqHz > 0.0f) ? (int16_t)(sinf(phase) * amp) : 0;
      phase += phaseInc;
      if (phase >= 2.0f * (float)M_PI) phase -= 2.0f * (float)M_PI;
      int32_t s32 = ((int32_t)s16) << 16;
      stereoBuf[2 * i]     = s32;
      stereoBuf[2 * i + 1] = s32;
    }
    i2s.write((uint8_t*)stereoBuf, n * 2 * sizeof(int32_t));
    sentFrames += n;
  }
  writeSilenceI2S();
}

void playFallAlertTone() {
  playToneI2S(880.0f, 180);
  playToneI2S(660.0f, 180);
  playToneI2S(880.0f, 220);
}

bool readExact(Stream& src, uint8_t* dst, size_t len, uint32_t timeoutMs = 1500) {
  size_t got = 0;
  uint32_t t0 = millis();
  while (got < len && (millis() - t0) < timeoutMs) {
    int avail = src.available();
    if (avail > 0) {
      size_t toRead = (size_t)avail < (len - got) ? (size_t)avail : (len - got);
      got += src.readBytes(dst + got, toRead);
    } else {
      delayMicroseconds(200);
    }
  }
  return got == len;
}

// Protocol 'P': laptop sends 'P' + uint16_le(n_samples) + int16_le[n_samples] -> plays on MAX98357A, replies 'K'
void handlePlay(Stream& src) {
  uint8_t hdr[2];
  if (!readExact(src, hdr, 2, 1000)) return;
  uint16_t nSamples = (uint16_t)hdr[0] | ((uint16_t)hdr[1] << 8);
  if (nSamples == 0) {
    writeSilenceI2S();
    src.write('K');
    return;
  }

  const size_t CHUNK = 128;
  int16_t pcm16[CHUNK];
  int32_t stereo32[CHUNK * 2];
  uint16_t remaining = nSamples;

  while (remaining > 0) {
    uint16_t n = (remaining > CHUNK) ? CHUNK : remaining;
    if (!readExact(src, (uint8_t*)pcm16, n * sizeof(int16_t), 1500)) return;
    if (i2s_ok) {
      for (uint16_t i = 0; i < n; i++) {
        int32_t s32 = ((int32_t)pcm16[i]) << 16;
        stereo32[2 * i]     = s32;
        stereo32[2 * i + 1] = s32;
      }
      i2s.write((uint8_t*)stereo32, n * 2 * sizeof(int32_t));
    }
    remaining -= n;
  }
  src.write('K');
}

// Protocol 'L': laptop sends 'L' + uint16_le(n_samples) -> ESP32 reads INMP441 mic and replies 'M' + int16_le[n_samples]
void handleListen(Stream& src) {
  uint8_t hdr[2];
  if (!readExact(src, hdr, 2, 1000)) return;
  uint16_t nSamples = (uint16_t)hdr[0] | ((uint16_t)hdr[1] << 8);

  src.write('M');

  const size_t CHUNK = 128;
  int32_t rawStereo[CHUNK * 2];
  int16_t outPcm16[CHUNK];
  uint16_t remaining = nSamples;

  while (remaining > 0) {
    uint16_t n = (remaining > CHUNK) ? CHUNK : remaining;
    size_t bytesToRead = n * 2 * sizeof(int32_t);
    size_t got = i2s_ok ? i2s.readBytes((char*)rawStereo, bytesToRead) : 0;
    size_t framesGot = got / (2 * sizeof(int32_t));

    for (uint16_t i = 0; i < n; i++) {
      if (i < framesGot) {
        int32_t left  = rawStereo[2 * i];
        int32_t right = rawStereo[2 * i + 1];
        int32_t raw   = (left != 0) ? left : right;
        int32_t val   = raw >> 14;
        if (val >  32767) val =  32767;
        if (val < -32768) val = -32768;
        outPcm16[i] = (int16_t)val;
      } else {
        outPcm16[i] = 0;
      }
    }
    src.write((const uint8_t*)outPcm16, n * sizeof(int16_t));
    remaining -= n;
  }
}

void testMicLevel() {
  if (!i2s_ok) { Out.println("# mic test: I2S not initialised"); return; }
  const int N = 16000;
  const size_t CHUNK = 128;
  int32_t rawStereo[CHUNK * 2];
  int32_t peak = 0;
  double sumSq = 0;
  int count = 0;

  for (int rem = N; rem > 0; rem -= CHUNK) {
    size_t n = (rem > (int)CHUNK) ? CHUNK : (size_t)rem;
    size_t got = i2s.readBytes((char*)rawStereo, n * 2 * sizeof(int32_t));
    size_t frames = got / (2 * sizeof(int32_t));
    for (size_t i = 0; i < frames; i++) {
      int32_t raw = (rawStereo[2 * i] != 0) ? rawStereo[2 * i] : rawStereo[2 * i + 1];
      int32_t s16 = raw >> 14;
      if (s16 >  32767) s16 =  32767;
      if (s16 < -32768) s16 = -32768;
      int32_t a = s16 < 0 ? -s16 : s16;
      if (a > peak) peak = a;
      sumSq += (double)s16 * (double)s16;
      count++;
    }
  }
  float rms = count > 0 ? sqrtf((float)(sumSq / count)) / 32768.0f : 0.0f;
  Out.printf("# mic test (1 s): peak=%ld/32767, rms=%.4f %s\n",
             (long)peak, rms, peak > 50 ? "(INMP441 OK)" : "(quiet/check wiring)");
}

// ---------------- Cloud HTTPS Check-In (Option 1: Google Cloud Run / Railway) ----------------
bool readHttpHeaders(WiFiClient& c, int& statusCode, int32_t& contentLen, String& xReply, String& xNext) {
  statusCode = 0; contentLen = -1; xReply = ""; xNext = "";
  uint32_t t0 = millis();
  while (!c.available() && c.connected() && (millis() - t0) < 20000) delay(5);
  if (!c.available()) return false;

  String statusLine = c.readStringUntil('\n');
  statusLine.trim();
  int sp1 = statusLine.indexOf(' ');
  if (sp1 > 0) statusCode = statusLine.substring(sp1 + 1).toInt();

  while (c.connected() || c.available()) {
    String line = c.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) break;
    if (line.startsWith("Content-Length:") || line.startsWith("content-length:")) {
      contentLen = line.substring(15).toInt();
    } else if (line.startsWith("X-Reply:") || line.startsWith("x-reply:")) {
      xReply = line.substring(8); xReply.trim();
    } else if (line.startsWith("X-Next:") || line.startsWith("x-next:")) {
      xNext = line.substring(7); xNext.trim();
    }
  }
  return statusCode >= 200 && statusCode < 300;
}

void streamHttpBodyToSpeaker(WiFiClient& c, int32_t contentLen) {
  const size_t CHUNK_SAMPLES = 128;
  uint8_t rawBytes[CHUNK_SAMPLES * 2];
  int32_t stereo32[CHUNK_SAMPLES * 2];
  int32_t remaining = contentLen;
  uint32_t lastRxMs = millis();

  while ((remaining < 0 || remaining > 0) && (millis() - lastRxMs) < 5000) {
    if (c.available() <= 0) {
      if (!c.connected()) break;
      delay(2);
      continue;
    }
    lastRxMs = millis();
    size_t want = sizeof(rawBytes);
    if (remaining > 0 && (int32_t)want > remaining) want = (size_t)remaining;
    size_t got = 0;
    while (got < want && (millis() - lastRxMs) < 2000) {
      int r = c.read(rawBytes + got, want - got);
      if (r > 0) { got += (size_t)r; lastRxMs = millis(); }
      else if (!c.connected()) break;
      else delay(1);
    }
    size_t nSamples = got / 2;
    if (nSamples > 0 && i2s_ok) {
      for (size_t i = 0; i < nSamples; i++) {
        int16_t s16 = (int16_t)((uint16_t)rawBytes[2 * i] | ((uint16_t)rawBytes[2 * i + 1] << 8));
        int32_t s32 = ((int32_t)s16) << 16;
        stereo32[2 * i] = s32; stereo32[2 * i + 1] = s32;
      }
      i2s.write((uint8_t*)stereo32, nSamples * 2 * sizeof(int32_t));
    }
    if (remaining > 0) remaining -= (int32_t)got;
  }
  writeSilenceI2S();
}

bool playCloudPrompt(const char* name) {
#if CLOUD_USE_HTTPS
  WiFiClientSecure c;
  c.setInsecure();
#else
  WiFiClient c;
#endif
  c.setTimeout(15000);
  if (!c.connect(CLOUD_SERVER_HOST, CLOUD_SERVER_PORT)) {
    Out.printf("# [Cloud] Failed to connect to %s:%u\n", CLOUD_SERVER_HOST, (unsigned)CLOUD_SERVER_PORT);
    return false;
  }
  c.printf("GET /prompt/%s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n", name, CLOUD_SERVER_HOST);
  int status = 0; int32_t contentLen = -1; String xReply, xNext;
  bool ok = readHttpHeaders(c, status, contentLen, xReply, xNext);
  if (ok) streamHttpBodyToSpeaker(c, contentLen);
  c.stop();
  return ok;
}

bool uploadMicToCloud(int attempt, float score, String& outReply, String& outNext) {
#if CLOUD_USE_HTTPS
  WiFiClientSecure c;
  c.setInsecure();
#else
  WiFiClient c;
#endif
  c.setTimeout(20000);
  if (!c.connect(CLOUD_SERVER_HOST, CLOUD_SERVER_PORT)) {
    Out.printf("# [Cloud] Failed to connect to %s:%u for /checkin\n", CLOUD_SERVER_HOST, (unsigned)CLOUD_SERVER_PORT);
    return false;
  }

  uint32_t totalSamples = AUDIO_RATE * RECORD_SECONDS;
  uint32_t totalBytes   = totalSamples * sizeof(int16_t);
  c.printf("POST /checkin?device_id=%s&attempt=%d&score=%.2f HTTP/1.1\r\n", DEVICE_ID, attempt, score);
  c.printf("Host: %s\r\nContent-Type: application/octet-stream\r\nContent-Length: %lu\r\nConnection: close\r\n\r\n",
           CLOUD_SERVER_HOST, (unsigned long)totalBytes);

  Out.printf("# [Cloud] Listening on INMP441 for %lu s (attempt %d)...\n", (unsigned long)RECORD_SECONDS, attempt);
  const size_t CHUNK = 128;
  int32_t rawStereo[CHUNK * 2];
  int16_t outPcm16[CHUNK];
  uint32_t remaining = totalSamples;
  while (remaining > 0 && c.connected()) {
    size_t n = (remaining > CHUNK) ? CHUNK : (size_t)remaining;
    size_t got = i2s_ok ? i2s.readBytes((char*)rawStereo, n * 2 * sizeof(int32_t)) : 0;
    size_t framesGot = got / (2 * sizeof(int32_t));
    for (size_t i = 0; i < n; i++) {
      if (i < framesGot) {
        int32_t left = rawStereo[2 * i], right = rawStereo[2 * i + 1];
        int32_t val = ((left != 0) ? left : right) >> 14;
        if (val > 32767) val = 32767;
        if (val < -32768) val = -32768;
        outPcm16[i] = (int16_t)val;
      } else {
        outPcm16[i] = 0;
      }
    }
    c.write((const uint8_t*)outPcm16, n * sizeof(int16_t));
    remaining -= n;
  }

  Out.println("# [Cloud] Audio sent -> waiting for STT + Claude + TTS reply...");
  int status = 0; int32_t contentLen = -1;
  bool ok = readHttpHeaders(c, status, contentLen, outReply, outNext);
  if (ok) {
    Out.printf("# [Cloud] reply='%s', next='%s' -> playing on MAX98357A...\n", outReply.c_str(), outNext.c_str());
    streamHttpBodyToSpeaker(c, contentLen);
  } else {
    Out.printf("# [Cloud] Server returned HTTP %d\n", status);
  }
  c.stop();
  return ok;
}

bool runCloudCheckIn(float score) {
  if (strlen(CLOUD_SERVER_HOST) == 0 || WiFi.status() != WL_CONNECTED) return false;
  if (!playCloudPrompt("question")) return false;
  for (int attempt = 1; attempt <= 2; attempt++) {
    String reply, nextAction;
    if (!uploadMicToCloud(attempt, score, reply, nextAction)) return false;
    if (nextAction != "retry") break;
  }
  resumeFromAlarm("cloud check-in done");
  return true;
}

// ---------------- I2C helpers ----------------
void writeReg(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

uint8_t readReg(uint8_t reg) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0xFF;
}

void checkReg(const char* name, uint8_t reg, uint8_t expected) {
  uint8_t got = readReg(reg);
  Out.printf("# reg %s (0x%02X) = 0x%02X, expected 0x%02X %s\n",
             name, reg, got, expected, got == expected ? "OK" : "MISMATCH");
}

bool readSample(int16_t v[7]) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(REG_ACCEL_XOUT_H);
  Wire.endTransmission(false);
  if (Wire.requestFrom(MPU_ADDR, (uint8_t)14) != 14) return false;
  for (int i = 0; i < 7; i++) {
    uint8_t hi = Wire.read();
    uint8_t lo = Wire.read();
    v[i] = (int16_t)((hi << 8) | lo);
  }
  return true;
}

void printHeader() {
  Out.printf("# firmware: %s\n", FW_VERSION);
  Out.println("# sensor: MPU-6050, raw int16 counts, no conversion on device");
  Out.println("# accel_range: +-16 g");
  Out.println("# accel_lsb_per_g: 2048");
  Out.println("# gyro_range: +-2000 dps");
  Out.println("# gyro_lsb_per_dps: 16.4");
  Out.printf("# sample_rate_hz: %lu\n", 1000000UL / PERIOD_US);
  Out.printf("# gyro_bias_dps: %.3f,%.3f,%.3f\n", gyro_bias[0], gyro_bias[1], gyro_bias[2]);
  Out.println("# columns: sample_index,micros,ax,ay,az,gx,gy,gz");
}

void calibrateGyro() {
  const int N = 100;
  float sg[3] = {0, 0, 0}, sa = 0, saa = 0;
  int got = 0;
  for (int k = 0; k < N; k++) {
    int16_t v[7];
    if (readSample(v)) {
      for (int a = 0; a < 3; a++) sg[a] += v[4 + a] / GYRO_LSB_PER_DPS;
      float m = sqrtf((float)v[0] * v[0] + (float)v[1] * v[1] + (float)v[2] * v[2]) / ACC_LSB_PER_G;
      sa += m; saa += m * m; got++;
    }
    delay(20);
  }
  if (got < N / 2) { Out.println("# WARNING: gyro calibration failed, bias = 0"); return; }
  float spread = sqrtf(fmaxf(saa / got - (sa / got) * (sa / got), 0));
  if (spread > 0.02f) { Out.println("# WARNING: moved during gyro calibration, bias = 0"); return; }
  for (int a = 0; a < 3; a++) gyro_bias[a] = sg[a] / got;
}

#if SELF_TEST
void selfTest() {
  float am[TV_WIN], gm[TV_WIN], f[FALL_N_FEATURES], proba[2], hf[5];
  int failed = 0;
  for (int w = 0; w < TV_N; w++) {
    for (int i = 0; i < TV_WIN; i++) {
      const float* a = &TV_ACC[(w * TV_WIN + i) * 3];
      const float* g = &TV_GYR[(w * TV_WIN + i) * 3];
      am[i] = sqrtf(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]);
      gm[i] = sqrtf(g[0] * g[0] + g[1] * g[1] + g[2] * g[2]);
    }
    fall_features(am, gm, TV_WIN, f);
    fall_model_predict_proba(f, FALL_N_FEATURES, proba, 2);
    har_features(&TV_HAR_MAG[w * TV_HAR_WIN], TV_HAR_WIN, hf);
    int har = har_model_predict(hf, 5);
    if (fabsf(proba[1] - TV_SCORE[w]) >= 1e-4f || har != TV_HAR_CLASS[w]) failed++;
  }
  Out.printf("# self-test: %s\n", failed ? "FAILED" : "all PASS");
}
#endif

// ---------------- models ----------------
void runModels() {
  uint32_t t0 = micros();
  float am[HAR_WIN], gm[HAR_WIN];
  for (int i = 0; i < HAR_WIN; i++) {
    int j = (ring_pos + i) % HAR_WIN;
    am[i] = am_ring[j]; gm[i] = gm_ring[j];
  }

  float hf[5];
  har_features(am, HAR_WIN, hf);
  int har = har_model_predict(hf, 5);

  const float* fam = am + (HAR_WIN - FALL_WIN);
  const float* fgm = gm + (HAR_WIN - FALL_WIN);
  float peak = 0;
  for (int i = 0; i < FALL_WIN; i++) if (fam[i] > peak) peak = fam[i];
  float score = -1.0f;
  bool fall = false;
  if (peak > FALL_TRIGGER_G) {
    float f[FALL_N_FEATURES], proba[2];
    fall_features(fam, fgm, FALL_WIN, f);
    fall_model_predict_proba(f, FALL_N_FEATURES, proba, 2);
    score = proba[1];
    fall = score >= FALL_PROBA_THRESHOLD;
  }

  uint32_t dt = micros() - t0;
  if (dt > max_model_us) max_model_us = dt;

  uint32_t newest = sample_index - 1;
  bool new_candidate = fall && !pending && newest >= lockout_until;
#if RAW_STREAM
  Out.printf("# result,%lu,%d,%.2f,%.3f,%d\n", newest, har, peak, score, fall ? 1 : 0);
#else
  if (score < 0)
    Out.printf("[%8.1f s]  Activity: %-7s | peak %5.2f g | fall check: -\n", millis() / 1000.0f, ACTIVITY_NAME[har], peak);
  else
    Out.printf("[%8.1f s]  Activity: %-7s | peak %5.2f g | fall check: score %.2f -> %s\n",
               millis() / 1000.0f, ACTIVITY_NAME[har], peak, score,
               !fall ? "not a fall" : new_candidate ? "possible fall, confirming..." : "checking");
#endif

  if (new_candidate) {
    int ip = 0;
    for (int i = 1; i < FALL_WIN; i++) if (fam[i] > fam[ip]) ip = i;
    pend_impact = (seq - FALL_WIN) + ip;
    pend_index  = newest - (FALL_WIN - 1) + ip;
    pend_score  = score; pend_peak = peak;
    pending     = true;
    lockout_until = newest + LOCKOUT_SAMPLES;
    int32_t from = (int32_t)pend_impact + PRE_FROM, to = (int32_t)pend_impact + PRE_TO;
    if (from < 0) from = 0;
    pend_has_pre = (to - from) >= 10;
    pend_pre[0] = pend_pre[1] = pend_pre[2] = 0;
    for (int32_t k = from; k < to; k++) {
      pend_pre[0] += vx[k % VEC_RING]; pend_pre[1] += vy[k % VEC_RING]; pend_pre[2] += vz[k % VEC_RING];
    }
    int32_t m0 = (int32_t)pend_impact + MOTION_FROM;
    if (m0 < 0) m0 = 0;
    pend_min_g = 99.0f; pend_max_dps = 0.0f;
    for (int32_t k = m0; k <= (int32_t)pend_impact; k++) {
      if (k < (int32_t)pend_impact && amh[k % VEC_RING] < pend_min_g) pend_min_g = amh[k % VEC_RING];
      if (gmh[k % VEC_RING] > pend_max_dps) pend_max_dps = gmh[k % VEC_RING];
    }
  }
}

float angleDeg(const float* u, const float* w) {
  float d = u[0] * w[0] + u[1] * w[1] + u[2] * w[2];
  float nu = sqrtf(u[0] * u[0] + u[1] * u[1] + u[2] * u[2]);
  float nw = sqrtf(w[0] * w[0] + w[1] * w[1] + w[2] * w[2]);
  if (nu < 1e-6f || nw < 1e-6f) return 180.0f;
  float c = d / (nu * nw);
  if (c > 1) c = 1;
  if (c < -1) c = -1;
  return acosf(c) * 57.29578f;
}

void resumeFromAlarm(const char* reason) {
  Out.printf("# Check-in resolved (%s): resuming monitoring.\n", reason);
  alarm_active = false;
  ring_filled = seq = since_result = 0;
  next_time = micros() + PERIOD_US;
}

void confirmPending(bool data_ok) {
  if (!pending) return;
  if (data_ok && seq < pend_impact + POST_TO) return;

  float turn = -1.0f;
  if (data_ok && pend_has_pre) {
    float post[3] = {0, 0, 0};
    for (uint32_t k = pend_impact + POST_FROM; k < pend_impact + POST_TO; k++) {
      post[0] += vx[k % VEC_RING]; post[1] += vy[k % VEC_RING]; post[2] += vz[k % VEC_RING];
    }
    turn = angleDeg(pend_pre, post);
  }
  pending = false;
  bool no_turn   = turn >= 0 && turn <= CONFIRM_MIN_TURN_DEG;
  bool no_motion = data_ok && pend_min_g > NO_DIP_ABOVE_G && pend_max_dps < SLOW_TURN_BELOW_DPS;
  if (no_turn || no_motion) {
    Out.printf("             -> NOT a fall (turn=%.0f deg, min_g=%.2f, max_dps=%.0f)\n",
               turn, pend_min_g, pend_max_dps);
    return;
  }

  falls++;
#if RAW_STREAM
  Out.printf("# EVENT FALL,%lu,%.3f,peak_g=%.2f,turn_deg=%.1f,min_g_before=%.2f,max_dps_before=%.0f\n",
             pend_index, pend_score, pend_peak, turn, pend_min_g, pend_max_dps);
#endif
  Out.println(ALERT_TEXT);
  Out.printf("Fall score %.2f, peak %.2f g, wrist turned %.0f deg.\n", pend_score, pend_peak, turn);
  playFallAlertTone();

  // Option 1: If CLOUD_SERVER_HOST is set in wifi_config.h, run the check-in directly with Cloud Run / Railway
  if (strlen(CLOUD_SERVER_HOST) > 0 && runCloudCheckIn(pend_score)) {
    return;
  }

#if HALT_ON_FALL
  alarm_active  = true;
  last_alert_ms = millis();
  Out.println("Sampling stopped. Waiting for laptop check-in ('r' to resume, or press BOOT button).");
#endif
}

// ---------------- setup ----------------
void setup() {
  Serial.setRxBufferSize(4096);
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT && ARDUINO_USB_MODE
  Serial.setTxTimeoutMs(0);             // on battery (no USB host) never wait for USB: lines just go to Wi-Fi
#endif
  delay(1500);

  pinMode(BTN_BOOT_PIN, INPUT_PULLUP);

  initAudio();
  playToneI2S(523.25f, 100);
  playToneI2S(659.25f, 140);

  // Wi-Fi: scan first (before WiFi.begin) so we can verify the radio sees 2.4 GHz networks
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true, true);
  delay(200);
  int found = WiFi.scanNetworks();
  Serial.printf("# [Wi-Fi] scan found %d networks:\n", found);
  for (int i = 0; i < found && i < 10; i++) {
    Serial.printf("#   [%d] '%s' (RSSI %d dBm, ch %d)\n", i, WiFi.SSID(i).c_str(), WiFi.RSSI(i), WiFi.channel(i));
  }

  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
    Serial.printf("\n# [Wi-Fi] disconnected, reason=%d (201=not found, 15/202=password/WPA3, 205=weak signal)\n",
                  info.wifi_sta_disconnected.reason);
  }, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);

  Out.printf("# connecting to Wi-Fi '%s'", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) { delay(250); Out.print("."); }
  if (WiFi.status() == WL_CONNECTED) {
    Out.printf(" connected, IP %s\n", WiFi.localIP().toString().c_str());
  } else {
    Out.printf(" not yet (status=%d; keeps retrying in background)\n", (int)WiFi.status());
  }
  if (MDNS.begin("espwatch-c3")) MDNS.addService("silverguard", "tcp", TCP_PORT);
  server.begin();
  server.setNoDelay(true);
  Out.printf("# laptop link: TCP port %u (espwatch-c3.local)\n", TCP_PORT);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(I2C_HZ);

  uint8_t who = readReg(REG_WHO_AM_I);
  Out.printf("# WHO_AM_I: 0x%02X\n", who);
  writeReg(REG_PWR_MGMT_1, VAL_PWR_MGMT_1);
  delay(100);
  writeReg(REG_CONFIG,       VAL_CONFIG);
  writeReg(REG_SMPLRT_DIV,   VAL_SMPLRT_DIV);
  writeReg(REG_GYRO_CONFIG,  VAL_GYRO_CONFIG);
  writeReg(REG_ACCEL_CONFIG, VAL_ACCEL_CONFIG);

  checkReg("PWR_MGMT_1",   REG_PWR_MGMT_1,   VAL_PWR_MGMT_1);
  checkReg("CONFIG",       REG_CONFIG,       VAL_CONFIG);
  checkReg("SMPLRT_DIV",   REG_SMPLRT_DIV,   VAL_SMPLRT_DIV);
  checkReg("GYRO_CONFIG",  REG_GYRO_CONFIG,  VAL_GYRO_CONFIG);
  checkReg("ACCEL_CONFIG", REG_ACCEL_CONFIG, VAL_ACCEL_CONFIG);

  Out.println("# calibrating gyro: keep the watch STILL for 2 s...");
  calibrateGyro();
#if SELF_TEST
  selfTest();
#endif

  printHeader();
  next_time   = micros() + PERIOD_US;
  last_status = micros();
}

// ---------------- loop ----------------
void loop() {
  // 1. laptop link: accept a (new) TCP connection; the newest one wins
  if (server.hasClient()) {
    WiFiClient c = server.accept();
    if (client && client.connected()) client.stop();
    client = c;
    client.setNoDelay(true);
    Serial.printf("# laptop connected over Wi-Fi from %s\n", client.remoteIP().toString().c_str());
    printHeader();
  }

  // 2. commands from the laptop (Wi-Fi) or USB: 'h', 'r', 'P', 'L', 'b', 'm'
  Stream* sources[2] = {&Serial, (client && client.connected()) ? (Stream*)&client : nullptr};
  for (Stream* src : sources) {
    while (src && src->available()) {
      char c = src->read();
      if (c == 'h') printHeader();
      else if (c == 't' || c == 'T') { Out.println("# Manual cloud check-in test..."); runCloudCheckIn(0.95f); next_time = micros() + PERIOD_US; }
      else if (c == 'P') { handlePlay(*src);   next_time = micros() + PERIOD_US; }
      else if (c == 'L') { handleListen(*src); next_time = micros() + PERIOD_US; }
      else if (c == 'b') { Out.println("# speaker test: playing the alert tone"); playFallAlertTone(); next_time = micros() + PERIOD_US; }
      else if (c == 'm') { testMicLevel(); next_time = micros() + PERIOD_US; }
      else if ((c == 'r' || c == 'R') && alarm_active) resumeFromAlarm("laptop 'r'");
    }
  }

  static uint32_t last_ip_print = 0;
  if (!(client && client.connected()) && WiFi.status() == WL_CONNECTED && millis() - last_ip_print > 10000) {
    last_ip_print = millis();
    Serial.printf("# waiting for laptop: python -m basestation.voice.watch_bridge --host %s\n",
                  WiFi.localIP().toString().c_str());
  }

  // 3. fall check-in mode
  if (alarm_active) {
    if (digitalRead(BTN_BOOT_PIN) == LOW) {
      delay(50);
      while (digitalRead(BTN_BOOT_PIN) == LOW) delay(10);
      resumeFromAlarm("watch BOOT button");
      return;
    }
    if (millis() - last_alert_ms >= 1000) {
      last_alert_ms = millis();
      Out.println(ALERT_TEXT);
    }
    return;
  }

  uint32_t now = micros();
  if ((int32_t)(now - next_time) < 0) return;

  uint32_t late = now - next_time;
  if (late >= PERIOD_US) {
    uint32_t missed = late / PERIOD_US;
    dropped += missed; sample_index += missed; next_time += missed * PERIOD_US;
    ring_filled = 0; confirmPending(false); seq = 0;
  }
  next_time += PERIOD_US;

  int16_t v[7];
  if (!readSample(v)) {
    read_errors++; sample_index++; ring_filled = 0; confirmPending(false); seq = 0;
    return;
  }

#if RAW_STREAM
  Out.printf("%lu,%lu,%d,%d,%d,%d,%d,%d\n",
             sample_index, now, v[0], v[1], v[2], v[4], v[5], v[6]);
#endif
  sample_index++; sent++;

  float ax = v[0] / ACC_LSB_PER_G, ay = v[1] / ACC_LSB_PER_G, az = v[2] / ACC_LSB_PER_G;
  float gx = v[4] / GYRO_LSB_PER_DPS - gyro_bias[0];
  float gy = v[5] / GYRO_LSB_PER_DPS - gyro_bias[1];
  float gz = v[6] / GYRO_LSB_PER_DPS - gyro_bias[2];
  am_ring[ring_pos] = sqrtf(ax * ax + ay * ay + az * az);
  gm_ring[ring_pos] = sqrtf(gx * gx + gy * gy + gz * gz);
  ring_pos = (ring_pos + 1) % HAR_WIN;
  if (ring_filled < HAR_WIN) ring_filled++;
  vx[seq % VEC_RING] = ax; vy[seq % VEC_RING] = ay; vz[seq % VEC_RING] = az;
  amh[seq % VEC_RING] = am_ring[(ring_pos + HAR_WIN - 1) % HAR_WIN];
  gmh[seq % VEC_RING] = gm_ring[(ring_pos + HAR_WIN - 1) % HAR_WIN];
  seq++; since_result++;

  if (ring_filled == HAR_WIN && since_result >= RESULT_EVERY) {
    since_result = 0;
    runModels();
  }
  confirmPending(true);

  if (now - last_status >= STATUS_US) {
    last_status = now;
    Out.printf("# status: sent=%lu dropped=%lu read_errors=%lu falls=%lu max_model_us=%lu\n",
               sent, dropped, read_errors, falls, max_model_us);
  }
}
