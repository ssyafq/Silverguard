import serial, threading, time, re, os

# understands both output modes of the sketch (OUTPUT_CSV 1 or 0)
READABLE = re.compile(r'\[\s*([\d.]+) s\]\s+#\d+\s+(MOVING|still)')
def parse(line):
    if line.startswith('W,'):
        f = line.split(',')
        return int(f[2]), f[8] == '1'
    m = READABLE.match(line)
    if m:
        return int(float(m.group(1)) * 1000), m.group(2) == 'MOVING'
    return None

PORT = 'COM5'          # Arduino IDE -> Tools -> Port shows the right one
BAUD = 115200

def open_port():
    while True:                                   # keep trying until the board is back
        try:
            return serial.Serial(PORT, BAUD, timeout=1)
        except serial.SerialException:
            time.sleep(0.5)

ser = open_port()
log = open('log.txt', 'w')
labels = open('labels.csv', 'w')
labels.write('ms,label\n'); labels.flush()
print('Saving to:', os.path.abspath('labels.csv'))
last_ms = 0
last_state = None

def reader():
    global ser, last_ms, last_state
    while True:
        try:
            line = ser.readline().decode(errors='ignore').strip()
        except (serial.SerialException, OSError):
            # ESP32-C3 SuperMini: pressing RST drops the USB port for a moment
            print('  (port lost, e.g. board reset: reconnecting...)')
            try:
                ser.close()
            except Exception:
                pass
            ser = open_port()
            print('  (reconnected)')
            continue
        if not line:
            continue
        log.write(line + '\n'); log.flush()
        r = parse(line)
        if r:
            last_ms, moving = r
            state = 'MOVING' if moving else 'still'
            if state != last_state:               # only show changes, keeps the screen readable
                print(f'  {last_ms/1000:7.1f} s  {state}')
                last_state = state
        elif not line.startswith('S,'):
            print('  device:', line)              # startup / calibration messages

threading.Thread(target=reader, daemon=True).start()
print('Recording to log.txt.')
print('  type a label + Enter (e.g. "fidget", "walk start", "walk stop")')
print('  type "cal" to recalibrate (sensor flat + still), "q" to stop')

while True:
    lab = input().strip()
    if not lab:
        continue                                  # ignore empty Enter
    if lab.lower() in ('q', 'qq', 'quit', 'exit'):
        break
    if lab.lower() == 'cal':
        try:
            ser.write(b'c')
            print('  >>> calibration requested: keep the sensor FLAT and STILL')
        except Exception:
            print('  (could not send, port not ready yet)')
        continue
    if last_ms == 0:
        print('  (no data received yet: is OUTPUT_CSV set to 1 and the sketch re-uploaded?)')
    labels.write(f'{last_ms},{lab}\n'); labels.flush()
    print(f'  >>> "{lab}" at {last_ms/1000:.1f} s')

ser.close(); log.close(); labels.close()
print('Saved log.txt and labels.csv')