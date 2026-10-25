"""Small process-performance measurements for stage checks."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import sys
from time import monotonic, process_time


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    """One point-in-time view of this application's resource use."""

    measured_at: float
    cpu_seconds: float
    memory_bytes: int | None
    peak_memory_bytes: int | None


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    """Resource changes measured between two snapshots."""

    elapsed_seconds: float
    average_cpu_percent: float
    memory_start_bytes: int | None
    memory_end_bytes: int | None
    peak_memory_bytes: int | None

    @property
    def memory_change_bytes(self) -> int | None:
        if self.memory_start_bytes is None or self.memory_end_bytes is None:
            return None
        return self.memory_end_bytes - self.memory_start_bytes


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _windows_memory_usage() -> tuple[int | None, int | None]:
    if sys.platform != "win32":
        return None, None

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        get_process_memory_info = kernel32.K32GetProcessMemoryInfo
        get_process_memory_info.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessMemoryCounters),
            wintypes.DWORD,
        )
        get_process_memory_info.restype = wintypes.BOOL
    except (AttributeError, OSError):
        return None, None

    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    succeeded = get_process_memory_info(
        kernel32.GetCurrentProcess(),
        ctypes.byref(counters),
        counters.cb,
    )
    if not succeeded:
        return None, None
    return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)


def take_process_snapshot() -> ProcessSnapshot:
    """Measure wall time, process CPU time, and Windows working-set memory."""
    memory_bytes, peak_memory_bytes = _windows_memory_usage()
    return ProcessSnapshot(
        measured_at=monotonic(),
        cpu_seconds=process_time(),
        memory_bytes=memory_bytes,
        peak_memory_bytes=peak_memory_bytes,
    )


def build_performance_report(
    start: ProcessSnapshot,
    end: ProcessSnapshot,
) -> PerformanceReport:
    """Summarize application resource use between two snapshots."""
    elapsed_seconds = max(0.0, end.measured_at - start.measured_at)
    cpu_seconds = max(0.0, end.cpu_seconds - start.cpu_seconds)
    average_cpu_percent = (
        cpu_seconds / elapsed_seconds * 100.0 if elapsed_seconds > 0.0 else 0.0
    )
    return PerformanceReport(
        elapsed_seconds=elapsed_seconds,
        average_cpu_percent=average_cpu_percent,
        memory_start_bytes=start.memory_bytes,
        memory_end_bytes=end.memory_bytes,
        peak_memory_bytes=end.peak_memory_bytes,
    )


def format_mebibytes(byte_count: int | None) -> str:
    """Format a byte count for a compact diagnostic log entry."""
    if byte_count is None:
        return "unavailable"
    return f"{byte_count / (1024 * 1024):.1f} MiB"
