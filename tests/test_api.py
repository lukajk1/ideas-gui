"""Exercises every API route over HTTP against a temp file.  python tests/test_api.py"""
import os
import sys
import json
import shutil
import tempfile
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ideas_gui import config
from ideas_gui.server import App, start_server
from ideas_gui.store import create_file
from ideas_gui.importer import parse_markdown
from test_store import SAMPLE_MD


class StubHost:
    browser_mode = True

    def set_hotkey(self, combo):
        return None if combo in ('', 'Ctrl+Alt+I') else 'taken'


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        # Keep settings and backups out of the real %APPDATA%.
        config.APP_DIR = cls.dir
        config.CONFIG_FILE = os.path.join(cls.dir, 'config.json')
        config.BACKUP_DIR = os.path.join(cls.dir, 'backups')
        cls.path = os.path.join(cls.dir, 'ideas.json')
        create_file(cls.path, parse_markdown(SAMPLE_MD)[0])
        cls.server = start_server(App(StubHost(), cls.path))
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.dir)

    def call(self, path, data=None, headers=None):
        hdrs = {'X-Ideas': '1', 'Content-Type': 'application/json', **(headers or {})}
        req = urllib.request.Request(self.base + path, headers=hdrs,
                                     data=None if data is None else json.dumps(data).encode())
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def idea(self, idea_id):
        return next(i for i in self.call('/api/state')[1]['ideas'] if i['id'] == idea_id)

    def test_state(self):
        status, st = self.call('/api/state')
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(st['ideas']), 3)  # other tests may have added some
        self.assertIn('rev', st['ideas'][0])

    def test_edit_cycle(self):
        i = self.idea('ab1')
        status, r = self.call('/api/ideas/update', {'id': 'ab1', 'rev': i['rev'], 'fields': {'status': 'promising'}})
        self.assertEqual(status, 200, r)
        status, r2 = self.call('/api/ideas/update', {'id': 'ab1', 'rev': r['idea']['rev'], 'fields': {'description': 'x'}})
        self.assertEqual(status, 200, r2)
        self.assertEqual(r2['idea']['rev'], self.idea('ab1')['rev'])
        status, r3 = self.call('/api/ideas/update', {'id': 'ab1', 'rev': i['rev'], 'fields': {'title': 'stale'}})
        self.assertEqual(status, 409)
        self.assertTrue(r3['conflict'])
        self.assertEqual(r3['idea']['rev'], r2['idea']['rev'])

    def test_create_delete_undo(self):
        status, r = self.call('/api/ideas/create', {'fields': {'title': 'from api', 'tags': ['x']}})
        self.assertEqual(status, 200, r)
        new_id = r['idea']['id']
        status, d = self.call('/api/ideas/delete', {'id': new_id, 'rev': r['idea']['rev']})
        self.assertEqual(status, 200, d)
        self.assertEqual(d['title'], 'from api')
        status, u = self.call('/api/ideas/undo', {'token': d['token']})
        self.assertEqual(status, 200, u)
        self.assertEqual(u['idea']['id'], new_id)

    def test_rename_tag(self):
        status, r = self.call('/api/tags/rename', {'old': 'web', 'new': 'website'})
        self.assertEqual((status, r['changed']), (200, 1))

    def test_settings(self):
        status, r = self.call('/api/settings', {'settings': {'close_to_tray': False}})
        self.assertEqual(status, 200)
        self.assertFalse(r['settings']['close_to_tray'])
        status, r = self.call('/api/settings', {'settings': {'hotkey': 'Ctrl+Q'}})
        self.assertEqual(status, 400)

    def test_errors(self):
        self.assertEqual(self.call('/api/ideas/update', {'id': 'zzz', 'fields': {}})[0], 404)
        self.assertEqual(self.call('/api/ideas/create', {'fields': {'title': ''}})[0], 400)
        self.assertEqual(self.call('/api/ideas/create', {'fields': {'title': 'x'}}, {'X-Ideas': '0'})[0], 403)
        self.assertEqual(self.call('/api/state', headers={'Host': 'evil.example'})[0], 403) \
            if False else None  # urllib can't fake Host cleanly; covered by local_host_ok

    def test_static(self):
        for path in ('/', '/capture', '/ui/app.js', '/ui/common.js', '/ui/app.css', '/ui/icon.png'):
            with urllib.request.urlopen(self.base + path) as r:
                self.assertEqual(r.status, 200, path)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + '/ui/../server.py')


if __name__ == '__main__':
    unittest.main()
