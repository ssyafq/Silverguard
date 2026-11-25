# espwatch_prototype: the FinalPrototype on the Waveshare watch, over Wi-Fi

This works the same way as [`firmware/FinalPrototype`](../FinalPrototype/), with two differences:

- It runs on the **Waveshare ESP32-S3-Touch-AMOLED-2.06** watch, using the watch's own parts.
- The watch talks to the laptop **over Wi-Fi instead of a USB cable**. The laptop and the watch must be on the same hotspot.

| | FinalPrototype (USB) | espwatch_prototype (Wi-Fi) |
|---|---|---|
| Board | ESP32-C3 + wired modules | Waveshare watch |
| Motion sensor | MPU-6050 | QMI8658 (built in) |
| Speaker + mic | MAX98357A + INMP441 | ES8311 codec (built in) |
| Link to laptop | USB serial | TCP port 3333 over Wi-Fi |
| Models | HAR + fall (same files) | same |
| Extra | | AMOLED screen, touch "I'M OK" / "NEED HELP", BOOT button, 30 s countdown |

On a fall, the watch beeps, shows the check-in screen and prints `FALL DETECTED`. The laptop then speaks "Are you okay?" through the watch speaker and records the reply through the watch mic. Whisper and Claude decide whether the wearer is ok, needs help or didn't reply. If they need help or didn't reply, the laptop sends a Telegram alert. Finally the laptop sends `r` and the watch resumes.

## Setup (once)

1. Copy `wifi_config.example.h` to `wifi_config.h` and put in your hotspot name and password. The hotspot must be 2.4 GHz; on iPhone, turn on "Maximize Compatibility". `wifi_config.h` is git-ignored.
2. In the Arduino IDE, install **GFX Library for Arduino** (for the screen). You need esp32 core 3.x.
3. Board: **ESP32S3 Dev Module**, with **USB CDC On Boot: Enabled**. Open `espwatch_prototype.ino` and upload.
4. Open the Serial Monitor once (115200) to check the boot output and get the IP:
   ```
   # audio: I2S OK, ES8311 (0x18) OK @ 16000 Hz
   # connecting to Wi-Fi 'YourHotspot'.... connected, IP 172.20.10.13
   # self-test: all PASS
   # waiting for laptop: python -m basestation.voice.watch_bridge --host 172.20.10.13
   ```
   You should hear a short chime at boot. In the Serial Monitor, type `b` to play the alert tone, or `m` to test the mic level (speak while it runs).

## Run

From the project root, with the venv active, and the laptop on the same hotspot:

```powershell
python -m basestation.voice.watch_bridge --host 172.20.10.13            # waits for a real fall
python -m basestation.voice.watch_bridge --host 172.20.10.13 --manual   # press Enter to test a check-in
```

Once the watch is flashed, it can run on battery. You can also keep the USB cable plugged in, so the Serial Monitor shows the same lines.

## Notes

- **Audio settings:** the pins are BCLK 41, WS 45, DOUT 40, DIN 42, MCLK 16, and amplifier enable 46. The codec is at 0x18. These come from Waveshare's `08_ES8311` example. If the reply is too quiet for Whisper, raise `MIC_GAIN` (0–7). For speaker loudness, change `SPEAKER_VOLUME` (0–100).
- **Untested parts:** this has not been flashed or tested on the watch yet. The ES8311 setup copies Espressif's driver sequence, but the speaker, mic and Wi-Fi need checking on the hardware. The laptop side was tested against a simulated watch.
- **If Wi-Fi drops:** if the link drops during monitoring, the bridge stops and sends a Telegram message saying falls are not being sent to the laptop. That matches the USB version when the cable is pulled. The watch itself keeps detecting falls and showing the check-in screen.
