"""
    python tests/test_store.py        # or: python -m pytest tests

Set IDEAS_MD to also import a real markdown file (it is only read).
"""
import os
import sys
import json
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ideas_gui.store import Store, Conflict, Corrupt, StoreError, create_file, decode
from ideas_gui.importer import parse_markdown

SAMPLE_MD = """# My Ideas Ledger

- ab1 first idea
Tags: game, weekend
Date: 2026-05-01

Description:
Some text.
- a bullet that looks a bit like a header

---

* cd2 second idea
Tags:game

Description:
Enter your details here...

---

# ef3 built thing
Tags: web
Date: 2026-04-22
Link: https://example.com

Description:
Line one.

Line three.

---
"""


class ImportTest(unittest.TestCase):
    def test_parse_markdown(self):
        ideas, notes = parse_markdown(SAMPLE_MD)
        self.assertEqual([i['id'] for i in ideas], ['ab1', 'cd2', 'ef3'])
        self.assertEqual([i['status'] for i in ideas], ['normal', 'promising', 'built'])
        self.assertEqual(ideas[0]['tags'], ['game', 'weekend'])
        self.assertEqual(ideas[0]['created'], '2026-05-01')
        self.assertIn('a bullet', ideas[0]['description'])
        self.assertEqual(ideas[1]['tags'], ['game'])
        self.assertEqual(ideas[1]['description'], '')  # the CLI's template text is dropped
        self.assertEqual(ideas[2]['description'], 'Link: https://example.com\n\nLine one.\n\nLine three.')
        self.assertEqual(notes, [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'ideas.json')
        create_file(self.path, parse_markdown(SAMPLE_MD)[0])
        self.store = Store(self.path, os.path.join(self.dir, 'backups'))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def get(self, idea_id):
        return next(i for i in self.store.snapshot()['ideas'] if i['id'] == idea_id)

    def test_create_appends(self):
        idea = self.store.create({'title': '  new   thing ', 'tags': 'web, Web, app'})
        self.assertEqual(idea['title'], 'new thing')
        self.assertEqual(idea['tags'], ['web', 'app'])
        self.assertEqual(idea['index'], 3)
        self.assertTrue(idea['created'])
        with self.assertRaises(StoreError):
            self.store.create({'title': '   '})

    def test_update_and_conflict(self):
        before = self.get('ab1')
        after = self.store.update('ab1', before['rev'], {'status': 'wip', 'description': 'a\n---\nb'})
        self.assertEqual(after['status'], 'wip')
        self.assertTrue(after['updated'])
        self.assertNotEqual(after['rev'], before['rev'])
        with self.assertRaises(Conflict):
            self.store.update('ab1', before['rev'], {'title': 'stale edit'})
        self.assertEqual(len(self.store.read()), 3)

    def test_noop_update_does_not_write(self):
        idea = self.get('ab1')
        mtime = os.path.getmtime(self.path)
        self.store.update('ab1', idea['rev'], {'title': idea['title']})
        self.assertEqual(os.path.getmtime(self.path), mtime)

    def test_delete_and_undo(self):
        with open(self.path, encoding='utf-8') as f:
            original = f.read()
        token, _ = self.store.delete('cd2', self.get('cd2')['rev'])
        self.assertEqual([i['id'] for i in self.store.read()], ['ab1', 'ef3'])
        self.store.undo_delete(token)
        with open(self.path, encoding='utf-8') as f:
            self.assertEqual(f.read(), original)

    def test_rename_merge_and_remove_tag(self):
        self.assertEqual(self.store.rename_tag('weekend', 'game'), 1)
        self.assertEqual(self.get('ab1')['tags'], ['game'])
        self.assertEqual(self.store.rename_tag('WEB', ''), 1)
        self.assertEqual(self.get('ef3')['tags'], [])

    def test_corrupt_file_is_never_written(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('{"ideas": [ {"id": "ab1", ')
        with self.assertRaises(Corrupt):
            self.store.create({'title': 'x'})
        with open(self.path, encoding='utf-8') as f:
            self.assertTrue(f.read().endswith('"ab1", '))

    def test_no_temp_files_left(self):
        self.store.create({'title': 'x'})
        self.assertEqual(sorted(os.listdir(self.dir)), ['backups', 'ideas.json'])

    def test_unknown_fields_survive(self):
        with open(self.path, encoding='utf-8') as f:
            data = json.load(f)
        data['ideas'][0]['links'] = ['https://example.com']
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        self.store.update('ab1', self.get('ab1')['rev'], {'title': 'renamed'})
        self.assertEqual(self.store.read()[0]['links'], ['https://example.com'])

    def test_line_of(self):
        with open(self.path, encoding='utf-8') as f:
            lines = f.read().split('\n')
        self.assertIn('"cd2"', lines[self.store.line_of('cd2') - 1])

    def test_create_file_refuses_to_overwrite(self):
        with self.assertRaises(StoreError):
            create_file(self.path)


@unittest.skipUnless(os.environ.get('IDEAS_MD'), 'set IDEAS_MD to import a real file')
class RealFileTest(unittest.TestCase):
    def test_real_import(self):
        with open(os.environ['IDEAS_MD'], encoding='utf-8') as f:
            ideas, notes = parse_markdown(f.read())
        from ideas_gui.store import encode
        self.assertEqual(decode(encode(ideas)), ideas)
        print(f'\n{len(ideas)} ideas parse cleanly; notes: {notes}')


if __name__ == '__main__':
    unittest.main()
