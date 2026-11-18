// ============================================================================
// ESP32-C3 SuperMini — MAX98357A Speaker + Bluetooth (BLE) + I2S Test
// ============================================================================
// IMPORTANT HARDWARE NOTE FOR ESP32-C3 SUPERMINI:
//   The ESP32-C3 chip ONLY has Bluetooth Low Energy (BLE 5.0).
//   It does NOT have Bluetooth Classic (BR/EDR), so BluetoothA2DPSink
//   (standard phone music streaming) is NOT supported by the ESP32-C3 silicon.
//
// WHAT THIS TEST SKETCH DOES:
//   1. Initializes the MAX98357A I2S Amplifier on ESP32-C3 SuperMini:
//        BCLK -> GPIO 4
//        LRC  -> GPIO 5
//        DIN  -> GPIO 6
//   2. Immediately plays a startup chime on boot so you can verify the
//      MAX98357A + speaker wiring right away without needing a phone.
//   3. Starts a Bluetooth Low Energy (BLE) receiver named:
//        "SilverGuard-C3-Speaker"
//      You can connect from your phone (using the free "nRF Connect" or
//      "LightBlue" app) OR use the Arduino Serial Monitor (115200 baud)
//      and send single-character commands to test the speaker:
//        '1' = Play 440 Hz test tone (1 second)
//        '2' = Play SilverGuard Fall Alert siren
//        '3' = Play chime melody
//        '4' = Toggle continuous test beep (every 2 seconds)
//        '0' = Stop / Mute
// ============================================================================

#include <Arduino.h>
#include <math.h>
#include "ESP_I2S.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// ==========================================
// ESP32-C3 SUPERMINI PIN CONFIGURATION
// ==========================================

// 1. Speaker Amplifier (MAX98357A — I2S Output)
#define I2S_BCLK      4   // MAX98357A BCLK (also shared with I2S Mic SCK)
#define I2S_LRC       5   // MAX98357A LRC / WS (also shared with I2S Mic WS)
#define I2S_DOUT      6   // MAX98357A DIN

// 2. Microphone (Reserved for next test step)
#define I2S_MIC_DIN   7   // INMP441 I2S Mic SD (if using digital I2S mic)
#define MIC_ADC_PIN   3   // MAX9814 / MAX4466 OUT (if using analog mic on ADC1)

// 3. Gyroscope / IMU — MPU6050 or QMI8658 (Reserved for next test step)
#define IMU_SDA_PIN   1   // MPU6050 SDA
#define IMU_SCL_PIN   0   // MPU6050 SCL

// Onboard Blue LED on ESP32-C3 SuperMini (Active LOW)
#define LED_PIN       8

// ==========================================
// AUDIO & BLE SETTINGS
// ==========================================

const char*    BLE_DEVICE_NAME = "SilverGuard-C3-Speaker";
const uint32_t SAMPLE_RATE     = 44100;
const int16_t  AMPLITUDE       = 10000;  // Volume (0 .. 32767); 10000 is loud & clean

// Nordic UART Service (NUS) UUIDs — works with nRF Connect / Serial Bluetooth apps
#define SERVICE_UUID           "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_RX "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_TX "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

I2SClass i2s;
BLECharacteristic* pTxCharacteristic = nullptr;
bool bleConnected   = false;
bool autoBeep       = true;   // Beeps every 3 s until you send '0' so you know speaker works
volatile char pendingCmd = 0;
unsigned long lastAutoBeepMs = 0;

// ==========================================
// I2S TONE GENERATOR FOR MAX98357A
// ==========================================

void playTone(float freqHz, uint32_t durationMs, int16_t volume = AMPLITUDE) {
  const size_t FRAMES_PER_CHUNK = 256;
  int16_t buffer[FRAMES_PER_CHUNK * 2]; // Stereo interleaved (Left + Right)

  uint32_t totalFrames = (SAMPLE_RATE * durationMs) / 1000;
  uint32_t framesSent  = 0;
  float phase          = 0.0f;
  float phaseInc       = (2.0f * (float)M_PI * freqHz) / (float)SAMPLE_RATE;

  digitalWrite(LED_PIN, LOW); // Turn ON SuperMini blue LED while playing

  while (framesSent < totalFrames) {
    size_t framesThisChunk = FRAMES_PER_CHUNK;
    if (totalFrames - framesSent < FRAMES_PER_CHUNK) {
      framesThisChunk = totalFrames - framesSent;
    }

    for (size_t i = 0; i < framesThisChunk; i++) {
      int16_t sample = (freqHz > 0.0f) ? (int16_t)(sinf(phase) * volume) : 0;
      phase += phaseInc;
      if (phase >= 2.0f * (float)M_PI) {
        phase -= 2.0f * (float)M_PI;
      }
      buffer[2 * i]     = sample; // Left channel
      buffer[2 * i + 1] = sample; // Right channel
    }

    i2s.write((uint8_t*)buffer, framesThisChunk * 2 * sizeof(int16_t));
    framesSent += framesThisChunk;
  }

  // Write a short block of zeros at the end to prevent pop/click on MAX98357A
  memset(buffer, 0, sizeof(buffer));
  i2s.write((uint8_t*)buffer, sizeof(buffer));

  digitalWrite(LED_PIN, HIGH); // Turn OFF SuperMini blue LED
}

void playStartupChime() {
  Serial.println("Playing startup chime (C5 - E5 - G5 - C6)...");
  playTone(523.25f, 150); // C5
  delay(30);
  playTone(659.25f, 150); // E5
  delay(30);
  playTone(783.99f, 150); // G5
  delay(30);
  playTone(1046.50f, 300); // C6
}

