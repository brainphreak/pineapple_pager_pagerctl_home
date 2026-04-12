"""web_server.py - Pagerctl Home web UI.

Generic pager management server — lives here under wardrive/ for legacy
import compatibility but is not wardrive-specific. Serves the modular
SPA in pagerctl_home/web/ and exposes APIs for live framebuffer mirror,
button injection, shell exec, and system info.

Adapted from loki's webapp.py pattern: plain HTTPServer + custom handler,
gzipped static file serving, client-side rendering (no Flask/WS).
"""

import os
import io
import gzip
import json
import queue
import struct
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from wardrive.config import LOOT_DIR, EXPORT_DIR, CAPTURE_DIR, DB_PATH, load_config, save_config


# Resolved at start_web_ui time
WEB_DIR = None

# Shared virtual-button queue. The web Control panel writes to it, and
# every UI loop (main home, wardrive, sysinfo, settings) drains it each
# frame so a press works regardless of which screen the user is on.
virt_buttons = queue.Queue()


def set_button_queue(q):
    """Legacy shim — no-op. The queue now lives here as a module-level
    singleton so subscreens can import it directly."""
    pass


def drain_virt_button():
    """Pop the next queued virtual button name, or None if empty."""
    try:
        return virt_buttons.get_nowait()
    except queue.Empty:
        return None


def wait_any_button(pager, poll_ms=30):
    """Block until a physical or virtual button press. Returns a button
    mask identical to what pager.wait_button() would return, so callers
    can keep using the existing `if btn & BTN_UP` pattern unchanged.

    Uses the event queue (has_input_events / get_input_event) rather
    than poll_input so there's exactly one input channel app-wide.
    Prevents the channel-split bug where a press consumed here would
    also sit in the other channel and get re-dispatched on return.
    """
    import time as _t
    try:
        from pagerctl import PAGER_EVENT_PRESS
    except Exception:
        PAGER_EVENT_PRESS = 1
    virt_map = {
        'up':    pager.BTN_UP,
        'down':  pager.BTN_DOWN,
        'left':  pager.BTN_LEFT,
        'right': pager.BTN_RIGHT,
        'a':     pager.BTN_A,
        'b':     pager.BTN_B,
        'power': pager.BTN_POWER,
    }
    while True:
        while pager.has_input_events():
            event = pager.get_input_event()
            if not event:
                break
            button, event_type, _ = event
            if event_type != PAGER_EVENT_PRESS:
                continue
            return button
        name = drain_virt_button()
        if name:
            return virt_map.get(name, 0)
        _t.sleep(poll_ms / 1000.0)


_MIME = {
    '.html': 'text/html; charset=utf-8',
    '.css':  'text/css; charset=utf-8',
    '.js':   'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png':  'image/png',
    '.ico':  'image/x-icon',
    '.svg':  'image/svg+xml',
    '.ttf':  'font/ttf',
    '.woff': 'font/woff',
    '.woff2':'font/woff2',
}

_BTN_NAMES = {'up', 'down', 'left', 'right', 'a', 'b', 'power'}


