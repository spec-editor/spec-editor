"""Cross-platform process and signal utilities.

Avoids os.kill / POSIX signals that fail on Windows.
"""

import os as _os
import signal as _signal
import sys as _sys


def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID exists (cross-platform)."""
    # POSIX: signal 0 checks existence
    try:
        _os.kill(pid, 0)
        return True
    except OSError:
        pass

    # Windows fallback
    if _sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True

    return False


def terminate_process(pid: int) -> bool:
    """Terminate a process by PID. Returns True if signal sent successfully.

    POSIX: SIGTERM. Windows: TerminateProcess.
    """
    if _sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        result = kernel32.TerminateProcess(handle, 1)
        kernel32.CloseHandle(handle)
        return bool(result)
    else:
        try:
            _os.kill(pid, _signal.SIGTERM)
            return True
        except OSError:
            return False


def get_signal_for_stop() -> int:
    """Return a signal suitable for clean shutdown (cross-platform).

    POSIX: SIGTERM. Windows: SIGBREAK.
    """
    if _sys.platform == "win32":
        return _signal.SIGBREAK
    return _signal.SIGTERM


def get_signal_for_interrupt() -> int:
    """Return a signal for user interrupt (Ctrl+C / Ctrl+Break).

    POSIX: SIGINT. Windows: SIGINT (Ctrl+C) — same on both.
    """
    return _signal.SIGINT


def set_signal_handlers(
    on_shutdown,
    on_interrupt=None,
) -> None:
    """Register cross-platform signal handlers for clean shutdown.

    POSIX: SIGTERM + SIGHUP + SIGINT.
    Windows: SIGBREAK + SIGINT.
    """
    stop_sig = get_signal_for_stop()
    intr_sig = get_signal_for_interrupt()

    _signal.signal(stop_sig, on_shutdown)
    # SIGHUP only exists on POSIX
    if hasattr(_signal, "SIGHUP"):
        _signal.signal(_signal.SIGHUP, on_shutdown)
    if on_interrupt:
        _signal.signal(intr_sig, on_interrupt)


def get_temp_dir() -> str:
    """Cross-platform temp directory path."""
    import tempfile
    return tempfile.gettempdir()
