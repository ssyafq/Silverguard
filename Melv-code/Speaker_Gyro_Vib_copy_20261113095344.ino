
#include <Arduino.h>
#include <Wire.h>
#include <math.h>
#include "ESP_I2S.h"
#include "BluetoothA2DPSink.h"

// ==========================================
// ESP32-WROOM-32 PIN CONFIGURATION
// ==========================================

#define SDA_PIN 21
#define SCL_PIN 22

#define MOTOR_PIN 15

#define I2S_BCLK 26
#define I2S_LRC  25
#define I2S_DOUT 27

// ==========================================
// BLUETOOTH SPEAKER SETTINGS
// ==========================================

const char *BT_NAME = "ESP32 Bluetooth Speaker";

const uint8_t SPEAKER_VOLUME = 100;
const uint8_t SPEAKER_MUTED = 0;

I2SClass i2s;
BluetoothA2DPSink a2dp_sink(i2s);

// ==========================================
// MOTOR SETTINGS
// ==========================================

const int MOTOR_POWER = 255;
const unsigned long VIBRATE_MS = 10000;

// ==========================================
// MPU6050 SETTINGS
// ==========================================

#define MPU_ADDR 0x68

const float ACC_THRESH_G = 1.0;

const unsigned long SAMPLE_US = 20000;
// 20000 microseconds = 50 Hz

// ==========================================
// MPU6050 REGISTERS
// ==========================================

#define REG_SMPLRT_DIV   0x19
#define REG_CONFIG       0x1A
#define REG_GYRO_CONFIG  0x1B
#define REG_ACCEL_CONFIG 0x1C
#define REG_ACCEL_XOUT_H 0x3B
#define REG_PWR_MGMT_1   0x6B

// ==========================================
// SENSOR SCALING
// ==========================================

const float ACCEL_SCALE = 4096.0;
const float GYRO_SCALE = 65.5;

// ==========================================
// VARIABLES
// ==========================================

bool motorRunning = false;
bool previousOver = false;

unsigned long motorStartMs = 0;
unsigned long nextSampleUs = 0;

uint8_t printCounter = 0;

// ==========================================
// MOTOR CONTROL
// ==========================================

void motorOn()
{
    analogWrite(MOTOR_PIN, MOTOR_POWER);

    motorRunning = true;
    motorStartMs = millis();
}

void motorOff()
{
    analogWrite(MOTOR_PIN, 0);

    motorRunning = false;
}

// ==========================================
// SPEAKER CONTROL
// ==========================================

void speakerOn()
{
    a2dp_sink.set_volume(SPEAKER_VOLUME);
}

void speakerOff()
{
    a2dp_sink.set_volume(SPEAKER_MUTED);
}

// ==========================================
// WRITE MPU6050 REGISTER
// ==========================================

bool writeReg(uint8_t reg, uint8_t value)
{
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(reg);
    Wire.write(value);

    return Wire.endTransmission() == 0;
}

// ==========================================
// READ MPU6050 SENSOR
// ==========================================

bool readSensor(float &accDyn, float &gyroMag)
{
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(REG_ACCEL_XOUT_H);

    if (Wire.endTransmission(false) != 0)
        return false;

    if (Wire.requestFrom(MPU_ADDR, (uint8_t)14) != 14)
        return false;

    // Read acceleration
    int16_t ax = (Wire.read() << 8) | Wire.read();
    int16_t ay = (Wire.read() << 8) | Wire.read();
    int16_t az = (Wire.read() << 8) | Wire.read();

    // Skip temperature
    Wire.read();
    Wire.read();

    // Read gyroscope
    int16_t gx = (Wire.read() << 8) | Wire.read();
    int16_t gy = (Wire.read() << 8) | Wire.read();
    int16_t gz = (Wire.read() << 8) | Wire.read();

    // Convert acceleration to g
    float fx = ax / ACCEL_SCALE;
    float fy = ay / ACCEL_SCALE;
    float fz = az / ACCEL_SCALE;

    // Calculate acceleration magnitude
    float accMag = sqrtf(
        fx * fx +
        fy * fy +
        fz * fz
    );

    // Remove gravity
    accDyn = fabsf(accMag - 1.0f);

    // Convert gyroscope to degrees per second
    float dx = gx / GYRO_SCALE;
    float dy = gy / GYRO_SCALE;
    float dz = gz / GYRO_SCALE;

    // Calculate gyroscope magnitude
    gyroMag = sqrtf(
        dx * dx +
        dy * dy +
        dz * dz
    );

    return true;
}

// ==========================================
// SETUP
// ==========================================

