"""Background services manager — wardrive scanner, GPS, capture.
All services run in background threads and expose data via variables.
Main thread is NEVER blocked by service operations."""

import os
import re
import json
import struct
import time
import queue
import sqlite3
import threading
import subprocess
from datetime import datetime


# ---------------------------------------------------------------------------
# GPS Service
# ---------------------------------------------------------------------------
class GpsService(threading.Thread):
    """Reads GPS from gpsd, updates shared state."""

    def __init__(self, stop_event):
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self.lat = 0.0
        self.lon = 0.0
        self.alt = 0.0
        self.speed = 0.0
        self.satellites = 0
        self.fix_mode = 0
        self._lock = threading.Lock()
        self._process = None
        self.device = ''
        self.enabled = False

    def configure(self, device, enabled=True):
        self.device = device
        self.enabled = enabled

    def get_state(self):
        with self._lock:
            return {
                'lat': self.lat, 'lon': self.lon, 'alt': self.alt,
                'speed': self.speed, 'sats': self.satellites,
                'fix': self.fix_mode, 'speed_mph': self.speed * 2.237,
            }

    def run(self):
        while not self.stop_event.is_set():
            if not self.enabled or not self.device:
                self.stop_event.wait(2)
                continue
            try:
                self._ensure_gpsd()
                self._read_gpspipe()
            except Exception:
                pass
            if not self.stop_event.is_set():
                time.sleep(2)

    def _ensure_gpsd(self):
        try:
            r = subprocess.run(['pgrep', '-x', 'gpsd'], capture_output=True, timeout=2)
            if r.returncode != 0 and self.device:
                subprocess.Popen(['gpsd', '-n', '-b', self.device],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1)
        except Exception:
            pass

    def _read_gpspipe(self):
        self._process = subprocess.Popen(
            ['gpspipe', '-w'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            for line in self._process.stdout:
                if self.stop_event.is_set():
                    break
                try:
                    msg = json.loads(line.strip())
                except Exception:
                    continue
                cls = msg.get('class', '')
                if cls == 'TPV':
                    with self._lock:
                        if 'lat' in msg: self.lat = msg['lat']
                        if 'lon' in msg: self.lon = msg['lon']
                        if 'alt' in msg or 'altHAE' in msg:
                            self.alt = msg.get('altHAE', msg.get('alt', 0))
                        if 'speed' in msg: self.speed = msg['speed']
                        if 'mode' in msg: self.fix_mode = msg['mode']
                elif cls == 'SKY':
                    sats = sum(1 for s in msg.get('satellites', []) if s.get('used'))
                    with self._lock:
                        self.satellites = sats
        finally:
            if self._process:
                self._process.terminate()
                self._process = None

    def stop(self):
        if self._process:
            self._process.terminate()


# ---------------------------------------------------------------------------
# WiFi Scanner Service
# ---------------------------------------------------------------------------
class ScannerService(threading.Thread):
    """Background WiFi scanner — passive beacon capture with raw frame parsing."""

    def __init__(self, stop_event):
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self.interface = 'wlan1mon'
        self.enabled = False
        self.paused = False
        self.current_channel = 0
        self.channels = list(range(1, 12)) + [36, 40, 44, 48, 149, 153, 157, 161, 165]
        self._tcpdump = None
        self._lock = threading.Lock()
        # In-memory AP tracking — no database
        self._aps = {}  # bssid -> ap dict
        self._stats = {'total': 0, 'open': 0, 'wep': 0, 'wpa': 0, 'wpa3': 0, 'hs': 0}
        self._new_last = 0

    def configure(self, interface='wlan1mon', enabled=True, channels=None):
        self.interface = interface
        self.enabled = enabled
        if channels:
            self.channels = channels

    def get_state(self):
        """Returns cached stats — never blocks."""
        with self._lock:
            return {
                'total': self._stats['total'],
                'new_last': self._new_last,
                'channel': self.current_channel,
                'stats': dict(self._stats),
            }

    def run(self):
        # Start channel hopper
        hopper = threading.Thread(target=self._hop_channels, daemon=True)
        hopper.start()

        while not self.stop_event.is_set():
            if not self.enabled or self.paused:
                self.stop_event.wait(1)
                continue
            try:
                self._capture_beacons()
            except Exception:
                pass
            if not self.stop_event.is_set():
                time.sleep(1)

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute('''CREATE TABLE IF NOT EXISTS access_points (
            bssid TEXT PRIMARY KEY, ssid TEXT, channel INTEGER,
            frequency INTEGER DEFAULT 0, encryption TEXT, auth_mode TEXT DEFAULT '',
            signal INTEGER, lat REAL, lon REAL, alt REAL,
            first_seen TEXT, last_seen TEXT, handshake INTEGER DEFAULT 0)''')
        conn.commit()
        conn.close()

    def _hop_channels(self):
        idx = 0
        while not self.stop_event.is_set():
            if self.enabled and not self.paused and self.channels:
                ch = self.channels[idx % len(self.channels)]
                try:
                    # Use nice to lower priority
                    subprocess.run(['nice', '-n', '10', 'iw', 'dev', self.interface,
                                    'set', 'channel', str(ch)],
                                   capture_output=True, timeout=2)
                    with self._lock:
                        self.current_channel = ch
                except Exception:
                    pass
                idx += 1
            self.stop_event.wait(0.5)

    def _capture_beacons(self):
        self._tcpdump = subprocess.Popen(
            ['nice', '-n', '10', 'tcpdump', '-i', self.interface, '-w', '-', '-U', '--immediate-mode'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            header = self._tcpdump.stdout.read(24)
            if len(header) < 24:
                return
            batch = []
            batch_time = time.time()

            while not self.stop_event.is_set() and not self.paused:
                pkt_header = self._tcpdump.stdout.read(16)
                if len(pkt_header) < 16:
                    break
                _, _, incl_len, _ = struct.unpack('<IIII', pkt_header)
                pkt_data = self._tcpdump.stdout.read(incl_len)
                if len(pkt_data) < incl_len:
                    break

                ap = self._parse_packet(pkt_data)
                if ap:
                    batch.append(ap)

                now = time.time()
                if now - batch_time >= 2 and batch:
                    self._store_batch(batch)
                    batch = []
                    batch_time = now
        finally:
            if self._tcpdump:
                self._tcpdump.terminate()
                self._tcpdump = None

    def _parse_packet(self, pkt):
        """Minimal beacon parser — extract BSSID, SSID, channel, signal, encryption."""
        if len(pkt) < 8:
            return None
        rt_len = struct.unpack_from('<H', pkt, 2)[0]
        if rt_len > len(pkt) or len(pkt) < rt_len + 24:
            return None

        # Radiotap signal + frequency
        signal, freq = -80, 0
        present = struct.unpack_from('<I', pkt, 4)[0]
        offset = 8
        p = present
        while p & (1 << 31):
            if offset + 4 > rt_len: break
            p = struct.unpack_from('<I', pkt, offset)[0]
            offset += 4
        if present & (1 << 0):
            offset = (offset + 7) & ~7; offset += 8
        if present & (1 << 1): offset += 1
        if present & (1 << 2): offset += 1
        if present & (1 << 3):
            offset = (offset + 1) & ~1
            if offset + 4 <= rt_len: freq = struct.unpack_from('<H', pkt, offset)[0]
            offset += 4
        if present & (1 << 4): offset += 2
        if present & (1 << 5):
            if offset < rt_len: signal = struct.unpack_from('b', pkt, offset)[0]

        # 802.11 frame
        frame = pkt[rt_len:]
        if len(frame) < 36:
            return None
        fc = struct.unpack_from('<H', frame, 0)[0]
        if (fc >> 2) & 0x03 != 0:  # Not management
            return None
        subtype = (fc >> 4) & 0x0f
        if subtype not in (8, 5):  # Not beacon or probe response
            return None

        bssid = ':'.join(f'{b:02X}' for b in frame[16:22])
        capability = struct.unpack_from('<H', frame, 34)[0]
        has_privacy = bool(capability & 0x0010)

        # Parse IEs
        ssid, channel, encryption, auth_mode = '', 0, 'Open', '[ESS]'
        ie_offset = 36
        has_rsn, has_wpa, has_sae = False, False, False

        while ie_offset + 2 <= len(frame):
            tag = frame[ie_offset]
            length = frame[ie_offset + 1]
            ie_offset += 2
            if ie_offset + length > len(frame):
                break
            ie_data = frame[ie_offset:ie_offset + length]

            if tag == 0:  # SSID
                try: ssid = ie_data.decode('utf-8', errors='replace')
                except: pass
            elif tag == 3 and length >= 1:  # DS Parameter
                channel = ie_data[0]
            elif tag == 48 and length >= 2:  # RSN
                has_rsn = True
                if length >= 8:
                    # Check AKM for SAE
                    try:
                        off = 2 + 4  # version + group cipher
                        if off + 2 <= length:
                            pc = struct.unpack_from('<H', ie_data, off)[0]
                            off += 2 + pc * 4
                            if off + 2 <= length:
                                ac = struct.unpack_from('<H', ie_data, off)[0]
                                off += 2
                                for _ in range(ac):
                                    if off + 4 <= length:
                                        akm = ie_data[off + 3]
                                        if akm in (8, 9): has_sae = True
                                        off += 4
                    except: pass
            elif tag == 221 and length >= 4:  # Vendor
                if ie_data[:4] == b'\x00\x50\xf2\x01':
                    has_wpa = True
            ie_offset += length

        if has_sae:
            encryption, auth_mode = 'WPA3', '[WPA3-SAE]'
        elif has_rsn:
            encryption, auth_mode = 'WPA2', '[WPA2-PSK-CCMP128]'
        elif has_wpa:
            encryption, auth_mode = 'WPA', '[WPA1-PSK]'
        elif has_privacy:
            encryption, auth_mode = 'WEP', '[WEP]'
        elif not ssid:
            encryption = 'Unknown'

        return {
            'bssid': bssid, 'ssid': ssid, 'channel': channel,
            'frequency': freq, 'signal': signal,
            'encryption': encryption, 'auth_mode': auth_mode,
        }

    def _store_batch(self, aps):
        """Store APs in memory — no database, instant."""
        new_count = 0
        for ap in aps:
            bssid = ap['bssid']
            if bssid not in self._aps:
                self._aps[bssid] = ap
                new_count += 1
            else:
                # Update if better signal
                if ap['signal'] > self._aps[bssid].get('signal', -100):
                    self._aps[bssid].update(ap)

        # Recalculate stats
        stats = {'total': 0, 'open': 0, 'wep': 0, 'wpa': 0, 'wpa3': 0, 'hs': 0}
        for a in self._aps.values():
            stats['total'] += 1
            enc = a.get('encryption', '')
            if enc == 'Open': stats['open'] += 1
            elif enc == 'WEP': stats['wep'] += 1
            elif enc in ('WPA', 'WPA2'): stats['wpa'] += 1
            elif enc == 'WPA3': stats['wpa3'] += 1

        with self._lock:
            self._stats = stats
            self._new_last = new_count

    def stop(self):
        if self._tcpdump:
            self._tcpdump.terminate()

    def get_db_stats(self):
        """Get full stats from DB."""
        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute('''SELECT COUNT(*),
                SUM(CASE WHEN encryption='Open' THEN 1 ELSE 0 END),
                SUM(CASE WHEN encryption='WEP' THEN 1 ELSE 0 END),
                SUM(CASE WHEN encryption IN ('WPA','WPA2') THEN 1 ELSE 0 END),
                SUM(CASE WHEN encryption='WPA3' THEN 1 ELSE 0 END),
                SUM(handshake) FROM access_points''').fetchone()
            conn.close()
            return {
                'total': row[0] or 0, 'open': row[1] or 0, 'wep': row[2] or 0,
                'wpa': row[3] or 0, 'wpa3': row[4] or 0, 'hs': row[5] or 0,
            }
        except Exception:
            return {'total': 0, 'open': 0, 'wep': 0, 'wpa': 0, 'wpa3': 0, 'hs': 0}


# ---------------------------------------------------------------------------
# Service Manager
# ---------------------------------------------------------------------------
class ServiceManager:
    """Manages all background services and exposes variables."""

    def __init__(self, config=None):
        self.stop_event = threading.Event()
        self.config = config or {}

        self.gps = GpsService(self.stop_event)
        self.scanner = ScannerService(self.stop_event)

        # Configure from config
        self._apply_config()

    def _apply_config(self):
        c = self.config
        # GPS
        self.gps.configure(
            device=c.get('gps_device', ''),
            enabled=c.get('gps_enabled', False)
        )
        # Scanner
        self.scanner.configure(
            interface=c.get('capture_interface', 'wlan1mon'),
            enabled=c.get('scan_enabled', False)
        )

    def start(self):
        """Start all enabled services."""
        self.gps.start()
        self.scanner.start()

    def stop(self):
        """Stop all services."""
        self.stop_event.set()
        self.gps.stop()
        self.scanner.stop()

    def get_variables(self):
        """Get all service variables for the variable resolver.
        NEVER blocks — only reads cached values."""
        gps = self.gps.get_state()
        scan = self.scanner.get_state()
        stats = scan.get('stats', {'total': 0, 'open': 0, 'wep': 0, 'wpa': 0, 'wpa3': 0, 'hs': 0})

        return {
            # GPS
            'gps_lat': f"{gps['lat']:.4f}" if gps['lat'] else '0.0',
            'gps_lon': f"{gps['lon']:.4f}" if gps['lon'] else '0.0',
            'gps_alt': f"{gps['alt']:.0f}",
            'gps_speed': f"{gps['speed_mph']:.0f}",
            'gps_sats': str(gps['sats']),
            'gps_fix': ['None', 'None', '2D', '3D'][min(gps['fix'], 3)],

            # Scanner
            'aps_total': str(stats['total']),
            'aps_open': str(stats['open']),
            'aps_wep': str(stats['wep']),
            'aps_wpa': str(stats['wpa']),
            'aps_wpa3': str(stats['wpa3']),
            'handshakes': str(stats['hs']),
            'scan_channel': str(scan['channel']),
            'scan_new': str(scan['new_last']),
        }
