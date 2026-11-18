// SilverGuard Watch App + On-Device Models: Waveshare ESP32-S3-Touch-AMOLED-2.06
//   - QMI8658 6-axis IMU at 50 Hz (SDA = GPIO 15, SCL = GPIO 14)
//   - HAR forest: still / moving, every 0.5 s on the last 2.56 s
//   - Fall model: 2.5 g trigger + 30-tree forest + wrist-turn (> 20 deg) + pre-impact motion check
//   - On-Watch App:
//       * AXP2101 PMIC (0x34): powers display/touch LDOs + reads battery % (reg 0xA4)
//       * FT3168 Touch (0x38): tap top half ("I'M OK") to cancel alert, bottom half ("NEED HELP") to escalate
//       * CO5300 2.06" AMOLED (410x502 QSPI): live status screen + full-screen Fall Check-In UI
//         (auto-enabled when "GFX Library for Arduino" is installed in Arduino IDE)
//   - Wi-Fi App Link: sends JSON events ({device_id, state, confidence, detector}) to Railway / Caregiver App

#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>

#if __has_include("fall_features.h")
  #include "fall_features.h"
  #include "fall_model.h"     // fall forest + FALL_TRIGGER_G, FALL_PROBA_THRESHOLD, FALL_WIN
  #include "har_model.h"      // still / moving forest
#elif __has_include("../firmware/silverguard_logger/fall_features.h")
  #include "../firmware/silverguard_logger/fall_features.h"
  #include "../firmware/silverguard_logger/fall_model.h"
  #include "../firmware/silverguard_logger/har_model.h"
#else
  #include "../../firmware/silverguard_logger/fall_features.h"
  #include "../../firmware/silverguard_logger/fall_model.h"
  #include "../../firmware/silverguard_logger/har_model.h"
#endif

// ---------------- switches ----------------
#define RAW_STREAM    0     // 0 = readable "Activity:" lines (live demo), 1 = raw CSV + '# result' lines (for record.py)
#define HALT_ON_FALL  1     // 1 = on a fall, pause sampling and show check-in screen until "I'M OK" / 'r' / BOOT button
#define SELF_TEST     1     // 1 = check features + models against Python at boot
#define ENABLE_TOUCH  1     // 1 = enable FT3168 capacitive touch screen ("I'M OK" / "NEED HELP" buttons)
#define ENABLE_SCREEN 1     // 1 = enable CO5300 AMOLED screen UI (requires "GFX Library for Arduino" installed)
#define ENABLE_WIFI   1     // 1 = send fall & status JSON to Railway / Caregiver App when WIFI_SSID is set

#if SELF_TEST
  #if __has_include("test_vectors.h")
    #include "test_vectors.h"
  #elif __has_include("../firmware/silverguard_logger/test_vectors.h")
    #include "../firmware/silverguard_logger/test_vectors.h"
  #else
    #include "../../firmware/silverguard_logger/test_vectors.h"
  #endif
#endif

#if ENABLE_SCREEN && __has_include(<Arduino_GFX_Library.h>)
  #include <Arduino_GFX_Library.h>
  #define HAS_ARDUINO_GFX 1
#else
  #define HAS_ARDUINO_GFX 0
#endif

// ---------------- Wi-Fi & Caregiver App settings ----------------
// Leave WIFI_SSID empty ("") for offline/USB serial testing. Fill in to connect to your Railway server / dashboard.
const char*    WIFI_SSID     = "";
const char*    WIFI_PASS     = "";
const char*    SERVER_URL    = "http://your-railway-app.up.railway.app/api/event";
const char*    DEVICE_ID     = "watch-01";
const uint32_t CHECKIN_MS    = 30000;     // 30 s countdown on the watch before auto-escalating "NEED HELP"

// ---------------- smartwatch hardware pinout ----------------
// From Waveshare ESP32-S3-Touch-AMOLED-2.06 Pin Definition diagram:
//   Shared I2C (QMI8658, FT3168, AXP2101, PCF85063, ES8311): SDA = GPIO 15, SCL = GPIO 14
//   CO5300 Display QSPI: CS = 12, SCK = 11, SIO0 = 4, SIO1 = 5, SIO2 = 6, SIO3 = 7, RST = 8, TE = 13
//   FT3168 Touch: RST = 9, INT = 38, I2C addr = 0x38
//   AXP2101 PMIC: I2C addr = 0x34 (controls DSI_PWR_EN & battery fuel gauge)
//   QMI8658 IMU: INT = 21, I2C addr = 0x6B
//   Buttons: BOOT = GPIO 0 (active LOW), PWR = GPIO 10
const char*    FW_VERSION    = "silverguard_watch_app 0.5 (ESP32-S3-Touch-AMOLED-2.06 + QMI8658 + Touch/Screen/Wi-Fi)";
const int      SDA_PIN       = 15;
const int      SCL_PIN       = 14;
const int      IMU_INT_PIN   = 21;
const int      BTN_BOOT_PIN  = 0;

