"""Reading a shell command for the policy: what it would do, as far as text can tell.

A command is split into its simple commands, honouring quotes, and each is judged by
its program and arguments rather than by a substring of the whole line. ``grep token
src`` searches for a word; ``cat token.txt`` prints a file. Three outcomes:

* **Refused** (``deny``): running in the background, code from the network, writes to
  the operating system, credentials, and the commands that destroy a tree or a
  machine. No one can approve these.
* **High risk** (``confirm``, ``high``): must be answered with a typed ``yes`` on the
  keyboard or the authenticated UI, and cannot be allowed for the session or approved
  in advance in ``commands.toml``.
* Anything else is left to the rest of the policy.

This is a screen, not a boundary. There is no shell parser here, a program can do
anything, and ``python -c`` is opaque: the sandbox is what holds. The screen exists so
that the question put to a person is the right one.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Literal

from odessy.authority.secret_paths import names_credential_path
from odessy.authority.workspace_authority import is_sensitive_workspace_path


@dataclass(frozen=True)
class Finding:
    """Why a command is refused or high risk. ``reason`` finishes "because ..."."""

    action: Literal["deny", "confirm"]
    rule: str
    reason: str


_BACKGROUND = Finding("deny", "command_background", "background or detached execution is outside the request lifecycle")
_REMOTE = Finding("deny", "command_remote_execution", "it runs code downloaded from the network")
_SYSTEM = Finding("deny", "command_system_write", "it writes to an operating-system location")
_DESTRUCTIVE = Finding("deny", "command_destructive", "the command is destructive or credential-seeking")
_CREDENTIALS = Finding("deny", "command_credentials", "the command is destructive or credential-seeking")


def _high(rule: str, reason: str) -> Finding:
    return Finding("confirm", rule, reason)


_INSTALL = _high("command_install", "it installs packages from the network")
_PRIVILEGE = _high("command_privilege", "it runs with elevated privileges")
_DISK = _high("command_disk", "it writes to a disk or partition directly")
_RECURSIVE = _high("command_recursive", "it deletes or changes a whole tree")
_HISTORY = _high("command_git_shared", "it rewrites shared history or changes how git runs code")
_PUBLISH = _high("command_publish", "publishing cannot be taken back")
_NETWORK = _high("command_exfiltration", "it can send data to another machine")
_ENCODED = _high("command_encoded", "the command it runs cannot be read")
_PERSISTENCE = _high("command_persistence", "the change outlives this turn")
_DOWNLOAD_RUN = _high("command_download_then_run", "it runs what it downloads")

# --- splitting ---------------------------------------------------------------------


def _split(command: str) -> tuple[list[list[str]], bool]:
    """The simple commands in ``command`` as lists of words, and whether one runs in
    the background. Quotes group words; ``>`` and ``<`` are words of their own."""

    commands: list[list[str]] = []
    words: list[str] = []
    word: list[str] = []
    quote: str | None = None
    background = False

    def end_word() -> None:
        if word:
            words.append("".join(word))
            word.clear()

    def end_command() -> None:
        nonlocal words
        end_word()
        if words:
            commands.append(words)
            words = []

    for index, char in enumerate(command):
        before = command[index - 1] if index else ""
        after = command[index + 1] if index + 1 < len(command) else ""
        if quote is not None:
            if char == quote:
                quote = None
            else:
                word.append(char)
        elif char in "'\"":
            quote = char
        elif char.isspace() and char != "\n":
            end_word()
        elif char in ";\n|()`":
            end_command()
        elif char == "&":
            if before == ">" or after == ">":  # 2>&1, &>file
                word.append(char)
            elif before == "&" or after == "&":
                end_command()
            elif word or words:
                background = True
                end_command()
            # A leading & is PowerShell's call operator, not a background job.
        elif char in "<>":
            end_word()
            words.append(char)
        else:
            word.append(char)
    end_command()
    return commands, background


def program_name(word: str) -> str:
    """``/usr/bin/RM.exe`` -> ``rm``."""

    name = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return re.sub(r"\.(exe|com|cmd|bat)$", "", name)


#: Programs that run the command after them.
_WRAPPERS = frozenset({"command", "exec", "time", "nice", "ionice", "stdbuf", "timeout", "xargs", "env", "busybox", "call"})
_ELEVATORS = frozenset({"sudo", "doas", "su", "pkexec", "runas", "gsudo"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _peel(words: list[str]) -> tuple[list[str], bool]:
    """The command under any wrappers and assignments, and whether one elevated it."""

    words = list(words)
    elevated = False
    while words:
        name = program_name(words[0])
        if _ASSIGNMENT.match(words[0]):
            words.pop(0)
        elif name in _ELEVATORS or name in _WRAPPERS:
            elevated = elevated or name in _ELEVATORS
            words.pop(0)
            while words and (words[0].startswith("-") or words[0].replace(".", "").isdigit()):
                words.pop(0)
        else:
            break
    return words, elevated


# --- the rules ---------------------------------------------------------------------

#: Something that fetches from the network...
_DOWNLOADER = r"(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod|\(new-object\s+net\.webclient\)\.downloadstring)"
#: ...and something that runs what it is given.
_INTERPRETER = r"(?:sh|bash|zsh|dash|ksh|fish|python3?|perl|ruby|node|iex|invoke-expression|powershell|pwsh|cmd)"
_REMOTE_EXECUTION = (
    # curl https://x/install.sh | sh,  iwr https://x | iex
    re.compile(rf"\b{_DOWNLOADER}\b[^|;&]*\|\s*(?:sudo\s+)?{_INTERPRETER}(?:\.exe)?\b"),
    # bash <(curl ...),  sh -c "$(curl ...)",  iex (iwr ...)
    re.compile(rf"\b{_INTERPRETER}\b[^;&|]*(?:<\(|\$\(|\()\s*{_DOWNLOADER}\b"),
)
_DECODE_AND_RUN = re.compile(rf"\bbase64\s+(?:-d|-di|--decode)\b[^|;&]*\|\s*(?:sudo\s+)?{_INTERPRETER}\b")


def matches_remote_execution(command: str) -> bool:
    """Download-and-run: the payload is chosen by whoever controls the URL.

    The first evaluation runs showed models proposing ``curl ... | sh`` when asked to
    "install the helper tool", and it was only *confirmed*: one "yes" from the user
    ran a script nobody had seen. Downloading a file is still allowed through the
    normal rules; piping it straight into an interpreter is not.
    """

    return any(pattern.search(command) for pattern in _REMOTE_EXECUTION)


#: Operating-system locations, in the slash-normalized, lowercased command.
#: POSIX roots must start a path ("src/lib/x" is the workspace, "/lib/x" is not).
_SYSTEM_LOCATION = (
    r"(?:(?<![\w./-])(?:/etc|/usr|/bin|/sbin|/boot|/lib|/system/library)/"
    r"|(?<![\w./-])(?:[a-z]:/windows|/c/windows)\b|%systemroot%|\$env:systemroot)"
)
#: Write verbs and redirections, in the same normalized command.
_WRITE = r"(?:>>?|\btee\b|\bcp\b|\bmv\b|\binstall\b|\bset-content\b|\badd-content\b|\bout-file\b|\bnew-item\b|\bcopy-item\b|\bmove-item\b|\bcopy\b|\bmove\b|\bxcopy\b|\brobocopy\b)"
_SYSTEM_WRITE = re.compile(rf"{_WRITE}[^;&|]*{_SYSTEM_LOCATION}")


def matches_system_write(command: str) -> bool:
    """A write whose target is an operating-system directory.

    A write verb or redirection followed, in the same simple command, by a system
    location. Reading such a location is unaffected. The workspace is the only place
    Odessy writes to.
    """

    return _SYSTEM_WRITE.search(command.replace("\\", "/")) is not None


_DETACHING = frozenset({"disown", "nohup", "schtasks", "setsid", "start", "start-job", "start-process", "saps"})
_MACHINE = frozenset({"format", "shutdown", "reboot", "halt", "poweroff", "stop-computer", "restart-computer"})
_DISK_TOOLS = frozenset({
    "dd", "fdisk", "parted", "diskpart", "wipefs", "shred", "mkswap", "blkdiscard", "bcdedit",
    "format-volume", "clear-disk", "remove-partition", "initialize-disk",
})
_REMOTE_SHELLS = frozenset({"ssh", "scp", "sftp", "ftp", "telnet", "nc", "ncat", "netcat", "socat"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
_POWERSHELLS = frozenset({"powershell", "pwsh"})
_DOWNLOADERS = frozenset({"curl", "wget", "iwr", "irm", "invoke-webrequest", "invoke-restmethod"})
_RUNNERS = _SHELLS | _POWERSHELLS | frozenset({"python", "python3", "py", "node", "perl", "ruby", "cmd"})
#: Programs that print or move a file named on their command line. A word such as
#: "token" names a file only for these: ``pytest test_tokenizer.py`` reads no secret.
_FILE_MOVERS = frozenset({
    "cat", "type", "get-content", "gc", "head", "tail", "less", "more", "bat", "tac", "nl", "strings",
    "base64", "xxd", "hexdump", "od", "sed", "awk", "cp", "copy", "copy-item", "mv", "move", "move-item",
    "rsync", "scp", "curl",
})
_SECRET_WORD = re.compile(r"token|secret|password|passwd|credential")
#: Commands that print a credential whatever their arguments say.
_CREDENTIAL_COMMANDS = (
    ("gh", "auth", "token"), ("git", "credential"), ("gcloud", "auth", "print-access-token"),
    ("security", "find-generic-password"), ("secret-tool", "lookup"), ("cmdkey", "/list"),
)
_PROFILES = frozenset({
    ".bashrc", ".bash_profile", ".bash_login", ".zshrc", ".zprofile", ".profile", ".gitconfig",
    "microsoft.powershell_profile.ps1", "$profile",
})
_PROFILE_WRITERS = frozenset({"tee", "cp", "mv", "ln", "copy", "move", "set-content", "add-content", "out-file", "copy-item", "move-item"})
_GIT_CODE_SETTINGS = re.compile(r"^(core\.(hookspath|sshcommand|pager|fsmonitor|editor|gitproxy)|alias\.|url\.|credential\.|filter\.|diff\.\S*\.command)")
_PUBLISHERS = (
    ("npm", "publish"), ("pnpm", "publish"), ("yarn", "publish"), ("twine", "upload"), ("cargo", "publish"),
    ("poetry", "publish"), ("flit", "publish"), ("hatch", "publish"), ("uv", "publish"), ("gem", "push"),
    ("dotnet", "nuget", "push"), ("docker", "push"), ("podman", "push"),
)
_SERVICE_CHANGES = frozenset({"enable", "disable", "mask", "unmask", "start", "stop", "restart", "daemon-reload"})

_INSTALLERS = frozenset({"pip", "pip3", "npm", "pnpm", "yarn", "uv", "cargo", "go"})


def matches_install(tokens: list[str]) -> bool:
    """Installing packages: code from the network that runs on this machine."""

    if not tokens:
        return False
    if tokens[0] in _INSTALLERS and ("install" in tokens or "add" in tokens[1:2]):
        return True
    return tokens[:3] in (["python", "-m", "pip"], ["python3", "-m", "pip"]) and "install" in tokens


def only_installs(command: str) -> bool:
    """Whether every part of ``command`` is a package install (or a ``cd`` to get there).

    A network cut is lifted for an install because it needs one, and only then: the
    rest of a chain must not get a network by standing next to one.
    """

    simple, background = _split(command.replace("\\", "/"))
    if background or not simple:
        return False
    parts = [[word.lower() for word in _peel(words)[0]] for words in simple]
    return any(matches_install(words) for words in parts) and all(
        matches_install(words) or words[:1] == ["cd"] for words in parts
    )


_FILE_CHANGERS = _PROFILE_WRITERS | frozenset({
    "rm", "del", "erase", "remove-item", "ri", "sed", "touch", "truncate", "new-item", "ren", "rename", "rename-item",
})


def changes_file_named(command: str, name: str) -> bool:
    """Whether ``command`` writes to or removes a file called ``name``. Reading one
    is not a change: a repository may have its own ``hooks.toml``."""

    for words in _split(command.replace("\\", "/"))[0]:
        peeled = _peel(words)[0]
        if not peeled:
            continue
        named = any(word.lower().rsplit("/", 1)[-1] == name for word in peeled[1:])
        if named and (">" in peeled or program_name(peeled[0]) in _FILE_CHANGERS):
            return True
    return False


def _flags(arguments: list[str]) -> set[str]:
    """Short options as single letters (lowercased), and long options whole."""

    flags: set[str] = set()
    for word in arguments:
        if word.startswith("--"):
            flags.add(word)
        elif word.startswith("-"):
            flags.update(word[1:])
    return flags


def _names_credentials(name: str, lowered: list[str]) -> bool:
    """A word that is a credential path, wherever it appears in the command."""

    for word in lowered:
        if names_credential_path(word):
            return True
        if "$" in word or "%" in word:
            if _SECRET_WORD.search(word):
                return True
        for part in re.split(r"[/:=,'\"()\s]+", word):
            if not part:
                continue
            if is_sensitive_workspace_path(part) and not _SECRET_WORD.search(part) and not part.endswith((".key", ".pem")):
                return True
    if name not in _FILE_MOVERS:
        return False
    return any(is_sensitive_workspace_path(word.rsplit(":", 1)[-1]) for word in lowered[1:] if not word.startswith("-"))


def _git_finding(arguments: list[str]) -> Finding | None:
    index = 0
    while index < len(arguments) and arguments[index].startswith("-"):
        index += 2 if arguments[index] in ("-c", "--git-dir", "--work-tree") else 1
    rest = arguments[index:]
    if not rest:
        return None
    verb, options = rest[0], rest[1:]
    if verb == "reset" and "--hard" in options:
        return _DESTRUCTIVE
    if verb == "clean" and not {"n", "--dry-run"} & _flags(options):
        return _DESTRUCTIVE
    if verb in ("show", "cat-file") and any(is_sensitive_workspace_path(word.rsplit(":", 1)[-1]) for word in options):
        return _CREDENTIALS
    if verb == "push":
        forced = {"f", "--force", "--mirror", "--delete", "--prune"} & _flags(options)
        lease = any(word.startswith("--force-with-lease") for word in options)
        refspec = any(word.startswith(("+", ":")) for word in options)
        if forced or lease or refspec:
            return _HISTORY
    if verb in ("filter-branch", "filter-repo"):
        return _HISTORY
    if verb == "config" and any(_GIT_CODE_SETTINGS.match(word) for word in options):
        return _HISTORY
    if verb == "remote" and options[:1] and options[0] in ("add", "set-url"):
        return _NETWORK
    if verb == "rm" and "r" in _flags(options):
        return _RECURSIVE
    return None


def _curl_sends_data(raw: list[str]) -> bool:
    """Uploading a file. ``raw`` keeps its case: curl's -F and -f are different."""

    for index, word in enumerate(raw):
        if word in ("-T", "-F", "--upload-file", "--form", "--post-file", "--body-file"):
            return True
        if word.startswith("--upload-file=") or word.startswith("--post-file=") or word.startswith("--body-file="):
            return True
        if word in ("-d", "--data", "--data-binary", "--data-raw", "--data-urlencode"):
            if index + 1 < len(raw) and raw[index + 1].startswith("@"):
                return True
        if word.startswith(("-d@", "--data=@", "--data-binary=@")):
            return True
    return False


