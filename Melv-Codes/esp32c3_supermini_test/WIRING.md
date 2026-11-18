# ESP32-C3 SuperMini — Speaker (MAX98357A), Microphone & Gyroscope Wiring

## 1. Speaker + MAX98357A Amplifier (I2S Output)

| MAX98357A Pin | ESP32-C3 SuperMini Pin | Notes |
|---|---|---|
| **VIN** | **5V** (or **3.3V**) | `5V` from USB gives louder, cleaner audio; `3.3V` also works |
| **GND** | **GND** | Common ground |
| **BCLK** | **GPIO 4** | I2S Bit Clock (can be shared with I2S Mic `SCK`) |
| **LRC** | **GPIO 5** | I2S Word Select / Left-Right Clock (can be shared with I2S Mic `WS`) |
| **DIN** | **GPIO 6** | I2S Serial Data Out from ESP32-C3 to amplifier |
| **GAIN** | *Leave unconnected* (or **GND**) | Unconnected = 9 dB default gain; tie to `GND` for 12 dB gain |
| **SD** | *Leave unconnected* | Internal pull-up keeps amp enabled (outputs `(L+R)/2` mono mix) |
| **Speaker + / -** | **4Ω / 8Ω Speaker** | Connect directly to speaker terminals on MAX98357A |

---

## 2. Microphone Wiring (Choose Your Mic Type)

### Option A: Digital I2S Microphone (e.g., INMP441 / SPH0645 / ICS-43434)
The ESP32-C3 has **1 hardware I2S bus (`I2S0`)**, which supports full-duplex (`TX` + `RX`) by sharing `BCLK` and `WS/LRC` with the MAX98357A:

| INMP441 Mic Pin | ESP32-C3 SuperMini Pin | Notes |
|---|---|---|
| **VDD** | **3.3V** | **3.3V ONLY** (do NOT connect INMP441 to 5V) |
| **GND** | **GND** | Common ground |
| **SCK** (BCLK) | **GPIO 4** | Shared with MAX98357A `BCLK` |
| **WS** (LRC) | **GPIO 5** | Shared with MAX98357A `LRC` |
| **SD** (DOUT) | **GPIO 7** | I2S Data In (`DIN`) to ESP32-C3 |
| **L/R** | **GND** | Selects Left channel (`GND` = Left, `3.3V` = Right) |

### Option B: Analog Microphone Module (e.g., MAX9814 / MAX4466 / KY-037)
If your microphone has a single `OUT` / `AO` analog pin instead of I2S pins:

| Analog Mic Pin | ESP32-C3 SuperMini Pin | Notes |
|---|---|---|
| **VCC / VDD** | **3.3V** | 3.3V power |
| **GND** | **GND** | Common ground |
| **OUT** | **GPIO 3** (`ADC1_CH3`) | Analog input pin on ESP32-C3 |

---

## 3. Gyroscope / IMU (MPU6050 or QMI8658 — I2C)

Avoid `GPIO 8` (onboard blue LED / strapping pin) and `GPIO 9` (BOOT button / strapping pin) by using `GPIO 1` and `GPIO 0`:

| Gyroscope Pin | ESP32-C3 SuperMini Pin | Notes |
|---|---|---|
| **VCC** | **3.3V** | 3.3V power |
| **GND** | **GND** | Common ground |
| **SDA** | **GPIO 1** | `Wire.begin(1, 0);` |
| **SCL** | **GPIO 0** | `Wire.begin(1, 0);` |

---

## Important Note on ESP32-C3 & Bluetooth Audio
* **ESP32-C3 SuperMini** only has **Bluetooth Low Energy (BLE 5.0)** hardware; it does **not** have **Bluetooth Classic (BR/EDR)** hardware.
* Because standard Bluetooth speakers use Bluetooth Classic (`A2DP`), `<BluetoothA2DPSink.h>` **only compiles on the original ESP32 (ESP32-WROOM-32)** and cannot run on ESP32-C3 or ESP32-S3.
* In [esp32c3_supermini_test.ino](./esp32c3_supermini_test.ino), the ESP32-C3 generates audio directly over I2S to the MAX98357A and exposes a **BLE service (`SilverGuard-C3-Speaker`)** so you can trigger test tones and the fall-alert siren from your phone (using **nRF Connect** / **LightBlue**) or the **Arduino Serial Monitor**.
