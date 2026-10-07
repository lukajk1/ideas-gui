"""
The ideas file: a single JSON document, kept small and human-readable.

    {
      "format": "ideas-gui",
      "schema": 1,
      "ideas": [
        {"id": "k3t", "status": "promising", "title": "...", "tags": ["game"],
         "created": "2026-05-01", "updated": "2026-10-07T14:03:11", "description": "..."}
      ]
    }

Saves are crash-safe: the new content is written to a temp file next to the
real one, flushed to disk, read back and checked, and only then swapped in with
an atomic rename. A file that fails to load is never written over. Each idea
has a `rev` (a hash of its content) so an edit made against an idea that has
since changed on disk (Drive sync, another machine) is refused, not merged
blindly.
"""
import os
import json
import time
import random
import string
import hashlib
import threading
from datetime import datetime

FORMAT = 'ideas-gui'
SCHEMA = 1
STATUSES = ['normal', 'promising', 'meh', 'wip', 'built']
FIELDS = ('id', 'status', 'title', 'tags', 'created', 'updated', 'description')

BACKUP_EVERY_SECONDS = 10 * 60
BACKUPS_KEPT = 50


class StoreError(Exception):
    status = 400


class NotFound(StoreError):
    status = 404


class Corrupt(StoreError):
    status = 500


class Conflict(StoreError):
    """The idea on disk is not the version the caller started editing."""
    status = 409

    def __init__(self, message, idea=None):
        super().__init__(message)
        self.idea = idea


def rev(idea):
    return hashlib.sha1(json.dumps(idea, sort_keys=True).encode('utf-8')).hexdigest()[:12]


def with_meta(idea, index):
    return {**idea, 'rev': rev(idea), 'index': index}


# ---------------------------------------------------------------------------
# Field cleaning
# ---------------------------------------------------------------------------

def clean_tags(value):
    if isinstance(value, str):
        value = value.split(',')
    seen, tags = set(), []
    for t in value or []:
        t = ' '.join(str(t).replace(',', ' ').split())
        if t and t.lower() not in seen:
            seen.add(t.lower())
            tags.append(t)
    return tags


def clean_title(title):
    title = ' '.join(str(title or '').split())
    if not title:
        raise StoreError('Title cannot be empty.')
    return title


def clean_date(value):
    value = str(value or '').strip()
    if value:
        try:
            datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            raise StoreError(f"Date must look like YYYY-MM-DD, got '{value}'.")
    return value


