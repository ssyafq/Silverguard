# ESP32-SpeakerUSBVer — Wiring & Usage Guide

This folder contains the self-contained **ESP32-C3 + MPU-6050 + INMP441 Mic + MAX98357A Speaker** USB Serial version of SilverGuard.

---

## 1. Pinout (ESP32-C3 SuperMini / ESP32-C3)

| Module | Module Pin | ESP32-C3 Pin | Notes |
|---|---|---|---|
| **MPU-6050 (I2C)** | `SDA` | **GPIO 1** | I2C Data |
| | `SCL` | **GPIO 0** | I2C Clock |
| | `VCC` / `GND` | **3.3V** / **GND** | |
| **MAX98357A (Speaker)** | `BCLK` | **GPIO 4** | Shared I2S Bit Clock |
| | `LRC` | **GPIO 5** | Shared I2S Word Select (Left/Right Clock) |
| | `DIN` | **GPIO 6** | I2S Serial Data Out (`DOUT`) from ESP32 |
| | `VIN` / `GND` | **5V (or 3.3V)** / **GND** | Leave `SD` and `GAIN` unconnected |
| **INMP441 (Mic)** | `SCK` | **GPIO 4** | Shared with MAX98357A `BCLK` |
| | `WS` | **GPIO 5** | Shared with MAX98357A `LRC` |
| | `SD` | **GPIO 7** | I2S Serial Data In (`DIN`) to ESP32 |
| | `L/R` | **GND** | Selects Left channel (`3.3V` also works automatically) |
| | `VDD` / `GND` | **3.3V ONLY** / **GND** | Do **not** connect INMP441 `VDD` to 5V |

---

## 2. Flashing & Hardware Self-Test

1. Open [ESP32-SpeakerUSBVer.ino](./ESP32-SpeakerUSBVer.ino) in Arduino IDE and upload it to your ESP32-C3.
2. On boot, you will hear a short two-note chime from the MAX98357A speaker.
3. Open **Arduino Serial Monitor** (`115200` baud):
   - Send **`b`** → plays the fall-alert siren on the MAX98357A speaker.
   - Send **`m`** → records 1 second from the INMP441 microphone and prints `# mic test (1 s): peak=..., rms=... (INMP441 OK)`.
4. **Close the Arduino Serial Monitor** before running the Python script so the USB COM port is free.

---

## 3. Running the Voice Check-In on the ESP32 Speaker & Mic

From the `Silverguard` project root, run:

```powershell
# Live watch mode (waits for the ESP32 to detect a fall, then speaks & listens through the ESP32):
python Melv-Codes/ESP32-SpeakerUSBVer/usb_voice_bridge.py

# Or specify your COM port explicitly:
python Melv-Codes/ESP32-SpeakerUSBVer/usb_voice_bridge.py --port COM5

# Manual test mode (press Enter anytime to test the voice check-in on the ESP32 speaker + mic):
python Melv-Codes/ESP32-SpeakerUSBVer/usb_voice_bridge.py --manual
```
