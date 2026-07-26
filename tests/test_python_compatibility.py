import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_package_sources_parse_with_python39_grammar():
    for path in (ROOT / "portfolio_news").glob("*.py"):
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            feature_version=(3, 9),
        )


def test_package_metadata_and_pins_support_python39():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    dev_requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.9,<3.15"' in pyproject
    assert 'requires = ["flit_core==3.12.0"]' in pyproject
    assert "google-auth==2.50.0" in requirements
    assert "google-auth-httplib2==0.3.0" in requirements
    assert "pytest==8.4.2" in dev_requirements


def test_package_sources_avoid_python310_dataclass_slots():
    for path in (ROOT / "portfolio_news").glob("*.py"):
        assert "slots=True" not in path.read_text(encoding="utf-8")
