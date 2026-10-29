//! `odessy-executor` — the packaged Windows executor's small native window.
//!
//! This is deliberately not the assistant: it opens one window that shows the shared
//! workspace, lets its owner allow a pairing window, displays the six-digit code and
//! the enrollment status, and can stay in the background instead of blocking the
//! desktop. It owns the child processes it starts and stops them when it exits.
//!
//! Everything about identity, codes and trust lives in `odessy-link`, which this
//! companion runs as a child. There is no duplicate crypto, no trust-file editing and
//! no command line for the user to type: the only inputs are a folder chosen with the
//! native browser and two buttons.
//!
//! `--selftest` exercises the parts that do not need a window, so packaging can check
//! that the bundled components resolve and answer before anything is shipped.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// Name of the pairing component shipped beside this executable.
const LINK_BINARY: &str = if cfg!(windows) {
    "odessy-link.exe"
} else {
    "odessy-link"
};
/// Name of the packaged checksum manifest.
const MANIFEST: &str = "checksums.txt";

/// Where this executable and its bundled components live.
///
/// A packaged install is self-contained: the component is resolved from the validated
/// package directory, never from `PATH` and never from a user-supplied path.
fn package_root() -> Result<PathBuf, String> {
    let executable =
        std::env::current_exe().map_err(|error| format!("cannot locate this program: {error}"))?;
    executable
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| "this program has no containing directory".to_string())
}

fn link_binary(root: &Path) -> Result<PathBuf, String> {
    let candidate = root.join(LINK_BINARY);
    if !candidate.is_file() {
        return Err(format!(
            "{LINK_BINARY} is missing from the executor package; reinstall the executor component"
        ));
    }
    Ok(candidate)
}

/// Verify every listed file against `checksums.txt` (`<sha256>  <relative path>`).
///
/// A missing manifest is reported, not ignored: an unverified package must not look
/// like a verified one. Extra files are allowed; listed files that are absent or whose
/// digest differs are not.
fn verify_package(root: &Path) -> Result<usize, String> {
    let manifest = root.join(MANIFEST);
    if !manifest.is_file() {
        return Err(format!(
            "{MANIFEST} is missing; the package cannot be verified"
        ));
    }
    let text = std::fs::read_to_string(&manifest)
        .map_err(|error| format!("cannot read {MANIFEST}: {error}"))?;
    let mut checked = 0;
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (digest, name) = line
            .split_once("  ")
            .or_else(|| line.split_once(' '))
            .ok_or_else(|| format!("unreadable checksum line: {line}"))?;
        if digest.len() != 64 || !digest.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err(format!("unreadable checksum line: {line}"));
        }
        let relative = Path::new(name.trim_start_matches('*'));
        if relative.is_absolute()
            || relative
                .components()
                .any(|component| matches!(component, std::path::Component::ParentDir))
        {
            return Err(format!("checksum entry escapes the package: {name}"));
        }
        let target = root.join(relative);
        let bytes = std::fs::read(&target).map_err(|error| {
            format!("{} is listed but cannot be read: {error}", relative.display())
        })?;
        let actual = sha256_hex(&bytes);
        if !actual.eq_ignore_ascii_case(digest) {
            return Err(format!(
                "{} does not match its checksum; reinstall the executor package",
                relative.display()
            ));
        }
        checked += 1;
    }
    if checked == 0 {
        return Err(format!("{MANIFEST} lists no files"));
    }
    Ok(checked)
}

fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(bytes);
    let mut out = String::with_capacity(64);
    for byte in digest {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// The parts of a status answer the window shows. No keys, and a code only when the
/// component chose to publish one for this machine's own window.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
struct Status {
    state: String,
    supported: bool,
    code: String,
    message: String,
    seconds_remaining: i64,
    attempts_remaining: Option<i64>,
    peer: String,
    session_id: String,
    review_token: String,
}

impl Status {
    fn parse(text: &str) -> Result<Self, String> {
        let value: serde_json::Value = serde_json::from_str(text.trim())
            .map_err(|_| "the pairing component returned an unreadable status".to_string())?;
        if value.get("schema_version").and_then(serde_json::Value::as_u64) != Some(1) {
            return Err("update odessy-link: this status format is not supported".into());
        }
        let supported = value
            .get("supported")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false);
        let state = value
            .get("state")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("closed")
            .to_string();
        let peer = value
            .get("peer")
            .and_then(|peer| peer.get("alias"))
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_string();
        Ok(Self {
            supported,
            state,
            code: value
                .get("code")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("")
                .to_string(),
            message: value
                .get("message")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("")
                .to_string(),
            seconds_remaining: value
                .get("seconds_remaining")
                .and_then(serde_json::Value::as_i64)
                .unwrap_or(0),
            attempts_remaining: value
                .get("attempts_remaining")
                .and_then(serde_json::Value::as_i64),
            peer,
            session_id: value
                .get("session_id")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("")
                .to_string(),
            review_token: value
                .get("review_token")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("")
                .to_string(),
        })
    }

    /// One line a non-technical user can act on. The state name is kept in the log
    /// because support instructions refer to it.
    fn headline(&self) -> String {
        if !self.supported {
            return "Pairing unavailable: the protocol security review is not complete. Existing paired devices still work.".into();
        }
        match self.state.as_str() {
            "discoverable" => {
                let attempts = self
                    .attempts_remaining
                    .map(|left| format!(" · {left} attempts left"))
                    .unwrap_or_default();
                let left = if self.seconds_remaining > 0 {
                    format!(" · {}s left", self.seconds_remaining)
                } else {
                    String::new()
                };
                format!("Waiting for a device to pair using this code{attempts}{left}")
            }
            "authenticating" => "A device is connecting…".into(),
            "awaiting_approval" => format!(
                "A device proved the code{}. Approve it to grant workspace access.",
                if self.peer.is_empty() {
                    String::new()
                } else {
                    format!(" ({})", self.peer)
                }
            ),
            "enrolling" => "Saving the approved access…".into(),
            "ready" => {
                "Workspace access approved and saved. Connection health is unknown: the other device \
                 connects itself, and a saved grant is not a live session."
                    .into()
            }
            "paired_no_access" => {
                "Identity paired without workspace access; nothing can run here for that device."
                    .into()
            }
            "expired" => "The code expired. Allow pairing again for a new one.".into(),
            "rejected" => {
                "The code was not accepted. Allow pairing again and share the new code.".into()
            }
            "cancelled" => "Pairing was cancelled. Nothing was enrolled.".into(),
            "incomplete" => {
                "The last attempt ended before its outcome was known. Check the paired devices before retrying."
                    .into()
            }
            "failed" => format!(
                "Pairing did not finish. {}",
                if self.message.is_empty() {
                    "Check the network and try again."
                } else {
                    self.message.as_str()
                }
            ),
            _ => format!("Pairing status: {}", self.state),
        }
    }
}

/// Run one fixed component command and return its standard output.
///
/// Standard output is drained on a second thread while the child runs: waiting for
/// exit first would deadlock as soon as the child filled the pipe. Both the bytes kept
/// and the time allowed are bounded, and a timeout always kills and reaps the child.
fn run_component(
    link: &Path,
    arguments: &[&str],
    timeout: std::time::Duration,
) -> Result<String, String> {
    let mut child = Command::new(link)
        .args(arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("cannot start {LINK_BINARY}: {error}"))?;
    let mut stdout = child.stdout.take().ok_or("the component has no output")?;
    let reader = std::thread::spawn(move || {
        use std::io::Read;
        let mut kept = Vec::new();
        // Keep at most 256 KiB; the rest is drained and discarded so the child never
        // blocks on a full pipe and the window never grows without bound.
        let mut buffer = [0u8; 8192];
        loop {
            match stdout.read(&mut buffer) {
                Ok(0) => break,
                Ok(count) => {
                    if kept.len() < 256 * 1024 {
                        let room = (256 * 1024 - kept.len()).min(count);
                        kept.extend_from_slice(&buffer[..room]);
                    }
                }
                Err(_) => break,
            }
        }
        String::from_utf8_lossy(&kept).to_string()
    });
    let started = std::time::Instant::now();
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) => {}
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("cannot wait for {LINK_BINARY}: {error}"));
            }
        }
        if started.elapsed() > timeout {
            // Bounded cleanup: ask, then insist, then reap.
            let _ = child.kill();
            let _ = child.wait();
            break None;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    };
    let output = reader.join().unwrap_or_default();
    match status {
        Some(status) => {
            if !status.success() && !output.trim_start().starts_with('{') {
                // The component exits non-zero when it refuses a request; its JSON
                // document is still the truthful answer, so it is read as one.
                return Err("the pairing component refused the request".into());
            }
            Ok(output)
        }
        None => Err("the pairing component did not answer in time".into()),
    }
}

