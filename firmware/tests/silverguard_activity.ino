/*
  SilverGuard - activity detection on ESP32 + MPU-6050
  - Reads the accelerometer at 50 Hz
  - Window = last 128 samples (2.56 s), same features as features.py
  - New result every HOP_LEN samples (default 25 = every 0.5 s)
  - Two columns: raw MOVING/still per window, and debounced WALKING/stationary
    (brief fidgets while sitting stay "stationary")
  - Calibrates accelerometer offsets at startup (lay the sensor FLAT, chip facing up, keep still)
*/

#include <Arduino.h>
#include <Wire.h>
#include <math.h>
#define OUTPUT_CSV 1
// ================= settings =================
#define I2C_SDA 3           // ESP32 DevKit default; ESP32-C3 boards differ, check your pinout
#define I2C_SCL 4
#define MPU_ADDR 0x68         // 0x69 if AD0 is pulled high

#define OUTPUT_CSV 0          // 0 = readable text, 1 = CSV (for logging / the Python dashboard)
#define LOG_RAW    0          // 1 = also print every raw sample (for checking against Python)
#define CALIBRATE  1          // 1 = measure offsets at startup (sensor flat + still)

const float MOVING_THRESHOLD = 0.05f;  // PLACEHOLDER: set from your own watch recordings
const int   SAMPLE_HZ  = 50;
const int   WINDOW_LEN = 128;          // 2.56 s, matches the Python windows
const int   HOP_LEN    = 25;           // 0.5 s between results. Use 128 to match Python's
                                       // non-overlapping windows exactly (stage-1 check)

// Must match features.py: pandas .std() -> 1, numpy .std() -> 0
#define STD_DDOF 1

// ---- debounce rule for the second column (walking / stationary) ----
// A 1-2 s fidget stays inside the 2.56 s window for ~3.5-4.5 s, so it shows up as
// several MOVING results in a row. Require movement to last longer than that.
const float MIN_WALK_SEC  = 8.0f;   // MOVING this long in a row -> walking
const float MIN_STILL_SEC = 3.0f;   // still this long in a row -> back to stationary
                                    // (so a short pause mid-walk doesn't flip it)

// ================= MPU-6050 registers =================
const uint8_t REG_SMPLRT_DIV   = 0x19;
const uint8_t REG_CONFIG       = 0x1A;
const uint8_t REG_ACCEL_CONFIG = 0x1C;
const uint8_t REG_ACCEL_XOUT_H = 0x3B;
const uint8_t REG_PWR_MGMT_1   = 0x6B;
const uint8_t REG_WHO_AM_I     = 0x75;
const float   LSB_PER_G        = 16384.0f;   // +-2 g range

// ================= state =================
float    magBuf[WINDOW_LEN];          // ring buffer of the last 128 magnitudes
int      writeIdx = 0;
uint32_t samplesSeen = 0;
uint32_t sinceLast = 0;
uint32_t resultIdx = 0;
uint32_t nextSampleUs = 0;
const uint32_t SAMPLE_PERIOD_US = 1000000UL / SAMPLE_HZ;   // 20 ms

float offX = 0, offY = 0, offZ = 0;   // calibration offsets (g)

// ================= MPU-6050 helpers =================
bool writeReg(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

int readReg(uint8_t reg) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return -1;
  if (Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)1) != 1) return -1;
  return Wire.read();
}

bool readAccelRaw(float &ax, float &ay, float &az) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(REG_ACCEL_XOUT_H);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)6) != 6) return false;
  int16_t rx = (Wire.read() << 8) | Wire.read();
  int16_t ry = (Wire.read() << 8) | Wire.read();
  int16_t rz = (Wire.read() << 8) | Wire.read();
  ax = rx / LSB_PER_G;
  ay = ry / LSB_PER_G;
  az = rz / LSB_PER_G;
  return true;
}

bool readAccel(float &ax, float &ay, float &az) {
  if (!readAccelRaw(ax, ay, az)) return false;
  ax -= offX; ay -= offY; az -= offZ;
  return true;
}

bool setupMPU() {
  int who = readReg(REG_WHO_AM_I);
  if (who < 0) return false;
  if (who != 0x68) Serial.printf("Note: WHO_AM_I = 0x%02X (clone chip?), continuing\n", who);
  bool ok = true;
  ok &= writeReg(REG_PWR_MGMT_1, 0x01);    // wake up, gyro X clock
  delay(100);
  ok &= writeReg(REG_ACCEL_CONFIG, 0x00);  // +-2 g
  ok &= writeReg(REG_CONFIG, 0x04);        // low-pass ~21 Hz (anti-aliasing)
  ok &= writeReg(REG_SMPLRT_DIV, 19);      // 1 kHz / 20 = 50 Hz
  return ok;
}

// Lay the sensor flat (chip facing up) and keep it still for ~2 s.
// Afterwards a still sensor should read x=0, y=0, z=1 g, so mag_mean ~= 1.00 like the datasets.
void calibrate() {
  const int N = 100;
  float sx = 0, sy = 0, sz = 0, sxx = 0, syy = 0, szz = 0;
  int got = 0;
  Serial.println("Calibrating: keep the sensor FLAT and STILL...");
  for (int k = 0; k < N; k++) {
    float x, y, z;
    if (readAccelRaw(x, y, z)) {
      sx += x; sy += y; sz += z;
      sxx += x * x; syy += y * y; szz += z * z;
      got++;
    }
    delay(20);
  }
  if (got < N / 2) { Serial.println("Calibration failed (read errors), using no offsets"); return; }
  float mx = sx / got, my = sy / got, mz = sz / got;
  float spread = sqrtf((sxx / got - mx * mx) + (syy / got - my * my) + (szz / got - mz * mz));
  if (spread > 0.02f) { Serial.println("Movement detected, calibration skipped (restart to retry)"); return; }
  if (mz < 0.7f) { Serial.println("Sensor not flat (z not pointing up), calibration skipped"); return; }
  offX = mx; offY = my; offZ = mz - 1.0f;
  Serial.printf("Calibrated. Offsets (g): x=%+.3f  y=%+.3f  z=%+.3f\n", offX, offY, offZ);
}

