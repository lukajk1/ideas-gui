"""
The local HTTP server the UI talks to. It listens on 127.0.0.1 only, on a
random port, and lives as long as the app does. Window-level actions (file
dialogs, showing/hiding windows, the hotkey) are delegated to a `host`, which
is the desktop app, or a stub in --browser mode.
"""
import os
import json
import mimetypes
import traceback
import subprocess
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

from . import __version__, config
from .store import Store, StoreError, Conflict, Corrupt, STATUSES, create_file
from .importer import import_file

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui')


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class App:
    """Everything the API needs: the current store plus the host for window actions."""

    def __init__(self, host, data_file=None):
        self.host = host
        self.store = None
        self.open_file(data_file or config.data_file())

    def open_file(self, path):
        self.store = Store(path, config.BACKUP_DIR) if path and os.path.isfile(path) else None

    def require_store(self):
        if not self.store:
            raise ApiError('No ideas file is set up yet.', 409)
        return self.store

    # -- routes --

    def get(self, route):
        if route == '/api/state':
            settings = config.gui_settings()
            base = {'appVersion': __version__, 'statuses': STATUSES, 'settings': settings,
                    'editor': os.path.basename(config.editor_exe()), 'browserMode': self.host.browser_mode}
            if not self.store:
                return {**base, 'needsSetup': True, 'path': config.data_file()}
            try:
                return {**base, **self.store.snapshot()}
            except Corrupt as e:
                # Shown as a blocking screen; nothing is ever written to a file that won't load.
                return {**base, 'needsSetup': True, 'loadError': str(e), 'path': self.store.path}
        if route == '/api/version':
            return {'version': self.store.version() if self.store else None}
        raise ApiError('Not found', 404)

    def post(self, route, data):
        if route == '/api/ideas/create':
            store = self.require_store()
            idea = store.create(data.get('fields') or {})
            return {'idea': idea, 'version': store.version()}
        if route == '/api/ideas/update':
            store = self.require_store()
            idea = store.update(data.get('id'), data.get('rev'), data.get('fields') or {})
            return {'idea': idea, 'version': store.version()}
        if route == '/api/ideas/delete':
            store = self.require_store()
            token, idea = store.delete(data.get('id'), data.get('rev'))
            return {'token': token, 'title': idea['title'], 'version': store.version()}
        if route == '/api/ideas/undo':
            store = self.require_store()
            idea = store.undo_delete(data.get('token'))
            return {'idea': idea, 'version': store.version()}
        if route == '/api/tags/rename':
            store = self.require_store()
            changed = store.rename_tag(data.get('old'), data.get('new'))
            return {'changed': changed, 'version': store.version()}

        if route == '/api/open/editor':
            store = self.require_store()
            line = store.line_of(data['id']) if data.get('id') else 1
            exe = config.editor_exe()
            args = [exe, f'-n{line}', store.path] if exe.lower().endswith('notepad++.exe') else [exe, store.path]
            subprocess.Popen(args)
            return {}
        if route == '/api/open/folder':
            which = data.get('which')
            if which == 'backups':
                os.makedirs(config.BACKUP_DIR, exist_ok=True)
                folder = config.BACKUP_DIR
            elif which == 'config':
                folder = config.APP_DIR
            else:
                folder = os.path.dirname(self.require_store().path)
            os.startfile(folder)
            return {}

        if route == '/api/file/choose':
            mode, note = data.get('mode'), None
            if mode == 'existing':
                path = self.host.pick_file('Open an ideas file', ('Ideas file (*.json)', 'All files (*.*)'))
                if path:
                    Store(path, config.BACKUP_DIR).read()  # refuse files that won't load
            elif mode == 'new':
                folder = self.host.pick_folder('Folder to create ideas.json in')
                path = os.path.join(folder, 'ideas.json') if folder else None
                if path:
                    create_file(path)
            elif mode == 'import':
                md = self.host.pick_file('Markdown ideas file to import (it is only read)',
                                         ('Markdown (*.md)', 'All files (*.*)'))
                path = os.path.join(os.path.dirname(md), 'ideas.json') if md else None
                if path:
                    ideas, notes = import_file(md, path)
                    note = f'Imported {len(ideas)} ideas.' + (' ' + ' '.join(notes) if notes else '')
            else:
                raise ApiError(f'Unknown mode {mode!r}')
            if not path:
                return {'cancelled': True}
            config.set_data_file(path)
            self.open_file(path)
            return {'path': path, 'note': note}

        if route == '/api/settings':
            changes = data.get('settings') or {}
            if 'hotkey' in changes:
                error = self.host.set_hotkey(changes['hotkey'])
                if error:
                    raise ApiError(error)
            return {'settings': config.update_gui_settings(changes)}

        if route == '/api/window/show':
            self.host.show_main(data.get('select'))
            return {}
        if route == '/api/window/capture-done':
            self.host.hide_capture()
            return {}
        if route == '/api/window/quit':
            self.host.quit()
            return {}
        raise ApiError('Not found', 404)


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, ctype):
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status, obj):
            self.send(status, json.dumps(obj).encode('utf-8'), 'application/json; charset=utf-8')

        def send_file(self, relpath):
            full = os.path.abspath(os.path.join(UI_DIR, unquote(relpath)))
            if not full.startswith(UI_DIR + os.sep) or not os.path.isfile(full):
                return self.send(404, b'Not found', 'text/plain')
            ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
            if ctype.startswith('text/') or ctype == 'application/javascript':
                ctype += '; charset=utf-8'
            with open(full, 'rb') as f:
                self.send(200, f.read(), ctype)

        def local_host_ok(self):
            # Rejects DNS-rebinding requests that reach us under another name.
            host = (self.headers.get('Host') or '').split(':')[0]
            return host in ('127.0.0.1', 'localhost')

        def handle_api(self, fn):
            try:
                self.send_json(200, fn())
            except Conflict as e:
                self.send_json(409, {'error': str(e), 'conflict': True, 'idea': e.idea})
            except (ApiError, StoreError) as e:
                self.send_json(e.status, {'error': str(e)})
            except Exception as e:
                traceback.print_exc()
                self.send_json(500, {'error': f'{type(e).__name__}: {e}'})

        def do_GET(self):
            if not self.local_host_ok():
                return self.send(403, b'Forbidden', 'text/plain')
            path = urlparse(self.path).path
            if path == '/':
                return self.send_file('index.html')
            if path == '/capture':
                return self.send_file('capture.html')
            if path.startswith('/ui/'):
                return self.send_file(path[len('/ui/'):])
            if path.startswith('/api/'):
                return self.handle_api(lambda: app.get(path))
            self.send(404, b'Not found', 'text/plain')

        def do_POST(self):
            # A custom header forces a CORS preflight, so web pages can't POST here.
            if not self.local_host_ok() or self.headers.get('X-Ideas') != '1':
                return self.send_json(403, {'error': 'Forbidden'})
            length = int(self.headers.get('Content-Length') or 0)
            try:
                data = json.loads(self.rfile.read(length) or b'{}')
            except json.JSONDecodeError:
                return self.send_json(400, {'error': 'Invalid JSON'})
            path = urlparse(self.path).path
            self.handle_api(lambda: app.post(path, data))

    return Handler


def start_server(app, port=0):
    import threading
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(app))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
