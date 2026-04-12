"""GPS reader using gpspipe for location data."""

import json
import subprocess
import threading
import time


class GpsState:
    """Thread-safe GPS state."""
    def __init__(self):
        self.lat = 0.0
        self.lon = 0.0
        self.alt = 0.0
        self.speed = 0.0  # m/s
        self.satellites = 0
        self.fix_mode = 0  # 0=none, 2=2D, 3=3D
        self.timestamp = ""
        self._lock = threading.Lock()

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k):
                    setattr(self, k, v)

    def copy(self):
        """Return a snapshot of current state."""
        with self._lock:
            s = GpsState()
            s.lat = self.lat
            s.lon = self.lon
            s.alt = self.alt
            s.speed = self.speed
            s.satellites = self.satellites
            s.fix_mode = self.fix_mode
            s.timestamp = self.timestamp
            return s

    @property
    def speed_mph(self):
        """Speed in mph."""
        with self._lock:
            return self.speed * 2.237

    @property
    def has_fix(self):
        with self._lock:
            return self.fix_mode >= 2


class GpsReader(threading.Thread):
    def __init__(self, device, baud, gps_state, stop_event):
        super().__init__(daemon=True)
        self.device = device
        self.baud = baud
        self.gps_state = gps_state
        self.stop_event = stop_event
        self._process = None

    def run(self):
        self._ensure_gpsd()
        while not self.stop_event.is_set():
            try:
                self._read_gpspipe()
            except Exception:
                pass
            if not self.stop_event.is_set():
                time.sleep(2)

    def _ensure_gpsd(self):
        """Start gpsd if not running."""
        try:
            result = subprocess.run(['pgrep', '-x', 'gpsd'],
                                    capture_output=True, timeout=3)
            if result.returncode != 0:
                self.restart_gpsd()
        except Exception:
            pass

    def restart_gpsd(self, device=None, baud=None):
        """Restart gpsd with given device. Does not modify system config."""
        if device:
            self.device = device
        if baud and baud != 'auto':
            self.baud = baud
        try:
            subprocess.run(['killall', 'gpsd'], capture_output=True, timeout=3)
            time.sleep(0.5)
            subprocess.Popen(
                ['gpsd', '-n', '-b', self.device],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception:
            pass
        time.sleep(1)

    def _read_gpspipe(self):
        """Read JSON from gpspipe -w."""
        self._process = subprocess.Popen(
            ['gpspipe', '-w'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True
        )
        try:
            for line in self._process.stdout:
                if self.stop_event.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                cls = msg.get('class', '')

                if cls == 'TPV':
                    updates = {}
                    if 'lat' in msg:
                        updates['lat'] = msg['lat']
                    if 'lon' in msg:
                        updates['lon'] = msg['lon']
                    if 'alt' in msg or 'altHAE' in msg:
                        updates['alt'] = msg.get('altHAE', msg.get('alt', 0.0))
                    if 'speed' in msg:
                        updates['speed'] = msg['speed']
                    if 'mode' in msg:
                        updates['fix_mode'] = msg['mode']
                    if 'time' in msg:
                        updates['timestamp'] = msg['time']
                    if updates:
                        self.gps_state.update(**updates)

                elif cls == 'SKY':
                    sats = 0
                    for sat in msg.get('satellites', []):
                        if sat.get('used', False):
                            sats += 1
                    self.gps_state.update(satellites=sats)
        finally:
            if self._process:
                self._process.terminate()
                self._process = None

    def stop(self):
        """Stop the GPS reader."""
        if self._process:
            self._process.terminate()
