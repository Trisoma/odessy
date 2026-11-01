"""Where credentials live: one list for the sandbox, the policy and the command rules.

The sandbox hides these from a confined command where it can, and the policy refuses a
command that names them, so a host whose backend cannot hide a path (landlock, Windows)
is still covered. ``~`` is the home of the machine that runs the command.
"""

from __future__ import annotations

#: Folders and files no confined command needs to read: credentials, keys, browser
#: profiles, and Odessy's own configuration. A path that does not exist on the host
#: is skipped there.
CREDENTIAL_PATHS: tuple[str, ...] = (
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.azure",
    "~/.kube",
    "~/.docker",
    "~/.netrc",
    "~/.pypirc",
    "~/.npmrc",
    "~/.git-credentials",
    "~/.config/gcloud",
    "~/.config/gh",
    "~/.config/odessy",
    "~/.local/share/keyrings",
    "~/.password-store",
    "~/.mozilla",
    "~/.config/google-chrome",
    "~/.config/chromium",
    "~/.config/BraveSoftware",
    "~/.config/microsoft-edge",
    "~/Library/Keychains",
    "~/Library/Application Support/Google/Chrome",
    "~/Library/Application Support/Firefox",
    "~/AppData/Roaming/Microsoft/Credentials",
    "~/AppData/Local/Microsoft/Credentials",
    "~/AppData/Roaming/Microsoft/Protect",
    "~/AppData/Local/Google/Chrome/User Data",
    "~/AppData/Local/Microsoft/Edge/User Data",
    "~/AppData/Roaming/Mozilla/Firefox",
)

#: A path with one of these as a segment is a credential, wherever it is.
SENSITIVE_SEGMENTS = frozenset({
    ".aws",
    ".azure",
    ".docker",
    ".git-credentials",
    ".gnupg",
    ".kube",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".ssh",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
})

_MARKERS = tuple("/" + path.removeprefix("~/").lower() + "/" for path in CREDENTIAL_PATHS)


def names_credential_path(word: str) -> bool:
    """Whether a word of a command is, or is under, a credential path."""

    return any(marker in f"/{word.lower().replace(chr(92), '/').rstrip('/')}/" for marker in _MARKERS)


__all__ = ["CREDENTIAL_PATHS", "SENSITIVE_SEGMENTS", "names_credential_path"]