def _inner_command(name: str, lowered: list[str], raw: list[str]) -> str | None:
    """What a shell was told to run, or None."""

    for index, word in enumerate(lowered[1:], start=1):
        if name in _SHELLS and word.startswith("-") and not word.startswith("--") and "c" in word[1:]:
            return " ".join(raw[index + 1:])
        if name in _POWERSHELLS and word in ("-c", "-command"):
            return " ".join(raw[index + 1:])
        if name == "cmd" and word in ("/c", "/k"):
            return " ".join(raw[index + 1:])
    return None


def _judge(words: list[str], depth: int) -> list[Finding]:
    """What one simple command does."""

    findings: list[Finding] = []
    if program_name(words[0]) in _DETACHING:
        findings.append(_BACKGROUND)
    peeled, elevated = _peel(words)
    if elevated:
        findings.append(_PRIVILEGE)
    if not peeled:
        return findings
    name = program_name(peeled[0])
    lowered = [word.lower() for word in peeled]
    arguments = lowered[1:]

    if name in _DETACHING:
        findings.append(_BACKGROUND)
    if any(word in ("--daemon", "--daemonize") for word in arguments):
        findings.append(_BACKGROUND)
    if name in ("docker", "podman", "docker-compose") and arguments[:1] and arguments[0] in ("run", "create", "up", "compose", "start"):
        if {"d", "--detach"} & _flags(arguments):
            findings.append(_BACKGROUND)
    if name in ("tmux", "screen") and "d" in _flags(arguments):
        findings.append(_BACKGROUND)

    if _names_credentials(name, lowered):
        findings.append(_CREDENTIALS)
    if name == "printenv" and any(_SECRET_WORD.search(word) for word in arguments):
        findings.append(_CREDENTIALS)
    if any(tuple(lowered[: len(entry)]) == entry for entry in _CREDENTIAL_COMMANDS):
        findings.append(_CREDENTIALS)

    flags = _flags(arguments)
    if name == "rm":
        recursive = "r" in flags or "--recursive" in flags
        forced = "f" in flags or "--force" in flags
        if recursive and forced:
            findings.append(_DESTRUCTIVE)
        elif recursive:
            findings.append(_RECURSIVE)
    # rm and del are PowerShell aliases too, where `-r` would be -Recurse; on POSIX it is
    # the flag above, so only the longer spellings count for them.
    shortest = 2 if name in ("remove-item", "ri") else 3
    if name in ("remove-item", "ri", "rm", "del") and any(
        len(word) >= shortest and "-recurse".startswith(word) for word in arguments
    ):
        findings.append(_DESTRUCTIVE)
    if name in ("del", "erase", "rmdir", "rd") and "/s" in arguments:
        findings.append(_DESTRUCTIVE)
    if name in _MACHINE:
        findings.append(_DESTRUCTIVE)

    if name == "git":
        finding = _git_finding(arguments)
        if finding is not None:
            findings.append(finding)
    if name == "find":
        if "-delete" in arguments:
            findings.append(_RECURSIVE)
        for flag in ("-exec", "-execdir", "-ok"):
            if flag in lowered:
                findings.extend(_judge(peeled[lowered.index(flag) + 1:] or [flag], depth))
    if name in ("chmod", "chown"):
        if "r" in flags or "--recursive" in flags:
            findings.append(_RECURSIVE)
        if name == "chmod" and any(word in ("777", "0777", "a+w", "o+w") for word in arguments):
            findings.append(_RECURSIVE)

    if name in _DISK_TOOLS or name.startswith("mkfs"):
        findings.append(_DISK)
    if name in _REMOTE_SHELLS:
        findings.append(_NETWORK)
    if name == "rsync" and any(":" in word for word in arguments if not word.startswith("-")):
        findings.append(_NETWORK)
    if name == "curl" and _curl_sends_data(peeled):
        findings.append(_NETWORK)
    if name in ("wget",) and any(word.startswith(("--post-file", "--body-file")) for word in peeled):
        findings.append(_NETWORK)
    if name in ("invoke-webrequest", "iwr", "invoke-restmethod", "irm"):
        if "-infile" in arguments or any(a == "-method" and b in ("post", "put") for a, b in zip(arguments, arguments[1:])):
            findings.append(_NETWORK)
    if any(tuple(lowered[: len(entry)]) == entry for entry in _PUBLISHERS):
        findings.append(_PUBLISH)

    if name in _POWERSHELLS:
        if any(word == "-ec" or (len(word) > 2 and "-encodedcommand".startswith(word)) for word in arguments):
            findings.append(_ENCODED)
        if any(a in ("-ep", "-executionpolicy", "-ex") and b == "bypass" for a, b in zip(arguments, arguments[1:])):
            findings.append(_ENCODED)
    if name in ("crontab", "launchctl", "new-service"):
        findings.append(_PERSISTENCE)
    if name == "systemctl" and any(word in _SERVICE_CHANGES for word in arguments):
        findings.append(_PERSISTENCE)
    if name == "reg" and arguments[:1] and arguments[0] in ("add", "delete", "import"):
        findings.append(_PERSISTENCE)
    if name == "sc" and arguments[:1] and arguments[0] in ("create", "config", "delete"):
        findings.append(_PERSISTENCE)
    if name in ("set-itemproperty", "new-itemproperty") and any(word.startswith(("hklm:", "hkcu:")) for word in arguments):
        findings.append(_PERSISTENCE)
    if (">" in arguments or name in _PROFILE_WRITERS) and any(program_name(word) in _PROFILES for word in arguments):
        findings.append(_PERSISTENCE)

    if matches_install(lowered):
        findings.append(_INSTALL)

    inner = _inner_command(name, lowered, peeled)
    if inner and depth < 3:
        findings.extend(_read(inner, depth + 1))
    return findings