class PagerHandler(BaseHTTPRequestHandler):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def log_message(self, format, *args):
        # Silence default HTTP log spam
        pass

    def _send_bytes(self, status, ctype, body, headers=None, gzip_ok=False):
        if gzip_ok and isinstance(body, (bytes, bytearray)) and self._accepts_gzip():
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=6) as gz:
                gz.write(body)
            body = buf.getvalue()
            extra_gzip = True
        else:
            extra_gzip = False
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        if extra_gzip:
            self.send_header('Content-Encoding', 'gzip')
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode('utf-8')
        self._send_bytes(status, 'application/json', body,
                         headers={'Access-Control-Allow-Origin': '*'},
                         gzip_ok=True)

    def _accepts_gzip(self):
        return 'gzip' in self.headers.get('Accept-Encoding', '')

    # ------------------------------------------------------------------
    # GET router
    # ------------------------------------------------------------------

    def do_GET(self):
        path = self.path.split('?', 1)[0]

        if path in ('/', '/index.html'):
            return self._serve_static('index.html')
        if path.startswith('/web/'):
            return self._serve_static(path[5:])  # strip leading /web/
        if path.startswith('/screen.png'):
            return self._serve_framebuffer()
        if path == '/api/sysinfo':
            return self._send_json(self._gather_sysinfo())
        if path == '/api/stats':
            return self._send_json(self._wardrive_stats())
        if path == '/api/files':
            return self._send_json(self._list_loot_files())
        if path.startswith('/download/'):
            return self._serve_download(path[10:])

        self.send_error(404)

    # ------------------------------------------------------------------
    # POST router
    # ------------------------------------------------------------------

    def do_POST(self):
        path = self.path.split('?', 1)[0]

        if path.startswith('/api/button/'):
            name = path[12:]
            return self._post_button(name)
        if path == '/api/terminal':
            return self._post_terminal()
        if path == '/api/settings':
            return self._post_settings()

        self.send_error(404)

    # ------------------------------------------------------------------
    # Static file serving
    # ------------------------------------------------------------------

    def _serve_static(self, rel):
        if '..' in rel or rel.startswith('/'):
            self.send_error(403)
            return
        full = os.path.join(WEB_DIR, rel)
        if not os.path.isfile(full):
            self.send_error(404)
            return
        ext = os.path.splitext(full)[1].lower()
        ctype = _MIME.get(ext, 'application/octet-stream')
        try:
            with open(full, 'rb') as f:
                body = f.read()
        except Exception:
            self.send_error(500)
            return
        gzip_ok = ctype.startswith('text/') or 'javascript' in ctype or 'json' in ctype or 'svg' in ctype
        self._send_bytes(200, ctype, body, gzip_ok=gzip_ok,
                         headers={'Cache-Control': 'public, max-age=60'})

    # ------------------------------------------------------------------
    # Live framebuffer (raw RGB565 + header) — copied from loki
    # ------------------------------------------------------------------

    def _serve_framebuffer(self):
        fb_path = '/dev/fb0'
        fb_width = 222
        fb_height = 480
        fb_size = fb_width * fb_height * 2  # RGB565
        rotation = 270  # pager is always landscape
        try:
            with open(fb_path, 'rb') as fb:
                raw = fb.read(fb_size)
            header = struct.pack('<HHH', fb_width, fb_height, rotation)
            body = header + raw
            self._send_bytes(200, 'application/octet-stream', body,
                             headers={'Cache-Control': 'no-cache, no-store'})
        except Exception:
            self.send_error(500)

    # ------------------------------------------------------------------
    # Button injection
    # ------------------------------------------------------------------

    def _post_button(self, name):
        if name not in _BTN_NAMES:
            return self._send_json({'error': 'unknown button'}, 400)
        try:
            virt_buttons.put_nowait(name)
        except Exception as e:
            return self._send_json({'error': str(e)}, 500)
        return self._send_json({'status': 'ok', 'button': name})

    # ------------------------------------------------------------------
    # Terminal exec
    # ------------------------------------------------------------------

    def _post_terminal(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            params = json.loads(body or '{}')
            cmd = (params.get('command') or '').strip()
        except Exception as e:
            return self._send_json({'error': str(e)}, 400)

        if not cmd:
            return self._send_json({'error': 'empty command'}, 400)

        # Minimal safety blocklist
        blocked = ['rm -rf /', 'mkfs', 'dd if=/dev/zero', '> /dev/sda',
                   'chmod -R 777 /', ':(){ :|:&};:']
        low = cmd.lower()
        for pat in blocked:
            if pat in low:
                return self._send_json({
                    'command': cmd,
                    'output': 'Command blocked for safety.',
                    'exit_code': -1,
                }, 403)

        try:
            r = subprocess.run(cmd, shell=True, capture_output=True,
                               text=True, timeout=30, cwd='/root')
            out = (r.stdout or '') + (r.stderr or '')
            return self._send_json({
                'command': cmd,
                'output': out,
                'exit_code': r.returncode,
            })
        except subprocess.TimeoutExpired:
            return self._send_json({
                'command': cmd,
                'output': 'Command timed out (30s).',
                'exit_code': -1,
            })
        except Exception as e:
            return self._send_json({'error': str(e)}, 500)

    # ------------------------------------------------------------------
    # System info (Dashboard tab)
    # ------------------------------------------------------------------

    def _gather_sysinfo(self):
        """Reuses sysinfo_ui's logic without needing the UI instance."""
        import glob
        s = {}

        try:
            with open('/proc/meminfo') as f:
                info = {}
                for line in f:
                    p = line.split()
                    if len(p) >= 2:
                        info[p[0].rstrip(':')] = int(p[1])
            total = info.get('MemTotal', 0) // 1024
            free = info.get('MemAvailable', info.get('MemFree', 0)) // 1024
            used = total - free
            s['mem'] = f"{used}/{total}M"
        except Exception:
            s['mem'] = '?'

        try:
            with open('/proc/loadavg') as f:
                s['cpu'] = f.read().split()[0]
        except Exception:
            s['cpu'] = '?'

        # Temp
        val = None
        for path in glob.glob('/sys/class/ieee80211/phy*/hwmon*/temp1_input') + \
                    glob.glob('/sys/class/hwmon/hwmon*/temp1_input'):
            try:
                with open(path) as f:
                    raw = int(f.read().strip())
                val = raw // 1000 if raw > 1000 else raw
                break
            except Exception:
                continue
        s['temp'] = f"{val}C" if val is not None else '?'

        try:
            r = subprocess.run(['df', '-h', '/mmc'], capture_output=True, text=True, timeout=2)
            parts = r.stdout.strip().split('\n')[1].split()
            s['disk'] = f"{parts[2]} {parts[4]}"
        except Exception:
            s['disk'] = '?'

        try:
            with open('/proc/uptime') as f:
                secs = int(float(f.read().split()[0]))
            s['uptime'] = f"{secs // 3600}h {(secs % 3600) // 60}m"
        except Exception:
            s['uptime'] = '?'

        try:
            s['procs'] = str(sum(1 for n in os.listdir('/proc') if n.isdigit()))
        except Exception:
            s['procs'] = '?'

        try:
            for p in glob.glob('/sys/class/power_supply/*/capacity'):
                with open(p) as f:
                    s['battery'] = f.read().strip() + '%'
                break
            else:
                s['battery'] = '?'
        except Exception:
            s['battery'] = '?'

        try:
            with open('/proc/sys/kernel/hostname') as f:
                s['hostname'] = f.read().strip()
        except Exception:
            s['hostname'] = '?'

        try:
            with open('/proc/version') as f:
                s['kernel'] = f.read().split()[2]
        except Exception:
            s['kernel'] = '?'

        # Interfaces
        ifaces = []
        try:
            r = subprocess.run(['ip', '-4', '-o', 'addr'], capture_output=True, text=True, timeout=2)
            for line in r.stdout.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 4:
                    name = parts[1]
                    ip = parts[3].split('/')[0]
                    if ip != '127.0.0.1':
                        ifaces.append([name, ip])
        except Exception:
            pass
        s['interfaces'] = ifaces

        # USB (filter internals)
        exclude_kw = ['host controller', 'hub', 'uart', 'spi', 'i2c',
                      'jtag', 'wireless_device', 'ehci', 'xhci', 'ohci',
                      'root hub']
        usb = []
        try:
            for prod in glob.glob('/sys/bus/usb/devices/*/product'):
                try:
                    with open(prod) as f:
                        name = f.read().strip()
                except Exception:
                    continue
                if not name:
                    continue
                low = name.lower()
                if any(kw in low for kw in exclude_kw):
                    continue
                usb.append(name)
        except Exception:
            pass
        s['usb'] = usb
        return s

    # ------------------------------------------------------------------
    # Wardrive stats (kept for later migration)
    # ------------------------------------------------------------------

    def _wardrive_stats(self):
        import sqlite3
        stats = {'total': 0, 'open': 0, 'wep': 0, 'wpa': 0, 'wpa3': 0, 'handshakes': 0}
        try:
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute('''SELECT COUNT(*),
                SUM(CASE WHEN encryption='Open' THEN 1 ELSE 0 END),
                SUM(CASE WHEN encryption='WEP' THEN 1 ELSE 0 END),
                SUM(CASE WHEN encryption IN ('WPA','WPA2') THEN 1 ELSE 0 END),
                SUM(CASE WHEN encryption='WPA3' THEN 1 ELSE 0 END),
                SUM(handshake)
                FROM access_points''').fetchone()
            stats = {
                'total': row[0] or 0, 'open': row[1] or 0, 'wep': row[2] or 0,
                'wpa': row[3] or 0, 'wpa3': row[4] or 0, 'handshakes': row[5] or 0,
            }
            recent = conn.execute(
                'SELECT bssid,ssid,encryption,auth_mode,signal,channel,frequency '
                'FROM access_points ORDER BY last_seen DESC LIMIT 20'
            ).fetchall()
            stats['recent_aps'] = [
                {'bssid': r[0], 'ssid': r[1], 'encryption': r[2], 'auth_mode': r[3],
                 'signal': r[4], 'channel': r[5], 'frequency': r[6]} for r in recent
            ]
            conn.close()
        except Exception:
            pass
        return stats

    def _list_loot_files(self):
        def _list(d, ext):
            out = []
            if not os.path.isdir(d):
                return out
            for name in sorted(os.listdir(d), reverse=True):
                if name.endswith(ext):
                    try:
                        size = os.path.getsize(os.path.join(d, name))
                    except Exception:
                        size = 0
                    out.append({'name': name, 'size': self._fmt_size(size)})
            return out
        return {
            'wigle': _list(EXPORT_DIR, '.csv'),
            'pcap': _list(CAPTURE_DIR, '.pcap'),
        }

    def _fmt_size(self, n):
        if n < 1024: return f'{n}B'
        if n < 1024 * 1024: return f'{n // 1024}KB'
        return f'{n // (1024*1024)}MB'

    def _serve_download(self, rel):
        if '..' in rel:
            self.send_error(403); return
        full = os.path.realpath(os.path.join(LOOT_DIR, rel))
        if not full.startswith(os.path.realpath(LOOT_DIR)) or not os.path.isfile(full):
            self.send_error(404); return
        try:
            size = os.path.getsize(full)
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition',
                             f'attachment; filename="{os.path.basename(full)}"')
            self.send_header('Content-Length', str(size))
            self.end_headers()
            with open(full, 'rb') as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk: break
                    try:
                        self.wfile.write(chunk)
                    except BrokenPipeError:
                        return
        except Exception:
            self.send_error(500)

    # ------------------------------------------------------------------
    # Settings POST (shared with settings_ui)
    # ------------------------------------------------------------------

    def _post_settings(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            data = json.loads(body or '{}')
        except Exception as e:
            return self._send_json({'error': str(e)}, 400)
        cfg = load_config()
        cfg.update(data)
        save_config(cfg)
        return self._send_json({'status': 'ok'})


# ----------------------------------------------------------------------
# Thread wrapper
# ----------------------------------------------------------------------

class WebServer(threading.Thread):
    def __init__(self, port=1337):
        super().__init__(daemon=True)
        self.port = port
        self.server = None

    def run(self):
        try:
            self.server = HTTPServer(('0.0.0.0', self.port), PagerHandler)
            self.server.serve_forever()
        except Exception:
            pass

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass


# Module-level singleton shared across the app
_singleton = None


def start_web_ui(port=1337):
    """Start the web UI if not already running. Safe to call repeatedly."""
    global _singleton, WEB_DIR
    if WEB_DIR is None:
        # Resolve web dir relative to this file — payloads/.../pagerctl_home/web/
        here = os.path.dirname(os.path.abspath(__file__))
        WEB_DIR = os.path.join(os.path.dirname(here), 'web')
    if _singleton is not None and _singleton.is_alive():
        return _singleton
    try:
        _singleton = WebServer(port=port)
        _singleton.start()
    except Exception:
        _singleton = None
    return _singleton


def stop_web_ui():
    global _singleton
    if _singleton is not None:
        try:
            _singleton.stop()
        except Exception:
            pass
        _singleton = None


def is_running():
    return _singleton is not None and _singleton.is_alive()
