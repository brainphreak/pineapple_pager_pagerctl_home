"""Screen elements — renderable items on a screen."""

import os

PAYLOAD_DIR = os.path.dirname(os.path.abspath(__file__))


class Element:
    """Base element with position and size."""

    def __init__(self, data, theme_dir):
        self.id = data.get('id', '')
        self.type = data.get('type', '')
        self.x = data.get('x', 0)
        self.y = data.get('y', 0)
        self.w = data.get('w', 0)
        self.h = data.get('h', 0)
        self.visible = data.get('visible', True)
        self.selectable = False
        self.data = data
        self.theme_dir = theme_dir

    def resolve_path(self, path):
        """Resolve a path relative to the theme directory."""
        if not path:
            return None
        if os.path.isabs(path):
            return path
        # Try theme dir first, then payload dir
        theme_path = os.path.join(self.theme_dir, path)
        if os.path.exists(theme_path):
            return theme_path
        payload_path = os.path.join(PAYLOAD_DIR, path)
        if os.path.exists(payload_path):
            return payload_path
        return theme_path  # Return theme path even if missing

    def render(self, pager, variables, is_selected=False):
        """Render this element. Override in subclasses."""
        pass

    def center_x(self):
        return self.x + self.w // 2

    def center_y(self):
        return self.y + self.h // 2


class TextElement(Element):
    """Static or variable text."""

    def __init__(self, data, theme_dir):
        super().__init__(data, theme_dir)
        self.text = data.get('text', '')
        self.font = self.resolve_path(data.get('font')) or os.path.join(PAYLOAD_DIR, 'fonts/body.ttf')
        self.font_size = data.get('font_size', 18)
        self.color = data.get('color', [255, 255, 255])
        self.align = data.get('align', 'left')

    def render(self, pager, variables, is_selected=False):
        if not self.visible:
            return
        text = variables.resolve(self.text)
        color = pager.rgb(self.color[0], self.color[1], self.color[2])
        tw = pager.ttf_width(text, self.font, self.font_size)

        if self.align == 'center':
            x = self.x - tw // 2
        elif self.align == 'right':
            x = self.x - tw
        else:
            x = self.x

        pager.draw_ttf(x, self.y, text, color, self.font, self.font_size)


class ImageElement(Element):
    """Static image."""

    def __init__(self, data, theme_dir):
        super().__init__(data, theme_dir)
        self.image_path = self.resolve_path(data.get('image'))

    def render(self, pager, variables, is_selected=False):
        if not self.visible or not self.image_path:
            return
        if os.path.isfile(self.image_path) and self.w > 0 and self.h > 0:
            try:
                pager.draw_image_file_scaled(self.x, self.y, self.w, self.h, self.image_path)
            except Exception:
                pass


