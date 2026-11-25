// SilverGuard espwatch: IMU recorder for the Waveshare ESP32-S3-Touch-AMOLED-2.06 watch
//
// Samples the QMI8658 at 50 Hz and sends every sample once as a CSV row, over Wi-Fi (TCP) or USB.
// capture_csv.py on the laptop saves the rows and cuts the overlapping windows exactly like training:
//   HAR (still/moving): 128 samples (2.56 s), hop 64 (50%)   <- notebooks/Model based of UCI DataSet/features.py
//   Fall model:         125 samples (2.5 s),  hop 62 (~50%)  <- notebooks/fall model/fad_utils.py
// Units match training: accel in g, gyro in deg/s with the gyro bias (measured at boot) removed.
// Sensor settings match the watch firmware (Melv-Codes/esp_watch): +-16 g, +-2048 dps, ODR ~62.5 Hz, LPF on.
//
// Wi-Fi: copy wifi_config.example.h to wifi_config.h and fill in your hotspot. The watch then runs a
// TCP server on port 3333 (name: espwatch.local) and the laptop connects to it. Without wifi_config.h
// the rows go to USB serial instead.
//
// Sampling runs in its own high-priority task, so a slow Wi-Fi link never delays it. Samples wait in a
// 60 s buffer; if the laptop disconnects and reconnects within 60 s, nothing is lost.
//
// Commands (from the laptop, over Wi-Fi or USB):
//   0..9  set the label column (default 0)
//   p     pause / resume recording
//   h     print the '#' header again

#include <Wire.h>
#include <atomic>

#if __has_include("wifi_config.h")
  #include "wifi_config.h"
  #define USE_WIFI 1
  #include <WiFi.h>
  #include <ESPmDNS.h>
#else
  #define USE_WIFI 0
#endif

// ---------------- hardware ----------------
const int      SDA_PIN    = 15;
const int      SCL_PIN    = 14;
const uint32_t I2C_HZ     = 400000;
uint8_t        IMU_ADDR   = 0x6B;       // QMI8658 (0x6A fallback checked in setup)
const uint8_t  PMIC_ADDR  = 0x34;       // AXP2101

const uint32_t PERIOD_US  = 20000;      // 20 ms = 50 Hz
const float    ACC_LSB_PER_G    = 2048.0f;   // +-16 g
const float    GYRO_LSB_PER_DPS = 16.0f;     // +-2048 dps

// QMI8658 registers and the values written (same as the watch firmware)
const uint8_t REG_WHO_AM_I = 0x00, VAL_WHO_AM_I = 0x05;
const uint8_t REG_CTRL1 = 0x02, VAL_CTRL1 = 0x40;   // address auto-increment, little-endian
const uint8_t REG_CTRL2 = 0x03, VAL_CTRL2 = 0x37;   // accel +-16 g, ODR ~62.5 Hz
const uint8_t REG_CTRL3 = 0x04, VAL_CTRL3 = 0x77;   // gyro +-2048 dps, ODR ~62.5 Hz
const uint8_t REG_CTRL5 = 0x06, VAL_CTRL5 = 0x11;   // low-pass filter on accel + gyro
const uint8_t REG_CTRL7 = 0x08, VAL_CTRL7 = 0x03;   // enable accel + gyro
const uint8_t REG_AX_L  = 0x35;                     // AX_L .. GZ_H, 12 bytes

const uint16_t TCP_PORT = 3333;

// ---------------- sample buffer (sampling task writes, loop() reads) ----------------
struct Sample {
  uint32_t index;      // 50 Hz slot number since boot: a jump means missed samples (a gap)
  uint32_t t_ms;
  float ax, ay, az;    // g
  float gx, gy, gz;    // deg/s, bias removed
  uint8_t label;
};

const uint32_t BUF_N = 3000;                  // 60 s at 50 Hz (~100 KB)
Sample buf[BUF_N];
std::atomic<uint32_t> w_count{0};             // samples written so far (slot = count % BUF_N)
uint32_t r_count = 0;                         // samples sent so far (only loop() touches this)
uint32_t overflow_lost = 0;

std::atomic<uint8_t> label{0};
std::atomic<bool>    paused{false};
std::atomic<uint32_t> read_errors{0}, missed_slots{0};
float gyro_bias[3] = {0, 0, 0};

#if USE_WIFI
WiFiServer server(TCP_PORT);
WiFiClient client;
#endif

