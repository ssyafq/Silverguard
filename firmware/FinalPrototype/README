# To run without problems:

**Flash ESP32-SpeakerUSBVer.ino to the ESP32** 

Make sure that connection is as follows:

const char*    FW_VERSION   = "ESP32-SpeakerUSBVer 0.5 (HAR + fall models + INMP441 mic + MAX98357A speaker)";
const int      SDA_PIN      = 1;          // MPU-6050 I2C data  (GPIO 1)
const int      SCL_PIN      = 0;          // MPU-6050 I2C clock (GPIO 0)

// Shared I2S bus (MAX98357A speaker amp + INMP441 digital microphone)
const int      I2S_BCLK_PIN = 4;          // MAX98357A BCLK + INMP441 SCK (GPIO 4)
const int      I2S_LRC_PIN  = 5;          // MAX98357A LRC  + INMP441 WS  (GPIO 5)
const int      I2S_DOUT_PIN = 6;          // MAX98357A DIN  (speaker data out, GPIO 6)
const int      I2S_DIN_PIN  = 7;          // INMP441   SD   (mic data in, GPIO 7)
const uint32_t AUDIO_RATE   = 16000;      // 16 kHz mono PCM (matches Whisper STT on laptop)
const int16_t  TONE_AMP     = 10000;      // Alert / test tone amplitude (0..32767)

# Once flashed and compiled into the ESP32:

**Run  in terminal:**

python -m basestation.voice.watch_bridge   

# If there are missing libraries:

**Run  in terminal:**

py -3.11 -m venv .venv

.venv\Scripts\activate

pip install -r requirements.txt
