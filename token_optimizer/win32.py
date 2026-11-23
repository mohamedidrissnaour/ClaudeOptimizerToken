"""The small Win32 surface required by the Windows-only companion."""

import ctypes as ct
from ctypes import wintypes as wt
from dataclasses import dataclass
import ntpath
import os
from pathlib import Path, PureWindowsPath
import re


user32 = ct.WinDLL('user32', use_last_error=True)
kernel32 = ct.WinDLL('kernel32', use_last_error=True)


def bind(dll, name, returns, *arguments):
    function = getattr(dll, name)
    function.restype = returns
    function.argtypes = arguments
    return function


foreground = bind(user32, 'GetForegroundWindow', wt.HWND)
_window_pid = bind(user32, 'GetWindowThreadProcessId', wt.DWORD, wt.HWND, ct.POINTER(wt.DWORD))
_open_process = bind(kernel32, 'OpenProcess', wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
_close_handle = bind(kernel32, 'CloseHandle', wt.BOOL, wt.HANDLE)
_image_name = bind(kernel32, 'QueryFullProcessImageNameW', wt.BOOL, wt.HANDLE, wt.DWORD,
                   wt.LPWSTR, ct.POINTER(wt.DWORD))
_class_name = bind(user32, 'GetClassNameW', ct.c_int, wt.HWND, wt.LPWSTR, ct.c_int)
_async_key = bind(user32, 'GetAsyncKeyState', wt.SHORT, ct.c_int)
_system_setting = bind(user32, 'SystemParametersInfoW', wt.BOOL, wt.UINT, wt.UINT,
                       ct.c_void_p, wt.UINT)
register_hotkey = bind(user32, 'RegisterHotKey', wt.BOOL, wt.HWND, ct.c_int, wt.UINT, wt.UINT)
unregister_hotkey = bind(user32, 'UnregisterHotKey', wt.BOOL, wt.HWND, ct.c_int)

_ENUM_CALLBACK = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
_enum_children = bind(user32, 'EnumChildWindows', wt.BOOL, wt.HWND, _ENUM_CALLBACK, wt.LPARAM)
_is_visible = bind(user32, 'IsWindowVisible', wt.BOOL, wt.HWND)
_window_rectangle = bind(user32, 'GetWindowRect', wt.BOOL, wt.HWND, ct.POINTER(wt.RECT))
_monitor_from_rect = bind(user32, 'MonitorFromRect', wt.HANDLE, ct.POINTER(wt.RECT), wt.DWORD)
_FOCUS_CALLBACK = ct.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)
_watch_foreground = bind(user32, 'SetWinEventHook', wt.HANDLE, wt.DWORD, wt.DWORD, wt.HMODULE,
                         _FOCUS_CALLBACK, wt.DWORD, wt.DWORD, wt.DWORD)
_unwatch_foreground = bind(user32, 'UnhookWinEvent', wt.BOOL, wt.HANDLE)


class ForegroundChanges:
    def __init__(self, callback):
        @_FOCUS_CALLBACK
        def changed(_hook, _event, _window, _object, _child, _thread, _when):
            callback()

        self.callback = changed
        self.handle = _watch_foreground(3, 3, None, changed, 0, 0, 0)  # foreground only; no keyboard hook

    def close(self):
        if self.handle:
            _unwatch_foreground(self.handle)
            self.handle = None


class _MonitorInfo(ct.Structure):
    _fields_ = [('size', wt.DWORD), ('monitor', wt.RECT), ('work', wt.RECT),
                ('flags', wt.DWORD), ('device', wt.WCHAR * 32)]


_monitor_info = bind(user32, 'GetMonitorInfoW', wt.BOOL, wt.HANDLE, ct.POINTER(_MonitorInfo))


