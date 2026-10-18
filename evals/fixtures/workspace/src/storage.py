"""Markdown file storage for notes."""

from pathlib import Path


class NoteStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, name: str, text: str) -> Path:
        # TODO: handle disk-full errors instead of crashing.
        path = self.directory / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        return path
