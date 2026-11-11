"""The private interpreter's application registration (.pth) and its repair survival.

Tool gateways are fresh `-I` processes of the installed dependency environment; they can
import the application only through a `.pth` that assembly writes. A dependency repair
rebuilds that environment from scratch, so the registration must be written again.
"""
from __future__ import annotations

from pathlib import Path

from installer.components import application
from installer.operations import component_repair


def _installed_root(tmp_path: Path) -> Path:
    root = tmp_path / "Odessy Äpp"
    (root / "application" / "src").mkdir(parents=True)
    (root / "dependencies" / "venv" / "Lib" / "site-packages").mkdir(parents=True)
    return root


def test_registration_is_absolute_and_idempotent(tmp_path):
    root = _installed_root(tmp_path)
    path = application.register_installed_source(root / "dependencies", root / "application")
    assert path == root / "dependencies" / "venv" / "Lib" / "site-packages" / "odessy-application.pth"
    expected = str((root / "application" / "src").resolve()) + "\n"
    assert path.read_text(encoding="utf-8") == expected
    assert Path(expected.strip()).is_absolute()
    # A second write (reinstall/upgrade) keeps exactly one registration.
    application.register_installed_source(root / "dependencies", root / "application")
    assert path.read_text(encoding="utf-8") == expected


def test_dependency_repair_restores_the_registration(tmp_path):
    root = _installed_root(tmp_path)
    path = root / "dependencies" / "venv" / "Lib" / "site-packages" / "odessy-application.pth"
    path.write_text("stale\n", encoding="utf-8")

    component_repair.restore_application_registration(root)

    assert path.read_text(encoding="utf-8") == str((root / "application" / "src").resolve()) + "\n"


def test_repair_without_an_application_registers_nothing(tmp_path):
    root = tmp_path / "bare"
    (root / "dependencies" / "venv" / "Lib" / "site-packages").mkdir(parents=True)

    component_repair.restore_application_registration(root)

    assert not (root / "dependencies" / "venv" / "Lib" / "site-packages"
                / "odessy-application.pth").exists()