def anchor_in_window(window: int) -> tuple[int, int, int, int] | None:
    """Fallback placement near the bottom of Claude if the editor is inaccessible."""
    rect = wt.RECT()
    if not _window_rectangle(window, ct.byref(rect)) or rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    inset_x = min(100, width // 7)
    bottom = rect.bottom - min(65, height // 8)
    top = max(rect.top, bottom - min(83, height // 5))
    return rect.left + inset_x, top, rect.right - inset_x, bottom


def monitor_at(bounds: tuple[int, int, int, int]) -> tuple[str, int, int] | None:
    """Return device name and native origin for conversion to Qt screen coordinates."""
    rect = wt.RECT(*bounds)
    monitor = _monitor_from_rect(ct.byref(rect), 2)  # MONITOR_DEFAULTTONEAREST
    info = _MonitorInfo()
    info.size = ct.sizeof(info)
    if monitor and _monitor_info(monitor, ct.byref(info)):
        return info.device, info.monitor.left, info.monitor.top
    return None


class _GuiThreadInfo(ct.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('flags', wt.DWORD), ('active', wt.HWND), ('focus', wt.HWND),
                ('capture', wt.HWND), ('menuOwner', wt.HWND), ('moveSize', wt.HWND), ('caret', wt.HWND),
                ('caretRect', wt.RECT)]


_gui_thread_info = bind(user32, 'GetGUIThreadInfo', wt.BOOL, wt.DWORD, ct.POINTER(_GuiThreadInfo))


def keyboard_focus(window: int) -> int:
    info = _GuiThreadInfo()
    info.cbSize = ct.sizeof(info)
    if _gui_thread_info(0, ct.byref(info)) and info.active == window and not info.flags & 0x1E:
        return info.focus or 0
    return 0


def process_image(pid: int) -> str:
    handle = _open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ''
    try:
        path = ct.create_unicode_buffer(32768)
        size = wt.DWORD(len(path))
        return path.value if _image_name(handle, 0, path, ct.byref(size)) else ''
    finally:
        _close_handle(handle)


def window_pid(window: int) -> int:
    pid = wt.DWORD()
    _window_pid(window, ct.byref(pid))
    return pid.value


_SQUIRREL_DIR = re.compile(r'app-\d+(?:\.\d+)+(?:[-+][\w.-]+)?', re.I)


def is_claude_executable(image: str, localappdata: str | None = None) -> bool:
    base = localappdata if localappdata is not None else os.environ.get('LOCALAPPDATA', '')
    if not (base and image):
        return False
    path = PureWindowsPath(ntpath.normpath(image))
    install = PureWindowsPath(base) / 'AnthropicClaude'
    if path.name.casefold() != 'claude.exe':
        return False
    return (path.parent == install or
            (path.parent.parent == install and bool(_SQUIRREL_DIR.fullmatch(path.parent.name))))


@dataclass(frozen=True, slots=True)
class Window:
    handle: int
    pid: int


def claude_window(handle: int | None) -> Window | None:
    if not handle:
        return None
    pid = window_pid(handle)
    name = ct.create_unicode_buffer(128)
    _class_name(handle, name, len(name))
    if (pid and name.value.startswith('Chrome_WidgetWin_') and
            is_claude_executable(process_image(pid))):
        return Window(handle, pid)
    return None


def renderer_children(window: int) -> list[int]:
    children: list[int] = []

    @_ENUM_CALLBACK
    def visit(child, _data):
        name = ct.create_unicode_buffer(128)
        _class_name(child, name, len(name))
        if name.value == 'Chrome_RenderWidgetHostHWND' and _is_visible(child):
            children.append(child)
        return True

    _enum_children(window, visit, 0)
    return children[:6]


def installed_claude() -> Path | None:
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        return None
    root = Path(base) / 'AnthropicClaude'
    candidates = [path for path in root.glob('app-*/claude.exe')
                  if is_claude_executable(str(path)) and path.is_file()]
    if candidates:
        return max(candidates, key=lambda p: tuple(map(int, re.findall(r'\d+', p.parent.name.split('+')[0]))))
    path = root / 'claude.exe'
    return path if path.is_file() else None


def motion_enabled() -> bool:
    flag = wt.BOOL()
    return not _system_setting(0x1042, 0, ct.byref(flag), 0) or bool(flag.value)


_ULONG_PTR = ct.c_size_t


class _MouseInput(ct.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD),
                ('flags', wt.DWORD), ('time', wt.DWORD), ('extra', _ULONG_PTR)]


class _KeyInput(ct.Structure):
    _fields_ = [('key', wt.WORD), ('scan', wt.WORD), ('flags', wt.DWORD),
                ('time', wt.DWORD), ('extra', _ULONG_PTR)]


class _HardwareInput(ct.Structure):
    _fields_ = [('message', wt.DWORD), ('low', wt.WORD), ('high', wt.WORD)]


class _InputPayload(ct.Union):
    _fields_ = [('mouse', _MouseInput), ('keyboard', _KeyInput), ('hardware', _HardwareInput)]


class _Input(ct.Structure):
    _fields_ = [('type', wt.DWORD), ('payload', _InputPayload)]


_send_input = bind(user32, 'SendInput', wt.UINT, wt.UINT, ct.POINTER(_Input), ct.c_int)


def modifiers_down() -> bool:
    return any(_async_key(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C, 0x56))


def paste_keystroke() -> bool:
    # Ctrl+V only. Never Enter, Tab, a click, or an instruction submission.
    entries = (_Input * 4)()
    for entry, (key, flags) in zip(entries, ((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2))):
        entry.type = 1
        entry.payload.keyboard = _KeyInput(key, 0, flags, 0, 0)
    accepted = _send_input(len(entries), entries, ct.sizeof(_Input))
    if 0 < accepted < len(entries):
        # Clean up a possible injected Ctrl-down if Windows accepted a partial batch.
        _send_input(1, ct.byref(entries[3]), ct.sizeof(_Input))
    return accepted == len(entries)
