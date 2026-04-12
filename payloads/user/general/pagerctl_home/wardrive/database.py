"""SQLite database for wardriving AP data."""

import os
import sqlite3
from datetime import datetime


class Database:
    def __init__(self, db_path):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS access_points (
                bssid TEXT PRIMARY KEY,
                ssid TEXT,
                channel INTEGER,
                frequency INTEGER DEFAULT 0,
                encryption TEXT,
                auth_mode TEXT DEFAULT '',
                signal INTEGER,
                lat REAL,
                lon REAL,
                alt REAL,
                first_seen TEXT,
                last_seen TEXT,
                handshake INTEGER DEFAULT 0
            )
        ''')
        self.conn.commit()

    def upsert_ap(self, ap, gps):
        """Insert or update an AP record with GPS data."""
        now = datetime.utcnow().isoformat()
        bssid = ap['bssid']

        existing = self.conn.execute(
            'SELECT signal, lat FROM access_points WHERE bssid = ?', (bssid,)
        ).fetchone()

        lat = gps.lat if gps and gps.fix_mode >= 2 else 0.0
        lon = gps.lon if gps and gps.fix_mode >= 2 else 0.0
        alt = gps.alt if gps and gps.fix_mode >= 3 else 0.0

        freq = ap.get('frequency', 0)
        auth_mode = ap.get('auth_mode', '')

        if existing:
            old_signal = existing[0] or -100
            if ap['signal'] > old_signal and lat != 0.0:
                self.conn.execute('''
                    UPDATE access_points
                    SET ssid=?, channel=?, frequency=?, encryption=?, auth_mode=?,
                        signal=?, lat=?, lon=?, alt=?, last_seen=?
                    WHERE bssid=?
                ''', (ap['ssid'], ap['channel'], freq, ap['encryption'],
                      auth_mode, ap['signal'], lat, lon, alt, now, bssid))
            else:
                self.conn.execute('''
                    UPDATE access_points
                    SET ssid=?, channel=?, frequency=?, encryption=?, auth_mode=?,
                        signal=MAX(signal, ?), last_seen=?
                    WHERE bssid=?
                ''', (ap['ssid'], ap['channel'], freq, ap['encryption'],
                      auth_mode, ap['signal'], now, bssid))
        else:
            self.conn.execute('''
                INSERT INTO access_points
                (bssid, ssid, channel, frequency, encryption, auth_mode,
                 signal, lat, lon, alt, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (bssid, ap['ssid'], ap['channel'], freq, ap['encryption'],
                  auth_mode, ap['signal'], lat, lon, alt, now, now))

        self.conn.commit()

    def mark_handshake(self, bssid):
        """Mark an AP as having a captured handshake."""
        self.conn.execute(
            'UPDATE access_points SET handshake=1 WHERE bssid=?', (bssid,))
        self.conn.commit()

    def correlate_open_bssids(self):
        """For hidden BSSIDs with no encryption, check if a sibling BSSID
        (same first 5 octets) has encryption and inherit it."""
        open_aps = self.conn.execute(
            "SELECT bssid FROM access_points WHERE encryption='Open' AND ssid=''"
        ).fetchall()
        for (bssid,) in open_aps:
            prefix = bssid[:14]  # First 5 octets "XX:XX:XX:XX:XX"
            sibling = self.conn.execute(
                "SELECT encryption, auth_mode FROM access_points WHERE bssid LIKE ? AND encryption != 'Open' LIMIT 1",
                (prefix + '%',)
            ).fetchone()
            if sibling:
                self.conn.execute(
                    "UPDATE access_points SET encryption=?, auth_mode=? WHERE bssid=?",
                    (sibling[0], sibling[1], bssid)
                )
        self.conn.commit()

    def get_stats(self):
        """Get aggregate stats for the dashboard."""
        row = self.conn.execute('''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN encryption='Open' THEN 1 ELSE 0 END),
                SUM(CASE WHEN encryption='WEP' THEN 1 ELSE 0 END),
                SUM(CASE WHEN encryption IN ('WPA', 'WPA2') THEN 1 ELSE 0 END),
                SUM(CASE WHEN encryption='WPA3' THEN 1 ELSE 0 END),
                SUM(handshake)
            FROM access_points
        ''').fetchone()
        return {
            'total': row[0] or 0,
            'open': row[1] or 0,
            'wep': row[2] or 0,
            'wpa2': row[3] or 0,
            'wpa3': row[4] or 0,
            'handshakes': row[5] or 0,
        }

    def get_new_count_since(self, timestamp):
        """Count APs first seen after timestamp."""
        row = self.conn.execute(
            'SELECT COUNT(*) FROM access_points WHERE first_seen > ?',
            (timestamp,)
        ).fetchone()
        return row[0] or 0

    def get_all_aps(self):
        """Get all APs for export."""
        cursor = self.conn.execute(
            'SELECT * FROM access_points ORDER BY first_seen')
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def close(self):
        self.conn.close()