// ---------------- I2C ----------------
void writeRegAddr(uint8_t dev, uint8_t reg, uint8_t val) {
  Wire.beginTransmission(dev); Wire.write(reg); Wire.write(val); Wire.endTransmission();
}
uint8_t readRegAddr(uint8_t dev, uint8_t reg) {
  Wire.beginTransmission(dev); Wire.write(reg); Wire.endTransmission(false);
  Wire.requestFrom(dev, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0xFF;
}

bool readRaw(int16_t v[6]) {     // ax ay az gx gy gz, raw counts
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(REG_AX_L);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(IMU_ADDR, (uint8_t)12) != 12) return false;
  for (int i = 0; i < 6; i++) {
    uint8_t lo = Wire.read(), hi = Wire.read();
    v[i] = (int16_t)((hi << 8) | lo);
  }
  return true;
}

// Keep the watch still for 2 s at boot: whatever the gyro reads then is bias.
void calibrateGyro() {
  const int N = 100;
  float sg[3] = {0, 0, 0}, sa = 0, saa = 0;
  int got = 0;
  for (int k = 0; k < N; k++) {
    int16_t v[6];
    if (readRaw(v)) {
      for (int a = 0; a < 3; a++) sg[a] += v[3 + a] / GYRO_LSB_PER_DPS;
      float m = sqrtf((float)v[0] * v[0] + (float)v[1] * v[1] + (float)v[2] * v[2]) / ACC_LSB_PER_G;
      sa += m; saa += m * m; got++;
    }
    delay(20);
  }
  if (got < N / 2) { Serial.println("# WARNING: gyro calibration failed (read errors), bias = 0"); return; }
  float spread = sqrtf(fmaxf(saa / got - (sa / got) * (sa / got), 0));
  if (spread > 0.02f) { Serial.println("# WARNING: moved during gyro calibration, bias = 0 (reset and keep still)"); return; }
  for (int a = 0; a < 3; a++) gyro_bias[a] = sg[a] / got;
}

// ---------------- sampling task: 50 Hz on a fixed schedule ----------------
void samplingTask(void*) {
  const uint32_t t0 = micros();
  uint32_t next_slot = 0;                     // slot we expect to read next
  TickType_t wake = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&wake, pdMS_TO_TICKS(PERIOD_US / 1000));
    uint32_t slot = (micros() - t0 + PERIOD_US / 2) / PERIOD_US;   // which 20 ms slot this is
    if (slot < next_slot) continue;                                // woke early: wait for the next tick
    if (slot > next_slot) missed_slots += slot - next_slot;        // late: those slots are lost (gap)
    next_slot = slot + 1;
    if (paused) continue;

    int16_t v[6];
    if (!readRaw(v)) { read_errors++; continue; }                  // missing index = gap

    uint32_t n = w_count.load(std::memory_order_relaxed);
    Sample& s = buf[n % BUF_N];
    s.index = slot;
    s.t_ms  = millis();
    s.ax = v[0] / ACC_LSB_PER_G;  s.ay = v[1] / ACC_LSB_PER_G;  s.az = v[2] / ACC_LSB_PER_G;
    s.gx = v[3] / GYRO_LSB_PER_DPS - gyro_bias[0];
    s.gy = v[4] / GYRO_LSB_PER_DPS - gyro_bias[1];
    s.gz = v[5] / GYRO_LSB_PER_DPS - gyro_bias[2];
    s.label = label;
    w_count.store(n + 1, std::memory_order_release);              // publish after the slot is filled
  }
}

// ---------------- output: Wi-Fi client if configured, else USB ----------------
Print& out() {
#if USE_WIFI
  return client;
#else
  return Serial;
#endif
}

bool outReady() {
#if USE_WIFI
  return client && client.connected();
#else
  return true;
#endif
}

void printHeader(Print& p) {
  p.println("# espwatch: QMI8658 recorder (Waveshare ESP32-S3-Touch-AMOLED-2.06)");
  p.printf ("# imu_addr: 0x%02X, rate: 50 Hz, accel +-16 g, gyro +-2048 dps, LPF on\n", IMU_ADDR);
  p.printf ("# gyro_bias_dps: %.3f,%.3f,%.3f (already subtracted)\n", gyro_bias[0], gyro_bias[1], gyro_bias[2]);
  p.println("# one row per sample; a jump in sample_index = missed samples (capture_csv.py restarts the window)");
  p.println("sample_index,t_ms,label,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps");
}

void handleCommand(char c) {
  if (c >= '0' && c <= '9') { label = c - '0'; out().printf("# label: %c\n", c); }
  else if (c == 'p' || c == 'P') { paused = !paused; out().println(paused ? "# paused" : "# resumed"); }
  else if (c == 'h' || c == 'H') printHeader(out());
}

