// watch_streaming.ino: the FULL watch firmware with live streaming to Cloud Run / Firestore.
//
// WHAT THIS FILE DOES
//   This is firmware/FinalPrototype/ESP32-SpeakerUSBVer.ino (copied 2026-11-21) plus 4 lines,
//   each marked "STREAMING:", that hook in silverguard_stream.h. Everything else (sensor, fall
//   and still/moving models, speaker, mic, USB voice check-in with watch_bridge.py) is unchanged
//   and still works over USB. On top of that, the watch now also sends its still/moving
//   changes, confirmed falls and a heartbeat online over Wi-Fi.
//
// Setup: copy stream_config.example.h to stream_config.h and fill it in.
// Arduino IDE: Tools -> Partition Scheme -> "Huge APP (3MB No OTA)" is REQUIRED (1.30 MB = 99% of the default).
// If FinalPrototype changes, re-copy it and re-add the 4 STREAMING lines.
//
// ---- original description ----
// SilverGuard logger + on-device models + USB Serial I2S Audio (ESP32-C3 + MPU-6050 + INMP441 + MAX98357A)
//   - MPU-6050 at 50 Hz (SDA = GPIO 1, SCL = GPIO 0)
//   - MAX98357A I2S Speaker Amp (BCLK = GPIO 4, LRC = GPIO 5, DIN = GPIO 6)
//   - INMP441 I2S Digital Mic   (SCK  = GPIO 4, WS  = GPIO 5, SD  = GPIO 7, L/R = GND)
//   - HAR forest: still / moving, every 0.5 s on the last 2.56 s
//   - Fall model: 2.5 g trigger + forest + wrist-turn + pre-impact motion check
//   - USB Serial Voice Check-In Audio: streams TTS audio from laptop to MAX98357A speaker
//     and records wearer's reply from INMP441 mic back to laptop for Whisper STT + Claude.

#include <Wire.h>
#include <ESP_I2S.h>
#include <Preferences.h>   // keeps the calibration in flash, so a bumped boot still uses it
#include "fall_features.h"
#include "fall_model.h"     // fall forest + FALL_TRIGGER_G, FALL_PROBA_THRESHOLD, FALL_WIN
#include "har_model.h"      // still / moving forest
#include "silverguard_stream.h" // STREAMING: Wi-Fi -> Cloud Run -> Firestore (needs stream_config.h)

// ---------------- switches ----------------
#define RAW_STREAM   0      // 0 = readable "Activity:" lines (live demo), 1 = raw CSV + '# result' lines (for record.py)
#define HALT_ON_FALL 1      // 1 = on a fall, stop sampling and repeat the alert until 'r' is sent (or reset)
#define SELF_TEST    1      // 1 = check features + models against Python at boot

#if SELF_TEST
#include "test_vectors.h"
#endif

// ---------------- settings ----------------
const char*    FW_VERSION   = "ESP32-SpeakerUSBVer 0.5 (HAR + fall models + INMP441 mic + MAX98357A speaker) + Wi-Fi stream";
const int      SDA_PIN      = 1;          // MPU-6050 I2C data  (GPIO 1)
const int      SCL_PIN      = 0;          // MPU-6050 I2C clock (GPIO 0)

// Shared I2S bus (MAX98357A speaker amp + INMP441 digital microphone)
const int      I2S_BCLK_PIN = 4;          // MAX98357A BCLK + INMP441 SCK (GPIO 4)
const int      I2S_LRC_PIN  = 5;          // MAX98357A LRC  + INMP441 WS  (GPIO 5)
const int      I2S_DOUT_PIN = 6;          // MAX98357A DIN  (speaker data out, GPIO 6)
const int      I2S_DIN_PIN  = 7;          // INMP441   SD   (mic data in, GPIO 7)
const uint32_t AUDIO_RATE   = 16000;      // 16 kHz mono PCM (matches Whisper STT on laptop)
const int16_t  TONE_AMP     = 10000;      // Alert / test tone amplitude (0..32767)

I2SClass i2s;
bool     i2s_ok = false;

const uint32_t I2C_HZ     = 400000;     // 400 kHz "fast mode", 14-byte read takes ~0.4 ms
const uint8_t  MPU_ADDR   = 0x68;       // AD0 pin low -> 0x68 (0x69 if AD0 is high)
const uint32_t PERIOD_US  = 20000;      // 20 ms between samples = 50 Hz
const uint32_t STATUS_US  = 10000000;   // status line every 10 s

