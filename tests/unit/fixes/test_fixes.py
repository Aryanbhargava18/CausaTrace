from __future__ import annotations

from causatrace.fixes import preview_fixes


def test_preview_fixes_returns_one_diff_without_writing_source() -> None:
    source = (
        "import glob\n"
        "import numpy as np\n"
        "import os\n"
        "from pathlib import Path\n"
        "from sklearn.ensemble import RandomForestClassifier\n"
        "\n"
        "model = RandomForestClassifier(n_estimators=2)\n"
        "rng = np.random.default_rng()\n"
        "files = os.listdir('.')\n"
        "matches = glob.glob('*.py')\n"
        "paths = Path('.').iterdir()\n"
        "values = list(set(['b', 'a']))\n"
        "for value in set(values):\n"
        "    print(value)\n"
    )

    result = preview_fixes(source, filename="example.py")

    assert result.changed is True
    assert result.warnings == ()
    assert result.transformed_source != source
    assert "RandomForestClassifier(n_estimators=2, random_state=0)" in result.transformed_source
    assert "np.random.default_rng(0)" in result.transformed_source
    assert "sorted(os.listdir('.'))" in result.transformed_source
    assert "sorted(glob.glob('*.py'))" in result.transformed_source
    assert "sorted(Path('.').iterdir())" in result.transformed_source
    assert "values = sorted(set(['b', 'a']))" in result.transformed_source
    assert "for value in sorted(set(values)):" in result.transformed_source
    assert "--- example.py\n" in result.unified_diff
    assert "+++ example.py (fixed)\n" in result.unified_diff


def test_preview_is_unchanged_for_existing_controls_and_plain_syntax() -> None:
    source = (
        "from pathlib import Path\n"
        "from sklearn.ensemble import RandomForestClassifier\n"
        "model = RandomForestClassifier(random_state=0)\n"
        "rng = __import__('numpy').random.default_rng(seed=0)\n"
        "files = sorted(Path('.').glob('*.py'))\n"
    )

    result = preview_fixes(source)

    assert result == preview_fixes(source)
    assert result.changed is False
    assert result.unified_diff == ""
    assert result.warnings == ()
    assert result.transformed_source == source


def test_preview_warns_and_skips_ambiguous_star_arguments() -> None:
    source = (
        "from numpy.random import default_rng\n"
        "from sklearn.ensemble import RandomForestClassifier\n"
        "kwargs = {}\n"
        "rng = default_rng(**kwargs)\n"
        "model = RandomForestClassifier(**kwargs)\n"
    )

    result = preview_fixes(source)

    assert result.changed is False
    assert len(result.warnings) == 2
    assert "default_rng(**kwargs)" in result.warnings[0]
    assert "sklearn estimator" in result.warnings[1]


def test_preview_resolves_import_aliases_for_recognized_estimators() -> None:
    result = preview_fixes(
        "from sklearn.ensemble import RandomForestClassifier as RFC\n"
        "model = RFC()\n"
    )

    assert result.transformed_source.endswith("model = RFC(random_state=0)\n")
    assert result.warnings == ()


def test_preview_reports_invalid_source_without_file_access() -> None:
    source = "model = RandomForestClassifier(\n"

    result = preview_fixes(source, filename="broken.py")

    assert result.changed is False
    assert result.transformed_source == source
    assert result.unified_diff == ""
    assert result.warnings == ("Could not preview fixes for broken.py: '(' was never closed at line 1.",)