// ================= window features =================
void processWindow() {
  float sum = 0, mn = magBuf[0], mx = magBuf[0];
  for (int k = 0; k < WINDOW_LEN; k++) {
    sum += magBuf[k];
    if (magBuf[k] < mn) mn = magBuf[k];
    if (magBuf[k] > mx) mx = magBuf[k];
  }
  float mean = sum / WINDOW_LEN;
  float sq = 0;
  for (int k = 0; k < WINDOW_LEN; k++) { float d = magBuf[k] - mean; sq += d * d; }
  float stdv = sqrtf(sq / (WINDOW_LEN - STD_DDOF));
  bool moving = stdv > MOVING_THRESHOLD;

  // ---- debounced state: brief movement during stillness stays "stationary" ----
  static const int WALK_RESULTS  = (int)(MIN_WALK_SEC  * SAMPLE_HZ / HOP_LEN + 0.5f);  // 16 at 0.5 s hop
  static const int STILL_RESULTS = (int)(MIN_STILL_SEC * SAMPLE_HZ / HOP_LEN + 0.5f);  // 6 at 0.5 s hop
  static int  movingRun = 0, stillRun = 0;
  static bool walking = false;

  if (moving) { movingRun++; stillRun = 0; }
  else        { stillRun++;  movingRun = 0; }

  if (!walking && movingRun >= WALK_RESULTS) walking = true;    // movement kept going
  if (walking  && stillRun  >= STILL_RESULTS) walking = false;  // stillness kept going

#if OUTPUT_CSV
  Serial.printf("W,%lu,%lu,%.5f,%.5f,%.5f,%.5f,%.5f,%d,%d\n",
                (unsigned long)resultIdx, (unsigned long)millis(),
                mean, stdv, mn, mx, mx - mn, moving ? 1 : 0, walking ? 1 : 0);
#else
  Serial.printf("[%7.1f s]  #%-5lu  %-7s  %-10s  std=%.3f   mean=%.3f   min=%.3f   max=%.3f   range=%.3f\n",
                millis() / 1000.0f, (unsigned long)resultIdx,
                moving ? "MOVING" : "still", walking ? "WALKING" : "stationary",
                stdv, mean, mn, mx, mx - mn);
#endif
  resultIdx++;
  // `moving`  (raw)       -> inactivity alert: any movement counts as a sign of life
  // `walking` (debounced) -> activity status on the dashboard
}

// ================= Arduino entry points =================
void setup() {
  Serial.begin(115200);
  delay(500);
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000);
  delay(200);                                   // give the MPU-6050 time to power up

  int tries = 0;
  while (!setupMPU()) {
    if (++tries == 3) Serial.println("Still no MPU-6050: check wiring, SDA/SCL pins and address");
    delay(500);
  }
  Serial.println("MPU-6050 ready");

#if CALIBRATE
  calibrate();
#endif

#if OUTPUT_CSV
  Serial.println("# result_idx,millis,mag_mean,mag_std,mag_min,mag_max,mag_range,moving,walking");
#else
  Serial.printf("Threshold: std > %.3f = MOVING. First result after 2.56 s, then every %.1f s\n",
                MOVING_THRESHOLD, (float)HOP_LEN / SAMPLE_HZ);
#endif
  nextSampleUs = micros();
}

void loop() {
    // serial command: 'c' = recalibrate without pressing reset (sensor flat + still)
  if (Serial.available()) {
    char c = Serial.read();
    if (c == 'c' || c == 'C') {
      calibrate();
      samplesSeen = 0;                          // start a fresh window with the new offsets
      sinceLast = 0;
      nextSampleUs = micros();
      return;
    }
  }
  // fixed-rate 50 Hz scheduler (no drift)
  uint32_t now = micros();
  if ((int32_t)(now - nextSampleUs) < 0) return;

  // If we fell far behind (e.g. Serial blocked while no program was reading the port),
  // do NOT burst through the backlog: that would read samples much faster than 50 Hz
  // and the windows would no longer be 2.56 s. Resync and refill the window instead.
  if ((int32_t)(now - nextSampleUs) > 3 * (int32_t)SAMPLE_PERIOD_US) {
    nextSampleUs = now;
    samplesSeen = 0;
    sinceLast = 0;
    Serial.println("# timing gap: resynced, window restarted");
  }
  nextSampleUs += SAMPLE_PERIOD_US;

  float ax, ay, az;
  if (!readAccel(ax, ay, az)) return;           // skip a failed read

#if LOG_RAW
  Serial.printf("S,%lu,%.5f,%.5f,%.5f\n", (unsigned long)millis(), ax, ay, az);
#endif

  magBuf[writeIdx] = sqrtf(ax * ax + ay * ay + az * az);
  writeIdx = (writeIdx + 1) % WINDOW_LEN;
  samplesSeen++;
  sinceLast++;

  // once the buffer is full, compute a result every HOP_LEN samples
  if (samplesSeen >= WINDOW_LEN && sinceLast >= HOP_LEN) {
    processWindow();
    sinceLast = 0;
  }
}