_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")


def _read(command: str, depth: int) -> list[Finding]:
    normalized = command.replace("\\", "/")
    lowered = normalized.lower()
    findings: list[Finding] = []
    simple, background = _split(normalized)
    if background:
        findings.append(_BACKGROUND)
    if matches_remote_execution(lowered):
        findings.append(_REMOTE)
    if matches_system_write(lowered):
        findings.append(_SYSTEM)
    if _DECODE_AND_RUN.search(lowered):
        findings.append(_ENCODED)
    for words in simple:
        findings.extend(_judge(words, depth))
    if depth < 3:
        for inner in _SUBSTITUTION.finditer(normalized):
            findings.extend(_read(inner.group(1) or inner.group(2) or "", depth + 1))
    programs = [program_name(words[0]) for words in simple]
    for index, name in enumerate(programs):
        if name in _DOWNLOADERS and any(
            later in _RUNNERS or later.startswith("./") or words[0].startswith("./")
            for later, words in zip(programs[index + 1:], simple[index + 1:])
        ):
            findings.append(_DOWNLOAD_RUN)
    return findings


_ORDER = {rule: index for index, rule in enumerate((_BACKGROUND, _REMOTE, _SYSTEM, _DESTRUCTIVE, _CREDENTIALS))}


def classify_command(command: str) -> Finding | None:
    """The strictest finding for ``command``, or None when it is unremarkable."""

    findings = _read(command, 0)
    refused = [finding for finding in findings if finding.action == "deny"]
    if refused:
        return min(refused, key=_ORDER.__getitem__)
    return findings[0] if findings else None


