"""System tray, scoped Windows hotkeys and the non-activating flow."""

from concurrent.futures import Future, ThreadPoolExecutor
from ctypes import wintypes as wt
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic

from PySide6.QtCore import QAbstractNativeEventFilter, QFileInfo, QLockFile, QSettings, QStandardPaths, QTimer
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QFileIconProvider, QMenu, QStyle, QSystemTrayIcon, QWidget

from .counting import CounterUnavailable
from .editor import Editor
from .engine import Options
from .local_model import eligible, propose
from .panel import Panel
from .win32 import (ForegroundChanges, Window, anchor_in_window, claude_window, foreground,
                    installed_claude, motion_enabled, register_hotkey, unregister_hotkey)
from .workflow import Capture, EditorError, Mode, Prepared, has_draft_text, prepare


_KEYS = {0x5A01: ('U', Mode.COUNT), 0x5A02: ('T', Mode.OPTIMIZE)}
_MODIFIERS = 0x4000 | 0x0001 | 0x0002  # MOD_NOREPEAT | MOD_ALT | MOD_CONTROL
_SPINNER_SECONDS = 1.05


class ScopedHotkeys(QAbstractNativeEventFilter):
    def __init__(self, window: int, callback):
        super().__init__()
        self.window = window
        self.callback = callback
        self.registered: set[int] = set()

    def acquire(self) -> list[str]:
        conflicts = []
        for identifier, (key, _) in _KEYS.items():
            if identifier not in self.registered:
                if register_hotkey(self.window, identifier, _MODIFIERS, ord(key)):
                    self.registered.add(identifier)
                else:
                    conflicts.append(key)
        return conflicts

    def release(self):
        for identifier in self.registered:
            unregister_hotkey(self.window, identifier)
        self.registered.clear()

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) not in (b'windows_dispatcher_MSG', b'windows_generic_MSG'):
            return False, 0
        msg = wt.MSG.from_address(int(message))
        if msg.message == 0x0312 and msg.hWnd == self.window and msg.wParam in self.registered:
            mode = _KEYS[msg.wParam][1]
            target = claude_window(foreground())
            if target is not None:
                QTimer.singleShot(0, lambda: self.callback(mode, expected=target))
            return True, 0
        return False, 0


def _load_icon(app: QApplication) -> QIcon:
    for name in ('claude.png', 'claude.ico'):
        original = Path(__file__).with_name('assets') / name
        icon = QIcon(str(original))
        if not icon.isNull():
            return icon
    executable = installed_claude()
    if executable:
        icon = QFileIconProvider().icon(QFileInfo(str(executable)))
        if not icon.isNull():
            return icon
    return app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)


def _dark_palette() -> QPalette:
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, '#1B1917'), (QPalette.ColorRole.Base, '#24211E'),
        (QPalette.ColorRole.AlternateBase, '#24211E'), (QPalette.ColorRole.WindowText, '#F5EEE6'),
        (QPalette.ColorRole.Text, '#F5EEE6'), (QPalette.ColorRole.Button, '#24211E'),
        (QPalette.ColorRole.ButtonText, '#F5EEE6'), (QPalette.ColorRole.Highlight, '#D97757'),
        (QPalette.ColorRole.HighlightedText, '#21150F'), (QPalette.ColorRole.ToolTipBase, '#29231F'),
        (QPalette.ColorRole.ToolTipText, '#F5EEE6'),
    ):
        palette.setColor(role, QColor(color))
    return palette


