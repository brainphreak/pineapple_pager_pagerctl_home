"""pager_dialogs.py - Shared modal dialogs for any subscreen.

edit_string(pager, prompt, current='', secret=False, ...)
    On-screen keyboard text input. Returns the entered string,
    or None if the user cancels.

popup_menu(pager, title, items, ...)
    Bordered modal picker. items is a list of (label, value)
    tuples. Returns the value of the selected item, or None on
    cancel.
"""

import os
import time

from wardrive.config import SCREEN_W, SCREEN_H, FONT_TITLE, FONT_MENU
from wardrive.web_server import wait_any_button


_KB_ROWS = [
    ['1','2','3','4','5','6','7','8','9','0','BK'],
    ['q','w','e','r','t','y','u','i','o','p','SP'],
    ['a','s','d','f','g','h','j','k','l','.','OK'],
    ['z','x','c','v','b','n','m','-','_','@','X'],
]

_KB_ROWS_NUMERIC = [
    ['7','8','9','BK'],
    ['4','5','6','.'],
    ['1','2','3','OK'],
    ['0',' ',' ','X'],
]

_KB_ROWS_HEX = [
    ['0','1','2','3','4','5','BK'],
    ['6','7','8','9','A','B','SP'],
    ['C','D','E','F',':','.','OK'],
    [' ',' ',' ',' ',' ',' ','X'],
]


def edit_string(pager, prompt, current='', secret=False, max_length=48,
                bg_drawer=None, keyboard=None):
    """Show an on-screen keyboard. Returns entered string or None.

    keyboard: optional layout override:
      None or 'full' — full alphanumeric (default)
      'numeric'      — 0-9 and . only (for IP/number entry)
      'hex'          — 0-9 A-F : . (for MAC addresses)
    """
    kb = {'numeric': _KB_ROWS_NUMERIC,
          'hex': _KB_ROWS_HEX}.get(keyboard, _KB_ROWS)
    buf = str(current or '')
    rows = len(kb)
    cols = len(kb[0])
    cell_w = 40
    cell_h = 28
    grid_x0 = 20
    grid_y0 = 80
    sel_row, sel_col = 0, 0
    fs = 18
    cell_fs = 16

    label_c = pager.rgb(180, 180, 180)
    val_c = pager.rgb(255, 220, 50)
    cell_c = pager.rgb(180, 180, 180)
    cell_sel_c = pager.rgb(255, 220, 50)
    sel_bg = pager.rgb(40, 40, 40)

    while True:
        if bg_drawer:
            bg_drawer()
        else:
            pager.clear(0)

        pager.draw_ttf(20, 40, f'{prompt}:', label_c, FONT_MENU, fs)
        lw = pager.ttf_width(f'{prompt}:', FONT_MENU, fs)
        display = ('*' * len(buf)) if secret else buf
        pager.draw_ttf(20 + lw + 8, 40, display + '_', val_c, FONT_MENU, fs)

        for r in range(rows):
            for c in range(cols):
                ch = kb[r][c]
                x = grid_x0 + c * cell_w
                y = grid_y0 + r * cell_h
                is_sel = (r == sel_row and c == sel_col)
                if is_sel:
                    pager.fill_rect(x - 2, y - 2, cell_w - 4, cell_h - 4, sel_bg)
                color = cell_sel_c if is_sel else cell_c
                tfs = cell_fs if len(ch) == 1 else cell_fs - 2
                pager.draw_ttf(x + 6, y + 2, ch, color, FONT_MENU, tfs)

        pager.flip()

        btn = wait_any_button(pager)
        if btn & pager.BTN_UP:
            sel_row = (sel_row - 1) % rows
        elif btn & pager.BTN_DOWN:
            sel_row = (sel_row + 1) % rows
        elif btn & pager.BTN_LEFT:
            sel_col = (sel_col - 1) % cols
        elif btn & pager.BTN_RIGHT:
            sel_col = (sel_col + 1) % cols
        elif btn & pager.BTN_A:
            ch = kb[sel_row][sel_col]
            if ch == 'BK':
                buf = buf[:-1]
            elif ch == 'SP':
                if len(buf) < max_length:
                    buf += ' '
            elif ch == 'OK':
                return buf
            elif ch == 'X':
                return None
            else:
                if len(buf) < max_length:
                    buf += ch
        elif btn & pager.BTN_B:
            return None