def clean_description(text):
    lines = str(text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    return '\n'.join(l.rstrip() for l in lines).strip('\n')


def apply_fields(idea, fields):
    if 'title' in fields:
        idea['title'] = clean_title(fields['title'])
    if 'status' in fields:
        if fields['status'] not in STATUSES:
            raise StoreError(f"Unknown status '{fields['status']}'.")
        idea['status'] = fields['status']
    if 'tags' in fields:
        idea['tags'] = clean_tags(fields['tags'])
    if 'created' in fields:
        idea['created'] = clean_date(fields['created'])
    if 'description' in fields:
        idea['description'] = clean_description(fields['description'])


def normalize(raw):
    """One idea as loaded from disk -> the canonical shape, tolerating missing fields."""
    if not isinstance(raw, dict) or not isinstance(raw.get('id'), str) or not raw['id']:
        raise Corrupt(f'An idea is missing its id: {str(raw)[:80]}')
    status = raw.get('status')
    return {
        'id': raw['id'],
        'status': status if status in STATUSES else 'normal',
        'title': str(raw.get('title') or '(untitled)'),
        'tags': clean_tags(raw.get('tags')),
        'created': str(raw.get('created') or ''),
        'updated': str(raw.get('updated') or ''),
        'description': str(raw.get('description') or ''),
        **{k: v for k, v in raw.items() if k not in FIELDS},  # keep fields from newer versions
    }


def decode(text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise Corrupt(f'The ideas file is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}')
    if not isinstance(data, dict) or not isinstance(data.get('ideas'), list):
        raise Corrupt('The ideas file has no "ideas" list.')
    if data.get('schema', 1) > SCHEMA:
        raise Corrupt(f"This file was written by a newer version of Ideas (schema {data['schema']}).")
    ideas = [normalize(i) for i in data['ideas']]
    ids = [i['id'] for i in ideas]
    if len(ids) != len(set(ids)):
        raise Corrupt('The ideas file contains duplicate ids.')
    return ideas


def encode(ideas):
    data = {'format': FORMAT, 'schema': SCHEMA, 'ideas': ideas}
    return json.dumps(data, indent=2, ensure_ascii=False) + '\n'


def new_id(ideas):
    taken = {i['id'] for i in ideas}
    chars = string.ascii_lowercase + string.digits
    while True:
        candidate = ''.join(random.choice(chars) for _ in range(3))
        if candidate not in taken:
            return candidate


def now_iso():
    return datetime.now().isoformat(timespec='seconds')


def create_file(path, ideas=()):
    if os.path.exists(path):
        raise StoreError(f'{os.path.basename(path)} already exists there.')
    atomic_write(path, encode(list(ideas)))


def atomic_write(path, text):
    data = text.encode('utf-8')
    tmp = f'{path}.{os.getpid()}.tmp'
    try:
        with open(tmp, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        with open(tmp, 'rb') as f:  # read back before it replaces anything
            if f.read() != data:
                raise StoreError('Saving failed: the temp file did not read back correctly.')
        # Drive sync or an editor can hold the file for a moment.
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise StoreError('Saving failed: the ideas file is locked by another program.')
                time.sleep(0.1)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

class Store:
    def __init__(self, path, backup_dir):
        self.path = path
        self.backup_dir = backup_dir
        self.lock = threading.RLock()
        self._cache = None  # (version, ideas)
        self._last_backup = 0.0
        self._undo = {}  # token -> (index, idea)

    def version(self):
        try:
            st = os.stat(self.path)
            return f'{st.st_mtime_ns}:{st.st_size}'
        except FileNotFoundError:
            return 'missing'

    def read(self):
        """The current ideas (a fresh list each call). Raises if the file is missing or corrupt."""
        with self.lock:
            if not os.path.isfile(self.path):
                raise StoreError(f'Ideas file not found: {self.path}')
            ver = self.version()
            if not (self._cache and self._cache[0] == ver):
                with open(self.path, 'r', encoding='utf-8-sig') as f:
                    self._cache = (ver, decode(f.read()))
            return [dict(i, tags=list(i['tags'])) for i in self._cache[1]]

    def snapshot(self):
        ideas = self.read()
        return {'path': self.path, 'version': self.version(),
                'ideas': [with_meta(i, n) for n, i in enumerate(ideas)]}

    def line_of(self, idea_id):
        """1-based line of an idea's "id" in the file, for opening it in a text editor."""
        needle = f'"id": {json.dumps(idea_id)}'
        with open(self.path, 'r', encoding='utf-8-sig') as f:
            for n, line in enumerate(f, 1):
                if line.strip().rstrip(',') == needle:
                    return n
        return 1

    # -- writing --

    def _backup(self, force=False):
        now = time.time()
        if not force and now - self._last_backup < BACKUP_EVERY_SECONDS:
            return
        os.makedirs(self.backup_dir, exist_ok=True)
        name = datetime.now().strftime('ideas-%Y%m%d-%H%M%S.json')
        with open(self.path, 'rb') as src, open(os.path.join(self.backup_dir, name), 'wb') as dst:
            dst.write(src.read())
        self._last_backup = now
        backups = sorted(f for f in os.listdir(self.backup_dir)
                         if f.startswith('ideas-') and f.endswith('.json'))
        for old in backups[:-BACKUPS_KEPT]:
            try:
                os.remove(os.path.join(self.backup_dir, old))
            except OSError:
                pass

    def _write(self, ideas, force_backup=False):
        text = encode(ideas)
        decode(text)  # never write something we couldn't load back
        self._backup(force_backup)
        atomic_write(self.path, text)
        self._cache = None

    def _find(self, ideas, idea_id):
        for n, idea in enumerate(ideas):
            if idea['id'] == idea_id:
                return n, idea
        raise NotFound(f"No idea with id '{idea_id}'.")

    def _check_rev(self, idea, n, expected):
        if expected is not None and expected != rev(idea):
            raise Conflict(f"'{idea['title']}' changed on disk since you opened it.", with_meta(idea, n))

    def create(self, fields):
        with self.lock:
            ideas = self.read()
            idea = {'id': new_id(ideas), 'status': 'normal', 'title': '', 'tags': [],
                    'created': datetime.now().strftime('%Y-%m-%d'), 'updated': now_iso(), 'description': ''}
            apply_fields(idea, {'title': '', **fields})
            ideas.append(idea)
            self._write(ideas)
            return with_meta(idea, len(ideas) - 1)

    def update(self, idea_id, expected_rev, fields):
        with self.lock:
            ideas = self.read()
            n, idea = self._find(ideas, idea_id)
            self._check_rev(idea, n, expected_rev)
            updated = dict(idea, tags=list(idea['tags']))
            apply_fields(updated, fields)
            if updated == idea:
                return with_meta(idea, n)
            updated['updated'] = now_iso()
            ideas[n] = updated
            self._write(ideas)
            return with_meta(updated, n)

    def delete(self, idea_id, expected_rev):
        with self.lock:
            ideas = self.read()
            n, idea = self._find(ideas, idea_id)
            self._check_rev(idea, n, expected_rev)
            del ideas[n]
            self._write(ideas, force_backup=True)
            token = f'{idea_id}-{int(time.time() * 1000)}'
            self._undo[token] = (n, idea)
            return token, idea

    def undo_delete(self, token):
        with self.lock:
            if token not in self._undo:
                raise StoreError('Nothing to undo.')
            n, idea = self._undo.pop(token)
            ideas = self.read()
            if any(i['id'] == idea['id'] for i in ideas):
                raise StoreError(f"An idea with id '{idea['id']}' already exists.")
            n = min(n, len(ideas))
            ideas.insert(n, idea)
            self._write(ideas)
            return with_meta(idea, n)

    def rename_tag(self, old, new):
        """Renames a tag everywhere; merges if `new` already exists; removes it if `new` is empty."""
        old = str(old or '').strip().lower()
        new = ' '.join(str(new or '').replace(',', ' ').split())
        if not old:
            raise StoreError('Which tag?')
        with self.lock:
            ideas = self.read()
            changed = 0
            for idea in ideas:
                if any(t.lower() == old for t in idea['tags']):
                    idea['tags'] = clean_tags([new if t.lower() == old else t for t in idea['tags']])
                    idea['updated'] = now_iso()
                    changed += 1
            if changed:
                self._write(ideas, force_backup=True)
            return changed