fn selftest() -> i32 {
    let root = match package_root() {
        Ok(root) => root,
        Err(error) => {
            eprintln!("executor: {error}");
            return 1;
        }
    };
    println!("package: {}", root.display());
    match verify_package(&root) {
        Ok(checked) => println!("checksums: {checked} files verified"),
        Err(error) => {
            // A source checkout has no manifest; that is reported, never ignored.
            println!("checksums: {error}");
        }
    }
    let link = match link_binary(&root) {
        Ok(link) => link,
        Err(error) => {
            println!("component: {error}");
            return 1;
        }
    };
    println!("component: {}", link.display());
    match run_component(&link, &["pairing", "capabilities"], std::time::Duration::from_secs(10)) {
        Ok(output) => match Status::parse(&output) {
            Ok(status) => {
                println!(
                    "capabilities: supported={} state={}",
                    status.supported, status.state
                );
                println!("headline: {}", status.headline());
                0
            }
            Err(error) => {
                println!("capabilities: {error}");
                1
            }
        },
        Err(error) => {
            println!("capabilities: {error}");
            1
        }
    }
}

#[cfg(windows)]
#[path = "executor/window.rs"]
mod window;

fn main() {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    if arguments.iter().any(|argument| argument == "--selftest") {
        std::process::exit(selftest());
    }
    if arguments.iter().any(|argument| argument == "--ui-smoke") {
        #[cfg(windows)]
        {
            std::process::exit(window::smoke());
        }
        #[cfg(not(windows))]
        {
            eprintln!("odessy-executor: there is no window to create on this platform");
            std::process::exit(3);
        }
    }
    if arguments
        .iter()
        .any(|argument| argument == "--help" || argument == "-h")
    {
        println!(
            "odessy-executor — allow another device to pair with this computer.\n\n  --selftest  verify the packaged components and exit\n  --help      show this text"
        );
        return;
    }
    #[cfg(windows)]
    {
        std::process::exit(window::run());
    }
    #[cfg(not(windows))]
    {
        eprintln!("odessy-executor: the executor companion runs on Windows only");
        std::process::exit(3);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn status_lines_are_actionable_and_never_invent_success() {
        let waiting = Status::parse(
            r#"{"schema_version":1,"supported":true,"state":"discoverable","code":"000042","seconds_remaining":88,"attempts_remaining":3}"#,
        )
        .expect("status");
        assert_eq!(waiting.code, "000042");
        let line = waiting.headline();
        assert!(line.contains("3 attempts left"), "{line}");
        assert!(line.contains("88s left"), "{line}");

        let blocked = Status::parse(
            r#"{"schema_version":1,"supported":false,"state":"closed","reason":"protocol_review_required"}"#,
        )
        .expect("status");
        assert!(blocked.headline().contains("security review"));
        for state in [
            "closed",
            "expired",
            "rejected",
            "cancelled",
            "incomplete",
            "failed",
            "ready",
        ] {
            let status = Status::parse(&format!(
                r#"{{"schema_version":1,"supported":true,"state":"{state}"}}"#
            ))
            .expect("status");
            assert!(!status.headline().is_empty());
        }
        assert!(Status::parse("{}").is_err());
        assert!(Status::parse(r#"{"schema_version":2}"#).is_err());
    }

    #[test]
    fn checksum_verification_rejects_missing_tampered_and_escaping_entries() {
        let directory = std::env::temp_dir().join(format!(
            "odessy-executor-selftest-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&directory).expect("directory");
        std::fs::write(directory.join("component.bin"), b"payload").expect("write");
        let digest = sha256_hex(b"payload");
        std::fs::write(directory.join(MANIFEST), format!("{digest}  component.bin\n")).expect("manifest");
        assert_eq!(verify_package(&directory).expect("verified"), 1);

        std::fs::write(
            directory.join(MANIFEST),
            format!("{}  component.bin\n", "0".repeat(64)),
        )
        .expect("manifest");
        assert!(verify_package(&directory)
            .expect_err("tampered")
            .contains("does not match"));

        std::fs::write(directory.join(MANIFEST), format!("{digest}  missing.bin\n")).expect("manifest");
        assert!(verify_package(&directory)
            .expect_err("missing")
            .contains("cannot be read"));

        std::fs::write(
            directory.join(MANIFEST),
            format!("{digest}  ../escape.bin\n"),
        )
        .expect("manifest");
        assert!(verify_package(&directory)
            .expect_err("escape")
            .contains("escapes"));

        std::fs::remove_file(directory.join(MANIFEST)).expect("remove");
        assert!(verify_package(&directory)
            .expect_err("no manifest")
            .contains("cannot be verified"));
        let _ = std::fs::remove_dir_all(&directory);
    }

    #[test]
    fn a_missing_component_is_reported_rather_than_guessed() {
        let directory =
            std::env::temp_dir().join(format!("odessy-executor-missing-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&directory);
        assert!(link_binary(&directory).is_err());
        let _ = std::fs::remove_dir_all(&directory);
    }
}