// Display & Touch pins (from Pin Definition diagram)
const int      LCD_CS_PIN    = 12;
const int      LCD_SCK_PIN   = 11;
const int      LCD_D0_PIN    = 4;
const int      LCD_D1_PIN    = 5;
const int      LCD_D2_PIN    = 6;
const int      LCD_D3_PIN    = 7;
const int      LCD_RST_PIN   = 8;
const int      LCD_TE_PIN    = 13;
const int      TOUCH_RST_PIN = 9;
const int      TOUCH_INT_PIN = 38;
const int      LCD_WIDTH     = 410;
const int      LCD_HEIGHT    = 502;

const uint32_t I2C_HZ        = 400000;    // 400 kHz "fast mode"
uint8_t        IMU_ADDR      = 0x6B;      // QMI8658 address (0x6A fallback checked in setup)
const uint8_t  TOUCH_ADDR    = 0x38;      // FT3168 capacitive touch controller
const uint8_t  PMIC_ADDR     = 0x34;      // AXP2101 power management IC
const uint32_t PERIOD_US     = 20000;     // 20 ms between samples = 50 Hz
const uint32_t STATUS_US     = 10000000;  // status line every 10 s

const float    ACC_LSB_PER_G    = 2048.0f;   // QMI8658 at +-16 g: 32768 / 16 = 2048 LSB/g
const float    GYRO_LSB_PER_DPS = 16.0f;     // QMI8658 at +-2048 dps: 32768 / 2048 = 16.0 LSB/dps
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

// QMI8658 register addresses
const uint8_t REG_WHO_AM_I = 0x00;  // device ID register (expected 0x05)
const uint8_t REG_CTRL1    = 0x02;  // serial interface settings (auto-increment + endianness)
const uint8_t REG_CTRL2    = 0x03;  // accelerometer full-scale range + ODR
const uint8_t REG_CTRL3    = 0x04;  // gyroscope full-scale range + ODR
const uint8_t REG_CTRL5    = 0x06;  // low-pass filter configuration
const uint8_t REG_CTRL7    = 0x08;  // sensor enable (aEN, gEN)
const uint8_t REG_AX_L     = 0x35;  // first of 12 contiguous data bytes (AX_L..AZ_H, GX_L..GZ_H)

// Values we write (and later read back to check)
const uint8_t VAL_WHO_AM_I = 0x05;  // QMI8658 WHO_AM_I value
const uint8_t VAL_CTRL1    = 0x40;  // ADDR_AI=1 (auto-increment register address on burst read), little-endian
const uint8_t VAL_CTRL2    = 0x37;  // aFS=011 (+-16 g), aODR=0111 (~62.5 Hz)
const uint8_t VAL_CTRL3    = 0x77;  // gFS=0111 (+-2048 dps), gODR=0111 (~62.5 Hz)
const uint8_t VAL_CTRL5    = 0x11;  // enable digital low-pass filter on both accel and gyro
const uint8_t VAL_CTRL7    = 0x03;  // aEN=1 (bit 0), gEN=1 (bit 1): enable both accel and gyro

// ---------------- counters ----------------
uint32_t next_time    = 0;   // micros() when the next sample is due
uint32_t sample_index = 0;   // counts sample slots; skipped slots leave a gap
uint32_t sent         = 0;   // samples read OK
uint32_t dropped      = 0;   // slots missed because we were late
uint32_t read_errors  = 0;   // I2C reads that did not return 12 bytes
uint32_t last_status  = 0;
uint32_t falls        = 0;   // fall events raised
uint32_t max_model_us = 0;   // slowest model run so far (must stay well below 20000)

