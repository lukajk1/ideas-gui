"""Reloads a window's page when WebView2's renderer or GPU process dies or stops responding.

pywebview doesn't handle WebView2's ProcessFailed event, so after one of those processes is lost
(Windows reclaiming memory from an app left in the tray, a GPU reset while a game runs) the window
just stays frozen until the app is restarted. Each failure is logged to LOG.
"""
import os
from datetime import datetime

LOG = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'ideas-gui',
                   'webview.log')


def log(message):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(f'{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n')
    except OSError:
        pass


def watch(window, name):
    """Call from the window's `loaded` event (it fires on every page load; this attaches once)."""
    if getattr(window, '_crash_watched', False):
        return
    window._crash_watched = True

    def on_failed(sender, args):
        kind = str(args.ProcessFailedKind)
        log(f'{name}: WebView2 {kind} (exit code {args.ExitCode})')
        if kind == 'BrowserProcessExited':
            return  # the whole browser is gone; nothing left to reload, only a restart helps
        try:
            sender.Reload()
            log(f'{name}: reloaded the page')
        except Exception as exc:  # noqa: BLE001  report it rather than crash the UI thread
            log(f'{name}: reload failed: {exc}')

    def attach():  # on the UI thread, which owns the control
        try:
            form.webview.CoreWebView2.ProcessFailed += on_failed  # pythonnet wraps it on +=
        except Exception as exc:  # noqa: BLE001
            log(f'{name}: not watching WebView2 for crashes: {exc}')

    try:
        from System import Action  # pythonnet, which pywebview's WebView2 backend runs on
        form = window.native
        form.Invoke(Action(attach))
    except Exception as exc:  # noqa: BLE001  another backend, or pywebview internals changed
        log(f'{name}: not watching WebView2 for crashes: {exc}')