# --- what runs without asking ------------------------------------------------------

#: Read-only commands allowed without confirmation, in both shells. PowerShell's own
#: names were missing, so on the Windows laptop the idiomatic `Get-ChildItem` needed a
#: confirmation that `ls` did not.
_ALLOWED_FIRST_TOKENS = frozenset(
    {
        "pwd", "ls", "dir", "get-childitem", "gci", "get-location", "gl",
        # Where a program is: prints a path, runs nothing.
        "which", "where", "where.exe", "get-command", "gcm",
    }
)

#: Commands that print files. Allowed unasked only for files inside the workspace;
#: see ``OdessyPolicy._reads_workspace_files``.
FILE_READERS = frozenset({"cat", "type", "get-content", "gc", "head", "tail", "wc"})

#: Well-known programs whose ``--version`` / ``--help`` prints and exits. A named list,
#: not "anything with --version": a script in the workspace runs its own code first.
_VERSION_PROGRAMS = frozenset(
    {
        "python", "python3", "py", "pip", "pip3", "node", "npm", "pnpm", "yarn", "deno", "bun",
        "git", "go", "cargo", "rustc", "java", "javac", "dotnet", "uv", "gcc", "g++", "clang",
        "cmake", "make", "ruby", "perl", "php", "ollama", "pwsh", "powershell",
    }
)
_VERSION_FLAGS = frozenset({"--version", "-version", "--help"})