def themed_keyboard(pager, prompt, default='', keyboard_json_path=None,
                     theme_dir=''):
    """Graphical keyboard using the Circuitry theme assets.

    The keyboard JSON defines a background image (the full keyboard
    layout), an input_area for the typed text, and a grid of key
    definitions each with a highlight overlay image. Navigation is
    via d-pad, A types the highlighted key, B cancels.

    Returns the entered string, or None on cancel.
    """
    import json as _json

    if not keyboard_json_path or not os.path.isfile(keyboard_json_path):
        return edit_string(pager, prompt, default)

    try:
        with open(keyboard_json_path) as f:
            kb_cfg = _json.load(f)
    except Exception:
        return edit_string(pager, prompt, default)

    qwerty = kb_cfg.get('qwerty', {})
    input_area = kb_cfg.get('input_area', {})
    bg_layers = (qwerty.get('background', {}).get('layers') or [])
    key_rows = qwerty.get('rows', [])

    if not key_rows:
        return edit_string(pager, prompt, default)

    # Load background image
    bg_handle = None
    for layer in bg_layers:
        ip = layer.get('image_path')
        if ip:
            full = os.path.join(theme_dir, ip)
            if os.path.isfile(full):
                try:
                    bg_handle = pager.load_image(full)
                except Exception:
                    pass
            break

    # Load highlight overlay images (usually one shared image)
    highlight_cache = {}
    for row in key_rows:
        for key in row:
            for sl in key.get('selected_layers', []):
                ip = sl.get('image_path')
                if ip and ip not in highlight_cache:
                    full = os.path.join(theme_dir, ip)
                    if os.path.isfile(full):
                        try:
                            highlight_cache[ip] = pager.load_image(full)
                        except Exception:
                            highlight_cache[ip] = None

    # Input area config
    ia_x = input_area.get('x', 15)
    ia_y = input_area.get('y', 15)
    ia_max = input_area.get('max_characters', 24)

    buf = str(default or '')
    sel_row = 0
    sel_col = 0
    num_rows = len(key_rows)

    label_c = pager.rgb(180, 180, 180)
    val_c = pager.rgb(255, 220, 50)

    while True:
        # Draw background
        if bg_handle:
            pager.draw_image(0, 0, bg_handle)
        else:
            pager.clear(0)

        # Draw prompt + current text in the input area
        pager.draw_ttf(ia_x, ia_y, f'{prompt}: {buf}_',
                       val_c, FONT_MENU, 16)

        # Draw key highlight for current selection
        row_keys = key_rows[sel_row]
        if sel_col < len(row_keys):
            key = row_keys[sel_col]
            kx = key.get('x', 0)
            ky = key.get('y', 0)
            for sl in key.get('selected_layers', []):
                ip = sl.get('image_path')
                handle = highlight_cache.get(ip)
                if handle:
                    ox = sl.get('x', 0)
                    oy = sl.get('y', 0)
                    try:
                        pager.draw_image(kx + ox, ky + oy, handle)
                    except Exception:
                        pass

        pager.flip()

        # Input
        btn = wait_any_button(pager)
        if btn & pager.BTN_UP:
            sel_row = (sel_row - 1) % num_rows
            sel_col = min(sel_col, len(key_rows[sel_row]) - 1)
        elif btn & pager.BTN_DOWN:
            sel_row = (sel_row + 1) % num_rows
            sel_col = min(sel_col, len(key_rows[sel_row]) - 1)
        elif btn & pager.BTN_LEFT:
            num_cols = len(key_rows[sel_row])
            sel_col = (sel_col - 1) % num_cols
        elif btn & pager.BTN_RIGHT:
            num_cols = len(key_rows[sel_row])
            sel_col = (sel_col + 1) % num_cols
        elif btn & pager.BTN_A:
            key = key_rows[sel_row][sel_col]
            kid = key.get('id', '')
            if kid == 'done':
                return buf
            elif kid == 'backspace':
                buf = buf[:-1]
            elif kid == 'cancel':
                return None
            elif kid == 'space' or kid == ' ':
                if len(buf) < ia_max:
                    buf += ' '
            elif len(kid) >= 1 and kid not in ('shift', 'symbols', 'letters'):
                if len(buf) < ia_max:
                    buf += kid
        elif btn & pager.BTN_B:
            return None


def popup_menu(pager, title, items, bg_drawer=None):
    """Modal bordered picker. items is [(label, value), ...].
    Returns selected value or None."""
    fs = 18
    row_h = 22
    title_h = 26

    widest = max(pager.ttf_width(lbl, FONT_MENU, fs) for lbl, _ in items)
    widest = max(widest, pager.ttf_width(title, FONT_MENU, fs))
    box_w = min(SCREEN_W - 40, widest + 40)
    box_h = title_h + len(items) * row_h + 16
    box_h = min(box_h, SCREEN_H - 30)
    bx = (SCREEN_W - box_w) // 2
    by = (SCREEN_H - box_h) // 2

    edge = pager.rgb(100, 200, 255)
    title_c = edge
    sel_c = pager.rgb(255, 220, 50)
    norm_c = pager.rgb(200, 200, 200)

    selected = 0
    scroll = 0
    max_visible = max(1, (box_h - title_h - 16) // row_h)

    while True:
        if bg_drawer:
            bg_drawer()
        # Box background + border
        pager.fill_rect(bx, by, box_w, box_h, pager.rgb(0, 0, 0))
        pager.fill_rect(bx, by, box_w, 1, edge)
        pager.fill_rect(bx, by + box_h - 1, box_w, 1, edge)
        pager.fill_rect(bx, by, 1, box_h, edge)
        pager.fill_rect(bx + box_w - 1, by, 1, box_h, edge)

        tw = pager.ttf_width(title, FONT_MENU, fs)
        pager.draw_ttf(bx + (box_w - tw) // 2, by + 6, title, title_c, FONT_MENU, fs)
        pager.fill_rect(bx + 4, by + title_h, box_w - 8, 1, edge)

        if selected < scroll:
            scroll = selected
        elif selected >= scroll + max_visible:
            scroll = selected - max_visible + 1

        visible_end = min(scroll + max_visible, len(items))
        for draw_row, i in enumerate(range(scroll, visible_end)):
            label, _ = items[i]
            y = by + title_h + 8 + draw_row * row_h
            color = sel_c if i == selected else norm_c
            lw = pager.ttf_width(label, FONT_MENU, fs)
            pager.draw_ttf(bx + (box_w - lw) // 2, y, label, color, FONT_MENU, fs)

        pager.flip()

        btn = wait_any_button(pager)
        if btn & pager.BTN_UP:
            selected = (selected - 1) % len(items)
        elif btn & pager.BTN_DOWN:
            selected = (selected + 1) % len(items)
        elif btn & pager.BTN_A:
            return items[selected][1]
        elif btn & pager.BTN_B or btn & pager.BTN_POWER:
            return None
