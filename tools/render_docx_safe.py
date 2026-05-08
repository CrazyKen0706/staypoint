from __future__ import annotations

import os
import runpy
import shutil
import sys
import tempfile
import uuid
from pathlib import Path


class SafeTemporaryDirectory:
    def __init__(self, suffix=None, prefix=None, dir=None, ignore_cleanup_errors=False):
        base = Path(dir or os.environ.get("TMP") or os.environ.get("TEMP") or ".")
        base.mkdir(parents=True, exist_ok=True)
        self.name = str(base / f"{prefix or 'tmp'}{uuid.uuid4().hex}{suffix or ''}")
        Path(self.name).mkdir(parents=True, exist_ok=False)
        self.ignore_cleanup_errors = ignore_cleanup_errors

    def __enter__(self):
        return self.name

    def cleanup(self):
        shutil.rmtree(self.name, ignore_errors=self.ignore_cleanup_errors)

    def __exit__(self, exc_type, exc, tb):
        self.cleanup()


def main() -> None:
    tempfile.TemporaryDirectory = SafeTemporaryDirectory  # type: ignore[assignment]
    script = Path(r"C:/Users/14572/.codex/plugins/cache/openai-primary-runtime/documents/26.430.10722/skills/documents/render_docx.py")
    sys.argv[0] = str(script)
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
