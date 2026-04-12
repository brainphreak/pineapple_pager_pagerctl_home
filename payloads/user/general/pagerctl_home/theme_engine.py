"""theme_engine.py - Circuitry theme engine orchestrator.

Loads a Circuitry-format theme, manages screen navigation (stack-based),
handles input routing via per-screen button maps, and renders using
cached image handles for maximum responsiveness on MIPS.
"""

import glob
import json
import os
import time as time_mod

from screen import Screen, load_screen
from renderer import Renderer


class LiveWidget:
    """A self-refreshing widget with its own update interval.

    Subclass and override render() to create custom widgets.
    The engine calls check_refresh() each frame and sets dirty if needed.
    """

    def __init__(self, name, interval=5.0):
        self.name = name
        self.interval = interval  # seconds between refreshes
        self._last_refresh = 0
        self._last_state = None   # for change detection

    def needs_refresh(self):
        """Returns True if enough time has passed since last refresh."""
        return (time_mod.time() - self._last_refresh) >= self.interval

    def mark_refreshed(self):
        self._last_refresh = time_mod.time()

    def get_state(self):
        """Return current state for change detection. Override in subclass."""
        return None

    def state_changed(self):
        """Check if state changed since last render."""
        state = self.get_state()
        if state != self._last_state:
            self._last_state = state
            return True
        return False

    def render(self, pager, renderer):
        """Draw the widget. Override in subclass."""
        pass


class BatteryWidget(LiveWidget):
    """Battery icon + percentage text widget.

    Position and size are fully driven by the theme's status bar JSON:
        x, y          : top-left of the battery icon (screen coords)
        icon_w, icon_h: icon dimensions in pixels (icon is scaled)
        text_size     : bitmap font size for the percentage text
        text_gap      : pixels between percentage text and icon
    """

    def __init__(self, x, y, layers_config, variables, color,
                 icon_w=46, icon_h=24, text_size=3, text_gap=4):
        super().__init__('battery', interval=3.0)
        self.x = x
        self.y = y
        self.icon_w = icon_w
        self.icon_h = icon_h
        self.text_size = text_size
        self.text_gap = text_gap
        self.layers = layers_config  # dict of state_name -> layer list
        self.variables = variables
        self.color = color  # rgb565 for percentage text

    def get_state(self):
        """Returns (icon_state, percent) for change detection.
        Reads sysfs directly to avoid variable resolver cache issues."""
        pct = 0
        status = 'discharging'
        try:
            for p in glob.glob('/sys/class/power_supply/*/capacity'):
                with open(p) as f:
                    pct = int(f.read().strip())
            for p in glob.glob('/sys/class/power_supply/*/status'):
                with open(p) as f:
                    status = f.read().strip().lower()
        except Exception:
            return ('discharging', 0)
        if status in ('full', 'charging', 'not charging'):
            if pct >= 95:
                return ('charged', pct)
            if pct >= 75:
                return ('charging_100', pct)
            if pct >= 50:
                return ('charging_75', pct)
            if pct >= 25:
                return ('charging_50', pct)
            return ('charging_25', pct)
        return ('discharging', pct)

    def render(self, pager, renderer):
        icon_state, pct = self.get_state()
        text = str(pct) + '%'
        tw = pager.text_width(text, self.text_size)
        text_x = self.x - self.text_gap - tw
        pager.draw_text(text_x, self.y, text, self.color, self.text_size)
        # Draw the icon scaled so it matches the clock's visual height.
        layers = self.layers.get(icon_state, [])
        if layers and 'image_path' in layers[0]:
            handle = renderer.image(layers[0]['image_path'])
            if handle:
                pager.draw_image_scaled(
                    self.x, self.y, self.icon_w, self.icon_h, handle)
        self.mark_refreshed()


class TimeWidget(LiveWidget):
    """Clock widget — updates every 30s.

    Position and size come from the status bar JSON:
        x, y       : top-left of the clock text
        text_size  : bitmap font size
    """

    def __init__(self, x, y, palette_color, pager_ref, text_size=3):
        super().__init__('time', interval=30.0)
        self.x = x
        self.y = y
        self.text_size = text_size
        self.color = palette_color  # rgb565
        self.pager_ref = pager_ref

    def get_state(self):
        return time_mod.strftime('%I:%M%p')

    def render(self, pager, renderer):
        t = time_mod.strftime('%I:%M')
        if t[0] == '0':
            t = t[1:]
        ampm = time_mod.strftime('%p')
        full = t + ampm
        pager.draw_text(self.x, self.y, full, self.color, self.text_size)
        self.mark_refreshed()


