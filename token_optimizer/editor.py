"""Read the real Electron composer using Windows UI Automation, not coordinates."""

import ctypes as ct
from ctypes import wintypes as wt
from dataclasses import replace as dataclass_replace
from threading import Event
from time import monotonic, sleep
import unicodedata

from comtypes import COINIT_MULTITHREADED, COMError, CoInitializeEx, CoUninitialize, GUID, IUnknown
from comtypes.client import CreateObject, GetModule

from .clipboard import TemporaryClipboard
from .win32 import (Window, bind, claude_window, foreground, is_claude_executable, keyboard_focus, modifiers_down,
                    paste_keystroke, process_image, renderer_children, user32)
from .workflow import (Capture, DraftObservation, EditorError, Prepared, can_replace, has_draft_text,
                       locate_selection, normalize_lines)


_MAX_TEXT = 120_000
_EDIT, _DOCUMENT, _BUTTON = 50004, 50030, 50000
_VALUE_PATTERN, _TEXT_PATTERN = 10002, 10014
_IS_READ_ONLY_ATTRIBUTE = 40015
_NAMES = {
    'répondre', 'répondre à claude', 'message à claude', 'demander à claude', 'nouveau message',
    'écrire un message', 'écrivez votre message', 'écrivez votre demande', 'écrivez un message',
    'saisissez votre message', 'tapez votre message', 'comment puis-je vous aider',
    'comment puis-je vous aider aujourd’hui', 'écrire à claude', 'envoyer un message à claude',
    'reply', 'reply to claude', 'ask claude', 'message claude', 'chat input', 'prompt editor',
    'write a message', 'write your prompt to claude', 'write your message', 'type a message',
    'how can i help you', 'how can i help you today', 'send a message to claude', 'message input',
}
_IDS = {'chat-input', 'chat-editor', 'prompt-input', 'prompt-textarea', 'composer-input', 'reply-input'}
_SEND = {'envoyer', 'envoyer le message', 'envoyer un message', 'envoyer la demande', 'arrêter la réponse',
         'arrêter la génération', 'send', 'send message', 'send prompt', 'stop response', 'stop generating'}
_SEND_IDS = {'send-button', 'send-message', 'send-message-button', 'stop-response-button'}
_STOP_NAMES = {'arrêter la réponse', 'arrêter la génération', 'interrompre la réponse',
               'stop response', 'stop generating', 'stop generation'}
_STOP_IDS = {'stop-response-button', 'stop-button', 'stop-generating-button'}
_CHANGED = 'Le brouillon, la sélection ou le focus a changé. Aucun collage effectué.'
_UNAVAILABLE = ('Place le curseur dans le champ de chat de Claude Desktop. Si le problème persiste, '
                'ouvre Claude avec le raccourci de compatibilité fourni.')


def _label(value: str | None) -> str:
    return ' '.join(unicodedata.normalize('NFC', value or '').casefold().split()).rstrip(' .…?!:')


def _string(element, prop: str) -> str:
    try:
        return _label(getattr(element, prop))
    except (COMError, AttributeError):
        return ''


_send_message = bind(user32, 'SendMessageTimeoutW', wt.LPARAM, wt.HWND, wt.UINT, wt.WPARAM,
                     wt.LPARAM, wt.UINT, wt.UINT, ct.POINTER(ct.c_size_t))
_accessible_pointer = ct.POINTER(IUnknown)
_legacy_accessible = bind(ct.WinDLL('oleacc'), 'AccessibleObjectFromWindow', ct.c_long,
                          wt.HWND, wt.DWORD, ct.POINTER(GUID), ct.POINTER(_accessible_pointer))
_IID_ACCESSIBLE = GUID('{618736E0-3C3D-11CF-810C-00AA00389B71}')


