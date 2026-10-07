"""
Ideas desktop app.

    python main.pyw              # the app (a window, a tray icon, the quick-capture hotkey)
    python main.pyw --tray       # start hidden in the tray (used by "start with Windows")
    python main.pyw --browser    # serve the UI to your normal browser instead (development)

Only one copy runs at a time: launching it again shows the running one.
"""
import os
import sys
import json
import time
import argparse
import threading
import webbrowser
import urllib.request

from . import __version__, config
from .server import App, start_server, UI_DIR

ICON_PNG = os.path.join(UI_DIR, 'icon.png')


def setup_logging():
    # A windowed exe has no console; keep errors somewhere readable.
    if sys.stderr is None or sys.stdout is None:
        os.makedirs(config.APP_DIR, exist_ok=True)
        log = open(os.path.join(config.APP_DIR, 'gui.log'), 'a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def signal_running_instance(show):
    """If another copy is running, asks it to show itself (when `show`) and returns True."""
    try:
        with open(config.INSTANCE_FILE, encoding='utf-8') as f:
            port = json.load(f)['port']
        route = '/api/window/show' if show else '/api/version'
        req = urllib.request.Request(f'http://127.0.0.1:{port}{route}', data=b'{}' if show else None,
                                     headers={'X-Ideas': '1', 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def write_instance_file(port):
    os.makedirs(config.APP_DIR, exist_ok=True)
    with open(config.INSTANCE_FILE, 'w', encoding='utf-8') as f:
        json.dump({'port': port, 'pid': os.getpid()}, f)


def remove_instance_file():
    try:
        with open(config.INSTANCE_FILE, encoding='utf-8') as f:
            ours = json.load(f).get('pid') == os.getpid()
        if ours:
            os.remove(config.INSTANCE_FILE)
    except Exception:
        pass


def windows_dark_mode():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize')
        return winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] == 0
    except OSError:
        return False


class BrowserHost:
    """--browser mode: no windows to manage; file dialogs come from tkinter."""
    browser_mode = True

    def _tk(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        return root

    def pick_file(self, title, file_types):
        from tkinter import filedialog
        root = self._tk()
        types = [(t.split(' (')[0], t.split('(')[1].rstrip(')')) for t in file_types]
        path = filedialog.askopenfilename(title=title, filetypes=types)
        root.destroy()
        return path or None

    def pick_folder(self, title):
        from tkinter import filedialog
        root = self._tk()
        path = filedialog.askdirectory(title=title)
        root.destroy()
        return path or None

    def set_hotkey(self, combo):
        return None

    def show_main(self, select=None):
        pass

    def hide_capture(self):
        pass

    def quit(self):
        os._exit(0)


class DesktopHost:
    browser_mode = False

    def __init__(self):
        self.main = None
        self.capture = None
        self.tray = None
        self.hotkey = None
        self.quitting = False

    # -- windows --

    def run(self, url, start_hidden, debug=False):
        import webview

        bg = '#191816' if windows_dark_mode() else '#f6f1e9'
        self.main = webview.create_window(
            'Ideas', url, width=1320, height=840, min_size=(760, 480),
            hidden=start_hidden, background_color=bg)
        self.capture = webview.create_window(
            'Quick capture', url + '/capture', width=560, height=360, resizable=False,
            hidden=True, frameless=True, easy_drag=False, on_top=True, background_color=bg)

        self.main.events.closing += self._on_main_closing
        self.capture.events.closing += self._on_capture_closing

        self._start_tray()
        webview.start(self._on_started, private_mode=False,
                      storage_path=config.WEBVIEW_STORAGE, debug=debug)
        self._cleanup()

    def _on_started(self):
        from .hotkey import GlobalHotkey
        self.hotkey = GlobalHotkey(self.show_capture)
        combo = config.gui_settings().get('hotkey')
        if combo:
            error = self.hotkey.set(combo)
            if error:
                print(f'[hotkey] {combo}: {error}', file=sys.stderr)

    def _on_main_closing(self):
        if self.quitting:
            return True
        if config.gui_settings().get('close_to_tray') and self.tray:
            threading.Thread(target=self.main.hide, daemon=True).start()
            return False
        threading.Thread(target=self.quit, daemon=True).start()
        return False

    def _on_capture_closing(self):
        if self.quitting:
            return True
        threading.Thread(target=self.hide_capture, daemon=True).start()
        return False

    def _bring_to_front(self, window):
        try:
            import ctypes
            hwnd = int(window.native.Handle.ToInt64())
            user32 = ctypes.windll.user32
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            # pywebview focuses the browser control only the first time a window is
            # shown; after a hide/show, keys would go to the frame instead of the page.
            from System import Action
            form = window.native
            form.Invoke(Action(lambda: form.browser.webview.Focus()))
        except Exception as e:
            print(f'[focus] {e}', file=sys.stderr)

    def show_main(self, select=None):
        self.main.show()
        self._bring_to_front(self.main)
        if select:
            self.main.evaluate_js(f'window.selectIdea && window.selectIdea({json.dumps(select)})')

    def show_capture(self):
        self.capture.evaluate_js('window.openCapture && window.openCapture()')
        self.capture.show()
        self._bring_to_front(self.capture)
        self.capture.evaluate_js('window.focusCapture && window.focusCapture()')

    def hide_capture(self):
        self.capture.hide()
        # Let the main window pick up the new idea straight away.
        self.main.evaluate_js('window.refreshSoon && window.refreshSoon()')

    def pick_file(self, title, file_types):
        import webview
        result = self.main.create_file_dialog(webview.FileDialog.OPEN, file_types=file_types)
        return result[0] if result else None

    def pick_folder(self, title):
        import webview
        result = self.main.create_file_dialog(webview.FileDialog.FOLDER)
        return result[0] if result else None

    def set_hotkey(self, combo):
        return self.hotkey.set(combo) if self.hotkey else None

    def quit(self):
        self.quitting = True
        for w in (self.capture, self.main):
            try:
                w.destroy()
            except Exception:
                pass

    def _cleanup(self):
        if self.hotkey:
            self.hotkey.stop()
        if self.tray:
            self.tray.stop()

    # -- tray --

    def _start_tray(self):
        try:
            import pystray
            from PIL import Image
        except ImportError:
            print('[tray] pystray/Pillow missing; no tray icon.', file=sys.stderr)
            return

        def hotkey_label(_):
            combo = config.gui_settings().get('hotkey')
            return f'Quick capture\t{combo}' if combo else 'Quick capture'

        menu = pystray.Menu(
            pystray.MenuItem('Open Ideas', lambda: self.show_main(), default=True),
            pystray.MenuItem(hotkey_label, lambda: self.show_capture()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Quit', lambda: self.quit()),
        )
        self.tray = pystray.Icon('ideas', Image.open(ICON_PNG), 'Ideas', menu)
        self.tray.run_detached()


def main():
    parser = argparse.ArgumentParser(description='Ideas')
    parser.add_argument('--tray', action='store_true', help='start hidden in the tray')
    parser.add_argument('--browser', action='store_true', help='use your web browser instead of a window')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-open', action='store_true', help='with --browser: do not open a browser tab')
    parser.add_argument('--debug', action='store_true', help='enable webview dev tools')
    parser.add_argument('--file', help='use this ideas file for this run (settings are unchanged)')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args()
    setup_logging()

    if not args.browser and signal_running_instance(show=not args.tray):
        return

    host = BrowserHost() if args.browser else DesktopHost()
    app = App(host, args.file)
    server = start_server(app, args.port)
    port = server.server_address[1]
    url = f'http://127.0.0.1:{port}'

    if args.browser:
        print(f'Ideas running at {url}  (Ctrl+C to stop)')
        if not args.no_open:
            webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return

    write_instance_file(port)
    try:
        host.run(url, start_hidden=args.tray, debug=args.debug)
    finally:
        remove_instance_file()
        server.shutdown()