const float    ACC_LSB_PER_G    = 2048.0f;   // +-16 g
const float    GYRO_LSB_PER_DPS = 16.4f;     // +-2000 dps
const int      HAR_WIN          = 128;       // 2.56 s, like the UCI training windows
const int      RESULT_EVERY     = 25;        // run the models every 25 samples = 0.5 s
const uint32_t LOCKOUT_SAMPLES  = 5 * 50;    // after a fall candidate, ignore new ones for 5 s (one fall = one event)

// Stage 3 (confirmation): a real fall leaves the wrist in a different position; a table hit or a clap does not.
const float    CONFIRM_MIN_TURN_DEG = 20.0f;
const int      PRE_FROM  = -75, PRE_TO  = -25;   // samples relative to the impact (-1.5 s .. -0.5 s)
const int      POST_FROM =  50, POST_TO = 125;   // samples relative to the impact (+1.0 s .. +2.5 s)
const int      VEC_RING  = 256;                  // 5.1 s of accel x/y/z history (enough for before + after)

// Stage 3b (no-fall-motion check): in the 1 s before a real fall's impact the wrist drops and rotates fast.
const float    NO_DIP_ABOVE_G      = 0.85f;     // min |a| before the spike stayed above this -> no drop
const float    SLOW_TURN_BELOW_DPS = 150.0f;    // max gyro before the spike stayed below this -> no fast rotation
const int      MOTION_FROM = -50;               // look at the 1 s before the impact

// MPU-6050 register addresses (from the register map)
const uint8_t REG_SMPLRT_DIV   = 0x19;
const uint8_t REG_CONFIG       = 0x1A;
const uint8_t REG_GYRO_CONFIG  = 0x1B;
const uint8_t REG_ACCEL_CONFIG = 0x1C;
const uint8_t REG_ACCEL_XOUT_H = 0x3B;  // first of 14 data bytes
const uint8_t REG_PWR_MGMT_1   = 0x6B;
const uint8_t REG_WHO_AM_I     = 0x75;

// Values we write (and later read back to check)
const uint8_t VAL_PWR_MGMT_1   = 0x01;  // SLEEP=0 (wake up), CLKSEL=1 (X-gyro clock, more stable than the internal 8 MHz)
const uint8_t VAL_CONFIG       = 0x04;  // DLPF_CFG=4: ~21 Hz accel / 20 Hz gyro low-pass, removes content above Nyquist (25 Hz)
const uint8_t VAL_SMPLRT_DIV   = 19;    // sensor rate = 1 kHz / (1 + 19) = 50 Hz (1 kHz because the DLPF is on)
const uint8_t VAL_GYRO_CONFIG  = 0x18;  // FS_SEL=3: +-2000 dps, 16.4 LSB per dps (falls can exceed 500 dps)
const uint8_t VAL_ACCEL_CONFIG = 0x18;  // AFS_SEL=3: +-16 g, 2048 LSB per g (impacts can exceed 8 g)

// ---------------- counters ----------------
uint32_t next_time    = 0;   // micros() when the next sample is due
uint32_t sample_index = 0;   // counts sample slots; skipped slots leave a gap
uint32_t sent         = 0;   // samples read OK
uint32_t dropped      = 0;   // slots missed because we were late
uint32_t read_errors  = 0;   // I2C reads that did not return 14 bytes
uint32_t last_status  = 0;
uint32_t falls        = 0;   // fall events raised
uint32_t max_model_us = 0;   // slowest model run so far (must stay well below 20000)

// ---------------- model state ----------------
float    gyro_bias[3] = {0, 0, 0};   // deg/s, measured at boot while still
float    acc_scale    = 1.0f;        // makes |a| read 1.00 g at rest (this MPU-6050 reads ~1.23 g; the models were trained on 1.00 g)
float    am_ring[HAR_WIN];           // last 128 accel magnitudes (g)
float    gm_ring[HAR_WIN];           // last 128 gyro magnitudes (deg/s)
int      ring_pos     = 0;           // where the next sample goes
int      ring_filled  = 0;           // how many valid samples (restart after a gap)
int      since_result = 0;
uint32_t lockout_until = 0;          // sample_index before which no new fall alarm is raised
bool     alarm_active  = false;      // true = a fall was detected and sampling is stopped
uint32_t last_alert_ms = 0;

float    vx[VEC_RING], vy[VEC_RING], vz[VEC_RING];   // accel x/y/z history (g) for the confirmation stage
float    amh[VEC_RING], gmh[VEC_RING];               // |accel| (g) and |gyro| (deg/s) history
uint32_t seq = 0;                    // samples stored since the last gap (sample k lives at vx[k % VEC_RING])

