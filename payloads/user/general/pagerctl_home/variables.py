"""Variable resolver — resolves ${varname} in text strings."""

import os
import time
import re
import subprocess
import threading


class VariableResolver:
    """Resolves ${varname} placeholders with live system data."""

    def __init__(self):
        self._cache = {}
        self._cache_time = {}
        self._cache_ttl = 1.0  # Refresh interval in seconds
        self._user_vars = {}
        self._lock = threading.Lock()

    def set_user_variables(self, variables):
        """Set user-defined variables from theme config."""
        self._user_vars = dict(variables)

    def resolve(self, text):
        """Replace all ${varname} in text with current values."""
        if '${' not in text:
            return text
        return re.sub(r'\$\{(\w+)\}', lambda m: str(self._get(m.group(1))), text)

    def _get(self, name):
        """Get a variable value, using cache if fresh enough."""
        # User variables first (no caching needed)
        if name in self._user_vars:
            return self._user_vars[name]

        # Check cache
        now = time.time()
        with self._lock:
            if name in self._cache and now - self._cache_time.get(name, 0) < self._cache_ttl:
                return self._cache[name]

        # Resolve system variable
        value = self._resolve_system(name)
        with self._lock:
            self._cache[name] = value
            self._cache_time[name] = now
        return value

    def _resolve_system(self, name):
        """Resolve a system variable."""
        resolvers = {
            # Time
            'time': lambda: time.strftime('%H:%M:%S'),
            'time_12': lambda: time.strftime('%I:%M %p'),
            'hour': lambda: time.strftime('%H'),
            'minute': lambda: time.strftime('%M'),
            'second': lambda: time.strftime('%S'),
            'date': lambda: time.strftime('%Y-%m-%d'),
            'date_short': lambda: time.strftime('%m/%d'),
            'day_name': lambda: time.strftime('%A'),
            'month_name': lambda: time.strftime('%B'),
            'year': lambda: time.strftime('%Y'),

            # Battery
            'battery_percent': self._get_battery,
            'battery_status': self._get_battery_status,

            # System
            'cpu_usage': self._get_cpu,
            'mem_used': lambda: self._get_mem('used'),
            'mem_total': lambda: self._get_mem('total'),
            'mem_percent': lambda: self._get_mem('percent'),
            'uptime': self._get_uptime,
            'hostname': lambda: self._read_file('/proc/sys/kernel/hostname', 'pager'),
            'temp': self._get_temp,

            # Network
            'ip_addr': self._get_ip,
            'ssid': self._get_ssid,

            # Storage
            'disk_used': lambda: self._get_disk('used'),
            'disk_free': lambda: self._get_disk('free'),
            'disk_percent': lambda: self._get_disk('percent'),

            # GPS (placeholder — can be connected to GPS reader)
            'gps_lat': lambda: '0.0',
            'gps_lon': lambda: '0.0',
            'gps_speed': lambda: '0',
            'gps_sats': lambda: '0',
            'gps_fix': lambda: 'None',
        }

        resolver = resolvers.get(name)
        if resolver:
            try:
                return resolver()
            except Exception:
                return '?'
        return f'${{{name}}}'

    def _read_file(self, path, default=''):
        try:
            with open(path) as f:
                return f.read().strip()
        except Exception:
            return default

    def _get_battery(self):
        import glob
        try:
            for p in glob.glob('/sys/class/power_supply/*/capacity'):
                return self._read_file(p, '?')
        except Exception:
            pass
        try:
            r = subprocess.run(['ubus', 'call', 'battery', 'info'],
                               capture_output=True, text=True, timeout=2)
            if r.returncode == 0:
                import json
                d = json.loads(r.stdout)
                return str(d.get('percent', d.get('capacity', '?')))
        except Exception:
            pass
        return '?'

    def _get_battery_status(self):
        import glob
        try:
            for p in glob.glob('/sys/class/power_supply/*/status'):
                return self._read_file(p, '?')
        except Exception:
            return '?'

    def _get_cpu(self):
        try:
            with open('/proc/loadavg') as f:
                return f.read().split()[0]
        except Exception:
            return '?'

    def _get_mem(self, field):
        try:
            info = {}
            with open('/proc/meminfo') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        info[parts[0].rstrip(':')] = int(parts[1])
            total = info.get('MemTotal', 0) // 1024
            free = info.get('MemAvailable', info.get('MemFree', 0)) // 1024
            used = total - free
            if field == 'total':
                return f'{total}M'
            elif field == 'used':
                return f'{used}M'
            elif field == 'percent':
                return f'{(used * 100 // total) if total else 0}'
        except Exception:
            return '?'

    def _get_uptime(self):
        try:
            with open('/proc/uptime') as f:
                secs = int(float(f.read().split()[0]))
            h = secs // 3600
            m = (secs % 3600) // 60
            return f'{h}h {m}m'
        except Exception:
            return '?'

    def _get_temp(self):
        try:
            for path in ['/sys/class/thermal/thermal_zone0/temp',
                         '/sys/devices/platform/temperature/temp']:
                if os.path.exists(path):
                    val = int(self._read_file(path, '0'))
                    if val > 1000:
                        val = val // 1000
                    return f'{val}C'
        except Exception:
            pass
        return '?'

    def _get_ip(self):
        try:
            r = subprocess.run(['ip', '-4', 'addr'], capture_output=True, text=True, timeout=2)
            for line in r.stdout.split('\n'):
                if 'inet ' in line and '127.0.0.1' not in line:
                    return line.strip().split()[1].split('/')[0]
        except Exception:
            pass
        return '?'

    def _get_ssid(self):
        try:
            r = subprocess.run(['iw', 'dev', 'wlan0cli', 'info'],
                               capture_output=True, text=True, timeout=2)
            for line in r.stdout.split('\n'):
                if 'ssid' in line.lower():
                    return line.strip().split(None, 1)[-1]
        except Exception:
            pass
        return '?'

    def _get_disk(self, field):
        try:
            r = subprocess.run(['df', '/mmc'], capture_output=True, text=True, timeout=2)
            lines = r.stdout.strip().split('\n')
            if len(lines) >= 2:
                parts = lines[1].split()
                total = int(parts[1]) // 1024
                used = int(parts[2]) // 1024
                free = int(parts[3]) // 1024
                pct = parts[4].rstrip('%')
                if field == 'used':
                    return f'{used}M'
                elif field == 'free':
                    return f'{free}M'
                elif field == 'percent':
                    return pct
        except Exception:
            pass
        return '?'
