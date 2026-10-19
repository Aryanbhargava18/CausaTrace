from __future__ import annotations

from pathlib import Path

from causatrace.locator import SourceLocator


def test_static_locator_reports_requested_heuristic_rule_families(tmp_path: Path) -> None:
    target = tmp_path / "program.py"
    target.write_text(
        "import os\n"
        "import random\n"
        "import numpy as np\n"
        "from datetime import datetime\n"
        "from pathlib import Path\n"
        "from sklearn.ensemble import RandomForestClassifier\n"
        "items = set(['b', 'a'])\n"
        "print(list(items))\n"
        "print(random.random())\n"
        "rng = np.random.default_rng()\n"
        "print(os.getenv('CAUSATRACE_TEST_VALUE'))\n"
        "print(Path('.').iterdir())\n"
        "print(datetime.now())\n"
        "model = RandomForestClassifier()\n",
        encoding="utf-8",
    )

    findings = SourceLocator(tmp_path).find_static(tmp_path)

    assert {finding.rule_id for finding in findings} == {
        "unordered_set",
        "python_random",
        "numpy_default_rng",
        "environment_read",
        "filesystem_listing",
        "implicit_local_time",
        "sklearn_random_state",
    }
    assert all(finding.location.kind == "static_rule" for finding in findings)
    assert all(finding.location.confidence == "heuristic" for finding in findings)
    assert all(finding.message.startswith("Heuristic:") for finding in findings)


def test_static_locator_does_not_warn_for_explicit_controls(tmp_path: Path) -> None:
    target = tmp_path / "stable.py"
    target.write_text(
        "import numpy as np\n"
        "from datetime import datetime, timezone\n"
        "from sklearn.ensemble import RandomForestClassifier\n"
        "print(sorted(set(['b', 'a'])))\n"
        "rng = np.random.default_rng(17)\n"
        "print(datetime.now(timezone.utc))\n"
        "model = RandomForestClassifier(random_state=17)\n",
        encoding="utf-8",
    )

    assert SourceLocator().find_static(target) == ()


def test_runtime_locator_prefers_project_frame_and_classifies_other_frames(
    tmp_path: Path,
) -> None:
    user_file = tmp_path / "program.py"
    user_file.write_text("print('ok')\n", encoding="utf-8")

    locations = SourceLocator(tmp_path).find_runtime(
        (
            {
                "operation": "random.random",
                "path": "/opt/venv/lib/python3.12/site-packages/pkg.py",
                "line": 8,
                "kind": "dependency",
                "frames": [
                    {
                        "path": "/opt/venv/lib/python3.12/site-packages/pkg.py",
                        "line": 8,
                        "kind": "dependency",
                    },
                    {"path": str(user_file), "line": 4, "kind": "user"},
                ],
            },
            {
                "operation": "time.localtime",
                "path": "/usr/local/lib/python3.12/time.py",
                "line": 12,
                "kind": "stdlib",
            },
            {
                "operation": "package.call",
                "path": "/opt/venv/lib/python3.12/site-packages/pkg.py",
                "line": 21,
                "kind": "dependency",
            },
            {
                "operation": "static-rule",
                "path": str(user_file),
                "line": 1,
                "kind": "static_rule",
            },
        )
    )

    assert [(location.kind, location.path, location.line) for location in locations] == [
        ("runtime_user", str(user_file), 4),
        ("runtime_stdlib", "/usr/local/lib/python3.12/time.py", 12),
        ("runtime_dependency", "/opt/venv/lib/python3.12/site-packages/pkg.py", 21),
        ("static_rule", str(user_file), 1),
    ]
    assert locations[0].confidence == "runtime"
    assert locations[-1].confidence == "heuristic"