class BrightnessWidget(LiveWidget):
    """Brightness icon widget — updates every 30s."""

    def __init__(self, x, y, layers_config, pager_ref):
        super().__init__('brightness', interval=30.0)
        self.x = x
        self.y = y
        self.layers = layers_config
        self.pager_ref = pager_ref

    def get_state(self):
        brt = self.pager_ref.get_brightness()
        if brt < 0:
            brt = 80
        if brt <= 15: return '15'
        if brt <= 25: return '25'
        if brt <= 50: return '50'
        if brt <= 75: return '75'
        return '100'

    def render(self, pager, renderer):
        key = self.get_state()
        layers = self.layers.get(key, [])
        renderer.draw_layers(layers, self.x, self.y)
        self.mark_refreshed()


class StaticIconWidget(LiveWidget):
    """Static icon — renders once, never refreshes."""

    def __init__(self, name, x, y, layers):
        super().__init__(name, interval=999999)
        self.x = x
        self.y = y
        self._layers = layers

    def render(self, pager, renderer):
        renderer.draw_layers(self._layers, self.x, self.y)
        self.mark_refreshed()


class ThemeEngine:
    """Main theme engine: load theme, navigate screens, render, handle input."""

    def __init__(self, pager, theme_dir, variables=None, font_path=None):
        self.pager = pager
        self.theme_dir = theme_dir
        self.variables = variables

        # Load theme.json
        with open(os.path.join(theme_dir, 'theme.json')) as f:
            self.theme = json.load(f)

        # Renderer with color palette and optional font override
        self.renderer = Renderer(
            pager, theme_dir,
            self.theme.get('color_palette', {}),
            font_path=font_path
        )

        # Feed template paths to renderer (toggles, checkboxes, etc.)
        template_paths = {}
        for section in ('toggle_templates', 'string_templates',
                        'radio_templates', 'signal_templates'):
            template_paths.update(self.theme.get(section, {}))
        self.renderer.set_template_paths(template_paths)

        # Screen stack for back navigation
        self.current_screen = None
        self.screen_stack = []
        self.dirty = True

        # Map target names -> JSON file paths
        self._targets = self._build_targets()

        # Load status bar configs and create live widgets
        self._status_bars = {}
        self.widgets = []  # LiveWidget instances
        for name, path in self.theme.get('status_bars', {}).items():
            full = os.path.join(theme_dir, path)
            if os.path.exists(full):
                with open(full) as f:
                    self._status_bars[name] = json.load(f)
        self._create_widgets_from_status_bar('default')

        # Load home screen (wargames_dashboard)
        home = self.theme.get('generic_menus', {}).get('dashboard_path')
        if home:
            self._load_screen(os.path.join(theme_dir, home))

    # -- Target resolution --

    def _build_targets(self):
        """Build a lookup from target name -> relative JSON path."""
        targets = {}
        t = self.theme
        gm = t.get('generic_menus', {})

        # All generic_menus entries
        for key, path in gm.items():
            targets[key] = path

        # Top-level *_path entries
        for key, val in t.items():
            if key.endswith('_path') and isinstance(val, str) and val.endswith('.json'):
                targets[key[:-5]] = val  # strip '_path'

        # Explicit mappings for common dashboard targets
        mappings = {
            'alerts_dashboard': gm.get('alerts_dashboard'),
            'payloads_dashboard': t.get('payloads_dashboard_path'),
            'recon_dashboard': t.get('recon_dashboard_path'),
            'pineap_menu': gm.get('pineap_menu'),
            'settings_menu': gm.get('settings_menu'),
            'status_screen': gm.get('status_screen'),
            'power_menu': gm.get('power_menu'),
        }
        for name, path in mappings.items():
            if path:
                targets[name] = path

        return targets

    # -- Screen management --

    def _load_screen(self, filepath):
        """Load screen from JSON and preload its images."""
        screen = load_screen(filepath)
        self.current_screen = screen
        self.renderer.preload(screen)
        self.dirty = True
        return screen

    def navigate_to(self, target):
        """Navigate to a named target.

        Returns:
            str or None: For function_* targets, returns the action name
            (e.g., 'shutdown'). For screen targets, returns None.
        """
        if not target:
            return None

        # Function targets (function_shutdown, function_sleep_screen, etc.)
        if target.startswith('function_'):
            return target[9:]

        # Launch targets (launch_loki, launch_wardrive, etc.)
        if target.startswith('launch_'):
            return target  # pass through to main loop

        # Wardrive dashboard — runs its own render loop
        if target == 'wardrive_dashboard':
            return 'wardrive'

        # System info dashboard
        if target == 'sysinfo_dashboard':
            return 'sysinfo'

        # Settings dashboard — handed off to settings_ui for the menu
        # and dialog rendering, keeping logic in one place.
        if target == 'settings_menu':
            return 'settings'

        # Inline toggle — no navigation
        if target == 'inline_toggle':
            return None

        # Dynamic payload screens
        if target == 'payloads_dashboard':
            self._show_payloads_screen()
            return None
        if target.startswith('payload_category_'):
            cat_name = target[17:]  # strip prefix
            self._show_category_screen(cat_name)
            return None

        path = self._targets.get(target)
        if not path:
            return None

        full = os.path.join(self.theme_dir, path)
        if not os.path.exists(full):
            return None

        # Push current screen onto stack
        if self.current_screen:
            self.screen_stack.append(self.current_screen)

        self._load_screen(full)
        self.pager.clear_input_events()
        return None

    def go_back(self):
        """Pop to previous screen. Returns False if already at root."""
        if self.screen_stack:
            self.current_screen = self.screen_stack.pop()
            self.dirty = True
            self.pager.clear_input_events()
            return True
        return False

    # -- Input handling --

    def handle_input(self, button):
        """Process a button press.

        Args:
            button: 'up', 'down', 'left', 'right', 'a', 'b', 'power'

        Returns:
            str or None: Action name for system functions, else None.
        """
        if not self.current_screen:
            return None

        # Power button always opens power menu
        if button == 'power':
            self.navigate_to('power_menu')
            return None

        # Map button to action via screen's button_map
        action = self.current_screen.button_map.get(button, 'noop')
        if action == 'noop':
            return None

        changed, target = self.current_screen.navigate(action)

        if changed:
            self.dirty = True
        elif target == '__back__':
            self.go_back()
        elif target:
            result = self.navigate_to(target)
            if result:
                return result  # system action (shutdown, sleep_screen, etc.)

        return None

    # -- Rendering --

    def render(self):
        """Render current screen. Only redraws when dirty flag is set."""
        if not self.dirty or not self.current_screen:
            return

        screen = self.current_screen
        p = self.pager

        # Clear background
        if screen.bg_color:
            c = screen.bg_color
            p.clear(p.rgb(c['r'], c['g'], c['b']))
        else:
            p.clear(0)

        # Background layers (circuit board image, pushbutton-up images, etc.)
        self.renderer.draw_layers(screen.bg_layers, variables=self.variables)

        # Menu items — selected item gets selected_layers, others get layers
        items = screen.items
        sel = screen.selected_index
        for i, item in enumerate(items):
            if i == sel:
                self.renderer.draw_layers(
                    item.selected_layers, item.x, item.y,
                    variables=self.variables)
            else:
                self.renderer.draw_layers(
                    item.layers, item.x, item.y,
                    variables=self.variables)

        # Live widgets (battery, time, etc.) — only on screens with a status bar
        if screen.status_bar:
            for w in self.widgets:
                w.render(p, self.renderer)

        p.flip()
        self.dirty = False

    def check_widgets(self):
        """Check if any widget needs a refresh. Sets dirty if so.
        Call this from the main loop each frame."""
        if not self.current_screen or not self.current_screen.status_bar:
            return
        for w in self.widgets:
            if w.needs_refresh() and w.state_changed():
                self.dirty = True
                return

    def _create_widgets_from_status_bar(self, bar_name):
        """Create LiveWidget instances from a status bar config."""
        config = self._status_bars.get(bar_name)
        if not config:
            return
        items = config.get('status_bar_items', {})

        # Layout left to right:
        # time(x=230) --- brightness(x=360) volume(x=388) --- battery%(x~400) battery_icon(x=438)

        if 'Time' in items:
            ti = items['Time']
            c = self.pager.WHITE
            if 'recolor_palette' in ti:
                c = self.renderer.color(ti['recolor_palette'])
            self.widgets.append(TimeWidget(
                ti.get('x', 213),
                ti.get('y', 5),
                c, self.pager,
                text_size=ti.get('text_size', 3)))

        if 'Battery' in items:
            bi = items['Battery']
            bat_color = self.renderer.color('lcd_text')
            bat_layers = dict(bi.get('layers', {}))
            # Discharging should show full bars, not empty battery
            bat_layers['discharging'] = bat_layers.get('charged', bat_layers.get('discharging', []))
            self.widgets.append(BatteryWidget(
                bi.get('x', 429),
                bi.get('y', 4),
                bat_layers, self.variables, bat_color,
                icon_w=bi.get('icon_w', 46),
                icon_h=bi.get('icon_h', 24),
                text_size=bi.get('text_size', 3),
                text_gap=bi.get('text_gap', 4)))

    # -- Dynamic payload screens --

    def _show_payloads_screen(self):
        """Generate and show a screen listing payload categories."""
        from payload_browser import scan_categories
        categories = scan_categories()
        if not categories:
            return

        # Build a screen config dynamically
        menu_items = []
        y = 72
        for cat_name, payloads in categories:
            count = len(payloads)
            menu_items.append({
                'id': cat_name,
                'x': 175, 'y': y,
                'layers': [
                    {'text': f'{cat_name} ({count})', 'text_color_palette': 'blue'}
                ],
                'selected_layers': [
                    {'image_path': 'assets/alerts_dashboard/sub.png', 'x': -25, 'y': 3},
                    {'text': f'{cat_name} ({count})', 'text_color_palette': 'yellow'}
                ],
                'target': f'payload_category_{cat_name.lower()}'
            })
            y += 22

        config = {
            'screen_name': 'payloads',
            'status_bar': 'default',
            'windowed_canvas': False,
            'button_map': {
                'a': 'select', 'b': 'back',
                'up': 'previous', 'down': 'next',
                'left': 'noop', 'right': 'noop'
            },
            'background': {
                'layers': [
                    {'image_path': 'assets/payloads_dashboard/payloads_bg.png', 'x': 0, 'y': 0}
                ],
                'background_color': {'r': 0, 'g': 0, 'b': 0}
            },
            'pages': [{'page_index': 0, 'menu_items': menu_items}]
        }

        if self.current_screen:
            self.screen_stack.append(self.current_screen)
        self.current_screen = Screen(config)
        self.renderer.preload(self.current_screen)
        self.dirty = True
        self.pager.clear_input_events()

    def _show_category_screen(self, cat_name):
        """Generate and show a screen listing payloads in a category."""
        from payload_browser import scan_categories
        categories = scan_categories()

        # Find matching category
        payloads = None
        for name, plist in categories:
            if name.lower() == cat_name:
                payloads = plist
                break
        if not payloads:
            return

        menu_items = []
        y = 72
        for info in payloads:
            menu_items.append({
                'id': info.title,
                'x': 175, 'y': y,
                'layers': [
                    {'text': info.title, 'text_color_palette': 'green'}
                ],
                'selected_layers': [
                    {'image_path': 'assets/alerts_dashboard/sub.png', 'x': -25, 'y': 3},
                    {'text': info.title, 'text_color_palette': 'yellow'}
                ],
                'target': f'launch_{info.title.lower().replace(" ", "_")}'
            })
            y += 22

        config = {
            'screen_name': f'category_{cat_name}',
            'status_bar': 'default',
            'windowed_canvas': False,
            'button_map': {
                'a': 'select', 'b': 'back',
                'up': 'previous', 'down': 'next',
                'left': 'noop', 'right': 'noop'
            },
            'background': {
                'layers': [
                    {'image_path': 'assets/payloads_dashboard/payloads_bg.png', 'x': 0, 'y': 0}
                ],
                'background_color': {'r': 0, 'g': 0, 'b': 0}
            },
            'pages': [{'page_index': 0, 'menu_items': menu_items}]
        }

        if self.current_screen:
            self.screen_stack.append(self.current_screen)
        self.current_screen = Screen(config)
        self.renderer.preload(self.current_screen)
        self.dirty = True
        self.pager.clear_input_events()

    # -- Configuration --

    def set_font(self, font_path):
        """Switch font for all text rendering. None = bitmap font."""
        self.renderer.font_path = font_path
        self.dirty = True

    def cleanup(self):
        """Free all cached resources."""
        self.renderer.free_all()
