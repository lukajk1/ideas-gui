"""
One-time import from the ideas CLI's markdown file. The markdown file is only
ever read, never written.

    python -m ideas_gui.importer "C:/.../ideas.md" "C:/.../ideas.json"

The markdown format: an idea starts at a "<status> <id> <title>" line that is
followed by a "Tags:" line, and runs to the next "---" line.

    - k3t some idea title
    Tags: game, weekend
    Date: 2026-05-01

    Description:
    Free text...

    ---
"""
import re
import sys

from .store import clean_tags, create_file

LEGACY_STATUS = {'-': 'normal', '*': 'promising', '+': 'meh', '>': 'wip', '#': 'built'}
HEADER_RE = re.compile(r'^([*+\->#]) ([a-z0-9]{3}) (.*)$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
TEMPLATE_DESC = 'Enter your details here...'


def parse_markdown(text):
    """Returns (ideas, notes): ideas in file order, notes about anything adjusted."""
    lines = text.lstrip('\ufeff').replace('\r\n', '\n').split('\n')
    ideas, notes, seen = [], [], set()
    i = 0
    while i < len(lines):
        m = HEADER_RE.match(lines[i])
        if not (m and i + 1 < len(lines) and lines[i + 1].startswith('Tags:')):
            i += 1
            continue
        j = i + 2
        while j < len(lines) and lines[j].strip() != '---':
            j += 1
        body = lines[i + 2:j]
        head, desc = (body[:body.index('Description:')], body[body.index('Description:') + 1:]) \
            if 'Description:' in body else ([], body)

        created, extra = '', []
        for h in head:
            if h.startswith('Date:'):
                value = h[len('Date:'):].strip()
                if DATE_RE.match(value):
                    created = value
                elif value:
                    extra.append(h)
            elif h.strip():
                extra.append(h)
        description = '\n'.join(l.rstrip() for l in desc).strip('\n')
        if description.strip() == TEMPLATE_DESC:
            description = ''
        if extra:  # unknown header lines: keep them rather than drop them
            description = '\n'.join(extra) + ('\n\n' + description if description else '')

        idea_id = m.group(2)
        if idea_id in seen:
            notes.append(f'line {i + 1}: duplicate id {idea_id} skipped ({m.group(3).strip()})')
        else:
            seen.add(idea_id)
            ideas.append({
                'id': idea_id,
                'status': LEGACY_STATUS[m.group(1)],
                'title': ' '.join(m.group(3).split()) or '(untitled)',
                'tags': clean_tags(lines[i + 1][len('Tags:'):]),
                'created': created,
                'updated': '',
                'description': description,
            })
        i = j + 1
    return ideas, notes


def import_file(md_path, json_path):
    with open(md_path, 'r', encoding='utf-8') as f:
        ideas, notes = parse_markdown(f.read())
    create_file(json_path, ideas)
    return ideas, notes


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    ideas, notes = import_file(sys.argv[1], sys.argv[2])
    print(f'Imported {len(ideas)} ideas into {sys.argv[2]}')
    for n in notes:
        print('  note:', n)
