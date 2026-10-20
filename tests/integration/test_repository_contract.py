from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_repository_reproducibility_contract_is_checked_in() -> None:
    required = (
        ".python-version",
        "uv.lock",
        "Dockerfile",
        "Makefile",
        "README.md",
        ".env.example",
        "LICENSE",
    )
    assert all((ROOT / path).is_file() for path in required)
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12.8"
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "test:" in makefile
    assert "judge:" in makefile
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "tzdata" in dockerfile
    assert 'CMD ["make", "test"]' in dockerfile


def test_env_template_has_no_secret_material() -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "API_KEY=" not in template
    assert "TOKEN=" not in template
    assert "SECRET=" not in template
    assert "PRIVATE_KEY" not in template
