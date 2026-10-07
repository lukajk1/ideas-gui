# Ideas

A desktop app for keeping an ideas ledger: browse, search, tag, and edit ideas, jot new ones down from anywhere with a global shortcut, and see what you've been thinking about over time.

The successor to the `ideas` CLI (`cs3/ideas`). The CLI and its `ideas.md` are left untouched; this app keeps its own file.

## Features

- List, detail and editor in one window; edits save as you type
- Search across titles, descriptions and tags; filter by status and tags (Ctrl+click for several)
- Sort by file order, date, last edited, title or status; shuffle; jump to a random idea
- Statuses: Normal, Promising, Meh, WIP, Built (keys 1–5)
- Tag suggestions while typing; rename or merge a tag across every idea
- Stats: counts by status, top tags, ideas added per month
- Quick capture: a global shortcut (Ctrl+Alt+I by default) pops up a small box from anywhere
- Lives in the tray; launching it again shows the running copy
- Picks up changes made elsewhere (another PC via Drive sync) within a couple of seconds, and refuses to silently overwrite an idea that changed underneath you
- Delete with undo; rolling local backups

Press `?` in the app for the keyboard shortcuts.

## Data

Everything is in one JSON file, by default `My Drive/ideas/ideas.json`, so Google Drive syncs it. Saving writes a temp file, flushes it to disk, reads it back, then swaps it in atomically. A file that fails to load is shown as an error and never written over. Before saving, a copy goes to `%APPDATA%\ideas-gui\backups` (at most every 10 minutes, and before every delete or tag rename; the newest 50 are kept).

```json
{
  "format": "ideas-gui",
  "schema": 1,
  "ideas": [
    { "id": "k3t", "status": "promising", "title": "…", "tags": ["game"],
      "created": "2026-05-01", "updated": "2026-10-07T14:03:11", "description": "…" }
  ]
}
```

Settings are in `%APPDATA%\ideas-gui\config.json`.

To import a CLI-style markdown file (it is only read): use the import button in Settings, or

```
python -m ideas_gui.importer "path/to/ideas.md" "path/to/ideas.json"
```

## Running from source

```
pip install -r requirements.txt
python main.pyw              # the app
python main.pyw --browser    # in your web browser instead (development)
python main.pyw --file X     # use another ideas file for this run
```

Tests: `python tests/test_store.py` and `python tests/test_api.py`.

## Building the installer

Needs PyInstaller and [Inno Setup 6](https://jrsoftware.org/isdl.php).

```
pip install pyinstaller
python installer/build.py    # -> installer/output/IdeasSetup-<version>.exe
```

The version comes from `ideas_gui/__init__.py`. The installer is per-user (no admin prompt), adds a Start menu entry, and optionally a desktop shortcut and start-with-Windows (in the tray).