#: Read-only git subcommands. Options that write a file, run an external diff program,
#: or read outside the repository are excluded.
_GIT_READS = frozenset({"status", "log", "show", "diff", "branch"})
_GIT_UNSAFE_OPTION_PREFIXES = ("--output", "--ext-diff", "--textconv", "--no-index")
_GIT_BRANCH_LIST_OPTIONS = frozenset(
    {"-a", "-r", "-v", "-vv", "--list", "--all", "--remotes", "--verbose", "--show-current"}
)

#: Anything that runs a second command. The allow rule looks at the first word only, so
#: without this `ls && python evil.py` was "low risk" because it began with `ls`.
# Parentheses and braces can evaluate commands in PowerShell, even without a
# separator. Treat expression syntax conservatively rather than parsing shells.
CHAINING = re.compile(r"[;&|`\n(){}]")


def _is_git_read(tokens: list[str]) -> bool:
    """``git status|log|show|diff|branch`` without options that write or reach out.

    ``git -C dir log`` and ``git -c key=value log`` do not match: the subcommand must
    come first. Like ``git status`` before it, a repository's own config can still run
    a program (a filter or hook); allowed commands run in read-only mode.
    """

    if len(tokens) < 2 or tokens[0] != "git" or tokens[1] not in _GIT_READS:
        return False
    rest = tokens[2:]
    if any(token.startswith(_GIT_UNSAFE_OPTION_PREFIXES) for token in rest):
        return False
    if tokens[1] == "branch":
        # `git branch name` creates a branch; only listing options are reads.
        return all(token in _GIT_BRANCH_LIST_OPTIONS for token in rest)
    return True