void playFallAlertSiren() {
  Serial.println("Playing SilverGuard Fall Alert Siren...");
  for (int i = 0; i < 4; i++) {
    playTone(880.0f, 200);  // High A5
    playTone(660.0f, 200);  // Low E5
  }
}

// ==========================================
// BLE CALLBACKS
// ==========================================

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* pServer) override {
    bleConnected = true;
    Serial.println("\n[BLE] Phone connected! Playing confirmation beep...");
    pendingCmd = '3'; // Play chime on BLE connect
  }

  void onDisconnect(BLEServer* pServer) override {
    bleConnected = false;
    Serial.println("\n[BLE] Phone disconnected. Restarting BLE advertising...");
    pServer->startAdvertising();
  }
};

class RxCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* pCharacteristic) override {
    String rxValue = pCharacteristic->getValue();
    if (rxValue.length() > 0) {
      char cmd = rxValue[0];
      Serial.printf("[BLE RX] Received command: '%c'\n", cmd);
      pendingCmd = cmd;
    }
  }
};

void sendBleStatus(const char* msg) {
  Serial.println(msg);
  if (bleConnected && pTxCharacteristic != nullptr) {
    pTxCharacteristic->setValue((uint8_t*)msg, strlen(msg));
    pTxCharacteristic->notify();
  }
}

void handleCommand(char cmd) {
  switch (cmd) {
    case '1':
      sendBleStatus("-> Playing 440 Hz test tone (1 s)");
      playTone(440.0f, 1000);
      break;
    case '2':
      sendBleStatus("-> Playing Fall Alert Siren");
      playFallAlertSiren();
      break;
    case '3':
      sendBleStatus("-> Playing Startup Chime");
      playStartupChime();
      break;
    case '4':
      autoBeep = !autoBeep;
      sendBleStatus(autoBeep ? "-> Auto-beep every 3s: ON" : "-> Auto-beep every 3s: OFF");
      break;
    case '0':
      autoBeep = false;
      sendBleStatus("-> Speaker muted (auto-beep OFF)");
      playTone(0.0f, 50);
      break;
    default:
      break;
  }
}

// ==========================================
// SETUP
// ==========================================

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH); // Off (active LOW)

  delay(1500); // Allow USB CDC serial on ESP32-C3 SuperMini to connect

  Serial.println();
  Serial.println("================================================");
  Serial.println("  ESP32-C3 SuperMini — MAX98357A Speaker Test");
  Serial.println("================================================");
  Serial.printf("  I2S Pins : BCLK = GPIO %d, LRC = GPIO %d, DIN = GPIO %d\n",
                I2S_BCLK, I2S_LRC, I2S_DOUT);

  // 1. Initialise I2S for MAX98357A
  Serial.println("Starting I2S audio...");
  i2s.setPins(I2S_BCLK, I2S_LRC, I2S_DOUT);

  if (!i2s.begin(
        I2S_MODE_STD,
        SAMPLE_RATE,
        I2S_DATA_BIT_WIDTH_16BIT,
        I2S_SLOT_MODE_STEREO,
        I2S_STD_SLOT_BOTH)) {
    Serial.println("ERROR: I2S initialisation failed! Check pin definitions.");
    while (true) {
      digitalWrite(LED_PIN, !digitalRead(LED_PIN));
      delay(200);
    }
  }
  Serial.println("I2S audio ready!");

  // 2. Play startup chime immediately to verify speaker & MAX98357A
  playStartupChime();

  // 3. Initialise BLE Server
  Serial.println("Starting BLE Server...");
  BLEDevice::init(BLE_DEVICE_NAME);
  BLEServer* pServer = BLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  BLEService* pService = pServer->createService(SERVICE_UUID);

  pTxCharacteristic = pService->createCharacteristic(
    CHARACTERISTIC_UUID_TX,
    BLECharacteristic::PROPERTY_NOTIFY
  );
  pTxCharacteristic->addDescriptor(new BLE2902());

  BLECharacteristic* pRxCharacteristic = pService->createCharacteristic(
    CHARACTERISTIC_UUID_RX,
    BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
  );
  pRxCharacteristic->setCallbacks(new RxCallbacks());

  pService->start();

  BLEAdvertising* pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  BLEDevice::startAdvertising();

  Serial.println("================================================");
  Serial.printf("  BLE Device Name : %s\n", BLE_DEVICE_NAME);
  Serial.println("  Commands (via Serial Monitor or BLE app):");
  Serial.println("    '1' = 440 Hz tone (1 s)");
  Serial.println("    '2' = Fall Alert siren");
  Serial.println("    '3' = Chime melody");
  Serial.println("    '4' = Toggle periodic test beep (every 3 s)");
  Serial.println("    '0' = Mute / Stop auto-beep");
  Serial.println("================================================");

  lastAutoBeepMs = millis();
}

// ==========================================
// MAIN LOOP
// ==========================================

void loop() {
  // Check USB Serial Monitor for commands
  while (Serial.available()) {
    char c = Serial.read();
    if (c >= '0' && c <= '9') {
      handleCommand(c);
    }
  }

  // Check BLE for commands sent from phone
  if (pendingCmd != 0) {
    char c = pendingCmd;
    pendingCmd = 0;
    handleCommand(c);
  }

  // Periodic short double-beep every 3 seconds while autoBeep is enabled
  if (autoBeep && (millis() - lastAutoBeepMs >= 3000)) {
    lastAutoBeepMs = millis();
    Serial.println("[TEST] Playing periodic test beep (send '0' to mute, '2' for siren)...");
    playTone(880.0f, 120);
    delay(80);
    playTone(1174.66f, 120);
  }
}