// a "possible fall" waiting for the after-spike data
bool     pending       = false;
uint32_t pend_impact   = 0;          // seq of the impact sample
uint32_t pend_index    = 0;          // sample_index of the impact (for the event line)
float    pend_pre[3]   = {0, 0, 0};  // mean gravity direction before the spike
bool     pend_has_pre  = false;
float    pend_score    = 0, pend_peak = 0;
float    pend_min_g    = 0, pend_max_dps = 0;   // fall motion in the 1 s before the spike

const char* ALERT_TEXT = "ALERT ALERT ALERT ALERT ALERT ALERT ALERT FALL DETECTEDDD!!!";
const char* ACTIVITY_NAME[2] = {"Still", "Moving"};   // HAR classes: 0 = still, 1 = moving

// ---------------- I2S audio helpers (MAX98357A speaker + INMP441 mic) ----------------
// INMP441 requires 64 BCLK cycles per stereo frame (32 bits/channel). MAX98357A also accepts
// 32-bit stereo I2S frames and mixes (L+R)/2 in mono. Sharing BCLK (GPIO 4) and WS/LRC (GPIO 5)
// lets both chips run on the ESP32-C3's single I2S0 controller simultaneously.
void initAudio() {
  i2s.setPins(I2S_BCLK_PIN, I2S_LRC_PIN, I2S_DOUT_PIN, I2S_DIN_PIN);
  i2s_ok = i2s.begin(
    I2S_MODE_STD,
    AUDIO_RATE,
    I2S_DATA_BIT_WIDTH_32BIT,
    I2S_SLOT_MODE_STEREO,
    I2S_STD_SLOT_BOTH
  );
  Serial.printf("# audio I2S (BCLK=%d, LRC=%d, DOUT=%d, DIN=%d @ %lu Hz): %s\n",
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

bool readSerialExact(uint8_t* dst, size_t len, uint32_t timeoutMs = 1500) {
  size_t got = 0;
  uint32_t t0 = millis();
  while (got < len && (millis() - t0) < timeoutMs) {
    int avail = Serial.available();
    if (avail > 0) {
      size_t toRead = (size_t)avail < (len - got) ? (size_t)avail : (len - got);
      got += Serial.readBytes(dst + got, toRead);
    } else {
      delayMicroseconds(200);
    }
  }
  return got == len;
}

// Protocol 'P': PC sends 'P' + uint16_le(n_samples) + int16_le[n_samples] -> plays on MAX98357A, replies 'K'
void handleSerialPlay() {
  uint8_t hdr[2];
  if (!readSerialExact(hdr, 2, 1000)) return;
  uint16_t nSamples = (uint16_t)hdr[0] | ((uint16_t)hdr[1] << 8);
  if (nSamples == 0) {
    writeSilenceI2S();
    Serial.write('K');
    return;
  }

  const size_t CHUNK = 128;
  int16_t pcm16[CHUNK];
  int32_t stereo32[CHUNK * 2];
  uint16_t remaining = nSamples;

  while (remaining > 0) {
    uint16_t n = (remaining > CHUNK) ? CHUNK : remaining;
    if (!readSerialExact((uint8_t*)pcm16, n * sizeof(int16_t), 1500)) return;
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
  Serial.write('K');
}

// Protocol 'L': PC sends 'L' + uint16_le(n_samples) -> ESP32 reads INMP441 mic and replies 'M' + int16_le[n_samples]
void handleSerialListen() {
  uint8_t hdr[2];
  if (!readSerialExact(hdr, 2, 1000)) return;
  uint16_t nSamples = (uint16_t)hdr[0] | ((uint16_t)hdr[1] << 8);

  Serial.write('M');  // sync byte telling Python that raw int16 PCM samples follow

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
        // INMP441 outputs 24-bit signed data in the top bits of the 32-bit slot (Left if L/R=GND, Right if L/R=3.3V)
        int32_t left  = rawStereo[2 * i];
        int32_t right = rawStereo[2 * i + 1];
        int32_t raw   = (left != 0) ? left : right;
        int32_t val   = raw >> 14;   // 24-bit MSB -> 16-bit with +6 dB digital gain for speech
        if (val >  32767) val =  32767;
        if (val < -32768) val = -32768;
        outPcm16[i] = (int16_t)val;
      } else {
        outPcm16[i] = 0;
      }
    }
    Serial.write((const uint8_t*)outPcm16, n * sizeof(int16_t));
    remaining -= n;
  }
}

// Serial command 'm': 1-second INMP441 microphone self-test for Arduino Serial Monitor
void testMicLevel() {
  if (!i2s_ok) {
    Serial.println("# mic test: I2S not initialised");
    return;
  }
  const int N = 16000;  // 1 second at 16 kHz
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
  Serial.printf("# mic test (1 s): peak=%ld/32767, rms=%.4f %s\n",
                (long)peak, rms, peak > 50 ? "(INMP441 OK)" : "(quiet/check SD=GPIO7, SCK=GPIO4, WS=GPIO5)");
}

