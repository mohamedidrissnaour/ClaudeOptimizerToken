"""Temporary Unicode clipboard, including preservation of ordinary rich formats."""

from contextlib import contextmanager
import ctypes as ct
from ctypes import wintypes as wt
from time import monotonic, sleep

from .win32 import bind, kernel32, user32
from .workflow import EditorError


_open = bind(user32, 'OpenClipboard', wt.BOOL, wt.HWND)
_close = bind(user32, 'CloseClipboard', wt.BOOL)
_empty = bind(user32, 'EmptyClipboard', wt.BOOL)
_enum = bind(user32, 'EnumClipboardFormats', wt.UINT, wt.UINT)
_get = bind(user32, 'GetClipboardData', wt.HANDLE, wt.UINT)
_set = bind(user32, 'SetClipboardData', wt.HANDLE, wt.UINT, wt.HANDLE)
_owner = bind(user32, 'GetClipboardOwner', wt.HWND)
_sequence = bind(user32, 'GetClipboardSequenceNumber', wt.DWORD)
_register = bind(user32, 'RegisterClipboardFormatW', wt.UINT, wt.LPCWSTR)
_allocate = bind(kernel32, 'GlobalAlloc', wt.HGLOBAL, wt.UINT, ct.c_size_t)
_free = bind(kernel32, 'GlobalFree', wt.HGLOBAL, wt.HGLOBAL)
_size = bind(kernel32, 'GlobalSize', ct.c_size_t, wt.HGLOBAL)
_lock = bind(kernel32, 'GlobalLock', ct.c_void_p, wt.HGLOBAL)
_unlock = bind(kernel32, 'GlobalUnlock', wt.BOOL, wt.HGLOBAL)
_duplicate = bind(ct.WinDLL('ole32'), 'OleDuplicateData', wt.HANDLE, wt.HANDLE, wt.WORD, wt.UINT)
_gdi = ct.WinDLL('gdi32')
_delete_object = bind(_gdi, 'DeleteObject', wt.BOOL, wt.HANDLE)

_GDI = (2, 9)  # bitmap, palette (duplicated by OleDuplicateData)
_MEMORY = (1, 4, 5, 6, 7, 8, 11, 12, 13, 15, 16, 17)
_UNICODE = 13
_MAX_SNAPSHOT = 64 * 1024 * 1024


@contextmanager
def _opened(owner: int, timeout: float = 0.35):
    end = monotonic() + timeout
    while not _open(owner):
        if monotonic() >= end:
            raise EditorError('Le presse-papiers est occupé. Réessaie dans un instant.')
        sleep(0.012)
    try:
        yield
    finally:
        _close()


def _release(items: list[tuple[int, int]]) -> None:
    while items:
        fmt, handle = items.pop()
        (_delete_object if fmt in _GDI else _free)(handle)


def _publish(items: list[tuple[int, int]]) -> None:
    # Ownership of each object moves to Windows only after SetClipboardData succeeds.
    while items:
        fmt, handle = items[0]
        if not _set(fmt, handle):
            raise EditorError('Windows n’a pas pu écrire dans le presse-papiers.')
        items.pop(0)


def _memory(data: bytes) -> int:
    handle = _allocate(2, len(data))  # GMEM_MOVEABLE
    pointer = _lock(handle) if handle else None
    if not pointer:
        if handle:
            _free(handle)
        raise EditorError('Mémoire insuffisante pour préparer le collage.')
    ct.memmove(pointer, data, len(data))
    _unlock(handle)
    return handle


def _snapshot(items: list[tuple[int, int]]) -> None:
    total = 0
    fmt = 0
    formats: set[int] = set()
    while True:
        ct.set_last_error(0)
        fmt = _enum(fmt)
        if not fmt:
            if ct.get_last_error():
                raise EditorError('Windows n’a pas pu lire les formats du presse-papiers.')
            return
        if fmt in formats:
            raise EditorError('Le contenu du presse-papiers a changé.')
        formats.add(fmt)
        if fmt not in _GDI and fmt not in _MEMORY and fmt < 0xC000:
            raise EditorError('Format du presse-papiers non préservable. Copie un texte simple, puis réessaie.')
        handle = _get(fmt)
        count = _size(handle) if handle and fmt not in _GDI else 0
        total += count
        if not handle or (fmt not in _GDI and not count) or total > _MAX_SNAPSHOT:
            raise EditorError('Impossible de préserver le presse-papiers. Copie un texte simple, puis réessaie.')
        copy = _duplicate(handle, fmt, 2)
        if not copy:
            raise EditorError('Impossible de préserver le presse-papiers. Aucun texte n’a été collé.')
        items.append((fmt, copy))


class TemporaryClipboard:
    """Restore saved data unless the user or another app copied something newer."""

    def __init__(self, owner: int, text: str):
        self.owner = owner
        self.text = text
        self.saved: list[tuple[int, int]] = []
        self.sequence = 0

    def __enter__(self):
        payload: list[tuple[int, int]] = []
        try:
            for name in ('ExcludeClipboardContentFromMonitorProcessing',
                         'CanIncludeInClipboardHistory', 'CanUploadToCloudClipboard'):
                fmt = _register(name)
                if not fmt:
                    raise EditorError('Windows n’a pas accepté le collage privé.')
                payload.append((fmt, _memory(bytes(4))))
            payload.append((_UNICODE, _memory((self.text.replace('\n', '\r\n') + '\0').encode('utf-16-le'))))
            with _opened(self.owner):
                _snapshot(self.saved)
                if not _empty():
                    raise EditorError('Impossible de préparer le presse-papiers.')
                try:
                    _publish(payload)
                except Exception:
                    if _empty():
                        _publish(self.saved)
                    raise
            self.sequence = _sequence()
            return self
        except Exception:
            _release(self.saved)
            raise
        finally:
            _release(payload)

    def is_current(self) -> bool:
        return _owner() == self.owner and _sequence() == self.sequence

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self.is_current():
                with _opened(self.owner, timeout=2.5):
                    if self.is_current():
                        if not _empty():
                            raise EditorError('Impossible de restaurer le presse-papiers.')
                        _publish(self.saved)
        except EditorError as error:
            raise EditorError('Restauration du presse-papiers incomplète. Vérifie le champ avant de réessayer.') from error
        finally:
            _release(self.saved)
