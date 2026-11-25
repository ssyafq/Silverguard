"""Record espwatch samples over Wi-Fi (or USB) and save them as CSV, windowed like the training data.

    python capture_csv.py --host espwatch.local --out walk --label 1 --seconds 120     # Wi-Fi
    python capture_csv.py --host 192.168.43.57  --out walk                             # Wi-Fi, by IP
    python capture_csv.py --port COM5 --out walk                                       # USB

Writes two files:
    walk_raw.csv      one row per sample, as sent by the watch
    walk_windows.csv  overlapping windows: HAR 128 samples / hop 64 (default) or --mode fall 125 / hop 62

Windows are cut like the training code (starts at 0, hop, 2*hop, ... of a gap-free stretch). A jump in
sample_index means samples were missed, so the stretch ends there and a new one starts.
If the Wi-Fi link drops, the script reconnects; the watch keeps up to 60 s of samples, so nothing is lost.
Stop with Ctrl+C. USB needs pyserial (pip install pyserial).
"""
import argparse
import socket
import time

MODES = {"har": (128, 64), "fall": (125, 62)}
TCP_PORT = 3333
WIN_COLS = "window_id,sample_in_window,"


class Windower:
    """Cuts gap-free stretches of samples into overlapping windows, like the training code."""

    def __init__(self, win, hop, f):
        self.win, self.hop, self.f = win, hop, f
        self.stretch = []          # (sample_index, row) of the current gap-free stretch, last `win` kept
        self.count = 0             # samples in the current stretch
        self.windows = 0

    def add(self, index, row):
        if self.stretch and index != self.stretch[-1][0] + 1:
            self.stretch, self.count = [], 0            # gap: start a new stretch
        self.stretch.append((index, row))
        self.count += 1
        if len(self.stretch) > self.win:
            self.stretch.pop(0)
        if self.count >= self.win and (self.count - self.win) % self.hop == 0:
            for k, (_, r) in enumerate(self.stretch):
                self.f.write(f"{self.windows},{k},{r}\n")
            self.windows += 1
            return True
        return False


def lines_tcp(host, label):
    """Yield lines from the watch, reconnecting if the link drops.
    Yields "" at least once a second, so the caller can check its time limit even with no data."""
    first = True
    while True:
        try:
            with socket.create_connection((host, TCP_PORT), timeout=5) as s:
                s.settimeout(1)
                print(f"connected to {host}:{TCP_PORT}")
                if first and label is not None:
                    s.sendall(str(label).encode())
                first = False
                buf = b""
                last_data = time.time()
                while True:
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        if time.time() - last_data > 15:   # status line every 10 s: 15 s of silence = dead link
                            raise ConnectionError("no data for 15 s")
                        yield ""
                        continue
                    if not chunk:
                        raise ConnectionError("watch closed the connection")
                    last_data = time.time()
                    buf += chunk
                    *done, buf = buf.split(b"\n")
                    for line in done:
                        yield line.decode(errors="replace").strip()
        except (OSError, ConnectionError) as e:
            print(f"link lost ({e}), retrying in 2 s...")
            yield ""
            time.sleep(2)


def lines_serial(port, label):
    import serial
    with serial.Serial(port, 115200, timeout=1) as ser:
        time.sleep(0.5)
        ser.reset_input_buffer()
        ser.write(b"h")
        if label is not None:
            ser.write(str(label).encode())
        while True:
            yield ser.readline().decode(errors="replace").strip()


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--host", help="watch IP or espwatch.local (Wi-Fi)")
    src.add_argument("--port", help="serial port, e.g. COM5 (USB)")
    ap.add_argument("--out", required=True, help="file name prefix, e.g. walk -> walk_raw.csv, walk_windows.csv")
    ap.add_argument("--mode", choices=MODES, default="har", help="window size: har (128/64) or fall (125/62)")
    ap.add_argument("--label", type=int, choices=range(10), help="label 0-9 sent to the watch at the start")
    ap.add_argument("--seconds", type=float, help="stop after this many seconds (default: until Ctrl+C)")
    args = ap.parse_args()

    win, hop = MODES[args.mode]
    lines = lines_tcp(args.host, args.label) if args.host else lines_serial(args.port, args.label)
    samples = 0
    header = None
    with open(f"{args.out}_raw.csv", "w", newline="") as fraw, \
         open(f"{args.out}_windows.csv", "w", newline="") as fwin:
        windower = Windower(win, hop, fwin)
        start = time.time()
        try:
            for line in lines:
                if args.seconds is not None and time.time() - start >= args.seconds:
                    break
                if not line:
                    continue
                if line.startswith("#"):
                    print(line)
                elif line.startswith("sample_index,"):
                    if header is None:
                        header = line
                        fraw.write(line + "\n")
                        fwin.write(WIN_COLS + line + "\n")
                elif header and line[0].isdigit():
                    parts = line.split(",")
                    if len(parts) != header.count(",") + 1:
                        continue                        # cut-off line
                    fraw.write(line + "\n")
                    samples += 1
                    if windower.add(int(parts[0]), line) and windower.windows % 10 == 0:
                        print(f"  {samples} samples ({samples / 50:.0f} s), {windower.windows} windows")
        except KeyboardInterrupt:
            pass
    print(f"saved {samples} samples to {args.out}_raw.csv and {windower.windows} "
          f"{args.mode} windows ({win}/{hop}) to {args.out}_windows.csv")


if __name__ == "__main__":
    main()