// ---------------- I2C helpers ----------------
void writeReg(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);                      // which register
  Wire.write(value);                    // what to put in it
  Wire.endTransmission();
}

uint8_t readReg(uint8_t reg) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);          // false = keep the bus (repeated start) for the read
  Wire.requestFrom(MPU_ADDR, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0xFF;   // 0xFF = no answer
}

void checkReg(const char* name, uint8_t reg, uint8_t expected) {
  uint8_t got = readReg(reg);
  Serial.printf("# reg %s (0x%02X) = 0x%02X, expected 0x%02X %s\n",
                name, reg, got, expected, got == expected ? "OK" : "MISMATCH");
}

// burst-read 14 bytes: ax ay az temp gx gy gz, each big-endian int16 (high byte first)
bool readSample(int16_t v[7]) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(REG_ACCEL_XOUT_H);
  Wire.endTransmission(false);
  if (Wire.requestFrom(MPU_ADDR, (uint8_t)14) != 14) return false;
  for (int i = 0; i < 7; i++) {
    uint8_t hi = Wire.read();                    // read in two steps: C++ does not fix the order inside one expression
    uint8_t lo = Wire.read();
    v[i] = (int16_t)((hi << 8) | lo);
  }
  return true;                                   // v[3] is temperature, not used
}

// Header: '#' lines that record.py saves at the top of each file.
void printHeader() {
  Serial.printf("# firmware: %s\n", FW_VERSION);
  Serial.println("# sensor: MPU-6050, raw int16 counts, no conversion on device");
  Serial.println("# accel_range: +-16 g");
  Serial.println("# accel_lsb_per_g: 2048");
  Serial.println("# gyro_range: +-2000 dps");
  Serial.println("# gyro_lsb_per_dps: 16.4");
  Serial.println("# dlpf: CONFIG=0x04 (~21 Hz accel, 20 Hz gyro)");
  Serial.printf("# sample_rate_hz: %lu\n", 1000000UL / PERIOD_US);
  Serial.printf("# gyro_bias_dps: %.3f,%.3f,%.3f (subtracted before the models, raw CSV is unchanged)\n",
                gyro_bias[0], gyro_bias[1], gyro_bias[2]);
  Serial.printf("# models: HAR forest (still=0/moving=1, %d samples), fall = peak > %.1f g then forest score >= %.3f (%d samples)\n",
                HAR_WIN, FALL_TRIGGER_G, FALL_PROBA_THRESHOLD, FALL_WIN);
  Serial.printf("# confirm: a fall candidate only alarms if the wrist turns > %.0f deg (before vs 1-2.5 s after the spike)\n",
                CONFIRM_MIN_TURN_DEG);
  Serial.println("# result columns: # result,sample_index,har,peak_g,fall_score,fall   (fall_score -1 = trigger not fired; fall = candidate)");
  Serial.printf("# confirm: also rejected if, in the 1 s before the spike, |a| stayed > %.2f g AND rotation < %.0f dps (no falling motion)\n",
                NO_DIP_ABOVE_G, SLOW_TURN_BELOW_DPS);
  Serial.println("# event lines: # EVENT FALL / # EVENT REJECTED,impact_sample_index,score,peak_g=..,turn_deg=..,min_g_before=..,max_dps_before=..");
  Serial.println("# columns: sample_index,micros,ax,ay,az,gx,gy,gz");
}

// Keep still ~2 s at boot: a still gyro should read 0, whatever it reads is bias.
// One 2 s attempt. Returns false if the watch moved (or reads failed); then nothing is changed.
bool calibrateOnce() {
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
  if (got < N / 2) { Serial.println("# calibration: too many read errors"); return false; }
  float spread = sqrtf(fmaxf(saa / got - (sa / got) * (sa / got), 0));
  if (spread > 0.02f) { Serial.println("# calibration: watch moved, keep it STILL..."); return false; }
  float rest_g = sa / got;                              // should be 1.00 g lying still
  if (rest_g < 0.7f || rest_g > 1.4f) {
    Serial.printf("# calibration: accel reads %.2f g at rest (expected ~1.0), rejected\n", rest_g);
    return false;
  }
  for (int a = 0; a < 3; a++) gyro_bias[a] = sg[a] / got;
  acc_scale = 1.0f / rest_g;
  Serial.printf("# accel_rest_g: %.3f -> scale %.3f (so rest reads 1.00 g for the models)\n", rest_g, acc_scale);
  return true;
}

