"""sysinfo_ui.py - System status dashboard for pagerctl_home.

Shows live system stats: CPU, memory, temp, network, disk, USB devices.
Takes over rendering while active, returns on B press.
"""

import os
import glob
import json
import time
import subprocess

from wardrive.web_server import drain_virt_button


FONT_MENU = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wardrive', 'fonts', 'menu.ttf')
FONT_TITLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wardrive', 'fonts', 'title.TTF')
SCREEN_W = 480
SCREEN_H = 222

# Layout/colors/positions live in the theme JSON. The path is resolved
# relative to the active theme dir (from the engine).
LAYOUT_REL = 'components/dashboards/sysinfo_dashboard.json'


class SysInfoUI:
    """System info dashboard. Theme-driven — all layout comes from
    `<theme>/components/dashboards/sysinfo_dashboard.json`."""

    def __init__(self):
        self.bg_handle = None
        self.pager = None
        self.engine = None
        self._cache = {}
        self._cache_time = 0
        self._last_cpu = None  # (total, idle) for delta CPU%
        self._layout = None
        self._layout_mtime = 0
        self._layout_path = None

    def run(self, pager, engine=None):
        self.pager = pager
        self.engine = engine

        # Load layout config (reload each entry so JSON edits take effect)
        self._load_layout()

        # Load background handle from theme-relative path
        bg_handle = None
        if self._layout and 'bg_image' in self._layout:
            bg_rel = self._layout['bg_image']
            theme_dir = engine.theme_dir if engine else os.path.join(
                os.path.dirname(__file__), 'themes', 'Circuitry')
            bg_path = os.path.join(theme_dir, bg_rel)
            if os.path.isfile(bg_path):
                bg_handle = pager.load_image(bg_path)
        self.bg_handle = bg_handle

        pager.clear_input_events()

        # Throttle redraws: stats only refresh every 2s and none of the
        # values change faster than that, so drawing more often just burns
        # CPU on the bg blit + TTF draws.
        next_render_at = 0.0

        while True:
            now = time.time()
            if now >= next_render_at:
                self._render()
                next_render_at = now + 2.0

            pressed = 0
            virt = None
            for _ in range(5):
                _, p, _ = pager.poll_input()
                pressed |= p
                virt = virt or drain_virt_button()
                if pressed or virt:
                    break
                time.sleep(0.04)

            # Virtual buttons from the web Control panel map to the same
            # exit semantics as physical B / Power.
            if virt == 'power':
                pager.clear_input_events()
                return 'power'
            if virt == 'b':
                pager.clear_input_events()
                return None

            if pressed:
                if pressed & pager.BTN_POWER:
                    self._drain_until_released(pager, pager.BTN_POWER)
                    return 'power'
                if pressed & pager.BTN_B:
                    self._drain_until_released(pager, pager.BTN_B)
                    return None

    def _drain_until_released(self, pager, btn_mask):
        """Wait for a button to be released and drain any parallel event
        queue entries so they don't leak into the caller's main loop.

        Fixes the "press B twice to go back" bug: poll_input's edge
        detection and the has_input_events queue are separate channels,
        and the main loop drains the queue on its next iteration. Without
        this, exiting a subscreen leaves a stale B-press in the queue
        that the home screen silently consumes.
        """
        deadline = time.time() + 0.5
        while time.time() < deadline:
            try:
                held, _, _ = pager.poll_input()
            except Exception:
                break
            if not (held & btn_mask):
                break
            time.sleep(0.01)
        pager.clear_input_events()

    def _load_layout(self):
        """Load (or reload) the sysinfo_dashboard.json theme file.

        Resolved relative to the active theme's dir. Hot-reloads on
        subsequent entries if the file changed on disk.
        """
        theme_dir = self.engine.theme_dir if self.engine else os.path.join(
            os.path.dirname(__file__), 'themes', 'Circuitry')
        path = os.path.join(theme_dir, LAYOUT_REL)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            self._layout = None
            return
        if self._layout is not None and path == self._layout_path and mtime == self._layout_mtime:
            return
        try:
            with open(path) as f:
                self._layout = json.load(f)
            self._layout_path = path
            self._layout_mtime = mtime
        except Exception:
            self._layout = None

    def _render(self):
        p = self.pager

        if self.bg_handle:
            p.draw_image(0, 0, self.bg_handle)
        else:
            p.clear(0)

        # Refresh stats every 2 seconds
        now = time.time()
        if now - self._cache_time > 2.0:
            self._cache = self._gather_stats()
            self._cache_time = now
        s = self._cache

        cfg = self._layout or {}
        fs = cfg.get('font_size', 20)
        row_h = cfg.get('row_height', 24)
        gap = cfg.get('label_gap', 4)
        colors = cfg.get('colors', {})
        c_label = p.rgb(*colors.get('label', [50, 80, 50]))
        c_value = p.rgb(*colors.get('value', [10, 30, 10]))
        c_warn = p.rgb(*colors.get('warn',  [120, 70, 0]))
        c_alert = p.rgb(*colors.get('alert', [140, 20, 20]))

        for col in cfg.get('columns', []):
            x = col.get('x', 40)
            y = col.get('y_start', 44)
            for row in col.get('rows', []):
                # Resolve label (static or from a stats key)
                label = row.get('label')
                if label is None:
                    lk = row.get('label_source')
                    if lk:
                        label = s.get(lk)
                # Resolve value
                src = row.get('source', '')
                val = s.get(src, '')
                # Skip empty rows (e.g. a 2nd USB slot when only one device)
                if (label is None or label == '') and (val is None or val == ''):
                    continue
                # Resolve value color from thresholds
                vc = c_value
                cs = row.get('color_source')
                if cs:
                    metric = s.get(cs, 0) or 0
                    try:
                        warn_t = row.get('warn', 60)
                        alert_t = row.get('alert', 85)
                        if metric > alert_t:
                            vc = c_alert
                        elif metric > warn_t:
                            vc = c_warn
                    except Exception:
                        pass
                # Truncate long values
                ml = row.get('max_length')
                if ml and isinstance(val, str) and len(val) > ml:
                    val = val[:ml]

                val_str = '' if val is None else str(val)
                if label:
                    label_text = f"{label}:"
                    p.draw_ttf(x, y, label_text, c_label, FONT_MENU, fs)
                    lw = p.ttf_width(label_text, FONT_MENU, fs)
                    p.draw_ttf(x + lw + gap, y, val_str, vc, FONT_MENU, fs)
                else:
                    p.draw_ttf(x, y, val_str, vc, FONT_MENU, fs)
                y += row_h

        # Status bar widgets (battery + time) from theme engine
        if self.engine:
            for w in self.engine.widgets:
                try:
                    w.render(p, self.engine.renderer)
                except Exception:
                    pass

        p.flip()

    def _gather_stats(self):
        s = {}

        # CPU — percent busy computed from /proc/stat deltas
        try:
            with open('/proc/stat') as f:
                parts = f.readline().split()
            # user, nice, system, idle, iowait, irq, softirq, ...
            vals = [int(x) for x in parts[1:8]]
            idle = vals[3] + vals[4]  # idle + iowait
            total = sum(vals)
            if self._last_cpu is not None:
                dt = total - self._last_cpu[0]
                di = idle - self._last_cpu[1]
                if dt > 0:
                    pct = int(round((dt - di) * 100 / dt))
                    pct = max(0, min(100, pct))
                    s['cpu'] = f"{pct}%"
                    s['cpu_pct'] = pct
                else:
                    s['cpu'] = '0%'
                    s['cpu_pct'] = 0
            else:
                s['cpu'] = '...'
                s['cpu_pct'] = 0
            self._last_cpu = (total, idle)
        except Exception:
            s['cpu'] = '?'
            s['cpu_pct'] = 0

        # Memory
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
            pct = (used * 100 // total) if total else 0
            s['mem'] = f"{used}/{total}M"
            s['mem_pct'] = pct
        except:
            s['mem'] = '?'
            s['mem_pct'] = 0

        # Temperature — try thermal zone first, then wifi phy hwmon
        # (pager exposes WiFi radio temp at /sys/class/ieee80211/phy*/hwmon*/temp1_input).
        val = None
        try:
            candidates = []
            candidates.extend(glob.glob('/sys/class/thermal/thermal_zone*/temp'))
            candidates.extend(glob.glob('/sys/class/ieee80211/phy*/hwmon*/temp1_input'))
            candidates.extend(glob.glob('/sys/class/hwmon/hwmon*/temp1_input'))
            for path in candidates:
                try:
                    with open(path) as f:
                        raw = int(f.read().strip())
                    # milli-°C (>1000) or deci-°C (>200) or plain °C
                    if raw > 1000:
                        val = raw // 1000
                    elif raw > 200:
                        val = raw // 10
                    else:
                        val = raw
                    break
                except Exception:
                    continue
        except Exception:
            pass
        if val is None:
            s['temp'] = '?'
            s['temp_val'] = 0
        else:
            s['temp'] = f"{val}C"
            s['temp_val'] = val

        # Disk — compact form "used pct"
        try:
            r = subprocess.run(['df', '-h', '/mmc'], capture_output=True, text=True, timeout=2)
            lines = r.stdout.strip().split('\n')
            if len(lines) >= 2:
                parts = lines[1].split()
                s['disk'] = f"{parts[2]} {parts[4]}"
            else:
                s['disk'] = '?'
        except:
            s['disk'] = '?'

        # Uptime
        try:
            with open('/proc/uptime') as f:
                secs = int(float(f.read().split()[0]))
            h = secs // 3600
            m = (secs % 3600) // 60
            s['uptime'] = f"{h}h {m}m"
        except:
            s['uptime'] = '?'

        # Battery
        try:
            for p in glob.glob('/sys/class/power_supply/*/capacity'):
                with open(p) as f:
                    pct = int(f.read().strip())
                s['battery'] = f"{pct}%"
                s['bat_pct'] = pct
                # Check charging
                for sp in glob.glob('/sys/class/power_supply/*/status'):
                    with open(sp) as f:
                        status = f.read().strip()
                    if status.lower() in ('charging', 'full'):
                        s['battery'] += ' (charging)'
                break
        except:
            s['battery'] = '?'
            s['bat_pct'] = 0

        # Hostname
        try:
            with open('/proc/sys/kernel/hostname') as f:
                s['hostname'] = f.read().strip()
        except:
            s['hostname'] = '?'

        # Network interfaces — flattened to if_name_N / if_val_N so the
        # theme JSON can reference specific slots.
        interfaces = []
        try:
            r = subprocess.run(['ip', '-4', '-o', 'addr'], capture_output=True, text=True, timeout=2)
            for line in r.stdout.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 4:
                    iface = parts[1]
                    ip = parts[3].split('/')[0]
                    if ip != '127.0.0.1':
                        interfaces.append((iface, ip))
        except:
            pass
        for i in range(4):
            if i < len(interfaces):
                name, ip = interfaces[i]
                s[f'if_name_{i}'] = name[:6]
                s[f'if_val_{i}'] = ip
            else:
                s[f'if_name_{i}'] = ''
                s[f'if_val_{i}'] = ''

        # USB devices — only real external devices. Filter out the
        # internal pager peripherals that sysfs exposes as USB
        # (root hubs, controllers, on-board wifi, debug UART/SPI/etc.)
        usb = []
        exclude_kw = ['host controller', 'hub', 'uart', 'spi', 'i2c',
                      'jtag', 'wireless_device', 'ehci', 'xhci', 'ohci',
                      'root hub']
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
        # Flatten USB list to indexed keys for the theme JSON
        for i in range(3):
            s[f'usb_{i}'] = usb[i] if i < len(usb) else ''

        # Process count — count /proc/<pid> dirs (BusyBox ps has no -e)
        try:
            count = sum(1 for name in os.listdir('/proc') if name.isdigit())
            s['procs'] = str(count)
        except Exception:
            s['procs'] = '?'

        # Kernel
        try:
            with open('/proc/version') as f:
                ver = f.read().split()[2]
                s['kernel'] = ver
        except:
            s['kernel'] = ''

        return s


_instance = None

def get_sysinfo():
    global _instance
    if _instance is None:
        _instance = SysInfoUI()
    return _instance