class ButtonElement(Element):
    """Interactive button — image, text, or both."""

    def __init__(self, data, theme_dir):
        super().__init__(data, theme_dir)
        self.selectable = True
        self.label = data.get('label', '')
        self.image_path = self.resolve_path(data.get('image'))
        self.image_selected = self.resolve_path(data.get('image_selected'))
        self.label_position = data.get('label_position', 'below')  # above, below, overlay, none
        self.font = self.resolve_path(data.get('font')) or os.path.join(PAYLOAD_DIR, 'fonts/body.ttf')
        self.font_size = data.get('font_size', 14)
        self.color = data.get('color', [255, 255, 255])
        self.selected_color = data.get('selected_color', [0, 255, 0])
        self.action = data.get('action', {})
        self.style = data.get('style', 'auto')  # auto, icon, text, icon_text, card

    def render(self, pager, variables, is_selected=False):
        if not self.visible:
            return

        color_rgb = self.selected_color if is_selected else self.color
        color = pager.rgb(color_rgb[0], color_rgb[1], color_rgb[2])

        # Draw image if available
        img = self.image_selected if (is_selected and self.image_selected) else self.image_path
        has_image = img and os.path.isfile(img) and self.w > 0 and self.h > 0
        if has_image:
            try:
                img_h = self.h
                if self.label and self.label_position in ('below', 'above'):
                    img_h = self.h - self.font_size - 4
                pager.draw_image_file_scaled(self.x, self.y, self.w, img_h, img)
                # Selection border for image buttons
                if is_selected:
                    sel_color = pager.rgb(self.selected_color[0], self.selected_color[1], self.selected_color[2])
                    pager.rect(self.x - 1, self.y - 1, self.w + 2, img_h + 2, sel_color)
            except Exception:
                pass

        # Draw selection indicator if no image (text-only buttons)
        if not has_image:
            if is_selected and self.w > 0 and self.h > 0:
                sel_bg = pager.rgb(30, 50, 80)
                pager.fill_rect(self.x, self.y, self.w, self.h, sel_bg)

        # Draw label
        if self.label and self.label_position != 'none':
            label = variables.resolve(self.label)
            tw = pager.ttf_width(label, self.font, self.font_size)

            if self.label_position == 'below':
                lx = self.x + (self.w - tw) // 2 if self.w else self.x
                ly = self.y + self.h - self.font_size - 2 if self.h else self.y
            elif self.label_position == 'above':
                lx = self.x + (self.w - tw) // 2 if self.w else self.x
                ly = self.y - self.font_size - 2
            elif self.label_position == 'overlay':
                lx = self.x + (self.w - tw) // 2 if self.w else self.x
                ly = self.y + (self.h - self.font_size) // 2 if self.h else self.y
            else:  # center/default for text-only buttons
                if self.w:
                    lx = self.x + (self.w - tw) // 2
                else:
                    lx = self.x - tw // 2 if self.style != 'text' else self.x
                ly = self.y + (self.h - self.font_size) // 2 if self.h else self.y

            pager.draw_ttf(lx, ly, label, color, self.font, self.font_size)


class WidgetElement(Element):
    """Live-updating widget using variables."""

    def __init__(self, data, theme_dir):
        super().__init__(data, theme_dir)
        self.widget_type = data.get('widget', '')
        self.format = data.get('format', '')
        self.font = self.resolve_path(data.get('font')) or os.path.join(PAYLOAD_DIR, 'fonts/body.ttf')
        self.font_size = data.get('font_size', 14)
        self.color = data.get('color', [120, 120, 120])
        self.align = data.get('align', 'left')
        self.auto_color = data.get('color') == 'auto'

        # Default formats per widget type
        if not self.format:
            defaults = {
                'clock': '${time}',
                'date': '${date}',
                'battery': '${battery_percent}%',
                'cpu': 'CPU: ${cpu_usage}',
                'memory': 'Mem: ${mem_percent}%',
                'ip': '${ip_addr}',
                'uptime': 'Up: ${uptime}',
                'gps': '${gps_lat}, ${gps_lon}',
                'disk': 'Disk: ${disk_percent}%',
                'ssid': '${ssid}',
                'temp': '${temp}',
            }
            self.format = defaults.get(self.widget_type, self.widget_type)

    def render(self, pager, variables, is_selected=False):
        if not self.visible:
            return
        text = variables.resolve(self.format)

        if self.auto_color:
            color = self._auto_color(pager, variables)
        else:
            color = pager.rgb(self.color[0], self.color[1], self.color[2])

        tw = pager.ttf_width(text, self.font, self.font_size)
        if self.align == 'center':
            x = self.x - tw // 2
        elif self.align == 'right':
            x = self.x - tw
        else:
            x = self.x

        pager.draw_ttf(x, self.y, text, color, self.font, self.font_size)

    def _auto_color(self, pager, variables):
        """Auto-color for battery widget: green > 50, yellow > 20, red below."""
        if self.widget_type == 'battery':
            try:
                pct = int(variables.resolve('${battery_percent}'))
                if pct > 50:
                    return pager.rgb(0, 255, 0)
                elif pct > 20:
                    return pager.rgb(255, 220, 50)
                else:
                    return pager.rgb(255, 60, 60)
            except Exception:
                pass
        return pager.rgb(self.color[0], self.color[1], self.color[2]) if isinstance(self.color, list) else pager.rgb(120, 120, 120)


def create_element(data, theme_dir):
    """Factory — create the right element type from JSON data."""
    element_types = {
        'text': TextElement,
        'image': ImageElement,
        'button': ButtonElement,
        'widget': WidgetElement,
    }
    cls = element_types.get(data.get('type', ''), Element)
    return cls(data, theme_dir)
