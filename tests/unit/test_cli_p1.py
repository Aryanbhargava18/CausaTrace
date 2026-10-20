from __future__ import annotations

from pathlib import Path

from causatrace.cli import main


def test_fix_is_preview_only_until_write_is_explicit(tmp_path: Path, capsys) -> None:
    target = tmp_path / "program.py"
    original = "import numpy as np\nprint(np.random.default_rng().random())\n"
    target.write_text(original, encoding="utf-8")

    assert main(["fix", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == original
    assert "Preview only" in capsys.readouterr().err

    assert main(["fix", str(target), "--write"]) == 0
    assert "default_rng(0)" in target.read_text(encoding="utf-8")
    assert "Verification: PASS" in capsys.readouterr().out
