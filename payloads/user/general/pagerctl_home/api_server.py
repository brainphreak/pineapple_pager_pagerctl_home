"""api_server.py - Unix socket HTTP API shim for DuckyScript commands.

Listens on /tmp/api.sock and handles requests from hak5cmd and shell-based
DuckyScript commands. Maps API endpoints to pagerctl hardware calls.

Runs as a daemon thread — started by pagerctl_home.py.
"""

import glob
import json
import os
import socketserver
import threading
from http.server import BaseHTTPRequestHandler


SOCKET_PATH = '/tmp/api.sock'
LOG_FILE = '/tmp/payload.log'

# Button mask → DuckyScript name
_BTN_NAMES = {
    0x01: 'UP', 0x02: 'DOWN', 0x04: 'LEFT', 0x08: 'RIGHT',
    0x10: 'A', 0x20: 'B', 0x40: 'POWER',
}

# LOG color names → (r, g, b)
_LOG_COLORS = {
    'red': (250, 72, 9), 'green': (42, 180, 42), 'blue': (106, 210, 249),
    'yellow': (231, 197, 74), 'cyan': (96, 205, 205), 'magenta': (205, 85, 155),
    'white': (255, 255, 255), 'gray': (128, 128, 128),
}

# Payload log screen — matches payload_log.json layout
_LOG_START_X = 6
_LOG_START_Y = 24
_LOG_LINE_H = 11      # small font (size 1) = 7px + 4px spacing
_LOG_MAX_LINES = 14
_LOG_MAX_CHARS = 50


class PayloadLog:
    """Manages the payload log display — themed scrollable text on screen."""

    def __init__(self):
        self.lines = []       # list of (text, color_name)
        self.bg_handle = None
        self.running_handle = None
        self.initialized = False

    def reset(self):
        self.lines = []
        self.initialized = False

    def add(self, msg, color_name='white'):
        self.lines.append((msg[:_LOG_MAX_CHARS], color_name))
        # Write to file too
        try:
            with open(LOG_FILE, 'a') as f:
                f.write(msg + '\n')
        except Exception:
            pass

    def render(self, pager, theme_dir):
        """Redraw the full payload log screen."""
        if not pager:
            return
        try:
            # Load background on first render
            if not self.initialized:
                bg_path = os.path.join(theme_dir, 'assets/payloadlog/payload_log_bg.png')
                if os.path.exists(bg_path):
                    self.bg_handle = pager.load_image(bg_path)
                run_path = os.path.join(theme_dir, 'assets/payloadlog/payload_running_indicator.png')
                if os.path.exists(run_path):
                    self.running_handle = pager.load_image(run_path)
                self.initialized = True

            # Draw background
            if self.bg_handle:
                pager.draw_image(0, 0, self.bg_handle)
            else:
                pager.clear(0)

            # Draw visible lines (last N lines, auto-scroll)
            visible = self.lines[-_LOG_MAX_LINES:]
            y = _LOG_START_Y
            for text, color_name in visible:
                rgb = _LOG_COLORS.get(color_name, (255, 255, 255))
                c = pager.rgb(rgb[0], rgb[1], rgb[2])
                pager.draw_text(_LOG_START_X, y, text, c, 1)
                y += _LOG_LINE_H

            # Running indicator
            if self.running_handle:
                pager.draw_image(0, 179, self.running_handle)

            pager.flip()
        except Exception:
            pass


# Shared log instance
_payload_log = PayloadLog()

# LED color shortcuts → (a_led, b_led) brightness
# a-button-led = green, b-button-led = red
_LED_COLORS = {
    'r': (0, 255), 'g': (255, 0), 'b': (0, 0),
    'y': (255, 255), 'c': (255, 0), 'm': (0, 255),
    'w': (255, 255), 'off': (0, 0),
}

# DPAD color names → (r, g, b)
_DPAD_COLORS = {
    'red': (255, 0, 0), 'green': (0, 255, 0), 'blue': (0, 0, 255),
    'cyan': (0, 255, 255), 'yellow': (255, 255, 0), 'magenta': (255, 0, 255),
    'white': (255, 255, 255), 'off': (0, 0, 0),
}


class ApiHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests from DuckyScript commands."""

    def do_GET(self):
        path = self._clean_path()
        response = self._route_get(path)
        self._respond(response)

    def do_POST(self):
        data = self._read_body()
        path = self._clean_path()
        response = self._route_post(path, data)
        self._respond(response)

    def do_PUT(self):
        self.do_POST()

    # -- Routing --

    def _route_get(self, path):
        if path == 'pager/power/get':
            return self._battery_info()
        if path == 'pineap/gps/get':
            return self._gps_info()
        return ({'error': 'not implemented'}, 404)

    def _route_post(self, path, data):
        # Pager hardware
        if path == 'pager/vibrate/play':
            return self._vibrate(data)
        if path == 'pager/beeper/play':
            return self._ringtone(data)
        if path == 'pager/display/dpad':
            return self._dpad_led(data)
        if path == 'system/led':
            return self._led(data)

        # Payload interaction
        if path == 'payload/interact/log':
            return self._log(data)
        if path == 'payload/interact/wait_for_input':
            return self._wait_input(data)
        if path == 'payload/interact/alert':
            _payload_log.reset()  # new payload, reset log screen
            return self._log_interact('ALERT', data)
        if path == 'payload/interact/error':
            return self._log_interact('ERROR', data)
        if path == 'payload/interact/confirmation':
            return {'confirmed': True}
        if path == 'payload/interact/list_picker':
            return self._wait_input(data)  # simplified: wait for button
        if path == 'payload/interact/string_picker':
            return {'text': data.get('default', '')}
        if path == 'payload/interact/ip_picker':
            return {'text': data.get('default', '0.0.0.0')}
        if path == 'payload/interact/mac_picker':
            return {'text': data.get('default', '00:00:00:00:00:00')}
        if path == 'payload/interact/number_picker':
            return {'text': data.get('default', '0')}
        if path == 'payload/interact/prompt':
            return self._wait_input(data)
        if path.startswith('payload/interact/spinner'):
            return {'id': '1', 'success': True}

        # Payload config
        if path.startswith('payload/config'):
            return {'success': True}

        # Payload config
        if path == 'payload/interact/prompt':
            return self._wait_input(data)

        return ({'error': 'not implemented'}, 404)

    # -- Pager hardware handlers --

    def _vibrate(self, data):
        pager = self.server.pager
        if not pager:
            return {'error': 'pager not available'}
        rtttl = data.get('ringtone', '')
        try:
            if rtttl:
                pager.play_rtttl(rtttl, pager.RTTTL_VIBRATE_ONLY)
            else:
                pager.vibrate(200)
        except Exception:
            try:
                pager.vibrate(200)
            except Exception:
                pass
        return {'success': True}

    def _ringtone(self, data):
        pager = self.server.pager
        if not pager:
            return {'error': 'pager not available'}
        rtttl = data.get('ringtone', '')
        vibrate = data.get('vibrate', False)
        if rtttl:
            mode = pager.RTTTL_SOUND_VIBRATE if vibrate else pager.RTTTL_SOUND_ONLY
            try:
                pager.play_rtttl(rtttl, mode)
            except Exception:
                pass
        return {'success': True}

    def _dpad_led(self, data):
        pager = self.server.pager
        if not pager:
            return {'error': 'pager not available'}
        color_name = data.get('led_color', 'off').lower()
        r, g, b = _DPAD_COLORS.get(color_name, (0, 0, 0))
        for direction in ('up', 'down', 'left', 'right'):
            try:
                pager.led_rgb(direction, r, g, b)
            except Exception:
                pass
        return {'success': True}

    def _led(self, data):
        pager = self.server.pager
        if not pager:
            return {'error': 'pager not available'}
        color = data.get('color', 'off').lower()

        # Handle raw_pattern (DO_A_BARREL_ROLL etc.) — just set color, skip pattern
        if 'raw_pattern' in data:
            # Complex LED animation — set all D-pad LEDs green briefly
            for d in ('up', 'down', 'left', 'right'):
                try:
                    pager.led_rgb(d, 0, 255, 0)
                except Exception:
                    pass
            return {'success': True}

        a_val, b_val = _LED_COLORS.get(color, (0, 0))
        try:
            pager.led_set('a-button-led', a_val)
            pager.led_set('b-button-led', b_val)
        except Exception:
            pass
        return {'success': True}

    def _battery_info(self):
        pct = 0
        charging = False
        try:
            for p in glob.glob('/sys/class/power_supply/*/capacity'):
                with open(p) as f:
                    val = f.read().strip()
                    pct = int(val) if val.isdigit() else 0
            for p in glob.glob('/sys/class/power_supply/*/status'):
                with open(p) as f:
                    status = f.read().strip().lower()
                    charging = status in ('charging', 'full')
        except Exception:
            pass
        return {'percent': pct, 'charging': charging}

    def _gps_info(self):
        return {'lat': 0.0, 'lon': 0.0, 'alt': 0.0, 'speed': 0.0}

    # -- Payload interaction handlers --

    def _log(self, data):
        msg = data.get('message', data.get('text', ''))
        color_name = data.get('color', 'green')
        _payload_log.add(msg, color_name)
        _payload_log.render(self.server.pager, self.server.theme_dir)
        return {'success': True}

    def _log_interact(self, kind, data):
        msg = data.get('message', data.get('text', data.get('title', '')))
        color = 'red' if kind == 'ERROR' else 'yellow'
        _payload_log.add(f'[{kind}] {msg}', color)
        _payload_log.render(self.server.pager, self.server.theme_dir)
        return {'success': True}

    def _wait_input(self, data):
        pager = self.server.pager
        if not pager:
            return {'button': 'A'}
        try:
            btn = pager.wait_button()
            for mask, name in _BTN_NAMES.items():
                if btn & mask:
                    return {'button': name}
        except Exception:
            pass
        return {'button': 'A'}

    # -- HTTP helpers --

    def _clean_path(self):
        path = self.path
        if path.startswith('/api/'):
            path = path[5:]
        elif path.startswith('/'):
            path = path[1:]
        return path

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length > 0 else b'{}'
        try:
            return json.loads(body) if body.strip() else {}
        except Exception:
            return {}

    def _respond(self, result):
        if isinstance(result, tuple):
            data, code = result
        else:
            data, code = result, 200
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Suppress HTTP access logs


class ThreadedUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Threaded HTTP server on a Unix domain socket."""
    daemon_threads = True
    allow_reuse_address = True


def start(pager, theme_dir=''):
    """Start the API server as a daemon thread. Returns the server instance."""
    # Clean up old socket
    try:
        os.unlink(SOCKET_PATH)
    except OSError:
        pass

    server = ThreadedUnixServer(SOCKET_PATH, ApiHandler)
    server.pager = pager
    server.theme_dir = theme_dir
    os.chmod(SOCKET_PATH, 0o777)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def stop(server):
    """Shut down the API server and remove the socket."""
    if server:
        server.shutdown()
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass
