import subprocess
import sys
from pathlib import Path


def test_native_trace_logging_survives_rotation(tmp_path) -> None:
    script = """
import faulthandler
import sys
from pathlib import Path
from app.core.log_setup import DiagnosticFileHandler
handler = DiagnosticFileHandler(Path(sys.argv[1]) / 'application.log',
                                maxBytes=1024, backupCount=3, encoding='utf-8')
try:
    handler.enable_crash_tracing()
    assert faulthandler.is_enabled()
    handler.doRollover()
    assert faulthandler.is_enabled()
    faulthandler.dump_traceback(file=handler.stream)
finally:
    handler.close()
assert not faulthandler.is_enabled()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "<string>" in (tmp_path / "application.log").read_text(encoding="utf-8")
