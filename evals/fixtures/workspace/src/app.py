"""HTTP entry point for Lumen."""

from src.storage import NoteStore

# TODO: add request logging before the first public release.


def create_app(store: NoteStore) -> dict:
    return {"store": store, "routes": ["/notes", "/health"]}


def health() -> dict:
    return {"status": "ok"}