class Application:
    def __init__(self, qt: QApplication):
        self.qt = qt
        self._icon = _load_icon(qt)
        qt.setWindowIcon(self._icon)
        self.panel = Panel(self._icon, motion=motion_enabled())
        self.settings = QSettings()
        self._load_settings()
        self.panel.settings_changed.connect(self._write_settings)
        self.panel.dismissed.connect(self._dismissed)

        # Stable, hidden HWND to own the temporary clipboard independently of the panel.
        self._anchor = QWidget()
        anchor = int(self._anchor.winId())
        self._editor = Editor(anchor)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='claude-editor')
        self._model_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='lm-studio')
        self._stage = 'idle'
        self._started = 0.0
        self._future: Future | None = None
        self._prepared: Prepared | None = None
        self._proposal: Prepared | None = None
        self._options: Options | None = None
        self._model_note = ''
        self._cancelled = Event()
        self._request: tuple[Mode, Window] | None = None
        self._panel_owner: Window | None = None
        self._composer: tuple[int, ...] | None = None
        self._had_text = False
        self._responding: bool | None = None
        self._lost_reads = 0
        self._watch_revision = 0
        self._watch_future: tuple[int, Future] | None = None
        self._draft_timer = QTimer(qt)
        self._draft_timer.setInterval(250)
        self._draft_timer.timeout.connect(self._watch_draft)

        self.tray = QSystemTrayIcon(self._icon, qt)
        self.tray.setToolTip('Token Optimizer — Claude Desktop uniquement')
        self._menu = QMenu()
        self._menu.addAction('Actif · Ctrl + Alt + U / T').setEnabled(False)
        self._menu.addSeparator()
        self._menu.addAction('Quitter Token Optimizer', qt.quit)
        self.tray.setContextMenu(self._menu)
        self.tray.show()

        self.hotkeys = ScopedHotkeys(anchor, self.start)
        qt.installNativeEventFilter(self.hotkeys)
        self._target = None
        self._last_focus = -1
        self._warned: set[str] = set()
        self._focus = QTimer(qt)
        self._focus.setInterval(140)
        self._focus.timeout.connect(self._watch_focus)
        self._focus.start()
        self._watch_focus()
        self._focus_events = ForegroundChanges(self._watch_focus)
        self._poll = QTimer(qt)
        self._poll.setInterval(30)
        self._poll.timeout.connect(self._work)
        qt.aboutToQuit.connect(self.close)

    def _load_settings(self):
        self.panel.reduce.setChecked(self.settings.value('reduce', True, type=bool))
        self.panel.clarity.setChecked(self.settings.value('clarify', True, type=bool))
        self.panel.agent.setChecked(self.settings.value('agent', False, type=bool))
        self.panel.strength.setValue(self.settings.value('strength', 1, type=int))
        self.panel.local_ai.setChecked(self.settings.value('local_ai', False, type=bool))

    def _write_settings(self):
        options = self.panel.options()
        for key in ('reduce', 'clarify', 'agent', 'strength', 'local_ai'):
            value = getattr(options, key)
            self.settings.setValue(key, int(value) if key == 'strength' else value)
        if self._proposal:
            self._proposal = None
            self.panel.clear_preview()

    def _watch_focus(self):
        handle = foreground() or 0
        if handle == self._last_focus:
            return
        self._last_focus = handle
        target = claude_window(handle)
        if target is None:
            self.hotkeys.release()
        elif target != self._target:
            conflicts = self.hotkeys.acquire()
            for key in conflicts:
                if key not in self._warned:
                    self._warned.add(key)
                    self.tray.showMessage('Raccourci indisponible', f'Ctrl + Alt + {key} est déjà utilisé.')
        self._target = target
        if self._request and handle != self._request[1].handle:
            self._cancelled.set()
        owner = self._panel_owner
        if owner and self.panel.isVisible() and handle not in (owner.handle, int(self.panel.winId())):
            self.panel.hide()

    def start(self, mode: Mode, *, expected: Window):
        if self._stage != 'idle':
            return
        target = claude_window(foreground())
        if target != expected:
            return
        options = self.panel.options()
        awaiting_confirmation = self._proposal if mode is Mode.OPTIMIZE and self.panel.preview.isVisible() else None
        self._proposal = None
        self._options = options
        self._model_note = ''
        incomplete_examples = options.examples and not (options.example_input.strip() and options.example_output.strip())
        incomplete_custom = options.custom and not options.instructions.strip()
        self._watch_revision += 1
        self._draft_timer.stop()
        self._cancelled = Event()
        self._request = mode, expected
        self._stage = 'read'

        def read():
            capture = self._editor.capture(expected, read_selection=(mode is Mode.OPTIMIZE), locate=True)
            try:
                if mode is Mode.OPTIMIZE and (incomplete_examples or incomplete_custom):
                    raise EditorError('Complète les exemples ou les consignes cochés dans Réglages.')
                if awaiting_confirmation:
                    if awaiting_confirmation.capture != capture:
                        raise EditorError('La sélection a changé. Rien n’a été collé ; relance le raccourci.')
                    return capture, replace(awaiting_confirmation, capture=capture), '', True
                return capture, prepare(mode, capture, options), '', False
            except (EditorError, CounterUnavailable) as error:
                return capture, None, str(error), False

        self._future = self._pool.submit(read)
        self._poll.start()

    def _cancel_work(self):
        if self._stage != 'idle':
            self._cancelled.set()

    def _dismissed(self):
        self._cancel_work()
        self._proposal = None
        self._panel_owner = None
        self._composer = None
        self._had_text = False
        self._responding = None
        self._lost_reads = 0
        self._watch_revision += 1
        self._draft_timer.stop()
        self.panel.reset()

    def _watch_draft(self):
        owner, composer = self._panel_owner, self._composer
        if not owner or not composer or not self.panel.isVisible():
            self._draft_timer.stop()
            return
        # Don't dismiss the popup while the user is editing its local settings.
        if foreground() != owner.handle or self._stage not in ('idle', 'wait', 'model'):
            return
        if self._watch_future:
            revision, pending = self._watch_future
            if not pending.done():
                return
            self._watch_future = None
            if revision == self._watch_revision:
                try:
                    observation = pending.result()
                except Exception:
                    observation = None
                self._lost_reads = self._lost_reads + 1 if observation is None else 0
                cleared = observation is not None and self._had_text and not observation.has_text
                began_response = (observation is not None and self._responding is False and
                                  observation.responding is True)
                if cleared or began_response or self._lost_reads >= 2:
                    self.panel.hide()
                    return
                if observation is not None:
                    self._had_text |= observation.has_text
                    if observation.responding is not None:
                        self._responding = observation.responding
                    self.panel.follow(observation.bounds)
        pending = self._pool.submit(self._editor.observe_draft, owner, composer, not self._had_text)
        self._watch_future = self._watch_revision, pending

    def _show(self, mode: Mode, owner: Window, capture: Capture | None,
              job: Prepared | None, message: str = ''):
        if foreground() != owner.handle or self._cancelled.is_set():
            return False
        self._panel_owner = owner
        self._composer = capture.runtime if capture else None
        self._had_text = bool(capture and has_draft_text(capture.text))
        self._responding = capture.responding if capture else None
        self._lost_reads = 0
        self.panel.working(mode)
        if message:
            self.panel.refused(message)
        elif mode is Mode.COUNT and job:
            self.panel.show_count(job.count)
        bounds = capture.bounds if capture else None
        self.panel.open_at(bounds or anchor_in_window(owner.handle))
        if self._composer:
            self._draft_timer.start()
        elif message:
            # A diagnostic with no verified composer must not linger over a later chat.
            revision = self._watch_revision
            QTimer.singleShot(5000, lambda: self._expire_notice(revision))
        return True

    def _expire_notice(self, revision: int):
        if revision == self._watch_revision and self._composer is None:
            self.panel.hide()

    def _done(self):
        self._poll.stop()
        self._stage = 'idle'
        self._future = None
        self._prepared = None
        self._options = None
        self._request = None

    def _work(self):
        if self._cancelled.is_set():
            if self._stage == 'paste' and self._future and not self._future.done():
                return  # Let the previous clipboard transaction finish restoring.
            if self._future:
                self._future.cancel()
            self.panel.reset()
            self._done()
            return
        if self._future and not self._future.done():
            return
        try:
            if self._stage == 'read':
                mode, owner = self._request
                capture, job, message, confirmed = self._future.result()
                self._future = None
                shown = self._show(mode, owner, capture, job, message)
                if not shown or job is None or mode is Mode.COUNT:
                    self._done()
                    return
                self._started = monotonic()
                self._prepared = job
                if not confirmed and self._options and eligible(job, self._options):
                    self.panel.model_working()
                    self._future = self._model_pool.submit(propose, job, self._options, self._cancelled)
                    self._stage = 'model'
                    return
                self._stage = 'wait'
            if self._stage == 'model':
                outcome = self._future.result()
                self._future = None
                if outcome.job:
                    self._proposal = outcome.job
                    self.panel.show_proposal(outcome.job.optimization)
                    self._done()
                    return
                self._model_note = outcome.note
                self._stage = 'wait'
            if self._stage == 'wait' and monotonic() - self._started >= _SPINNER_SECONDS:
                job = self._prepared
                if job.replacement is None:
                    self.panel.show_result(job.optimization, verified=False, note=self._model_note)
                    self._done()
                else:
                    self._future = self._pool.submit(self._editor.replace, job, self._cancelled)
                    self._stage = 'paste'
            elif self._stage == 'paste':
                verified = self._future.result()
                self.panel.show_result(self._prepared.optimization, verified=verified, note=self._model_note)
                self._done()
        except (EditorError, CounterUnavailable) as error:
            self._refused(str(error))
            self._done()
        except Exception as error:
            self._refused(f'Erreur Windows ({type(error).__name__}). Vérifie le champ de Claude avant de réessayer.')
            self._done()

    def _refused(self, message: str):
        if self._stage == 'read' and self._request:
            mode, owner = self._request
            self._show(mode, owner, None, None, message)
        elif self.panel.isVisible():
            self.panel.refused(message)

    def close(self):
        self._cancel_work()
        self._focus.stop()
        self._focus_events.close()
        self._poll.stop()
        self._draft_timer.stop()
        self.hotkeys.release()
        self.qt.removeNativeEventFilter(self.hotkeys)
        self._pool.submit(self._editor.close)
        self._pool.shutdown(wait=True)
        self._model_pool.shutdown(wait=False, cancel_futures=True)
        self.tray.hide()


def run() -> int:
    import sys

    qt = QApplication(sys.argv)
    qt.setOrganizationName('LocalTools')
    qt.setApplicationName('ClaudeTokenOptimizer')
    qt.setApplicationDisplayName('Token Optimizer')
    qt.setStyle('Fusion')
    qt.setPalette(_dark_palette())
    qt.setQuitOnLastWindowClosed(False)
    path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    path.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path / 'optimizer.lock'))
    if not lock.tryLock(0):
        return 0
    app = Application(qt)
    return qt.exec()
