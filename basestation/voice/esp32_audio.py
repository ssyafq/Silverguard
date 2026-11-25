"""Audio bridge to the ESP32 watch (MAX98357A speaker + INMP441 microphone).

Transports:
  * USB Serial (`ESP32_PORT` in `.env` or `voice.esp32_port` in `config.yaml`, or auto-detected port).
  * Wi-Fi: `ESP32_PORT=tcp:<watch ip>` (or watch_bridge --host): a TCP socket to the Waveshare watch
    running firmware/espwatch_prototype. Same bytes as over USB.

Protocol (16 kHz, 16-bit signed mono PCM), the same on both transports:
  * Play chunk (PC → ESP32 speaker):
      b'P' + uint16_le(n_samples) + int16_le[n_samples]  → ESP32 replies b'K'
  * Record chunk (ESP32 mic → PC):
      b'L' + uint16_le(n_samples)                        → ESP32 replies b'M' + int16_le[n_samples]
"""

from __future__ import annotations

import os
import struct
import time
from typing import Optional

import numpy as np
import serial
import serial.tools.list_ports

from basestation.voice.settings import voice_config

ESP32_SAMPLE_RATE = 16_000
PLAY_CHUNK_SAMPLES = 512      # 512 samples = 1024 bytes = 32 ms per packet (fits in ESP32 RX buffer)
RECORD_CHUNK_SAMPLES = 4000   # 4000 samples = 8000 bytes = 0.25 s per packet

_ser: Optional[serial.Serial] = None


def configured_port() -> str:
    """Return the configured serial port ('auto', 'COM5', or '' to use laptop audio)."""
    env_port = os.environ.get("ESP32_PORT", "").strip()
    if env_port:
        return env_port
    cfg_port = str(voice_config().get("esp32_port", "auto") or "").strip()
    return cfg_port


def find_esp32_port() -> Optional[str]:
    """Auto-detect a connected ESP32 USB serial port."""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None
    # Prefer ports matching common ESP32 USB VIDs (Espressif 0x303A, CP210x 0x10C4, CH340 0x1A86)
    esp_vids = {0x303A, 0x10C4, 0x1A86, 0x0403}
    for p in ports:
        if p.vid in esp_vids:
            return p.device
        desc = f"{p.description} {p.manufacturer or ''}".lower()
        if any(k in desc for k in ("esp32", "usb jtag", "cp210", "ch340", "usb serial")):
            return p.device
    return ports[0].device


class WifiLink:
    """The watch over Wi-Fi (firmware/espwatch_prototype, TCP port 3333), with the parts of the
    pyserial API this module and watch_bridge use. The bytes are the same as over USB."""

    def __init__(self, host: str, port: int = 3333, timeout: float = 2.0):
        import socket
        self.port = f"{host}:{port} (Wi-Fi)"
        self.timeout = timeout
        self._buf = bytearray()
        try:
            self._sock = socket.create_connection((host, port), timeout=5)
        except OSError as exc:
            raise serial.SerialException(f"could not reach the watch at {host}:{port}: {exc}") from exc
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.is_open = True

    def _fill(self, timeout: float) -> bool:
        """Wait up to `timeout` s for more bytes. False on timeout; raises if the watch is gone."""
        import socket
        self._sock.settimeout(max(timeout, 0.001))
        try:
            chunk = self._sock.recv(65536)
        except socket.timeout:
            return False
        except OSError as exc:
            raise serial.SerialException(f"Wi-Fi link to the watch lost: {exc}") from exc
        if not chunk:
            raise serial.SerialException("the watch closed the Wi-Fi connection")
        self._buf.extend(chunk)
        return True

    def read(self, size: int = 1) -> bytes:
        deadline = time.perf_counter() + self.timeout
        while len(self._buf) < size and self._fill(deadline - time.perf_counter()):
            pass
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out

    def readline(self) -> bytes:
        deadline = time.perf_counter() + self.timeout
        while b"\n" not in self._buf and self._fill(deadline - time.perf_counter()):
            pass
        end = self._buf.find(b"\n") + 1 or len(self._buf)
        out = bytes(self._buf[:end])
        del self._buf[:end]
        return out

    def write(self, data: bytes) -> int:
        try:
            self._sock.sendall(data)
        except OSError as exc:
            raise serial.SerialException(f"Wi-Fi link to the watch lost: {exc}") from exc
        return len(data)

    def flush(self) -> None:
        pass                                     # sendall() already handed everything to the network

    def reset_input_buffer(self) -> None:
        """Drop everything received so far (like pyserial), without waiting."""
        self._buf.clear()
        while self._fill(0.001):
            self._buf.clear()

    def close(self) -> None:
        self.is_open = False
        try:
            self._sock.close()
        except OSError:
            pass


