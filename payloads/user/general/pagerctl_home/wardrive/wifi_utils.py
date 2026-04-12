"""wifi_utils.py - WiFi scan / connect / hotspot helpers.

The pager comes with pre-configured uci wireless entries:
    wireless.wlan0cli   = STA (client)   on phy0 (2.4 GHz)
    wireless.wlan0mon   = monitor        on phy0
    wireless.wlan0wpa   = WPA2 AP        on phy0 (disabled by default)
    wireless.wlan0open  = open AP        on phy0 (disabled by default)

We reuse those rather than creating new ones so we don't fight the
firmware's preset layout. Switching 'client connection' is a matter of
updating wireless.wlan0cli's ssid + key; toggling the hotspot is a
matter of enabling/disabling wireless.wlan0wpa and setting its ssid/key.
"""

import os
import subprocess


CLIENT_IFACE = 'wlan0cli'         # connects as a client to external APs
HOTSPOT_IFACE = 'wlan0wpa'        # acts as an AP for phones to join
SCAN_IFACE = 'wlan0cli'           # we scan from the managed client iface


def _uci(*args):
    """Run uci with the given args, return stdout or ''. Silent on error."""
    try:
        r = subprocess.run(['uci'] + list(args),
                            capture_output=True, text=True, timeout=5)
        return r.stdout
    except Exception:
        return ''


def _uci_set(key, value):
    _uci('set', f'{key}={value}')


def _uci_commit(package='wireless'):
    _uci('commit', package)


def _wifi_reload():
    try:
        subprocess.run(['wifi', 'reload'], capture_output=True, timeout=15)
    except Exception:
        pass


# ----------------------------------------------------------------------
# Client (STA) mode
# ----------------------------------------------------------------------

def get_client_ssid():
    """Return the SSID the pager is currently configured to join, or ''."""
    raw = _uci('get', f'wireless.{CLIENT_IFACE}.ssid').strip()
    return raw.strip("'")


def get_client_status():
    """Try `iw dev wlanXcli link` to return the SSID we're actually
    associated with (or '' if not associated)."""
    try:
        r = subprocess.run(['iw', 'dev', CLIENT_IFACE, 'link'],
                            capture_output=True, text=True, timeout=3)
        for line in r.stdout.split('\n'):
            line = line.strip()
            if line.lower().startswith('ssid:'):
                return line.split(None, 1)[1].strip()
    except Exception:
        pass
    return ''


def connect_network(ssid, password, encryption='psk2'):
    """Configure wlan0cli for the given network and reload WiFi.

    encryption: 'psk2' (WPA2), 'psk' (WPA), or 'none'.
    Returns True if uci commands succeeded (doesn't verify connection).
    """
    if not ssid:
        return False
    _uci_set(f'wireless.{CLIENT_IFACE}.ssid', f"'{ssid}'")
    if password:
        _uci_set(f'wireless.{CLIENT_IFACE}.encryption', f"'{encryption}'")
        _uci_set(f'wireless.{CLIENT_IFACE}.key', f"'{password}'")
    else:
        _uci_set(f'wireless.{CLIENT_IFACE}.encryption', "'none'")
        _uci('delete', f'wireless.{CLIENT_IFACE}.key')
    _uci_set(f'wireless.{CLIENT_IFACE}.disabled', "'0'")
    _uci_commit('wireless')
    _wifi_reload()
    return True


# ----------------------------------------------------------------------
# Scan
# ----------------------------------------------------------------------

def scan_networks():
    """Return a list of dicts: [{'ssid': 'x', 'signal': -60, 'enc': 'wpa2'}, ...]
    sorted by signal strength (strongest first). Hidden SSIDs are skipped.
    Runs `iw dev <iface> scan` which requires the client interface to
    be up in managed mode (it normally is on this platform)."""
    results = []
    try:
        r = subprocess.run(['iw', 'dev', SCAN_IFACE, 'scan'],
                            capture_output=True, text=True, timeout=12)
    except Exception:
        return results
    cur = None
    for line in r.stdout.split('\n'):
        stripped = line.strip()
        if line.startswith('BSS '):
            if cur and cur.get('ssid'):
                results.append(cur)
            cur = {'ssid': '', 'signal': -100, 'enc': 'open'}
        elif cur is None:
            continue
        elif stripped.startswith('signal:'):
            try:
                # e.g. "signal: -48.00 dBm"
                cur['signal'] = int(float(stripped.split()[1]))
            except Exception:
                pass
        elif stripped.startswith('SSID:'):
            cur['ssid'] = stripped.split(':', 1)[1].strip()
        elif 'WPA' in stripped and 'version' in stripped.lower():
            cur['enc'] = 'wpa'
        elif 'RSN' in stripped and 'version' in stripped.lower():
            cur['enc'] = 'wpa2'
        elif stripped.startswith('capability:') and 'Privacy' in stripped:
            if cur.get('enc') == 'open':
                cur['enc'] = 'wep'
    if cur and cur.get('ssid'):
        results.append(cur)
    # Deduplicate by SSID, keep strongest
    by_ssid = {}
    for ap in results:
        prev = by_ssid.get(ap['ssid'])
        if prev is None or ap['signal'] > prev['signal']:
            by_ssid[ap['ssid']] = ap
    return sorted(by_ssid.values(), key=lambda x: x['signal'], reverse=True)


# ----------------------------------------------------------------------
# Hotspot (AP) mode
# ----------------------------------------------------------------------

def get_hotspot_state():
    """Return (enabled_bool, ssid_str, key_str)."""
    dis = _uci('get', f'wireless.{HOTSPOT_IFACE}.disabled').strip().strip("'")
    ssid = _uci('get', f'wireless.{HOTSPOT_IFACE}.ssid').strip().strip("'")
    key = _uci('get', f'wireless.{HOTSPOT_IFACE}.key').strip().strip("'")
    return (dis == '0', ssid, key)


def set_hotspot(enabled, ssid=None, password=None):
    """Enable/disable the hotspot AP interface. Optionally update
    ssid + key. Runs `wifi reload` on success."""
    if ssid:
        _uci_set(f'wireless.{HOTSPOT_IFACE}.ssid', f"'{ssid}'")
    if password is not None:
        _uci_set(f'wireless.{HOTSPOT_IFACE}.encryption', "'psk2'")
        _uci_set(f'wireless.{HOTSPOT_IFACE}.key', f"'{password}'")
    _uci_set(f'wireless.{HOTSPOT_IFACE}.mode', "'ap'")
    _uci_set(f'wireless.{HOTSPOT_IFACE}.disabled',
             "'0'" if enabled else "'1'")
    _uci_commit('wireless')
    _wifi_reload()
    return True