// Try up to 5 times (~12 s). A good result is saved to flash; if every try fails (e.g. the watch
// is being handled while it boots), the last saved calibration is used instead of no correction.
void calibrateGyro() {
  Preferences prefs;
  prefs.begin("sg_calib", false);
  for (int attempt = 1; attempt <= 5; attempt++) {
    if (calibrateOnce()) {
      prefs.putFloat("acc_scale", acc_scale);
      prefs.putFloat("gb0", gyro_bias[0]); prefs.putFloat("gb1", gyro_bias[1]); prefs.putFloat("gb2", gyro_bias[2]);
      prefs.end();
      return;
    }
    delay(500);
  }
  if (prefs.isKey("acc_scale")) {
    acc_scale    = prefs.getFloat("acc_scale", 1.0f);
    gyro_bias[0] = prefs.getFloat("gb0", 0); gyro_bias[1] = prefs.getFloat("gb1", 0); gyro_bias[2] = prefs.getFloat("gb2", 0);
    Serial.printf("# WARNING: could not calibrate (kept moving), using the SAVED calibration: scale %.3f\n", acc_scale);
  } else {
    Serial.println("# WARNING: could not calibrate and nothing saved yet: no correction. Press reset with the watch STILL.");
  }
  prefs.end();
}

#if SELF_TEST
// Run the Python-made test windows through the same C code and compare the answers.
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
    float worst = 0;
    for (int k = 0; k < FALL_N_FEATURES; k++) {
      float ref = TV_FEAT[w * FALL_N_FEATURES + k];
      float err = fabsf(f[k] - ref) / fmaxf(1.0f, fabsf(ref));    // relative error (absolute below 1)
      if (err > worst) worst = err;
    }
    fall_model_predict_proba(f, FALL_N_FEATURES, proba, 2);
    bool score_ok = fabsf(proba[1] - TV_SCORE[w]) < 1e-4f;

    har_features(&TV_HAR_MAG[w * TV_HAR_WIN], TV_HAR_WIN, hf);
    float hworst = 0;
    for (int k = 0; k < 5; k++) {
      float ref = TV_HAR_FEAT[w * 5 + k];
      float err = fabsf(hf[k] - ref) / fmaxf(1.0f, fabsf(ref));
      if (err > hworst) hworst = err;
    }
    int har = har_model_predict(hf, 5);
    bool ok = worst < 1e-3f && score_ok && hworst < 1e-3f && har == TV_HAR_CLASS[w];
    if (!ok) failed++;
    Serial.printf("# self-test %-15s fall features err %.1e, score %.3f (python %.3f), HAR features err %.1e, HAR %d (python %d)  %s\n",
                  TV_NAME[w], worst, proba[1], TV_SCORE[w], hworst, har, TV_HAR_CLASS[w], ok ? "PASS" : "FAIL");
  }
  Serial.printf("# self-test: %s\n", failed ? "FAILED - device maths does not match Python, do not trust the models" : "all PASS");
}
#endif

