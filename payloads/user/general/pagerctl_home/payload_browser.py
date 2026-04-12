"""payload_browser.py - Dynamic payload browser for pagerctl_home.

Scans the payloads/ directory for launch scripts organized by category.
Each script has metadata comments:
  # Title: Payload Name
  # Requires: /path/to/installed/payload
  # Category: Category Name

Only shows payloads where the Requires path exists (installed).
"""

import os
import re


PAYLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'payloads')


class PayloadInfo:
    """Parsed metadata from a launch script."""
    __slots__ = ('title', 'requires', 'category', 'script_path')

    def __init__(self, title, requires, category, script_path):
        self.title = title
        self.requires = requires
        self.category = category
        self.script_path = script_path

    def is_installed(self):
        """Check if the payload is installed on this device."""
        return os.path.isdir(self.requires)


def parse_script(path):
    """Parse a launch script's metadata comments."""
    title = requires = category = ''
    try:
        with open(path) as f:
            for line in f:
                if not line.startswith('#'):
                    if title:
                        break
                    continue
                if line.startswith('# Title:'):
                    title = line[8:].strip()
                elif line.startswith('# Requires:'):
                    requires = line[11:].strip()
                elif line.startswith('# Category:'):
                    category = line[11:].strip()
    except Exception:
        pass
    if title and requires:
        return PayloadInfo(title, requires, category, path)
    return None


def scan_categories():
    """Scan the payloads/ directory and return categories with installed payloads.

    Returns:
        list of (category_name, [PayloadInfo, ...]) sorted alphabetically.
        Only categories with at least one installed payload are included.
    """
    if not os.path.isdir(PAYLOADS_DIR):
        return []

    categories = []
    for cat_name in sorted(os.listdir(PAYLOADS_DIR)):
        cat_path = os.path.join(PAYLOADS_DIR, cat_name)
        if not os.path.isdir(cat_path):
            continue

        installed = []
        for fname in sorted(os.listdir(cat_path)):
            if not fname.endswith('.sh'):
                continue
            info = parse_script(os.path.join(cat_path, fname))
            if info and info.is_installed():
                installed.append(info)

        if installed:
            # Capitalize category name for display
            display_name = cat_name.replace('_', ' ').title()
            categories.append((display_name, installed))

    return categories


def find_payload(name):
    """Find a payload by title (case-insensitive). Returns PayloadInfo or None."""
    name_lower = name.lower()
    if not os.path.isdir(PAYLOADS_DIR):
        return None
    for cat_name in os.listdir(PAYLOADS_DIR):
        cat_path = os.path.join(PAYLOADS_DIR, cat_name)
        if not os.path.isdir(cat_path):
            continue
        for fname in os.listdir(cat_path):
            if not fname.endswith('.sh'):
                continue
            info = parse_script(os.path.join(cat_path, fname))
            if info and info.title.lower() == name_lower:
                return info
    return None