// ---------------- model & watch UI state ----------------
float    gyro_bias[3] = {0, 0, 0};   // deg/s, measured at boot while still
float    am_ring[HAR_WIN];           // last 128 accel magnitudes (g)
float    gm_ring[HAR_WIN];           // last 128 gyro magnitudes (deg/s)
int      ring_pos     = 0;           // where the next sample goes
int      ring_filled  = 0;           // how many valid samples (restart after a gap)
int      since_result = 0;
uint32_t lockout_until = 0;          // sample_index before which no new fall alarm is raised
bool     alarm_active  = false;      // true = a fall was detected and watch is asking "ARE YOU OK?"
bool     help_sent     = false;      // true = "NEED HELP" was tapped or 30 s countdown expired
uint32_t alarm_start_ms = 0;         // millis() when the fall alarm started
uint32_t last_alert_ms  = 0;
int      last_har       = -1;        // 0 = Still, 1 = Moving
float    last_peak_g    = 1.0f;

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

#if HAS_ARDUINO_GFX
Arduino_DataBus *gfx_bus = new Arduino_ESP32QSPI(
    LCD_CS_PIN, LCD_SCK_PIN, LCD_D0_PIN, LCD_D1_PIN, LCD_D2_PIN, LCD_D3_PIN);
Arduino_GFX *gfx = new Arduino_CO5300(
    gfx_bus, LCD_RST_PIN, 0 /* rotation */, LCD_WIDTH, LCD_HEIGHT, 22 /* col offset */, 0, 0, 0);
#endif

