// stream_demo.ino: test sketch for the streaming path, WITHOUT the sensor or the models.
//
// WHAT THIS FILE DOES
//   Pretends to be the watch's HAR model so you can check Wi-Fi → Cloud Run → Firestore on
//   any ESP32 before touching the real firmware. You type commands in the Serial Monitor and
//   the same streaming code the real watch uses (silverguard_stream.h) sends them online.
//
// Setup: copy stream_config.example.h to stream_config.h and fill it in. Board: ESP32-C3
// (any ESP32 works). Serial Monitor at 115200:
//   s = pretend HAR says "still"     m = pretend HAR says "moving"
//   f = send a fake confirmed fall   p = print send statistics
// The fake HAR result repeats every 0.5 s like the real firmware, so the debounce applies:
// type 'm', wait 3 s, and the change is sent. Watch it arrive with tools/live_status.py.

#include "silverguard_stream.h"

int fake_har = 0;            // 0 = still, 1 = moving
uint32_t last_result = 0;

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("# stream_demo: s = still, m = moving, f = fake fall, p = stats");
  sgstream::begin();
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 's') { fake_har = 0; Serial.println("# demo: HAR now says still"); }
    else if (c == 'm') { fake_har = 1; Serial.println("# demo: HAR now says moving"); }
    else if (c == 'f') sgstream::sendFall(0.93f, 3.4f, 47.0f);
    else if (c == 'p') sgstream::printStats();
  }
  if (millis() - last_result >= 500) {      // same rhythm as runModels() in the real firmware
    last_result = millis();
    sgstream::onHar(fake_har);
  }
}
