import sys

import pytest

from app.core.performance import (
    ProcessSnapshot,
    build_performance_report,
    format_mebibytes,
    take_process_snapshot,
)


def test_performance_report_calculates_resource_changes() -> None:
    start = ProcessSnapshot(
        measured_at=10.0,
        cpu_seconds=2.0,
        memory_bytes=100 * 1024 * 1024,
        peak_memory_bytes=110 * 1024 * 1024,
    )
    end = ProcessSnapshot(
        measured_at=20.0,
        cpu_seconds=2.5,
        memory_bytes=104 * 1024 * 1024,
        peak_memory_bytes=120 * 1024 * 1024,
    )

    report = build_performance_report(start, end)

    assert report.elapsed_seconds == 10.0
    assert report.average_cpu_percent == 5.0
    assert report.memory_change_bytes == 4 * 1024 * 1024
    assert format_mebibytes(report.memory_start_bytes) == "100.0 MiB"
    assert format_mebibytes(report.memory_change_bytes) == "4.0 MiB"


def test_unknown_memory_has_clear_log_value() -> None:
    assert format_mebibytes(None) == "unavailable"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process counters")
def test_windows_snapshot_reads_process_memory() -> None:
    snapshot = take_process_snapshot()

    assert snapshot.memory_bytes is not None
    assert snapshot.memory_bytes > 0
    assert snapshot.peak_memory_bytes is not None
    assert snapshot.peak_memory_bytes >= snapshot.memory_bytes