// Every 0.5 s: run both models on the newest samples in the rings.
void runModels() {
  uint32_t t0 = micros();
  float am[HAR_WIN], gm[HAR_WIN];
  for (int i = 0; i < HAR_WIN; i++) {               // unroll the ring into time order (oldest first)
    int j = (ring_pos + i) % HAR_WIN;
    am[i] = am_ring[j]; gm[i] = gm_ring[j];
  }

  // HAR on all 128 samples
  float hf[5];
  har_features(am, HAR_WIN, hf);
  int har = har_model_predict(hf, 5);
  sgstream::onHar(har);                             // STREAMING: sends only debounced still/moving changes

  // fall model on the newest 125 samples
  const float* fam = am + (HAR_WIN - FALL_WIN);
  const float* fgm = gm + (HAR_WIN - FALL_WIN);
  float peak = 0;
  for (int i = 0; i < FALL_WIN; i++) if (fam[i] > peak) peak = fam[i];
  float score = -1.0f;                              // -1 = stage 1 (trigger) did not fire
  bool fall = false;
  if (peak > FALL_TRIGGER_G) {                      // stage 1: big spike?
    float f[FALL_N_FEATURES], proba[2];
    fall_features(fam, fgm, FALL_WIN, f);           // stage 2: the forest decides
    fall_model_predict_proba(f, FALL_N_FEATURES, proba, 2);
    score = proba[1];
    fall = score >= FALL_PROBA_THRESHOLD;
  }

  uint32_t dt = micros() - t0;
  if (dt > max_model_us) max_model_us = dt;

  uint32_t newest = sample_index - 1;               // index of the last sample in the window
  bool new_candidate = fall && !pending && newest >= lockout_until;
#if RAW_STREAM
  Serial.printf("# result,%lu,%d,%.2f,%.3f,%d\n", newest, har, peak, score, fall ? 1 : 0);   // machine-readable for record.py
#else
  if (score < 0)                                    // trigger did not fire: forest not run
    Serial.printf("[%8.1f s]  Activity: %-7s | peak %5.2f g | fall check: -\n", millis() / 1000.0f, ACTIVITY_NAME[har], peak);
  else                                              // big spike: show what the forest thought
    Serial.printf("[%8.1f s]  Activity: %-7s | peak %5.2f g | fall check: score %.2f (alarm at %.2f) -> %s\n",
                  millis() / 1000.0f, ACTIVITY_NAME[har], peak, score, FALL_PROBA_THRESHOLD,
                  !fall ? "not a fall" : new_candidate ? "possible fall, checking wrist position for 2.5 s..." : "possible fall (already checking)");
#endif

  // stage 3 starts here: remember where gravity pointed before the spike, decide 2.5 s later in confirmPending()
  if (new_candidate) {
    int ip = 0;                                     // where the spike is inside the 125-sample window
    for (int i = 1; i < FALL_WIN; i++) if (fam[i] > fam[ip]) ip = i;
    pend_impact = (seq - FALL_WIN) + ip;            // window = the last FALL_WIN stored samples
    pend_index  = newest - (FALL_WIN - 1) + ip;
    pend_score  = score; pend_peak = peak;
    pending     = true;
    lockout_until = newest + LOCKOUT_SAMPLES;       // the same spike will appear in the next windows: ignore it
    // mean accel vector before the spike (only as far back as we have data since the last gap)
    int32_t from = (int32_t)pend_impact + PRE_FROM, to = (int32_t)pend_impact + PRE_TO;
    if (from < 0) from = 0;
    pend_has_pre = (to - from) >= 10;
    pend_pre[0] = pend_pre[1] = pend_pre[2] = 0;
    for (int32_t k = from; k < to; k++) {
      pend_pre[0] += vx[k % VEC_RING]; pend_pre[1] += vy[k % VEC_RING]; pend_pre[2] += vz[k % VEC_RING];
    }
    // how the wrist moved in the 1 s leading into the spike: lowest |a| and fastest rotation
    int32_t m0 = (int32_t)pend_impact + MOTION_FROM;
    if (m0 < 0) m0 = 0;
    pend_min_g = 99.0f; pend_max_dps = 0.0f;
    for (int32_t k = m0; k <= (int32_t)pend_impact; k++) {
      if (k < (int32_t)pend_impact && amh[k % VEC_RING] < pend_min_g) pend_min_g = amh[k % VEC_RING];
      if (gmh[k % VEC_RING] > pend_max_dps) pend_max_dps = gmh[k % VEC_RING];
    }
  }
}

// Angle (deg) between two vectors = how far the wrist turned relative to gravity.
float angleDeg(const float* u, const float* w) {
  float d = u[0] * w[0] + u[1] * w[1] + u[2] * w[2];
  float nu = sqrtf(u[0] * u[0] + u[1] * u[1] + u[2] * u[2]), nw = sqrtf(w[0] * w[0] + w[1] * w[1] + w[2] * w[2]);
  if (nu < 1e-6f || nw < 1e-6f) return 180.0f;
  float c = d / (nu * nw);
  if (c > 1) c = 1;
  if (c < -1) c = -1;
  return acosf(c) * 57.29578f;
}