def _is_version_query(tokens: list[str]) -> bool:
    return len(tokens) == 2 and tokens[0] in _VERSION_PROGRAMS and tokens[1] in _VERSION_FLAGS


def matches_allow(command: str, tokens: list[str]) -> bool:
    if not tokens or CHAINING.search(command):
        return False
    if tokens[0] in _ALLOWED_FIRST_TOKENS:
        return True
    if _is_version_query(tokens):
        return True
    if tokens[0] == "echo" and not any(mark in command for mark in ("$", "%")):
        # Without variables: `echo $env:API_KEY` would print a secret into the turn.
        return True
    return _is_git_read(tokens)


#: Error output thrown away or merged into stdout. Neither writes a file, so neither
#: makes a read "may modify state". Anything else after the name (`/dev/null.txt`) is
#: a file, and stays a write.
_DISCARDED_STDERR = re.compile(r"(?<![\w>])2>(?:&1|\s*(?:/dev/null|\$null|nul))(?![^\s;|&)])")


def matches_confirm(command: str, tokens: list[str]) -> bool:
    if not tokens:
        return False
    write_commands = {
        "touch",
        "mkdir",
        "copy",
        "cp",
        "move",
        "mv",
        "set-content",
        "new-item",
    }
    if ">" in _DISCARDED_STDERR.sub("", command):
        return True
    if tokens[0] == "git" and not (_is_git_read(tokens) or _is_version_query(tokens)):
        return True
    return tokens[0] in write_commands


__all__ = [
    "CHAINING",
    "FILE_READERS",
    "Finding",
    "classify_command",
    "matches_allow",
    "matches_confirm",
    "matches_install",
    "matches_remote_execution",
    "matches_system_write",
    "changes_file_named",
    "only_installs",
    "program_name",
]
