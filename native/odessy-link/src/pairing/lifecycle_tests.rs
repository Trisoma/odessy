//! Requirement-level tests for durable enrollment sessions.
//!
//! These exercise the real store, the real trust registry and the real staged
//! commit. They do not need a socket: the exchange has its own tests, and the
//! protocol itself stays closed until M0 is approved.

use super::cli::commit;
use super::profile;
use super::store::{self, Store};
use crate::identity::Identity;
use rusqlite::Connection;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard};

/// `ODESSY_LINK_STATE_DIR` is process-wide, so tests that touch real trust state take
/// this lock rather than racing another test's environment.
static ENVIRONMENT: Mutex<()> = Mutex::new(());

struct Fixture {
    directory: PathBuf,
    guard: MutexGuard<'static, ()>,
}

impl Fixture {
    fn new(name: &str) -> Self {
        let guard = ENVIRONMENT.lock().unwrap_or_else(|error| error.into_inner());
        let directory = std::env::temp_dir().join(format!(
            "odessy-link-lifecycle-{name}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&directory).expect("state directory");
        std::env::set_var("ODESSY_LINK_STATE_DIR", &directory);
        Self { directory, guard }
    }

    fn store(&self) -> Store {
        Store::open(&self.directory.join("pairing.sqlite3")).expect("store")
    }

    /// The way `pairing status` and the companion inspect a session.
    fn store_readonly(&self) -> Store {
        Store::open_readonly(&self.directory.join("pairing.sqlite3")).expect("store")
    }

    fn workspace(&self) -> PathBuf {
        let path = self.directory.join("workspace");
        std::fs::create_dir_all(&path).expect("workspace");
        path
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        std::env::remove_var("ODESSY_LINK_STATE_DIR");
        let _ = std::fs::remove_dir_all(&self.directory);
        let _ = &self.guard;
    }
}

/// Move a session's window into the past without waiting two minutes.
fn backdate(path: &Path, session: &str, seconds: i64) {
    let connection = Connection::open(path).expect("sqlite");
    connection
        .execute(
            "UPDATE session SET expires_at = ?2 WHERE session_id = ?1",
            rusqlite::params![session, store::now() - seconds],
        )
        .expect("backdate");
}

fn pending_peer(store: &Store, alias: &str, identity: &Identity, session: &str) {
    store
        .transition(session, &["discoverable"], "authenticating", "test")
        .expect("authenticating");
    store
        .record_pending_peer(
            session,
            alias,
            &identity.installation_id,
            &identity.fingerprint(),
            &identity.ca_pem,
            &identity.certificate_pem,
        )
        .expect("pending peer");
}

#[test]
fn a_code_keeps_its_leading_zeros_in_persistent_state() {
    let fixture = Fixture::new("code");
    let store = fixture.store();
    let session = store
        .begin_host(None, "000042", "token", "op", "session-code")
        .expect("session");
    assert_eq!(session.code, "000042");
    drop(store);
    let reopened = fixture.store();
    let session = reopened.session("session-code").expect("read").expect("row");
    assert_eq!(session.code, "000042");
    assert_eq!(session.state, "discoverable");
    assert!(store::valid_code(&session.code));
}

#[test]
fn a_session_past_its_window_is_expired_not_silently_usable() {
    let fixture = Fixture::new("window");
    let store = fixture.store();
    let session = store
        .begin_host(None, "111111", "token", "op", "session-window")
        .expect("session");
    assert_eq!(session.expires_at - session.created_at, store::WINDOW_SECONDS);
    backdate(&fixture.directory.join("pairing.sqlite3"), "session-window", 1);
    assert_eq!(store.refresh("session-window").expect("refresh").state, "expired");
    assert!(!store::is_open("expired"));
    // A second begin is allowed once the previous window is closed.
    let next = store
        .begin_host(None, "222222", "token2", "op2", "session-window-2")
        .expect("new window");
    assert_eq!(next.state, "discoverable");
}

#[test]
fn an_expired_window_is_expired_by_recovery_after_a_restart() {
    let fixture = Fixture::new("recovery-expiry");
    let store = fixture.store();
    store
        .begin_host(None, "333333", "token", "op", "session-expire")
        .expect("session");
    backdate(&fixture.directory.join("pairing.sqlite3"), "session-expire", 5);
    drop(store);
    let recovered = fixture.store();
    assert_eq!(
        recovered.session("session-expire").expect("read").expect("row").state,
        "expired"
    );
}

#[test]
fn three_attempts_are_shared_across_sessions_and_reserved_atomically() {
    let fixture = Fixture::new("attempts");
    let store = fixture.store();
    store
        .begin_host(None, "444444", "token", "op", "session-attempts")
        .expect("session");
    for expected in [true, true, true] {
        assert_eq!(store.reserve_attempt().expect("reserve"), expected);
    }
    assert!(
        !store.reserve_attempt().expect("reserve"),
        "a fourth guess must be refused"
    );
    assert_eq!(store.selection().expect("budget").attempts_remaining(), 0);
    // A new window is the operator's action, and it restores the budget.
    store
        .begin_host(None, "555555", "token2", "op2", "session-attempts-2")
        .expect("new window");
    assert_eq!(store.selection().expect("budget").attempts_remaining(), 3);
}

#[test]
fn concurrent_attempt_reservations_never_exceed_the_budget() {
    let fixture = Fixture::new("concurrent");
    let path = fixture.directory.join("pairing.sqlite3");
    let store = std::sync::Arc::new(Store::open(&path).expect("store"));
    store
        .begin_host(None, "666666", "token", "op", "session-concurrent")
        .expect("session");
    let mut handles = Vec::new();
    for _ in 0..8 {
        let store = store.clone();
        handles.push(std::thread::spawn(move || store.reserve_attempt()));
    }
    let granted = handles
        .into_iter()
        .map(|handle| handle.join().expect("join").expect("reserve"))
        .filter(|granted| *granted)
        .count();
    assert_eq!(granted, store::MAX_AUTH_ATTEMPTS as usize);
}

#[test]
fn at_most_two_unauthenticated_connections_are_open_at_once() {
    let fixture = Fixture::new("connections");
    let store = fixture.store();
    store
        .begin_host(None, "777777", "token", "op", "session-connections")
        .expect("session");
    assert!(store.reserve_connection("session-connections").expect("first"));
    assert!(store.reserve_connection("session-connections").expect("second"));
    assert!(
        !store.reserve_connection("session-connections").expect("third"),
        "a third concurrent unauthenticated connection must be refused"
    );
    // Releasing a finished connection frees the slot; the attempt budget remains the
    // cumulative limit, so a window cannot be kept open by reconnecting.
    store.release_connection().expect("release");
    assert!(store.reserve_connection("session-connections").expect("reuse"));
    assert_eq!(
        store.session("session-connections").expect("read").expect("row").connections,
        3,
        "the session still records how many connections were made"
    );
    assert_eq!(store.selection().expect("budget").connections_remaining(), 0);
    // Releases never drive the count below zero.
    for _ in 0..5 {
        store.release_connection().expect("release");
    }
    assert_eq!(store.selection().expect("budget").connections_used, 0);
}

#[test]
fn an_interrupted_commit_is_reported_incomplete_and_never_completed_or_reverted() {
    let fixture = Fixture::new("interrupted");
    let store = fixture.store();
    let peer = Identity::generate("lifecyclepeer1").expect("identity");
    store
        .begin_host(Some(&fixture.workspace()), "123456", "token", "op", "session-interrupted")
        .expect("session");
    pending_peer(&store, "peer-interrupted", &peer, "session-interrupted");
    store
        .begin_commit("session-interrupted", "token")
        .expect("commit started");
    store
        .set_commit_stage("session-interrupted", "peer")
        .expect("stage");
    // Process termination here: the registry may or may not hold the peer. Recovery is
    // time-scoped, so the death is aged past the live-commit window first.
    let connection = Connection::open(fixture.directory.join("pairing.sqlite3")).expect("sqlite");
    connection
        .execute(
            "UPDATE session SET updated_at = ?1 WHERE session_id = 'session-interrupted'",
            rusqlite::params![store::now() - store::LIVE_COMMIT_SECONDS - 5],
        )
        .expect("age the commit");
    drop(connection);
    drop(store);
    let recovered = fixture.store();
    let session = recovered
        .session("session-interrupted")
        .expect("read")
        .expect("row");
    assert_eq!(session.state, "incomplete");
    assert_eq!(session.error.as_deref(), Some("commit_interrupted"));
    assert_eq!(session.commit_stage, "peer", "the stage is not rewritten");
    // Recovery did not enroll anything on the operator's behalf.
    assert!(crate::open_registry()
        .expect("registry")
        .peer("peer-interrupted")
        .expect("read")
        .is_none());
}

#[test]
fn cancelling_during_a_commit_reports_an_unknown_outcome() {
    let fixture = Fixture::new("cancel-commit");
    let store = fixture.store();
    let peer = Identity::generate("lifecyclepeer2").expect("identity");
    store
        .begin_host(None, "123457", "token", "op", "session-cancel")
        .expect("session");
    pending_peer(&store, "peer-cancel", &peer, "session-cancel");
    store
        .begin_commit("session-cancel", "token")
        .expect("commit started");
    let (session, cancelled) = store.cancel("session-cancel").expect("cancel");
    assert!(!cancelled, "a commit in progress is not cancelled");
    assert_eq!(session.state, "enrolling");
}

#[test]
fn cancelling_an_open_window_is_durable_and_enrolls_nothing() {
    let fixture = Fixture::new("cancel-open");
    let store = fixture.store();
    store
        .begin_host(None, "123458", "token", "op", "session-cancel-open")
        .expect("session");
    let (session, cancelled) = store.cancel("session-cancel-open").expect("cancel");
    assert!(cancelled);
    assert_eq!(session.state, "cancelled");
    drop(store);
    let reopened = fixture.store();
    assert_eq!(
        reopened.session("session-cancel-open").expect("read").expect("row").state,
        "cancelled"
    );
    // Cancelling again is truthful: nothing changed.
    let (_, again) = reopened.cancel("session-cancel-open").expect("cancel");
    assert!(!again);
}

#[test]
fn a_stale_review_token_cannot_start_a_commit() {
    let fixture = Fixture::new("stale");
    let store = fixture.store();
    let peer = Identity::generate("lifecyclepeer3").expect("identity");
    store
        .begin_host(None, "123459", "token", "op", "session-stale")
        .expect("session");
    pending_peer(&store, "peer-stale", &peer, "session-stale");
    let error = store
        .begin_commit("session-stale", "not-the-token")
        .expect_err("a stale token must be refused");
    assert!(error.contains("stale"), "{error}");
    assert_eq!(
        store.session("session-stale").expect("read").expect("row").state,
        "awaiting_approval"
    );
}

#[test]
fn the_operator_approval_commits_identity_and_workspace_then_stops_bumping_the_revision() {
    let fixture = Fixture::new("commit");
    let workspace = fixture.workspace();
    let store = fixture.store();
    let peer = Identity::generate("lifecyclepeer4").expect("identity");
    store
        .begin_host(Some(&workspace), "123460", "token", "op", "session-commit")
        .expect("session");
    pending_peer(&store, "peer-commit", &peer, "session-commit");
    store
        .set_grant_workspace("session-commit", &workspace.to_string_lossy())
        .expect("workspace review");
    store
        .begin_commit("session-commit", "token")
        .expect("commit");
    let session = commit(&store, "session-commit", Some(&workspace), false).expect("commit");
    assert_eq!(session.state, "ready", "identity plus the reviewed workspace");
    assert_eq!(session.commit_stage, "done");
    let registry = crate::open_registry().expect("registry");
    let enrolled = registry.peer("peer-commit").expect("read").expect("row");
    assert_eq!(enrolled.fingerprint_sha256, peer.fingerprint());
    assert!(enrolled.has_execution_grant());
    let revision = registry.revision().expect("revision");
    // A second call is the same approved operation, so it must not grant again.
    let again = commit(&store, "session-commit", Some(&workspace), false).expect("repeat");
    assert_eq!(again.state, "ready");
    assert_eq!(registry.revision().expect("revision"), revision);
}

#[test]
fn identity_only_approval_enrolls_without_execution_access() {
    let fixture = Fixture::new("no-access");
    let store = fixture.store();
    let peer = Identity::generate("lifecyclepeer5").expect("identity");
    store
        .begin_host(None, "123461", "token", "op", "session-no-access")
        .expect("session");
    pending_peer(&store, "peer-no-access", &peer, "session-no-access");
    store
        .begin_commit("session-no-access", "token")
        .expect("commit");
    let session = commit(&store, "session-no-access", None, false).expect("commit");
    assert_eq!(session.state, "paired_no_access");
    let registry = crate::open_registry().expect("registry");
    let enrolled = registry.peer("peer-no-access").expect("read").expect("row");
    assert!(
        !enrolled.has_execution_grant(),
        "enrollment alone must never grant execution"
    );
}

#[test]
fn replacing_an_alias_needs_an_explicit_replacement() {
    let fixture = Fixture::new("replace");
    let store = fixture.store();
    let first = Identity::generate("lifecyclepeer6").expect("identity");
    let second = Identity::generate("lifecyclepeer7").expect("identity");
    store
        .begin_host(None, "123462", "token", "op", "session-replace")
        .expect("session");
    pending_peer(&store, "peer-shared", &first, "session-replace");
    store
        .begin_commit("session-replace", "token")
        .expect("commit");
    commit(&store, "session-replace", None, false).expect("first");

    store
        .begin_host(None, "123463", "token2", "op2", "session-replace-2")
        .expect("second session");
    pending_peer(&store, "peer-shared", &second, "session-replace-2");
    store
        .begin_commit("session-replace-2", "token2")
        .expect("commit");
    let error = commit(&store, "session-replace-2", None, false)
        .expect_err("a different certificate must not take over an alias silently");
    assert!(error.contains("different certificate"), "{error}");
    let registry = crate::open_registry().expect("registry");
    assert_eq!(
        registry.peer("peer-shared").expect("read").expect("row").fingerprint_sha256,
        first.fingerprint(),
        "the original peer is untouched"
    );
}

#[test]
fn lifecycle_events_stay_bounded() {
    let fixture = Fixture::new("events");
    let store = fixture.store();
    store
        .begin_host(None, "123464", "token", "op", "session-events")
        .expect("session");
    for _ in 0..40 {
        let _ = store.transition("session-events", &["discoverable"], "authenticating", "probe");
        let _ = store.transition("session-events", &["authenticating"], "discoverable", "probe");
    }
    let events = store.events("session-events").expect("events");
    assert!(
        events.len() <= store::MAX_EVENTS as usize,
        "events must stay bounded, got {}",
        events.len()
    );
    assert!(!events.is_empty());
}

#[test]
fn a_wrong_state_transition_is_refused_rather_than_recorded() {
    let fixture = Fixture::new("transition");
    let store = fixture.store();
    store
        .begin_host(None, "123465", "token", "op", "session-transition")
        .expect("session");
    assert!(store
        .transition("session-transition", &["authenticating"], "awaiting_approval", "no")
        .is_err());
    assert!(store
        .transition("session-transition", &["discoverable"], "not-a-state", "no")
        .is_err());
    assert!(store
        .record_pending_peer("session-transition", "peer", "inst", &"0".repeat(64), "ca", "cert")
        .is_err());
}

#[test]
fn inspection_never_marks_a_live_commit_interrupted() {
    let fixture = Fixture::new("live-commit");
    let peer = Identity::generate("lifecyclepeer8").expect("identity");
    let store = fixture.store();
    store
        .begin_host(None, "123467", "token", "op", "session-live")
        .expect("session");
    pending_peer(&store, "peer-live", &peer, "session-live");
    store
        .begin_commit("session-live", "token")
        .expect("commit started");
    // A second process looking at the session (what `pairing status` and the companion
    // do once a second) must not run recovery against it.
    let inspector = fixture.store_readonly();
    assert_eq!(
        inspector.session("session-live").expect("read").expect("row").state,
        "enrolling",
        "inspection must not interrupt a commit in progress"
    );
    // Recovery itself also refuses to call a recent commit interrupted.
    drop(store);
    let recovered = fixture.store();
    assert_eq!(
        recovered.session("session-live").expect("read").expect("row").state,
        "enrolling"
    );
    // Once the commit has been silent past the live window, it is reported, not guessed.
    let connection = Connection::open(fixture.directory.join("pairing.sqlite3")).expect("sqlite");
    connection
        .execute(
            "UPDATE session SET updated_at = ?1 WHERE session_id = 'session-live'",
            rusqlite::params![store::now() - store::LIVE_COMMIT_SECONDS - 5],
        )
        .expect("age the commit");
    drop(connection);
    drop(recovered);
    let recovered = fixture.store();
    assert_eq!(
        recovered.session("session-live").expect("read").expect("row").state,
        "incomplete"
    );
}

#[test]
fn codes_are_six_digits_and_leading_zeros_occur() {
    let mut saw_leading_zero = false;
    for _ in 0..2000 {
        let code = profile::random_code().expect("code");
        assert_eq!(code.len(), 6, "{code}");
        assert!(code.bytes().all(|byte| byte.is_ascii_digit()), "{code}");
        if code.starts_with('0') {
            saw_leading_zero = true;
        }
    }
    // With rejection sampling over 1e6 values, a run of 2000 without a leading zero is
    // astronomically unlikely; this still bounds itself rather than looping forever.
    assert!(saw_leading_zero, "codes in 000000..099999 must be reachable");
}

#[test]
fn a_saved_grant_is_not_reported_as_a_working_connection() {
    let fixture = Fixture::new("connectivity");
    let store = fixture.store();
    let peer = Identity::generate("lifecyclepeer9").expect("identity");
    store
        .begin_host(None, "123468", "token", "op", "session-ready")
        .expect("session");
    pending_peer(&store, "peer-ready", &peer, "session-ready");
    store
        .set_grant_workspace("session-ready", &fixture.workspace().to_string_lossy())
        .expect("workspace review");
    store.begin_commit("session-ready", "token").expect("commit");
    commit(&store, "session-ready", Some(&fixture.workspace()), false).expect("commit");
    let session = store.session("session-ready").expect("read").expect("row");
    assert_eq!(session.state, "ready");
    let value = super::snapshot(Some(&session), profile::Profile::TestOnly);
    let message = value["message"].as_str().unwrap_or("");
    assert!(
        message.is_empty() || !message.to_lowercase().contains("connected"),
        "ready is a saved grant, not a live connection: {message}"
    );
}

#[test]
fn the_profile_gate_is_closed_without_both_gates() {
    assert_eq!(
        profile::resolve(false).expect("production"),
        profile::Profile::Production
    );
    let value = super::capabilities();
    assert_eq!(value["supported"], serde_json::json!(false));
    assert_eq!(value["state"], serde_json::json!("closed"));
    assert_eq!(value["reason"], serde_json::json!("protocol_review_required"));
    assert_eq!(value["approved"], serde_json::json!(false));
    // Nothing in a status document claims approval.
    let fixture = Fixture::new("gate");
    let store = fixture.store();
    let session = store
        .begin_host(None, "123466", "token", "op", "session-gate")
        .expect("session");
    let value = super::snapshot(Some(&session), profile::Profile::TestOnly);
    assert_eq!(value["approved"], serde_json::json!(false));
    assert_eq!(
        value["profile"],
        serde_json::json!("test-adapter-unapproved")
    );
}
