"""
Settings, in %APPDATA%\\ideas-gui\\config.json. Separate from the ideas CLI's
own config, so neither app can repoint the other.
"""
import os
import json
import shutil
import threading

APP_DIR = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'ideas-gui')
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')
BACKUP_DIR = os.path.join(APP_DIR, 'backups')
INSTANCE_FILE = os.path.join(APP_DIR, 'instance.json')
WEBVIEW_STORAGE = os.path.join(os.environ.get('LOCALAPPDATA') or APP_DIR, 'ideas-gui', 'webview')

DEFAULTS = {
    'hotkey': 'Ctrl+Alt+I',   # quick capture; '' turns it off
    'close_to_tray': True,
    'editor': '',             # '' = Notepad++ if installed, else Notepad
}

NOTEPADPP_PATHS = [
    r'C:\Program Files\Notepad++\notepad++.exe',
    r'C:\Program Files (x86)\Notepad++\notepad++.exe',
]

_lock = threading.Lock()


def load():
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(config):
    with _lock:
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = CONFIG_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
        os.replace(tmp, CONFIG_FILE)


def data_file():
    return load().get('data_file') or ''


def set_data_file(path):
    config = load()
    config['data_file'] = os.path.abspath(path).replace('\\', '/')
    save(config)


def gui_settings():
    return {**DEFAULTS, **load().get('settings', {})}


def update_gui_settings(changes):
    config = load()
    settings = {**DEFAULTS, **config.get('settings', {})}
    for key, value in changes.items():
        if key in DEFAULTS:
            settings[key] = value
    config['settings'] = settings
    save(config)
    return settings


def editor_exe():
    custom = gui_settings().get('editor')
    if custom and os.path.isfile(custom):
        return custom
    for path in NOTEPADPP_PATHS:
        if os.path.isfile(path):
            return path
    return shutil.which('notepad++') or 'notepad.exe'