// ---------------- I2C helpers ----------------
void writeRegAddr(uint8_t devAddr, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(devAddr);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

uint8_t readRegAddr(uint8_t devAddr, uint8_t reg) {
  Wire.beginTransmission(devAddr);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom(devAddr, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0xFF;
}

void writeReg(uint8_t reg, uint8_t value) { writeRegAddr(IMU_ADDR, reg, value); }
uint8_t readReg(uint8_t reg)              { return readRegAddr(IMU_ADDR, reg); }

void checkReg(const char* name, uint8_t reg, uint8_t expected) {
  uint8_t got = readReg(reg);
  Serial.printf("# reg %s (0x%02X) = 0x%02X, expected 0x%02X %s\n",
                name, reg, got, expected, got == expected ? "OK" : "MISMATCH");
}

// ---------------- AXP2101 PMIC (Power & Battery) ----------------
void initPMIC() {
  // Enable ALDO1..4 and BLDO1..2 power rails (DSI_PWR_EN for CO5300 display + FT3168 touch)
  uint8_t ldo_en = readRegAddr(PMIC_ADDR, 0x90);
  if (ldo_en != 0xFF) {
    writeRegAddr(PMIC_ADDR, 0x90, ldo_en | 0x3F);
    delay(20);
  }
}

int readBatteryPercent() {
  uint8_t pct = readRegAddr(PMIC_ADDR, 0xA4);  // AXP2101 E-Gauge battery percentage (0..100)
  return (pct <= 100) ? (int)pct : -1;
}

// ---------------- FT3168 Touch Controller ----------------
void initTouch() {
#if ENABLE_TOUCH
  pinMode(TOUCH_RST_PIN, OUTPUT);
  pinMode(TOUCH_INT_PIN, INPUT_PULLUP);
  digitalWrite(TOUCH_RST_PIN, LOW);
  delay(10);
  digitalWrite(TOUCH_RST_PIN, HIGH);
  delay(50);
#endif
}

bool readTouch(uint16_t &x, uint16_t &y) {
#if ENABLE_TOUCH
  Wire.beginTransmission(TOUCH_ADDR);
  Wire.write(0x02);  // FT3168 TD_STATUS (number of touch points)
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(TOUCH_ADDR, (uint8_t)5) != 5) return false;
  uint8_t touches = Wire.read() & 0x0F;
  uint8_t xh = Wire.read(), xl = Wire.read();
  uint8_t yh = Wire.read(), yl = Wire.read();
  if (touches == 0) return false;
  x = ((xh & 0x0F) << 8) | xl;
  y = ((yh & 0x0F) << 8) | yl;
  return true;
#else
  return false;
#endif
}

// ---------------- Wi-Fi Caregiver App Sender ----------------
// Sends JSON matching context.md section 4:
//   {"device_id":"watch-01","state":"fall","confidence":0.91,"detector":"fall_rf30_v0.4","reply":"help"}
void sendEventToApp(const char* state, float confidence, const char* reply = "") {
#if ENABLE_WIFI
  if (WiFi.status() != WL_CONNECTED || strlen(SERVER_URL) == 0) return;
  HTTPClient http;
  http.setTimeout(3000);
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");

  String payload = String("{\"device_id\":\"") + DEVICE_ID +
                   "\",\"state\":\"" + state +
                   "\",\"confidence\":" + String(confidence, 2) +
                   ",\"detector\":\"fall_rf30_v0.4\"";
  if (strlen(reply) > 0) {
    payload += String(",\"reply\":\"") + reply + "\"";
  }
  payload += "}";

  int code = http.POST(payload);
  Serial.printf("# Wi-Fi POST %s -> HTTP %d\n", payload.c_str(), code);
  http.end();
#endif
}

// ---------------- CO5300 AMOLED Screen UI ----------------
void drawMonitoringScreen(int har, float peak_g, bool force_clear = false) {
#if HAS_ARDUINO_GFX
  if (force_clear) {
    gfx->fillScreen(0x0000);  // Black background saves AMOLED battery
    gfx->setTextColor(0xFFFF);
    gfx->setTextSize(3);
    gfx->setCursor(55, 40);
    gfx->print("SILVERGUARD");
  }
  // Battery indicator
  int batt = readBatteryPercent();
  gfx->fillRect(40, 95, 330, 35, 0x0000);
  gfx->setTextSize(2);
  gfx->setTextColor(0x07E0);  // Green
  gfx->setCursor(55, 100);
  if (batt >= 0) gfx->printf("Battery: %d%%", batt);
  else           gfx->print("Watch Monitoring");

  // Activity card
  uint16_t cardColor = (har == 1) ? 0x03EF : 0x2104;  // Cyan-blue when Moving, dark grey when Still
  gfx->fillRoundRect(35, 160, 340, 160, 20, cardColor);
  gfx->setTextColor(0xFFFF);
  gfx->setTextSize(2);
  gfx->setCursor(130, 185);
  gfx->print("ACTIVITY");
  gfx->setTextSize(5);
  gfx->setCursor((har == 1) ? 115 : 130, 235);
  gfx->print((har >= 0 && har <= 1) ? ACTIVITY_NAME[har] : "Ready");

  // Peak G footer
  gfx->fillRect(35, 350, 340, 50, 0x0000);
  gfx->setTextSize(2);
  gfx->setTextColor(0xC618);
  gfx->setCursor(95, 365);
  gfx->printf("Peak Accel: %.2f g", peak_g);
#endif
}

void drawFallAlertScreen(int seconds_left, bool escalated) {
#if HAS_ARDUINO_GFX
  if (escalated) {
    gfx->fillScreen(0xF800);  // Full Red
    gfx->setTextColor(0xFFFF);
    gfx->setTextSize(4);
    gfx->setCursor(45, 140);
    gfx->print("ALERT SENT!");
    gfx->setTextSize(2);
    gfx->setCursor(45, 220);
    gfx->print("Caregiver notified.");
    gfx->setCursor(35, 340);
    gfx->print("Tap green / press BOOT");
    gfx->setCursor(85, 370);
    gfx->print("when you are safe.");
    return;
  }

  // Top half (y: 0..250): Giant GREEN "I'M OK" button
  gfx->fillRect(0, 0, LCD_WIDTH, LCD_HEIGHT / 2, 0x05E0);
  gfx->setTextColor(0xFFFF);
  gfx->setTextSize(5);
  gfx->setCursor(95, 75);
  gfx->print("I'M OK");
  gfx->setTextSize(2);
  gfx->setCursor(70, 150);
  gfx->print("Tap here to cancel");

  // Bottom half (y: 251..502): Giant RED "NEED HELP" button + countdown
  gfx->fillRect(0, LCD_HEIGHT / 2, LCD_WIDTH, LCD_HEIGHT / 2, 0xF800);
  gfx->setTextColor(0xFFFF);
  gfx->setTextSize(4);
  gfx->setCursor(65, 310);
  gfx->print("NEED HELP");
  gfx->setTextSize(2);
  gfx->setCursor(45, 385);
  gfx->printf("Auto-alert in %d s...", seconds_left);
#endif
}

// ---------------- QMI8658 Burst Read ----------------
// burst-read 12 bytes from QMI8658: ax ay az gx gy gz, each little-endian int16 (low byte first).
// Stores accel in v[0..2] and gyro in v[4..6] (leaving v[3] = 0) so downstream indexing matches MPU-6050 code.
bool readSample(int16_t v[7]) {
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(REG_AX_L);
  Wire.endTransmission(false);
  if (Wire.requestFrom(IMU_ADDR, (uint8_t)12) != 12) return false;
  const int idx[6] = {0, 1, 2, 4, 5, 6};
  for (int i = 0; i < 6; i++) {
    uint8_t lo = Wire.read();                    // QMI8658 is little-endian: low byte first
    uint8_t hi = Wire.read();
    v[idx[i]] = (int16_t)((hi << 8) | lo);
  }
  v[3] = 0;                                      // unused temperature slot
  return true;
}

// Header: '#' lines that record.py saves at the top of each file.
void printHeader() {
  Serial.printf("# firmware: %s\n", FW_VERSION);
  Serial.printf("# sensor: QMI8658 (I2C 0x%02X on SDA=%d, SCL=%d), raw int16 counts\n", IMU_ADDR, SDA_PIN, SCL_PIN);
  Serial.println("# accel_range: +-16 g");
  Serial.println("# accel_lsb_per_g: 2048");
  Serial.println("# gyro_range: +-2048 dps");
  Serial.println("# gyro_lsb_per_dps: 16.0");
  Serial.println("# dlpf: CTRL5=0x11 (accel + gyro LPF enabled, ODR ~62.5 Hz)");
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
  if (got < N / 2) { Serial.println("# WARNING: gyro calibration failed (read errors), bias = 0"); return; }
  float spread = sqrtf(fmaxf(saa / got - (sa / got) * (sa / got), 0));
  if (spread > 0.02f) { Serial.println("# WARNING: moved during gyro calibration, bias = 0 (reset to retry, keep still)"); return; }
  for (int a = 0; a < 3; a++) gyro_bias[a] = sg[a] / got;
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

  // Refresh watch screen only when HAR class changes (keeps 50 Hz sampling fast)
  if (har != last_har) {
    last_har = har;
    last_peak_g = peak;
    drawMonitoringScreen(har, peak, false);
  }

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
#if RAW_STREAM
  Serial.printf("# EVENT FALL,%lu,%.3f,peak_g=%.2f,turn_deg=%.1f,min_g_before=%.2f,max_dps_before=%.0f\n",
                pend_index, pend_score, pend_peak, turn, pend_min_g, pend_max_dps);
#endif
  Serial.println(ALERT_TEXT);
  if (turn < 0) Serial.println("(could not check the wrist position: data gap, alarming to be safe)");
  else Serial.printf("Fall score %.2f, peak %.2f g, wrist turned %.0f deg, lowest %.2f g and fastest turn %.0f dps before the impact.\n",
                     pend_score, pend_peak, turn, pend_min_g, pend_max_dps);

  // Notify Railway server / Caregiver App that a fall was detected and check-in started
  sendEventToApp("fall", pend_score);

#if HALT_ON_FALL
  alarm_active   = true;
  help_sent      = false;
  alarm_start_ms = millis();
  last_alert_ms  = millis();
  drawFallAlertScreen(CHECKIN_MS / 1000, false);
  Serial.println("Check-in active on watch screen: Tap GREEN ('I'M OK'), tap RED ('NEED HELP'), press BOOT button, or send 'r'.");
#endif
}

void resumeFromAlarm(const char* reason) {
  Serial.printf("# Check-in resolved (%s): resuming monitoring.\n", reason);
  sendEventToApp("still", pend_score, "ok");
  alarm_active = false;
  help_sent    = false;
  ring_filled  = 0;                                     // start fresh windows: the old ones contain the fall
  seq          = 0;
  since_result = 0;
  last_har     = -1;
  drawMonitoringScreen(0, 1.0f, true);
  next_time    = micros() + PERIOD_US;
  Serial.println("Resumed: monitoring again (first result after 2.56 s).");
}

void escalateHelp(const char* reason) {
  if (help_sent) return;
  help_sent = true;
  Serial.printf("# ESCALATING FALL ALERT (%s) -> notifying caregiver!\n", reason);
  sendEventToApp("fall", pend_score, "help");
  drawFallAlertScreen(0, true);
}

// ---------------- setup ----------------
void setup() {
  Serial.begin(115200);                 // baud is ignored over native USB, but keep it standard
  delay(1500);                          // give the PC time to open the USB serial port

  pinMode(BTN_BOOT_PIN, INPUT_PULLUP);  // top side button (GPIO 0, active LOW)
  pinMode(IMU_INT_PIN, INPUT);          // QMI8658 interrupt pin (GPIO 21)

  Wire.begin(SDA_PIN, SCL_PIN);         // SDA (GPIO 15) first, then SCL (GPIO 14)
  Wire.setClock(I2C_HZ);

  // Power on display/touch rails via AXP2101 PMIC & reset FT3168 touch controller
  initPMIC();
  initTouch();

#if HAS_ARDUINO_GFX
  if (gfx->begin()) {
    drawMonitoringScreen(0, 1.0f, true);
  }
#else
  Serial.println("# note: install 'GFX Library for Arduino' in Library Manager to enable the 2.06\" AMOLED screen UI");
#endif

#if ENABLE_WIFI
  if (strlen(WIFI_SSID) > 0) {
    Serial.printf("# Connecting to Wi-Fi '%s'...", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 8000) {
      delay(250);
      Serial.print(".");
    }
    if (WiFi.status() == WL_CONNECTED)
      Serial.printf(" connected! IP: %s\n", WiFi.localIP().toString().c_str());
    else
      Serial.println(" timed out (running in offline watch mode).");
  }
#endif

  uint8_t who = readReg(REG_WHO_AM_I);
  if (who != VAL_WHO_AM_I) {
    IMU_ADDR = 0x6A;                    // Check alternate QMI8658 address
    if (readReg(REG_WHO_AM_I) == VAL_WHO_AM_I) {
      who = VAL_WHO_AM_I;
    } else {
      IMU_ADDR = 0x6B;
    }
  }
  Serial.printf("# QMI8658 I2C addr: 0x%02X, WHO_AM_I: 0x%02X, Battery: %d%%\n",
                IMU_ADDR, who, readBatteryPercent());
  if (who != VAL_WHO_AM_I) Serial.println("# WARNING: WHO_AM_I is not 0x05 (check QMI8658 connection / power), continuing");

  writeReg(REG_CTRL1, VAL_CTRL1);
  writeReg(REG_CTRL2, VAL_CTRL2);
  writeReg(REG_CTRL3, VAL_CTRL3);
  writeReg(REG_CTRL5, VAL_CTRL5);
  writeReg(REG_CTRL7, VAL_CTRL7);
  delay(100);                           // let the gyro and accel settle after enabling

  // read back to prove the settings stuck
  checkReg("CTRL1", REG_CTRL1, VAL_CTRL1);
  checkReg("CTRL2", REG_CTRL2, VAL_CTRL2);
  checkReg("CTRL3", REG_CTRL3, VAL_CTRL3);
  checkReg("CTRL5", REG_CTRL5, VAL_CTRL5);
  checkReg("CTRL7", REG_CTRL7, VAL_CTRL7);

  Serial.println("# calibrating gyro: keep the watch STILL for 2 s...");
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
  // 1. Serial commands from PC: 'h' = print header, 'r' = resume after a fall alert
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'h') printHeader();
    if ((c == 'r' || c == 'R') && alarm_active) {
      resumeFromAlarm("serial 'r'");
    }
  }

  // 2. Fall Check-In Mode: check BOOT button, touchscreen ("I'M OK" vs "NEED HELP"), and 30 s countdown
  if (alarm_active) {
    // Physical BOOT button on watch side cancels alert ("I'M OK")
    if (digitalRead(BTN_BOOT_PIN) == LOW) {
      delay(50);
      while (digitalRead(BTN_BOOT_PIN) == LOW) delay(10);
      resumeFromAlarm("watch BOOT button");
      return;
    }

    // Touchscreen tap check (top half = GREEN "I'M OK", bottom half = RED "NEED HELP")
    uint16_t tx = 0, ty = 0;
    if (readTouch(tx, ty)) {
      if (ty < (LCD_HEIGHT / 2)) {
        resumeFromAlarm("touchscreen I'M OK");
        delay(300);
        return;
      } else if (!help_sent) {
        escalateHelp("touchscreen NEED HELP");
        delay(300);
      }
    }

    // Repeat serial alert & update countdown every 1 s; auto-escalate if no response after 30 s
    if (millis() - last_alert_ms >= 1000) {
      last_alert_ms = millis();
      Serial.println(ALERT_TEXT);
      uint32_t elapsed = millis() - alarm_start_ms;
      if (!help_sent) {
        if (elapsed >= CHECKIN_MS) {
          escalateHelp("30s check-in timeout (no reply)");
        } else {
          int secs_left = (CHECKIN_MS - elapsed + 999) / 1000;
          drawFallAlertScreen(secs_left, false);
        }
      }
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
  if (!readSample(v)) {
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
  float ax = v[0] / ACC_LSB_PER_G, ay = v[1] / ACC_LSB_PER_G, az = v[2] / ACC_LSB_PER_G;
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
    Serial.printf("# status: sent=%lu dropped=%lu read_errors=%lu falls=%lu max_model_us=%lu batt=%d%%\n",
                  sent, dropped, read_errors, falls, max_model_us, readBatteryPercent());
  }
}