def get_serial(port: Optional[str] = None) -> Optional[serial.Serial]:
    """Return an open connection to the ESP32, or None if disabled/unavailable.

    `port` is a COM port ('COM5', 'auto') or, for the Wi-Fi watch, 'tcp:<ip or name>' (e.g. 'tcp:172.20.10.13').
    """
    global _ser
    if _ser is not None and _ser.is_open:
        return _ser

    target = port or configured_port()
    if not target or target.lower() in ("none", "off", "laptop", "false"):
        return None
    if target.lower().startswith("tcp:"):
        try:
            _ser = WifiLink(target[4:].strip())
            return _ser
        except serial.SerialException as exc:
            print(f"  [esp32_audio] {exc}")
            return None
    if target.lower() == "auto":
        target = find_esp32_port()
        if not target:
            return None

    try:
        s = serial.Serial()
        s.port = target
        s.baudrate = 921600
        s.timeout = 2.0
        s.dtr = False
        s.rts = False
        s.open()
        _ser = s
        return _ser
    except Exception as exc:
        print(f"  [esp32_audio] Could not open serial port {target}: {exc}")
        return None


def close_serial() -> None:
    """Close the shared ESP32 serial connection if open."""
    global _ser
    if _ser is not None:
        try:
            _ser.close()
        except Exception:
            pass
        _ser = None


def use_esp32_audio() -> bool:
    """True if ESP32 serial audio is enabled and a board is reachable."""
    return get_serial() is not None


def _resample_to_16k_mono(data: np.ndarray, samplerate: int) -> np.ndarray:
    """Convert float32 audio of any sample rate / channel count to 16 kHz mono int16."""
    audio = np.asarray(data, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if samplerate != ESP32_SAMPLE_RATE and audio.size > 0:
        duration = audio.size / float(samplerate)
        new_len = int(round(duration * ESP32_SAMPLE_RATE))
        if new_len > 0:
            old_idx = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
            new_idx = np.linspace(0.0, 1.0, num=new_len, endpoint=False)
            audio = np.interp(new_idx, old_idx, audio).astype(np.float32)
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2")


def _wait_for_byte(ser: serial.Serial, expected: bytes, timeout_s: float = 2.0) -> bool:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        b = ser.read(1)
        if b == expected:
            return True
    return False


def play_audio(data: np.ndarray, samplerate: int) -> bool:
    """Stream audio to the ESP32 MAX98357A speaker over USB Serial.

    Returns True if played on the ESP32 speaker, or False if no ESP32 is connected.
    """
    ser = get_serial()
    if ser is None:
        return False

    pcm16 = _resample_to_16k_mono(data, samplerate)
    if pcm16.size == 0:
        return True

    try:
        ser.reset_input_buffer()
        total = pcm16.size
        offset = 0
        while offset < total:
            chunk = pcm16[offset:offset + PLAY_CHUNK_SAMPLES]
            n = int(chunk.size)
            header = b"P" + struct.pack("<H", n)
            ser.write(header + chunk.tobytes())
            ser.flush()
            if not _wait_for_byte(ser, b"K", timeout_s=1.5):
                raise TimeoutError("ESP32 did not ACK audio playback chunk ('K')")
            offset += n

        # Send a 0-length play packet to tell ESP32 to flush silence & mute pop
        ser.write(b"P" + struct.pack("<H", 0))
        ser.flush()
        _wait_for_byte(ser, b"K", timeout_s=1.0)
        return True
    except Exception as exc:
        print(f"  [esp32_audio] Speaker playback error: {exc}")
        return False


def record_audio(seconds: float, samplerate: int = ESP32_SAMPLE_RATE) -> Optional[np.ndarray]:
    """Record mono float32 audio from the ESP32 INMP441 microphone over USB Serial.

    Returns a 1-D float32 numpy array in [-1, 1] at 16 kHz, or None if unavailable.
    """
    ser = get_serial()
    if ser is None:
        return None

    total_samples = max(1, int(round(seconds * ESP32_SAMPLE_RATE)))
    collected = []
    remaining = total_samples

    try:
        ser.reset_input_buffer()
        while remaining > 0:
            n = min(RECORD_CHUNK_SAMPLES, remaining)
            cmd = b"L" + struct.pack("<H", n)
            ser.write(cmd)
            ser.flush()

            # Wait for 'M' sync marker before raw PCM bytes
            if not _wait_for_byte(ser, b"M", timeout_s=2.0):
                raise TimeoutError("ESP32 did not start mic capture ('M' marker timeout)")

            n_bytes = n * 2
            buf = bytearray()
            deadline = time.perf_counter() + (n / ESP32_SAMPLE_RATE) + 2.0
            while len(buf) < n_bytes and time.perf_counter() < deadline:
                part = ser.read(n_bytes - len(buf))
                if part:
                    buf.extend(part)

            if len(buf) < n_bytes:
                raise TimeoutError(f"ESP32 mic read incomplete ({len(buf)}/{n_bytes} bytes)")

            chunk_i16 = np.frombuffer(bytes(buf), dtype="<i2")
            collected.append(chunk_i16)
            remaining -= n

        pcm16 = np.concatenate(collected) if collected else np.zeros(0, dtype="<i2")
        return pcm16.astype(np.float32) / 32768.0
    except Exception as exc:
        print(f"  [esp32_audio] Mic recording error: {exc}")
        return None


def send_resume() -> None:
    """Send 'r' to the ESP32 to clear the fall alarm and resume 50 Hz IMU monitoring."""
    ser = get_serial()
    if ser is not None:
        try:
            ser.write(b"r")
            ser.flush()
        except Exception:
            pass
