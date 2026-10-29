//! Two-process CLI tests: the exact commands the desktop helper and the Windows
//! companion run. The adapter is opt-in through the `test-profile` feature and
//! `ODESSY_LINK_TEST_PAIRING_ADAPTER=1`; without both, the binary has no exchange.

use serde_json::Value;
use std::process::Command;

const BIN: &str = env!("CARGO_BIN_EXE_odessy-link");

struct State {
    directory: std::path::PathBuf,
}

impl State {
    fn new(name: &str) -> Self {
        let directory = std::env::temp_dir().join(format!(
            "odessy-link-cli-{name}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&directory).expect("state directory");
        Self { directory }
    }

    fn command(&self) -> Command {
        let mut command = Command::new(BIN);
        command
            .env("ODESSY_LINK_STATE_DIR", &self.directory)
            .env("ODESSY_LINK_TEST_PAIRING_ADAPTER", "1");
        command
    }

    fn init(&self) {
        let output = self.command().arg("init").output().expect("init");
        assert!(
            output.status.success(),
            "init failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    fn run(&self, arguments: &[&str]) -> (bool, String, String) {
        let output = self
            .command()
            .args(arguments)
            .output()
            .expect("odessy-link run");
        (
            output.status.success(),
            String::from_utf8_lossy(&output.stdout).to_string(),
            String::from_utf8_lossy(&output.stderr).to_string(),
        )
    }

    fn json(&self, arguments: &[&str]) -> Value {
        let (ok, stdout, stderr) = self.run(arguments);
        assert!(ok, "{arguments:?} failed: {stderr}");
        serde_json::from_str(stdout.trim()).unwrap_or_else(|error| panic!("{arguments:?} returned {stdout:?}: {error}"))
    }

    /// Run with a JSON document on stdin, which is how the code travels: it must not
    /// appear in a process command line.
    #[cfg(feature = "test-profile")]
    fn json_stdin(&self, arguments: &[&str], body: &str) -> Value {
        use std::io::Write as _;
        let mut child = self
            .command()
            .args(arguments)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .expect("spawn");
        child
            .stdin
            .take()
            .expect("stdin")
            .write_all(body.as_bytes())
            .expect("write code");
        let output = child.wait_with_output().expect("wait");
        assert!(
            output.status.success(),
            "{arguments:?} failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        serde_json::from_slice(&output.stdout).expect("snapshot")
    }
}

impl Drop for State {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.directory);
    }
}

#[cfg(feature = "test-profile")]
fn workspace(state: &State) -> std::path::PathBuf {
    let path = state.directory.join("workspace");
    std::fs::create_dir_all(&path).expect("workspace");
    path
}

#[test]
fn a_build_without_the_test_profile_has_no_pairing_exchange() {
    let state = State::new("gate");
    state.init();
    // No profile requested: the production snapshot, always closed.
    let snapshot = state.json(&["pairing", "capabilities"]);
    assert_eq!(snapshot["supported"], Value::Bool(false));
    assert_eq!(snapshot["reason"], Value::String("protocol_review_required".into()));
    assert_eq!(snapshot["state"], Value::String("closed".into()));
    // A code cannot be produced for a window that was never opened.
    if cfg!(feature = "test-profile") {
        // The feature is present, so this invocation may open a window; it must still
        // say the profile is unapproved.
        let snapshot = state.json(&["pairing", "begin", "--profile", "test-adapter"]);
        assert_eq!(
            snapshot["profile"],
            Value::String("test-adapter-unapproved".into())
        );
        assert_eq!(snapshot["approved"], Value::Bool(false));
        assert_eq!(snapshot["supported"], Value::Bool(true));
    } else {
        // Without the feature the adapter does not exist, and asking for it is an
        // error rather than a silent fallback to something unreviewed.
        let (ok, stdout, stderr) = state.run(&["pairing", "begin", "--profile", "test-adapter"]);
        assert!(!ok, "a build without the adapter must refuse to open a window");
        assert!(
            stderr.contains("no unapproved pairing profile"),
            "{stderr}"
        );
        assert!(!stdout.contains("code"), "{stdout}");
    }
}

#[cfg(feature = "test-profile")]
mod journey {
    use super::*;
    use std::io::{BufRead, BufReader};

    /// The ordinary-user journey on one machine pair: no certificate export, no
    /// fingerprint, no executable path and no terminal command.
    #[test]
    fn laptop_and_jetson_pair_with_a_six_digit_code_and_an_explicit_approval() {
        let laptop = State::new("laptop");
        let jetson = State::new("jetson");
        laptop.init();
        jetson.init();
        let shared = workspace(&laptop);
        let shared = shared.to_string_lossy().to_string();

        let began = laptop.json(&[
            "pairing",
            "begin",
            "--workspace",
            &shared,
            "--profile",
            "test-adapter",
        ]);
        assert_eq!(began["state"], Value::String("discoverable".into()));
        let code = began["code"].as_str().expect("code").to_string();
        assert_eq!(code.len(), 6, "{code}");
        assert!(code.bytes().all(|byte| byte.is_ascii_digit()), "{code}");
        let session = began["session_id"].as_str().expect("session").to_string();
        assert!(!session.is_empty());

        // The listener is a separate process, exactly as the companion runs it.
        let mut child = laptop
            .command()
            .args([
                "pairing",
                "listen",
                "--listen",
                "127.0.0.1:0",
                "--profile",
                "test-adapter",
            ])
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .expect("listener");
        let stderr = child.stderr.take().expect("stderr");
        let (sender, receiver) = std::sync::mpsc::channel();
        let reader = std::thread::spawn(move || {
            let mut sent = false;
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                if let Some(rest) = line.strip_prefix("pairing listener on port ") {
                    let port = rest.split_whitespace().next().unwrap_or("").to_string();
                    if !sent {
                        let _ = sender.send(port);
                        sent = true;
                    }
                }
            }
        });
        let port = receiver
            .recv_timeout(std::time::Duration::from_secs(20))
            .expect("the listener must report its port");
        assert!(!port.is_empty());

        let joined = jetson.json_stdin(
            &[
                "pairing",
                "join",
                "--address",
                &format!("127.0.0.1:{port}"),
                "--code-stdin",
                "--profile",
                "test-adapter",
            ],
            &format!("{{\"code\":\"{code}\"}}"),
        );
        assert_eq!(joined["role"], Value::String("joiner".into()));
        assert_eq!(joined["state"], Value::String("paired_no_access".into()));
        assert!(joined["peer"]["alias"].as_str().is_some());

        let output = child.wait_with_output().expect("listener exits");
        let _ = reader.join();
        assert!(
            output.status.success(),
            "listener failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        let host: Value =
            serde_json::from_slice(&output.stdout).expect("listener snapshot");
        assert_eq!(host["state"], Value::String("awaiting_approval".into()));
        let token = host["review_token"].as_str().expect("review token").to_string();
        assert!(!token.is_empty());

        // A stale token is refused rather than guessed at.
        let refused = laptop.json(&[
            "pairing",
            "confirm",
            "--session-id",
            &session,
            "--review-token",
            "00000000000000000000000000000000",
            "--profile",
            "test-adapter",
        ]);
        assert_eq!(refused["committed"], Value::Bool(false));

        // The laptop operator approves the authenticated request and workspace access.
        let confirmed = laptop.json(&[
            "pairing",
            "confirm",
            "--session-id",
            &session,
            "--review-token",
            &token,
            "--workspace",
            &shared,
            "--profile",
            "test-adapter",
        ]);
        assert_eq!(confirmed["committed"], Value::Bool(true), "{confirmed}");
        assert_eq!(confirmed["state"], Value::String("ready".into()));
        assert_eq!(confirmed["grant_workspace"], Value::String(shared.clone()));

        // The host now has an executable peer; the joiner has an address book entry
        // with no capability on either side.
        let host_peers = laptop.json(&["peers", "list", "--json"]);
        let peer = &host_peers["peers"][0];
        assert_eq!(peer["capability_mcp"], Value::Bool(true));
        assert_eq!(peer["workspace"], Value::String(shared.clone()));
        assert_eq!(peer["revoked"], Value::Bool(false));
        let joiner_peers = jetson.json(&["peers", "list", "--json"]);
        let host_peer = &joiner_peers["peers"][0];
        assert_eq!(
            host_peer["capability_mcp"],
            Value::Bool(false),
            "enrolling the host must not grant it anything locally"
        );
        assert_eq!(host_peer["revoked"], Value::Bool(false));

        // A second confirm is the same approved operation, not a second grant.
        let revision = host_peers["grant_revision"].clone();
        let again = laptop.json(&[
            "pairing",
            "confirm",
            "--session-id",
            &session,
            "--review-token",
            &token,
            "--profile",
            "test-adapter",
        ]);
        assert_eq!(again["committed"], Value::Bool(true));
        assert_eq!(
            laptop.json(&["peers", "list", "--json"])["grant_revision"],
            revision,
            "a repeat approval must not change the grant revision"
        );

        // Status after the fact is truthful, and no code is rendered any more.
        let status = laptop.json(&["pairing", "status", "--profile", "test-adapter"]);
        assert_eq!(status["state"], Value::String("ready".into()));
        assert!(status["code"].is_null());
        assert_eq!(status["approved"], Value::Bool(false));
    }

    #[test]
    fn a_wrong_code_is_refused_and_the_window_exhausts_after_three_attempts() {
        let laptop = State::new("laptop-wrong");
        let jetson = State::new("jetson-wrong");
        laptop.init();
        jetson.init();
        let began = laptop.json(&["pairing", "begin", "--profile", "test-adapter"]);
        let session = began["session_id"].as_str().expect("session").to_string();
        let code = began["code"].as_str().expect("code").to_string();
        // A different six-digit value, guaranteed not to match.
        let wrong = if code == "000000" { "000001" } else { "000000" };

        for attempt in 1..=3 {
            let mut child = laptop
                .command()
                .args([
                    "pairing",
                    "listen",
                    "--listen",
                    "127.0.0.1:0",
                    "--seconds",
                    "30",
                    "--profile",
                    "test-adapter",
                ])
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .spawn()
                .expect("listener");
            let stderr = child.stderr.take().expect("stderr");
            let (sender, receiver) = std::sync::mpsc::channel();
            std::thread::spawn(move || {
                let mut sent = false;
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    if let Some(rest) = line.strip_prefix("pairing listener on port ") {
                        if !sent {
                            let _ = sender.send(rest.split_whitespace().next().unwrap_or("").to_string());
                            sent = true;
                        }
                    }
                }
            });
            let port = receiver
                .recv_timeout(std::time::Duration::from_secs(20))
                .expect("port");
            let joined = jetson.json_stdin(
                &[
                    "pairing",
                    "join",
                    "--address",
                    &format!("127.0.0.1:{port}"),
                    "--code-stdin",
                    "--profile",
                    "test-adapter",
                ],
                &format!("{{\"code\":\"{wrong}\"}}"),
            );
            assert!(
                joined["state"] == Value::String("rejected".into())
                    || joined["state"] == Value::String("incomplete".into()),
                "attempt {attempt}: {joined}"
            );
            let status = laptop.json(&["pairing", "status", "--profile", "test-adapter"]);
            assert_eq!(
                status["attempts_remaining"].as_i64().expect("attempts"),
                (3 - attempt) as i64,
                "attempt {attempt}: {status}"
            );
            // A window that still has attempts keeps listening; the test stops it.
            let _ = child.kill();
            let _ = child.wait();
            if attempt < 3 {
                assert!(
                    status["state"] == Value::String("discoverable".into()),
                    "the window stays open while attempts remain: {status}"
                );
            } else {
                assert_eq!(status["state"], Value::String("rejected".into()));
            }
        }
        let final_status = laptop.json(&["pairing", "status", "--session-id", &session, "--profile", "test-adapter"]);
        assert_eq!(final_status["attempts_remaining"].as_i64(), Some(0));
    }
}
