"""Start the local shortcut listener without a console or a visible window."""

import ctypes
import os
from pathlib import Path
import traceback


def main() -> int:
    # Qt uses Windows' ICU API, not any same-named DLL inherited from PATH.
    system_icu = ctypes.WinDLL('icuuc.dll', winmode=0x00000800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
    from token_optimizer.application import run

    return run()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        try:
            diagnostics = Path(os.environ['LOCALAPPDATA']) / 'LocalTools' / 'ClaudeTokenOptimizer'
            diagnostics.mkdir(parents=True, exist_ok=True)
            (diagnostics / 'demarrage.log').write_text(traceback.format_exc(), encoding='utf-8')
        except (OSError, KeyError):
            pass
        raise SystemExit(1)