void setup()
{
    Serial.begin(9600);

    // Configure motor
    pinMode(MOTOR_PIN, OUTPUT);
    analogWriteResolution(MOTOR_PIN, 8);
    motorOff();

    delay(1000);

    Serial.println();
    Serial.println("================================");
    Serial.println("ESP32 MPU6050 BLUETOOTH DEVICE");
    Serial.println("================================");

    // --------------------------------------
    // INITIALISE MPU6050
    // --------------------------------------

    Wire.begin(SDA_PIN, SCL_PIN);
    Wire.setClock(400000);

    Serial.println("Starting MPU6050...");

    Wire.beginTransmission(MPU_ADDR);

    if (Wire.endTransmission() != 0)
    {
        Serial.println("ERROR: MPU6050 not found!");
        Serial.println("Check SDA, SCL, VCC and GND.");

        while (true)
            delay(1000);
    }

    writeReg(REG_PWR_MGMT_1, 0x00);
    delay(100);

    writeReg(REG_CONFIG, 0x03);
    writeReg(REG_SMPLRT_DIV, 0x00);
    writeReg(REG_GYRO_CONFIG, 0x08);
    writeReg(REG_ACCEL_CONFIG, 0x10);

    Serial.println("MPU6050 connected!");

    // --------------------------------------
    // INITIALISE I2S AUDIO
    // --------------------------------------

    Serial.println("Starting I2S audio...");

    i2s.setPins(I2S_BCLK, I2S_LRC, I2S_DOUT);

    if (!i2s.begin(
        I2S_MODE_STD,
        44100,
        I2S_DATA_BIT_WIDTH_16BIT,
        I2S_SLOT_MODE_STEREO,
        I2S_STD_SLOT_BOTH
    ))
    {
        Serial.println("ERROR: I2S initialisation failed!");

        while (true)
            delay(1000);
    }

    Serial.println("I2S audio ready!");

    // --------------------------------------
    // START BLUETOOTH SPEAKER
    // --------------------------------------

    Serial.println("Starting Bluetooth...");

    a2dp_sink.start(BT_NAME);

    // Mute speaker until movement is detected
    speakerOff();

    Serial.println("Bluetooth device name:");
    Serial.println(BT_NAME);

    // --------------------------------------
    // DISPLAY SETTINGS
    // --------------------------------------

    Serial.println();
    Serial.println("================================");
    Serial.println("SYSTEM READY");
    Serial.println("================================");

    Serial.println("Acceleration threshold: 1.0 g");
    Serial.println("Motor power: MAXIMUM");
    Serial.println("Speaker volume: 100");
    Serial.println("Activation duration: 10 seconds");
    Serial.println("Sampling frequency: 50 Hz");
    Serial.println("Serial display: 10 Hz");

    Serial.println("================================");

    nextSampleUs = micros();
}

// ==========================================
// MAIN LOOP
// ==========================================

void loop()
{
    // --------------------------------------
    // STOP MOTOR AND MUTE SPEAKER
    // AFTER 10 SECONDS
    // --------------------------------------

    if (motorRunning &&
        millis() - motorStartMs >= VIBRATE_MS)
    {
        motorOff();
        speakerOff();

        Serial.println();
        Serial.println("================================");
        Serial.println("10 SECONDS COMPLETED");
        Serial.println("MOTOR OFF");
        Serial.println("SPEAKER MUTED");
        Serial.println("================================");
    }

    // --------------------------------------
    // SAMPLE SENSOR AT 50 HZ
    // --------------------------------------

    unsigned long nowUs = micros();

    if ((long)(nowUs - nextSampleUs) < 0)
        return;

    nextSampleUs += SAMPLE_US;

    float accDyn;
    float gyroMag;

    // --------------------------------------
    // READ MPU6050
    // --------------------------------------

    if (!readSensor(accDyn, gyroMag))
    {
        Serial.println("MPU6050 I2C read error!");

        nextSampleUs = micros() + SAMPLE_US;
        return;
    }

    // --------------------------------------
    // DETECT ACCELERATION SPIKE
    // --------------------------------------

    bool over = (accDyn > ACC_THRESH_G);

    if (over && !previousOver && !motorRunning)
    {
        motorOn();
        speakerOn();

        Serial.println();
        Serial.println("!!! ACCELERATION SPIKE DETECTED !!!");

        Serial.println("MOTOR ON");
        Serial.println("SPEAKER ON");

        Serial.println("POWER: MAXIMUM");
        Serial.println("DURATION: 10 SECONDS");
        Serial.println();
    }

    previousOver = over;

    // --------------------------------------
    // DISPLAY SENSOR READINGS AT 10 HZ
    // --------------------------------------

    if (++printCounter >= 5)
    {
        printCounter = 0;

        Serial.print("Acceleration: ");
        Serial.print(accDyn, 2);
        Serial.print(" g");

        Serial.print(" | Gyroscope: ");
        Serial.print(gyroMag, 1);
        Serial.print(" deg/s");

        Serial.print(" | Motor: ");
        Serial.print(motorRunning ? "ON" : "OFF");

        Serial.print(" | Speaker: ");
        Serial.println(motorRunning ? "ON" : "MUTED");
    }
}