// Called after every stored sample: once 2.5 s have passed since the spike, accept or reject the candidate.
// data_ok = false means the history broke (gap): then we alarm anyway, missing a fall is worse than a false alarm.
void confirmPending(bool data_ok) {
  if (!pending) return;
  if (data_ok && seq < pend_impact + POST_TO) return;   // after-spike data not complete yet

  float turn = -1.0f;                                   // -1 = could not measure
  if (data_ok && pend_has_pre) {
    float post[3] = {0, 0, 0};
    for (uint32_t k = pend_impact + POST_FROM; k < pend_impact + POST_TO; k++) {
      post[0] += vx[k % VEC_RING]; post[1] += vy[k % VEC_RING]; post[2] += vz[k % VEC_RING];
    }
    turn = angleDeg(pend_pre, post);                    // sums point the same way as means: no need to divide
  }
  pending = false;
  bool no_turn   = turn >= 0 && turn <= CONFIRM_MIN_TURN_DEG;                                   // position did not change
  bool no_motion = data_ok && pend_min_g > NO_DIP_ABOVE_G && pend_max_dps < SLOW_TURN_BELOW_DPS;  // no drop + slow turn
  bool confirmed = !no_turn && !no_motion;

  if (!confirmed) {
#if RAW_STREAM
    Serial.printf("# EVENT REJECTED,%lu,%.3f,peak_g=%.2f,turn_deg=%.1f,min_g_before=%.2f,max_dps_before=%.0f\n",
                  pend_index, pend_score, pend_peak, turn, pend_min_g, pend_max_dps);
#else
    if (no_turn)
      Serial.printf("             -> NOT a fall: wrist only turned %.0f deg (needs > %.0f). Probably a hit / clap / tap. (before: lowest %.2f g, fastest turn %.0f dps)\n",
                    turn, CONFIRM_MIN_TURN_DEG, pend_min_g, pend_max_dps);
    else
      Serial.printf("             -> NOT a fall: no falling motion before the knock (lowest %.2f g, needs < %.2f; fastest turn %.0f dps, needs > %.0f). Probably put down.\n",
                    pend_min_g, NO_DIP_ABOVE_G, pend_max_dps, SLOW_TURN_BELOW_DPS);
#endif
    return;
  }

  falls++;
  sgstream::sendFall(pend_score, pend_peak, turn);  // STREAMING: confirmed fall goes online at once
#if RAW_STREAM
  Serial.printf("# EVENT FALL,%lu,%.3f,peak_g=%.2f,turn_deg=%.1f,min_g_before=%.2f,max_dps_before=%.0f\n",
                pend_index, pend_score, pend_peak, turn, pend_min_g, pend_max_dps);
#endif
  Serial.println(ALERT_TEXT);
  if (turn < 0) Serial.println("(could not check the wrist position: data gap, alarming to be safe)");
  else Serial.printf("Fall score %.2f, peak %.2f g, wrist turned %.0f deg, lowest %.2f g and fastest turn %.0f dps before the impact.\n",
                     pend_score, pend_peak, turn, pend_min_g, pend_max_dps);
  playFallAlertTone();
#if HALT_ON_FALL
  alarm_active  = true;                                 // loop() stops sampling and keeps repeating the alert
  last_alert_ms = millis();
  Serial.println("Sampling stopped. Send 'r' to resume (or press reset).");
#endif
}

// ---------------- setup ----------------
void setup() {
  Serial.setRxBufferSize(4096);         // larger serial buffer for smooth 16 kHz audio streaming from laptop
  Serial.begin(115200);                 // baud is ignored over native USB, but keep it standard
  delay(1500);                          // give the PC time to open the USB serial port

  initAudio();
  playToneI2S(523.25f, 100);            // short C5-E5 boot chime on MAX98357A speaker
  playToneI2S(659.25f, 140);

  Wire.begin(SDA_PIN, SCL_PIN);         // SDA (GPIO 1) first, then SCL (GPIO 0)
  Wire.setClock(I2C_HZ);

  uint8_t who = readReg(REG_WHO_AM_I);
  Serial.printf("# WHO_AM_I: 0x%02X\n", who);
  if (who != 0x68) Serial.println("# WARNING: WHO_AM_I is not 0x68 (clone chip or wiring problem), continuing");

  writeReg(REG_PWR_MGMT_1, VAL_PWR_MGMT_1);
  delay(100);                           // let the gyro clock settle after wake-up
  writeReg(REG_CONFIG,       VAL_CONFIG);
  writeReg(REG_SMPLRT_DIV,   VAL_SMPLRT_DIV);
  writeReg(REG_GYRO_CONFIG,  VAL_GYRO_CONFIG);
  writeReg(REG_ACCEL_CONFIG, VAL_ACCEL_CONFIG);

  // read back to prove the settings stuck
  checkReg("PWR_MGMT_1",   REG_PWR_MGMT_1,   VAL_PWR_MGMT_1);
  checkReg("CONFIG",       REG_CONFIG,       VAL_CONFIG);
  checkReg("SMPLRT_DIV",   REG_SMPLRT_DIV,   VAL_SMPLRT_DIV);
  checkReg("GYRO_CONFIG",  REG_GYRO_CONFIG,  VAL_GYRO_CONFIG);
  checkReg("ACCEL_CONFIG", REG_ACCEL_CONFIG, VAL_ACCEL_CONFIG);

  Serial.println("# calibrating gyro: keep the watch STILL for 2 s...");
  calibrateGyro();
#if SELF_TEST
  selfTest();
#endif

  printHeader();
  sgstream::begin();                    // STREAMING: start Wi-Fi + the network task
  next_time   = micros() + PERIOD_US;
  last_status = micros();
}

