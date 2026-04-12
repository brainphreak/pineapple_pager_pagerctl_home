"""settings_ui.py - Theme-driven settings menu for pagerctl_home.

Reads the category/item schema from a theme JSON so that users can
edit/add/remove settings entries without touching Python.

Schema path (relative to active theme dir):
    components/dashboards/settings_dashboard.json

Takes over rendering while active and returns to the home screen on B.
Power exits and requests the power menu.
"""

import os
import json
import time
import subprocess

from wardrive.config import (load_config, save_config, SETTINGS_FILE,
                              backup_settings, list_backups, restore_backup)
from wardrive.web_server import wait_any_button


FONT_MENU = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wardrive', 'fonts', 'menu.ttf')
FONT_TITLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wardrive', 'fonts', 'title.TTF')
SCREEN_W = 480
SCREEN_H = 222

LAYOUT_REL = 'components/dashboards/settings_dashboard.json'


class SettingsUI:
    """Two-pane settings navigator driven by a theme JSON schema."""

    def __init__(self):
        self.bg_handle = None
        self.pager = None
        self.engine = None
        self.config = None
        self._layout = None
        self._layout_path = None
        self._layout_mtime = 0

    def run(self, pager, engine=None):
        self.pager = pager
        self.engine = engine
        self.config = load_config()
        self._load_layout()

        if not self._layout:
            return None

        # Load background via theme-relative path
        self.bg_handle = None
        bg_rel = self._layout.get('bg_image')
        if bg_rel:
            theme_dir = engine.theme_dir if engine else os.path.join(
                os.path.dirname(__file__), 'themes', 'Circuitry')
            bg_path = os.path.join(theme_dir, bg_rel)
            if os.path.isfile(bg_path):
                self.bg_handle = pager.load_image(bg_path)

        pager.clear_input_events()
        return self._category_loop()

    def _load_layout(self):
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

    # -- Color helpers --

    def _color(self, name):
        c = self._layout.get('colors', {}).get(name, [255, 255, 255])
        return self.pager.rgb(*c)

    def _drain_until_released(self, btn_mask):
        """Wait for a button to be released and clear the event queue.
        Prevents stale events from leaking to the main loop after we
        return (the "press B twice to go back" bug)."""
        p = self.pager
        deadline = time.time() + 0.5
        while time.time() < deadline:
            try:
                held, _, _ = p.poll_input()
            except Exception:
                break
            if not (held & btn_mask):
                break
            time.sleep(0.01)
        p.clear_input_events()

    # -- Drawing primitives --

    def _draw_bg(self):
        p = self.pager
        if self.bg_handle:
            p.draw_image(0, 0, self.bg_handle)
        else:
            p.clear(0)

    def _draw_title(self, text):
        p = self.pager
        size = self._layout.get('title_size', 24)
        tw = p.ttf_width(text, FONT_TITLE, size)
        p.draw_ttf((SCREEN_W - tw) // 2, 12, text, self._color('title'), FONT_TITLE, size)

    def _draw_widgets(self):
        if self.engine:
            for w in self.engine.widgets:
                try:
                    w.render(self.pager, self.engine.renderer)
                except Exception:
                    pass

    def _draw_list(self, items, selected, x, y_start, row_h, fs,
                   color_normal, color_selected):
        p = self.pager
        for i, label in enumerate(items):
            y = y_start + i * row_h
            c = color_selected if i == selected else color_normal
            p.draw_ttf(x, y, label, c, FONT_MENU, fs)

    # -- Main navigation loop --

    def _category_loop(self):
        """Left pane: category list. A selects, B exits, power → power menu."""
        cfg = self._layout
        categories = cfg.get('categories', [])
        if not categories:
            return None
        fs = cfg.get('font_size', 18)
        row_h = cfg.get('row_height', 22)
        left_x = cfg.get('left_x', 20)
        y_start = cfg.get('y_start', 52)

        selected = 0
        while True:
            self._draw_bg()

            # Category list on left
            labels = [c.get('label', c.get('id', '?')) for c in categories]
            self._draw_list(labels, selected, left_x, y_start, row_h, fs,
                            self._color('category'), self._color('category_selected'))

            # Preview of selected category items on right (no values yet)
            cat = categories[selected]
            right_x = cfg.get('right_x', 180)
            preview_items = [i.get('label', '?') for i in cat.get('items', [])]
            for i, label in enumerate(preview_items[:6]):
                y = y_start + i * row_h
                self.pager.draw_ttf(right_x, y, label, self._color('dim'), FONT_MENU, fs)

            self._draw_widgets()
            self.pager.flip()

            btn = wait_any_button(self.pager)
            if btn & self.pager.BTN_UP:
                selected = (selected - 1) % len(categories)
            elif btn & self.pager.BTN_DOWN:
                selected = (selected + 1) % len(categories)
            elif btn & self.pager.BTN_A or btn & self.pager.BTN_RIGHT:
                result = self._item_loop(categories, selected)
                if result == 'power':
                    self.pager.clear_input_events()
                    return 'power'
            elif btn & self.pager.BTN_POWER:
                self._drain_until_released(self.pager.BTN_POWER)
                return 'power'
            elif btn & self.pager.BTN_B or btn & self.pager.BTN_LEFT:
                self._drain_until_released(self.pager.BTN_B)
                return None

    def _item_loop(self, categories, cat_index):
        """Right pane: items within a category, showing live values.
        Left pane: category list stays visible with the active category
        highlighted (dim, not selectable while we're drilling into items)."""
        cfg = self._layout
        category = categories[cat_index]
        items = category.get('items', [])
        if not items:
            return
        fs = cfg.get('font_size', 18)
        row_h = cfg.get('row_height', 22)
        left_x = cfg.get('left_x', 20)
        right_x = cfg.get('right_x', 180)
        y_start = cfg.get('y_start', 52)

        selected = 0
        while True:
            self._draw_bg()

            # Left pane: category list, active one highlighted dim
            for i, c in enumerate(categories):
                y = y_start + i * row_h
                label = c.get('label', '?')
                color = self._color('category_selected') if i == cat_index else self._color('dim')
                self.pager.draw_ttf(left_x, y, label, color, FONT_MENU, fs)

            # Draw items with current values
            for i, item in enumerate(items):
                y = y_start + i * row_h
                label = item.get('label', '?')
                value = self._format_item_value(item)
                is_sel = (i == selected)
                label_c = self._color('category_selected' if is_sel else 'category')
                value_c = self._color('value_selected' if is_sel else 'value')
                self.pager.draw_ttf(right_x, y, label + ':', label_c, FONT_MENU, fs)
                lw = self.pager.ttf_width(label + ':', FONT_MENU, fs)
                if value:
                    self.pager.draw_ttf(right_x + lw + 6, y, value, value_c, FONT_MENU, fs)

            self._draw_widgets()
            self.pager.flip()

            btn = wait_any_button(self.pager)
            if btn & self.pager.BTN_UP:
                selected = (selected - 1) % len(items)
            elif btn & self.pager.BTN_DOWN:
                selected = (selected + 1) % len(items)
            elif btn & self.pager.BTN_A:
                self._activate_item(items[selected])
            elif btn & self.pager.BTN_POWER:
                self._drain_until_released(self.pager.BTN_POWER)
                return 'power'
            elif btn & self.pager.BTN_B or btn & self.pager.BTN_LEFT:
                self._drain_until_released(self.pager.BTN_B)
                return None

    # -- Item value formatting / activation --

    def _format_item_value(self, item):
        t = item.get('type')
        if t == 'toggle':
            return 'ON' if self.config.get(item['key'], False) else 'OFF'
        if t == 'cycle':
            val = self.config.get(item['key'])
            return self._format_scalar(val, item)
        if t == 'action':
            return ''
        if t == 'service':
            run, en = self._service_state(item.get('service_name', ''),
                                          item.get('process_name'))
            run_s = 'Run' if run else 'Stop'
            en_s = 'Auto' if en else 'Man'
            return f'{run_s}/{en_s}'
        return str(self.config.get(item.get('key'), ''))

    def _format_scalar(self, val, item):
        if val is None or val == '':
            if item.get('include_none'):
                return 'None'
            return '?'
        fmt = item.get('formatter')
        if fmt == 'seconds':
            try:
                n = int(val)
                if n == 0:
                    return 'Never'
                if n < 60:
                    return f'{n}s'
                return f'{n // 60}m'
            except Exception:
                return str(val)
        # For file-backed cycles, show the basename without extension.
        if item.get('source_dir'):
            ext = item.get('extension', '')
            name = str(val)
            if ext and name.endswith(ext):
                name = name[:-len(ext)]
            return name
        suffix = item.get('suffix', '')
        return f'{val}{suffix}'

    def _resolve_options(self, item):
        """Return the option list for a cycle item — static options[] or
        files from source_dir (+extension, + optional 'None' sentinel)."""
        static = item.get('options')
        if static:
            return static
        src_dir = item.get('source_dir')
        if not src_dir:
            return []
        ext = item.get('extension', '')
        try:
            files = sorted(
                f for f in os.listdir(src_dir)
                if not ext or f.endswith(ext)
            )
        except Exception:
            files = []
        if item.get('include_none'):
            files = ['None'] + files
        return files

    def _activate_item(self, item):
        t = item.get('type')
        if t == 'toggle':
            key = item['key']
            self.config[key] = not self.config.get(key, False)
            save_config(self.config)
            self._apply_side_effect(item)
        elif t == 'cycle':
            self._cycle_item(item)
        elif t == 'action':
            self._run_action(item)
        elif t == 'service':
            self._service_picker(item)

    def _service_state(self, name, process_name=None):
        """Return (running, enabled) tuple for an init.d service.

        BusyBox init.d `running` is unreliable — many services default to
        returning 0 even when nothing is up. Use `pidof` as ground truth
        and fall back to init.d's answer only if the binary isn't found.
        """
        if not name:
            return (False, False)
        path = f'/etc/init.d/{name}'
        if not os.path.isfile(path):
            return (False, False)
        proc = process_name or name
        try:
            r = subprocess.run(['pidof', proc], capture_output=True, timeout=2)
            run = r.returncode == 0 and bool(r.stdout.strip())
        except Exception:
            # pidof failed — fall back to init.d's (unreliable) answer
            run = self._rc(path, 'running') == 0
        en = self._rc(path, 'enabled') == 0
        return (run, en)

    def _rc(self, *args):
        try:
            return subprocess.run(list(args), capture_output=True,
                                  timeout=3).returncode
        except Exception:
            return -1

    def _service_action(self, name, action):
        path = f'/etc/init.d/{name}'
        if not os.path.isfile(path):
            return False
        try:
            subprocess.run([path, action], capture_output=True, timeout=10)
            return True
        except Exception:
            return False

    def _service_picker(self, item):
        """Start/Stop/Restart/Enable/Disable picker for an init.d service."""
        name = item.get('service_name', '')
        p = self.pager
        if not name:
            return
        cfg = self._layout
        fs = cfg.get('font_size', 18)
        row_h = cfg.get('row_height', 22)
        right_x = cfg.get('right_x', 180)
        y_start = cfg.get('y_start', 52)

        selected = 0
        while True:
            run, en = self._service_state(name, item.get('process_name'))
            actions = []
            if run:
                actions.append(('Stop', 'stop'))
                actions.append(('Restart', 'restart'))
            else:
                actions.append(('Start', 'start'))
            if en:
                actions.append(('Disable Autostart', 'disable'))
            else:
                actions.append(('Enable Autostart', 'enable'))

            self._draw_bg()
            # Header showing service + state on the left pane area
            state = f'{name}: {"Running" if run else "Stopped"} / {"Auto" if en else "Manual"}'
            p.draw_ttf(20, y_start, state, self._color('category_selected'),
                       FONT_MENU, fs)
            # Action list on the right
            for i, (label, _) in enumerate(actions):
                y = y_start + (i + 2) * row_h
                color = self._color('category_selected' if i == selected else 'category')
                p.draw_ttf(right_x, y, label, color, FONT_MENU, fs)
            self._draw_widgets()
            p.flip()

            btn = wait_any_button(p)
            if btn & p.BTN_UP:
                selected = (selected - 1) % len(actions)
            elif btn & p.BTN_DOWN:
                selected = (selected + 1) % len(actions)
            elif btn & p.BTN_A:
                _, act = actions[selected]
                ok = self._service_action(name, act)
                self._flash(f'{name} {act}' + ('' if ok else ' failed'))
                if act == 'stop':
                    selected = 0
            elif btn & p.BTN_POWER:
                return
            elif btn & p.BTN_B:
                return

    def _cycle_item(self, item):
        """Advance to the next option. Options are either static (item.options)
        or dynamically resolved from source_dir."""
        key = item['key']
        options = self._resolve_options(item)
        if not options:
            return
        current = self.config.get(key)
        try:
            idx = options.index(current)
        except ValueError:
            idx = -1
        new_val = options[(idx + 1) % len(options)]
        self.config[key] = new_val
        save_config(self.config)
        self._apply_side_effect(item)

    def _apply_side_effect(self, item):
        """Settings that need immediate hardware action when changed."""
        apply = item.get('apply')
        key = item.get('key')
        if apply == 'brightness':
            try:
                self.pager.set_brightness(int(self.config.get('brightness', 80)))
            except Exception:
                pass
        if key == 'web_server':
            from wardrive.web_server import start_web_ui, stop_web_ui
            if self.config.get('web_server', False):
                port = self.config.get('web_port', 1337)
                start_web_ui(port=port)
                self._flash(f"Web UI on :{port}")
            else:
                stop_web_ui()
                self._flash('Web UI stopped')

    # Map named RTTTL presets → melody strings or None (silent)
    _RTTTL_PRESETS = {
        'None': None,
        'Tetris': None,      # resolved dynamically from Pager class
        'Level Up': None,
        'Game Over': None,
    }

    def _resolve_rtttl(self, name):
        """Look up a preset name on the Pager class (filled lazily)."""
        mapping = {
            'Tetris': 'RTTTL_TETRIS',
            'Level Up': 'RTTTL_LEVEL_UP',
            'Game Over': 'RTTTL_GAME_OVER',
        }
        attr = mapping.get(name)
        if not attr:
            return None
        return getattr(type(self.pager), attr, None)

    def _run_action(self, item):
        action = item.get('action')
        if action == 'backup':
            path = backup_settings()
            self._flash('Backed up' if path else 'Backup failed')
        elif action == 'restore':
            self._restore_picker()
        elif action == 'test_vibrate':
            try:
                self.pager.vibrate(150)
            except Exception:
                pass
            self._flash('Vibrate')
        elif action == 'test_beep':
            if not self.config.get('sound_enabled', True):
                self._flash('Sound is off')
                return
            try:
                self.pager.beep(1000, 200)
            except Exception:
                pass
            self._flash('Beep')
        elif action == 'preview':
            if not self.config.get('sound_enabled', True):
                self._flash('Sound is off')
                return
            src = item.get('source')
            name = self.config.get(src, 'None') if src else 'None'
            if not name or name == 'None':
                self._flash('(silent)')
                return
            melody = self._load_rtttl_file(name)
            if melody:
                try:
                    self.pager.play_rtttl(melody)
                except Exception:
                    pass
                display = name[:-6] if name.endswith('.rtttl') else name
                self._flash(f'Playing: {display}')
            else:
                self._flash('Not found')

    def _load_rtttl_file(self, filename, source_dir='/lib/pager/ringtones'):
        """Read an RTTTL file from disk. Returns the melody string or None."""
        path = os.path.join(source_dir, filename)
        try:
            with open(path) as f:
                return f.read().strip()
        except Exception:
            return None

    def _restore_picker(self):
        files = list_backups()
        if not files:
            self._flash('No backups found')
            return
        selected = 0
        cfg = self._layout
        fs = cfg.get('font_size', 18)
        row_h = cfg.get('row_height', 22)
        right_x = cfg.get('right_x', 180)
        y_start = cfg.get('y_start', 52)

        while True:
            self._draw_bg()
            for i, fname in enumerate(files[:8]):
                y = y_start + i * row_h
                c = self._color('category_selected' if i == selected else 'category')
                # Show "20260412_120000" → "2026-04-12 12:00"
                shown = fname.replace('settings_', '').replace('.bak.json', '')
                self.pager.draw_ttf(right_x, y, shown, c, FONT_MENU, fs)
            self._draw_widgets()
            self.pager.flip()

            btn = wait_any_button(self.pager)
            if btn & self.pager.BTN_UP:
                selected = (selected - 1) % len(files)
            elif btn & self.pager.BTN_DOWN:
                selected = (selected + 1) % len(files)
            elif btn & self.pager.BTN_A:
                ok = restore_backup(files[selected])
                self._flash('Restored' if ok else 'Restore failed')
                if ok:
                    self.config = load_config()
                return
            elif btn & self.pager.BTN_B:
                return

    def _flash(self, msg, duration=1.0):
        p = self.pager
        self._draw_bg()
        fs = 18
        tw = p.ttf_width(msg, FONT_MENU, fs)
        box_w = max(tw + 40, 220)
        box_h = 50
        bx = (SCREEN_W - box_w) // 2
        by = (SCREEN_H - box_h) // 2
        p.fill_rect(bx, by, box_w, box_h, p.rgb(0, 0, 0))
        edge = self._color('title')
        p.fill_rect(bx, by, box_w, 1, edge)
        p.fill_rect(bx, by + box_h - 1, box_w, 1, edge)
        p.fill_rect(bx, by, 1, box_h, edge)
        p.fill_rect(bx + box_w - 1, by, 1, box_h, edge)
        p.draw_ttf(bx + (box_w - tw) // 2, by + (box_h - fs) // 2, msg,
                   p.rgb(255, 255, 255), FONT_MENU, fs)
        p.flip()
        time.sleep(duration)
        p.clear_input_events()


_instance = None

def get_settings():
    global _instance
    if _instance is None:
        _instance = SettingsUI()
    return _instance