// ---------------- setup ----------------
void setup() {
  Serial.begin(115200);
  delay(1500);                                     // let the PC open the USB port

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(I2C_HZ);

  uint8_t ldo = readRegAddr(PMIC_ADDR, 0x90);      // AXP2101: make sure the sensor rails are on
  if (ldo != 0xFF) { writeRegAddr(PMIC_ADDR, 0x90, ldo | 0x3F); delay(20); }

  uint8_t who = readRegAddr(IMU_ADDR, REG_WHO_AM_I);
  if (who != VAL_WHO_AM_I) {
    IMU_ADDR = 0x6A;
    who = readRegAddr(IMU_ADDR, REG_WHO_AM_I);
    if (who != VAL_WHO_AM_I) IMU_ADDR = 0x6B;
  }
  if (who != VAL_WHO_AM_I) Serial.printf("# WARNING: QMI8658 WHO_AM_I = 0x%02X (expected 0x05), continuing\n", who);

  writeRegAddr(IMU_ADDR, REG_CTRL1, VAL_CTRL1);
  writeRegAddr(IMU_ADDR, REG_CTRL2, VAL_CTRL2);
  writeRegAddr(IMU_ADDR, REG_CTRL3, VAL_CTRL3);
  writeRegAddr(IMU_ADDR, REG_CTRL5, VAL_CTRL5);
  writeRegAddr(IMU_ADDR, REG_CTRL7, VAL_CTRL7);
  delay(100);

  Serial.println("# calibrating gyro: keep the watch STILL for 2 s...");
  calibrateGyro();

#if USE_WIFI
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);                            // no modem sleep: steadier latency
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("# connecting to Wi-Fi '%s'", WIFI_SSID);
  for (int i = 0; i < 60 && WiFi.status() != WL_CONNECTED; i++) { delay(250); Serial.print("."); }
  if (WiFi.status() == WL_CONNECTED) Serial.printf(" connected, IP %s\n", WiFi.localIP().toString().c_str());
  else Serial.println(" not yet (keeps retrying in the background)");
  WiFi.setAutoReconnect(true);
  if (MDNS.begin("espwatch")) MDNS.addService("espwatch", "tcp", TCP_PORT);
  server.begin();
  server.setNoDelay(true);
  Serial.printf("# TCP server on port %u (espwatch.local)\n", TCP_PORT);
#else
  printHeader(Serial);
#endif

  // sampling on core 1 at high priority (loop() and Wi-Fi run below it)
  xTaskCreatePinnedToCore(samplingTask, "imu50hz", 4096, nullptr, configMAX_PRIORITIES - 2, nullptr, 1);
}

// ---------------- loop: commands, connection, send buffered rows ----------------
void loop() {
  while (Serial.available()) handleCommand(Serial.read());

#if USE_WIFI
  static bool had_client = false;
  static uint32_t last_ip_print = 0;
  if (!(client && client.connected())) {
    if (had_client) { Serial.println("# laptop disconnected, buffering (up to 60 s)"); had_client = false; }
    WiFiClient c = server.available();
    if (c) {
      client = c;
      client.setNoDelay(true);
      bool first = !had_client && r_count == 0;
      if (first) r_count = w_count.load(std::memory_order_acquire);   // first connection: start fresh
      had_client = true;
      Serial.printf("# laptop connected from %s\n", client.remoteIP().toString().c_str());
      printHeader(client);
    } else {
      if (WiFi.status() == WL_CONNECTED && millis() - last_ip_print > 10000) {
        last_ip_print = millis();
        Serial.printf("# waiting for laptop: python capture_csv.py --host %s\n", WiFi.localIP().toString().c_str());
      }
      delay(5);
      return;
    }
  }
  while (client.available()) handleCommand(client.read());
#endif

  // drop the oldest rows if the laptop fell more than 60 s behind (shows up as a gap)
  uint32_t w = w_count.load(std::memory_order_acquire);
  if (w - r_count > BUF_N) { overflow_lost += (w - r_count) - BUF_N; r_count = w - BUF_N; }

  // send up to 50 rows per pass so commands are still handled quickly
  char line[128];
  for (int k = 0; k < 50 && r_count != w && outReady(); k++) {
    const Sample& s = buf[r_count % BUF_N];
    int n = snprintf(line, sizeof line, "%lu,%lu,%u,%.4f,%.4f,%.4f,%.3f,%.3f,%.3f\n",
                     (unsigned long)s.index, (unsigned long)s.t_ms, s.label,
                     s.ax, s.ay, s.az, s.gx, s.gy, s.gz);
    out().write((const uint8_t*)line, n);
    r_count++;
  }

  static uint32_t last_status = 0;
  if (millis() - last_status >= 10000 && outReady()) {
    last_status = millis();
    out().printf("# status: samples=%lu backlog=%lu missed_slots=%lu read_errors=%lu overflow_lost=%lu label=%u%s\n",
                 (unsigned long)w, (unsigned long)(w - r_count), (unsigned long)missed_slots.load(),
                 (unsigned long)read_errors.load(), (unsigned long)overflow_lost, label.load(),
                 paused ? " PAUSED" : "");
  }
  delay(1);
}
