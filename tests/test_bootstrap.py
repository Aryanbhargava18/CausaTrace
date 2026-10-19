from causatrace import __version__
from causatrace.cli import build_parser


def test_package_version_and_cli_exist() -> None:
    assert __version__ == "0.1.0"
    assert build_parser().prog == "causatrace"