class Editor:
    """One instance on a COM worker thread; no global UIA or clipboard listener."""

    def __init__(self, clipboard_owner: int):
        self.clipboard_owner = clipboard_owner
        self.client = None
        self.typelib = None

    def _connect(self) -> None:
        if self.client is not None:
            return
        CoInitializeEx(COINIT_MULTITHREADED)
        try:
            try:
                from comtypes.gen import UIAutomationClient as typelib
            except ImportError:
                typelib = GetModule('UIAutomationCore.dll')
            try:
                client = CreateObject(typelib.CUIAutomation8, interface=typelib.IUIAutomation2)
                client.ConnectionTimeout = 650
                client.TransactionTimeout = 1100
                client.AutoSetFocus = False
            except (AttributeError, COMError, OSError):
                client = CreateObject(typelib.CUIAutomation, interface=typelib.IUIAutomation)
            self.client = client
            self.typelib = typelib
        except Exception:
            CoUninitialize()
            raise

    def close(self) -> None:
        if self.client is not None:
            self.client = None
            self.typelib = None
            CoUninitialize()

    def _activate_accessibility(self, window: Window) -> None:
        # Ask only the verified Claude renderer(s) for their accessibility tree.
        # Chromium's legacy screen-reader notification uses custom MSAA object 1.
        for child in renderer_children(window.handle):
            if foreground() != window.handle:
                return
            result = ct.c_size_t()
            _send_message(child, 0x003D, 0, 1, 0x0003, 90, ct.byref(result))  # WM_GETOBJECT
            acc = _accessible_pointer()
            _legacy_accessible(child, 0xFFFFFFFC, ct.byref(_IID_ACCESSIBLE), ct.byref(acc))  # OBJID_CLIENT
            try:
                self.client.ElementFromHandle(child)
            except COMError:
                pass

    def _belongs_to_window(self, element, window: Window) -> bool:
        walker = self.client.RawViewWalker
        for _ in range(36):
            if not element:
                return False
            if int(element.CurrentNativeWindowHandle or 0) == window.handle:
                return True
            element = walker.GetParentElement(element)
        return False

    def _has_nearby_button(self, element, names, ids) -> bool:
        walker = self.client.RawViewWalker
        condition = self.client.CreatePropertyCondition(30003, _BUTTON)  # ControlType
        owner = element
        for _ in range(5):
            owner = walker.GetParentElement(owner)
            if not owner or owner.CurrentControlType == _DOCUMENT or owner.CurrentNativeWindowHandle:
                break
            rect = owner.CurrentBoundingRectangle
            if rect.bottom - rect.top > 350:
                break
            buttons = owner.FindAll(4, condition)  # TreeScope_Descendants, in this small compose group only
            for index in range(min(buttons.Length, 24)):
                button = buttons.GetElement(index)
                if (_string(button, 'CurrentName') in names or
                        _string(button, 'CurrentAutomationId') in ids):
                    return True
        return False

    @staticmethod
    def _bounds(element) -> tuple[int, int, int, int] | None:
        try:
            rect = element.CurrentBoundingRectangle
            if rect.right > rect.left and rect.bottom > rect.top:
                return rect.left, rect.top, rect.right, rect.bottom
        except COMError:
            pass
        return None

    def _responding(self, element) -> bool | None:
        try:
            return self._has_nearby_button(element, _STOP_NAMES, _STOP_IDS)
        except COMError:
            return None

    def _is_composer(self, element) -> bool:
        if element.CurrentIsPassword or not element.CurrentIsEnabled:
            return False
        role = _string(element, 'CurrentAriaRole')
        control = element.CurrentControlType
        if control not in (_EDIT, _DOCUMENT) and role != 'textbox':
            return False
        name = _string(element, 'CurrentName')
        identity = _string(element, 'CurrentAutomationId')
        if name in _NAMES or identity in _IDS or _string(element, 'CurrentHelpText') in _NAMES:
            return True
        cls = _string(element, 'CurrentClassName')
        aria = _string(element, 'CurrentAriaProperties')
        unnamed_edit = not name and control == _EDIT and not identity
        return ((unnamed_edit or 'prosemirror' in cls or 'tiptap' in cls or 'multiline=true' in aria)
                and self._has_nearby_button(element, _SEND, _SEND_IDS))

    @staticmethod
    def _pattern(element, index: int, interface):
        try:
            unknown = element.GetCurrentPattern(index)
            return unknown.QueryInterface(interface) if unknown else None
        except COMError:
            return None

    def _capture_once(self, window: Window, read_selection: bool, locate: bool) -> Capture | None:
        element = self.client.GetFocusedElement()
        if not element or not element.CurrentHasKeyboardFocus:
            return None
        pid = element.CurrentProcessId
        if pid != window.pid and not is_claude_executable(process_image(pid)):
            return None
        if not self._belongs_to_window(element, window) or not self._is_composer(element):
            return None
        runtime = tuple(element.GetRuntimeId() or ())
        if not runtime:
            return None
        value = self._pattern(element, _VALUE_PATTERN, self.typelib.IUIAutomationValuePattern)
        if value is not None and value.CurrentIsReadOnly:
            return None
        text_pattern = self._pattern(element, _TEXT_PATTERN, self.typelib.IUIAutomationTextPattern)
        if text_pattern is None:
            if not read_selection and value is not None:
                draft = value.CurrentValue or ''
                return self._make_capture(window, runtime, draft, element, locate)
            return None
        document = text_pattern.DocumentRange
        if document is None or not document:
            return None
        try:
            readonly = document.GetAttributeValue(_IS_READ_ONLY_ATTRIBUTE)
            if type(readonly) in (bool, int) and bool(readonly):
                return None
        except COMError:
            pass
        draft = document.GetText(_MAX_TEXT + 1) or ''
        snapshot = self._make_capture(window, runtime, draft, element, locate)
        if not read_selection:
            return snapshot
        ranges = text_pattern.GetSelection()
        if not ranges or ranges.Length != 1:
            return snapshot
        selected_range = ranges.GetElement(0)
        if not selected_range:
            return snapshot
        selected = selected_range.GetText(_MAX_TEXT + 1) or ''
        if not selected:
            return snapshot
        prefix = document.Clone()
        prefix.MoveEndpointByRange(1, selected_range, 0)  # End <- selection.Start
        start, end = locate_selection(draft, prefix.GetText(_MAX_TEXT + 1) or '', selected)
        return dataclass_replace(snapshot, start=start, end=end)

    def _make_capture(self, window: Window, runtime: tuple[int, ...], draft: str, element, locate: bool) -> Capture:
        if len(draft.encode('utf-16-le')) // 2 > _MAX_TEXT or '\0' in draft:
            raise EditorError('Brouillon trop long ou format illisible ; aucun changement.')
        bounds = self._bounds(element) if locate else None
        responding = self._responding(element) if locate and not has_draft_text(draft) else None
        return Capture(window.handle, window.pid, runtime, normalize_lines(draft), bounds=bounds,
                       responding=responding)

    def capture(self, window: Window, *, read_selection: bool, activate: bool = True,
                locate: bool = False) -> Capture:
        if foreground() != window.handle or claude_window(window.handle) != window:
            raise EditorError(_CHANGED)
        try:
            self._connect()
            attempts = 4 if activate else 1
            for index in range(attempts):
                if foreground() != window.handle:
                    raise EditorError(_CHANGED)
                try:
                    result = self._capture_once(window, read_selection, locate)
                    if result is not None and foreground() == window.handle:
                        return result
                except COMError:
                    pass
                if index == 0 and activate:
                    self._activate_accessibility(window)
                if index + 1 < attempts:
                    sleep(0.09)
        except (COMError, OSError, UnicodeError) as error:
            raise EditorError(_UNAVAILABLE) from error
        raise EditorError(_UNAVAILABLE)

    def observe_draft(self, window: Window, runtime: tuple[int, ...], watch_send: bool) -> DraftObservation | None:
        """Inspect only the exact composer that opened the visible popup, never the chat."""
        if self.client is None or foreground() != window.handle:
            return None
        try:
            element = self.client.GetFocusedElement()
            if (not element or tuple(element.GetRuntimeId() or ()) != runtime or
                    not self._belongs_to_window(element, window)):
                return None
            pattern = self._pattern(element, _TEXT_PATTERN, self.typelib.IUIAutomationTextPattern)
            if pattern:
                document = pattern.DocumentRange
                text = document.GetText(_MAX_TEXT + 1) if document else ''
            else:
                value = self._pattern(element, _VALUE_PATTERN, self.typelib.IUIAutomationValuePattern)
                if not value or value.CurrentIsReadOnly:
                    return None
                text = value.CurrentValue
            has_text = has_draft_text(text)
            responding = self._responding(element) if watch_send and not has_text else None
            bounds = self._bounds(element)
            return (DraftObservation(has_text, bounds, responding)
                    if foreground() == window.handle else None)
        except (COMError, OSError):
            return None

    def replace(self, job: Prepared, cancelled: Event) -> bool:
        """True only if the provider reads back the exact expected draft."""
        new = job.replacement
        if new is None or '\0' in new or len(job.expected_draft.encode('utf-16-le')) // 2 > _MAX_TEXT:
            raise EditorError('Aucun remplacement valide pour cette sélection.')
        target = Window(job.capture.window, job.capture.pid)
        if cancelled.is_set() or modifiers_down():
            raise EditorError('Relâche les touches du raccourci, puis réessaie.')
        if not can_replace(job, self.capture(target, read_selection=True, activate=False)):
            raise EditorError(_CHANGED)
        focused = keyboard_focus(target.handle)
        if not focused:
            raise EditorError(_CHANGED)

        with TemporaryClipboard(self.clipboard_owner, new) as clipboard:
            current = self.capture(target, read_selection=True, activate=False)
            if (cancelled.is_set() or not can_replace(job, current) or modifiers_down() or
                    not clipboard.is_current() or foreground() != target.handle or
                    keyboard_focus(target.handle) != focused):
                raise EditorError(_CHANGED)
            if not paste_keystroke():
                raise EditorError('Windows n’a pas confirmé le collage. Vérifie le champ de Claude.')

            deadline = monotonic() + 0.72
            while monotonic() < deadline:
                sleep(0.06)
                if cancelled.is_set() or foreground() != target.handle:
                    break
                try:
                    updated = self.capture(target, read_selection=False, activate=False)
                except EditorError:
                    continue
                if updated.runtime == job.capture.runtime and updated.text == job.expected_draft:
                    return True
        return False