// ---------------- loop ----------------
void loop() {
  // commands from the PC:
  //   'h' = print header (record.py)
  //   'r' = resume after a fall alert
  //   'P' = stream 16 kHz PCM audio chunk from PC to MAX98357A speaker
  //   'L' = record 16 kHz PCM audio chunk from INMP441 mic to PC
  //   'b' = test MAX98357A speaker beep (Serial Monitor)
  //   'm' = test INMP441 microphone 1s level (Serial Monitor)
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'h') printHeader();
    else if (c == 'P') {
      handleSerialPlay();
      next_time = micros() + PERIOD_US;
    }
    else if (c == 'L') {
      handleSerialListen();
      next_time = micros() + PERIOD_US;
    }
    else if (c == 'b') {
      Serial.println("# speaker test: playing chime on MAX98357A...");
      playFallAlertTone();
      next_time = micros() + PERIOD_US;
    }
    else if (c == 'm') {
      testMicLevel();
      next_time = micros() + PERIOD_US;
    }
    else if ((c == 'r' || c == 'R') && alarm_active) {
      alarm_active = false;
      ring_filled  = 0;                              // start fresh windows: the old ones contain the fall
      seq          = 0;
      since_result = 0;
      next_time    = micros() + PERIOD_US;
      Serial.println("Resumed: monitoring again (first result after 2.56 s).");
    }
  }

  // fall detected: stop sampling and keep shouting until someone resumes or resets
  if (alarm_active) {
    if (millis() - last_alert_ms >= 1000) {
      last_alert_ms = millis();
      Serial.println(ALERT_TEXT);
    }
    return;
  }

  uint32_t now = micros();
  if ((int32_t)(now - next_time) < 0) return;   // not time yet (signed difference survives micros() wrap-around)

  // more than one period late -> those slots are lost: count them and skip ahead
  uint32_t late = now - next_time;
  if (late >= PERIOD_US) {
    uint32_t missed = late / PERIOD_US;
    dropped      += missed;
    sample_index += missed;
    next_time    += missed * PERIOD_US;
    ring_filled   = 0;                           // the window now has a hole: start filling it again
    confirmPending(false);                       // a check in progress can't finish: alarm to be safe
    seq           = 0;
  }
  next_time += PERIOD_US;                        // schedule the next sample from the plan, not from 'now' (no drift)

  int16_t v[7];
  bool ok = readSample(v);
  for (int tries = 0; !ok && tries < 2; tries++) ok = readSample(v);   // a glitched I2C read: retry (~0.4 ms each) instead of wiping the window
  if (!ok) {
    read_errors++; sample_index++;               // leave a gap so Python can see it
    ring_filled = 0;
    confirmPending(false);
    seq = 0;
    return;
  }

#if RAW_STREAM
  Serial.printf("%lu,%lu,%d,%d,%d,%d,%d,%d\n",
                sample_index, now, v[0], v[1], v[2], v[4], v[5], v[6]);
#endif
  sample_index++;
  sent++;

  // convert to real units for the models (same units as training: g and deg/s, gyro bias removed)
  const float acc_k = acc_scale / ACC_LSB_PER_G;
  float ax = v[0] * acc_k, ay = v[1] * acc_k, az = v[2] * acc_k;
  float gx = v[4] / GYRO_LSB_PER_DPS - gyro_bias[0];
  float gy = v[5] / GYRO_LSB_PER_DPS - gyro_bias[1];
  float gz = v[6] / GYRO_LSB_PER_DPS - gyro_bias[2];
  am_ring[ring_pos] = sqrtf(ax * ax + ay * ay + az * az);
  gm_ring[ring_pos] = sqrtf(gx * gx + gy * gy + gz * gz);
  ring_pos = (ring_pos + 1) % HAR_WIN;
  if (ring_filled < HAR_WIN) ring_filled++;
  vx[seq % VEC_RING] = ax; vy[seq % VEC_RING] = ay; vz[seq % VEC_RING] = az;   // x/y/z history for stage 3
  amh[seq % VEC_RING] = am_ring[(ring_pos + HAR_WIN - 1) % HAR_WIN];              // the magnitudes just computed
  gmh[seq % VEC_RING] = gm_ring[(ring_pos + HAR_WIN - 1) % HAR_WIN];
  seq++;
  since_result++;

  if (ring_filled == HAR_WIN && since_result >= RESULT_EVERY) {
    since_result = 0;
    runModels();
  }
  confirmPending(true);                          // is a possible fall waiting for its after-spike data?

  if (now - last_status >= STATUS_US) {
    last_status = now;
    Serial.printf("# status: sent=%lu dropped=%lu read_errors=%lu falls=%lu max_model_us=%lu\n",
                  sent, dropped, read_errors, falls, max_model_us);
  }
}
