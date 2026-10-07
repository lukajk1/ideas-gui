"""
A system-wide hotkey via Win32 RegisterHotKey, on a thread of its own with a
message loop. The hotkey can be changed at runtime with set().
"""
import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

WM_HOTKEY = 0x0312
WM_APP_SET = 0x8000 + 1
WM_QUIT = 0x0012
PM_NOREMOVE = 0
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
HOTKEY_ID = 1

MODIFIERS = {'ctrl': MOD_CONTROL, 'control': MOD_CONTROL, 'alt': MOD_ALT,
             'shift': MOD_SHIFT, 'win': MOD_WIN, 'meta': MOD_WIN}
NAMED_KEYS = {'space': 0x20, 'enter': 0x0D, 'tab': 0x09, 'insert': 0x2D, 'home': 0x24,
              'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22, 'pause': 0x13,
              '`': 0xC0, '-': 0xBD, '=': 0xBB, '[': 0xDB, ']': 0xDD, '\\': 0xDC,
              ';': 0xBA, "'": 0xDE, ',': 0xBC, '.': 0xBE, '/': 0xBF}

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def parse_combo(combo):
    """'Ctrl+Alt+I' -> (modifiers, virtual key). Raises ValueError."""
    parts = [p.strip() for p in combo.replace(' ', '').split('+') if p.strip()]
    if len(parts) < 2:
        raise ValueError('Use at least one modifier plus a key, like Ctrl+Alt+I.')
    mods = 0
    for p in parts[:-1]:
        if p.lower() not in MODIFIERS:
            raise ValueError(f"Unknown modifier '{p}'.")
        mods |= MODIFIERS[p.lower()]
    key = parts[-1]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.lower() in NAMED_KEYS:
        vk = NAMED_KEYS[key.lower()]
    elif key[:1].lower() == 'f' and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"Unsupported key '{key}'.")
    return mods, vk


class GlobalHotkey:
    def __init__(self, callback):
        self.callback = callback
        self._thread_id = None
        self._ready = threading.Event()
        self._request = None
        self._result = None
        self._done = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name='hotkey').start()
        self._ready.wait(2)

    def _loop(self):
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)  # creates the queue
        self._thread_id = kernel32.GetCurrentThreadId()
        self._ready.set()
        registered = None  # (mods, vk) currently held
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                threading.Thread(target=self.callback, daemon=True).start()
            elif msg.message == WM_APP_SET:
                previous = registered
                if registered:
                    user32.UnregisterHotKey(None, HOTKEY_ID)
                    registered = None
                error = None
                if self._request:
                    mods, vk = self._request
                    if user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
                        registered = self._request
                    else:
                        error = 'That shortcut is already taken by another program.'
                        # Keep the old hotkey working rather than ending up with none.
                        if previous and user32.RegisterHotKey(None, HOTKEY_ID, previous[0] | MOD_NOREPEAT, previous[1]):
                            registered = previous
                self._result = error
                self._done.set()
        if registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def set(self, combo):
        """Registers `combo` (or clears the hotkey if empty). Returns an error string or None."""
        try:
            self._request = parse_combo(combo) if combo else None
        except ValueError as e:
            return str(e)
        self._done.clear()
        user32.PostThreadMessageW(self._thread_id, WM_APP_SET, 0, 0)
        if not self._done.wait(2):
            return 'The hotkey thread did not respond.'
        return self._result

    def stop(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
