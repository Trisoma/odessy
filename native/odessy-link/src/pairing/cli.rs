//! Experimental six-digit authenticated enrollment and local approval.
use serde_json::{json, Value};
use std::io::{BufRead, Write};
use std::path::PathBuf;

use super::{exchange, profile, store};

/// The message a production build answers with.



/// Limits, reported so a client never has to hard-code its own copy.
pub fn limits() -> Value {
    json!({
        "code_digits": store::CODE_DIGITS,
        "window_seconds": store::WINDOW_SECONDS,
        "failed_attempts": store::MAX_AUTH_ATTEMPTS,
        "unauthenticated_connections": store::MAX_UNAUTHENTICATED_CONNECTIONS,
        "handshake_inactivity_seconds": exchange::INACTIVITY.as_secs(),
        "frame_bytes": exchange::MAX_FRAME,
        "connection_bytes": exchange::MAX_AGGREGATE,
    })
}

/// The production capability snapshot. Always closed, and never derived from a
/// build flag or environment variable.
pub fn capabilities() -> Value {
    json!({
        "schema_version": 1,
        "supported": true,
        "state": "closed",
        "reason": "experimental",
        "message": "Experimental six-digit pairing. Approve the device and workspace on the host.",
        "session_id": "",
        "review_token": "",
        "profile": profile::Profile::Experimental.name(),
        "approved": false,
        "limits": limits(),
    })
}



/// The stdio capability boundary. It answers every request with the closed snapshot
/// and never dispatches, echoes a code or opens a session.
pub fn serve(mut input: impl BufRead, mut output: impl Write) -> Result<(), String> {
    let mut total = 0;
    loop {
        let mut frame = Vec::new();
        loop {
            let available = input.fill_buf().map_err(|_| "pairing input unavailable")?;
            if available.is_empty() {
                if frame.is_empty() {
                    return Ok(());
                }
                return Err("incomplete pairing frame".into());
            }
            let count = available
                .iter()
                .position(|byte| *byte == b'\n')
                .map_or(available.len(), |end| end + 1);
            if frame.len() + count > exchange::MAX_FRAME || total + count > exchange::MAX_AGGREGATE {
                return Err("pairing input limit exceeded".into());
            }
            let complete = available[count - 1] == b'\n';
            frame.extend_from_slice(&available[..count]);
            input.consume(count);
            total += count;
            if complete {
                break;
            }
        }
        let request: Value =
            serde_json::from_slice(&frame).map_err(|_| "invalid pairing request")?;
        let id = request.get("id").and_then(Value::as_str).filter(|value| {
            !value.is_empty()
                && value.len() <= 80
                && value
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        });
        let mut reply = capabilities();
        reply["id"] = id.map_or(Value::Null, |value| json!(value));
        serde_json::to_writer(&mut output, &reply).map_err(|_| "pairing reply unavailable")?;
        output.write_all(b"\n").map_err(|_| "pairing reply unavailable")?;
        output.flush().map_err(|_| "pairing reply unavailable")?;
    }
}

/// Where the durable pairing state lives: beside the trust registry, so a test can
/// point `ODESSY_LINK_STATE_DIR` elsewhere without touching real trust.
pub fn state_path() -> Result<PathBuf, String> {
    Ok(crate::state_dir()?.join("pairing.sqlite3"))
}

pub fn open_store() -> Result<store::Store, String> {
    let path = state_path()?;
    if let Some(parent) = path.parent() {
        crate::store::ensure_state_dir(parent)?;
    }
    store::Store::open(&path)
}

/// Inspection opens the same file without recovery, so looking at a session can never
/// turn another command's live commit into `incomplete`.
pub fn open_store_readonly() -> Result<store::Store, String> {
    let path = state_path()?;
    if let Some(parent) = path.parent() {
        crate::store::ensure_state_dir(parent)?;
    }
    store::Store::open_readonly(&path)
}

/// A status document for a session, or the closed snapshot when there is none.
pub fn snapshot(session: Option<&store::Session>, active: profile::Profile) -> Value {

    let Some(session) = session else {
        return capabilities();
    };
    let now = store::now();
    let mut value = json!({
        "schema_version": 1,
        "supported": true,
        "state": session.state,
        "reason": if session.state == "closed" { "unapproved_test_profile" } else { "" },
        "session_id": session.session_id,
        "role": session.role,
        "seconds_remaining": (session.expires_at - now).max(0),
        "connections": session.connections,
        "connections_remaining": store::MAX_UNAUTHENTICATED_CONNECTIONS - session.connections,
        "review_token": "",
        "profile": active.name(),
        "approved": false,
        "limits": limits(),
        "message": "",
    });
    if let Ok(store) = store::Store::open(&match state_path() {
        Ok(path) => path,
        Err(_) => return value,
    }) {
        if let Ok(selection) = store.selection() {
            value["attempts_remaining"] = json!(selection.attempts_remaining());
            value["connections_remaining"] = json!(selection.connections_remaining());
        }
        if let Ok(events) = store.events(&session.session_id) {
            value["events"] = json!(events
                .into_iter()
                .map(|(at, state, detail)| json!({"at": at, "state": state, "detail": detail}))
                .collect::<Vec<_>>());
        }
    }
    // A code is shown only to the host's own operator while the window is open.
    if session.role == store::HOST && store::is_open(&session.state) {
        value["code"] = json!(session.code);
    }
    if session.state == "awaiting_approval" {
        value["review_token"] = json!(session.review_token);
    }
    if let Some(peer) = &session.peer_alias {
        value["peer"] = json!({
            "alias": peer,
            "installation_id": session.peer_installation_id,
            "fingerprint_sha256": session.peer_fingerprint,
        });
    }
    value["workspace"] = json!(session.workspace);
    value["grant_workspace"] = json!(session.grant_workspace);
    value["error"] = json!(session.error);
    value["commit_stage"] = json!(session.commit_stage);
    value
}

fn print_json(value: &Value) -> Result<(), crate::Failure> {
    println!("{value}");
    Ok(())
}

/// Options every pairing verb accepts, plus the resolved profile.
struct Options {
    profile: profile::Profile,
    values: std::collections::BTreeMap<String, String>,
}

fn options(arguments: &[String], allowed: &[&str]) -> Result<Options, crate::Failure> {
    let mut allowed_with_profile = allowed.to_vec();
    allowed_with_profile.push("--profile");
    let values = crate::parse(arguments, &allowed_with_profile)?;
    let profile = profile::resolve(values.get("--profile").map(String::as_str) == Some("test-adapter"))
        .map_err(crate::Failure::Runtime)?;
    Ok(Options { profile, values })
}

/// `odessy-link pairing <verb>`.
pub fn run(arguments: &[String]) -> Result<(), crate::Failure> {
    let Some(verb) = arguments.first().map(String::as_str) else {
        return Err(crate::Failure::Usage(
            "pairing requires capabilities, status, begin, listen, join, confirm or cancel".into(),
        ));
    };
    if verb == "--stdio" {
        if arguments.len() != 1 {
            return Err(crate::Failure::Usage(
                "pairing --stdio takes no options".into(),
            ));
        }
        return serve(std::io::stdin().lock(), std::io::stdout().lock())
            .map_err(crate::Failure::Runtime);
    }
    match verb {
        "capabilities" => {
            let options = options(&arguments[1..], &[])?;
            print_json(&match options.profile {
                profile::Profile::Experimental => capabilities(),
            })
        }
        "status" => {
            let options = options(&arguments[1..], &["--session-id"])?;
            // Inspection must not run recovery: a live commit in another process
            // would be reported as interrupted.
            let store = open_store_readonly().map_err(crate::Failure::Runtime)?;
            let session = match options.values.get("--session-id") {
                Some(id) => store.session(id).map_err(crate::Failure::Runtime)?,
                None => store.current().map_err(crate::Failure::Runtime)?,
            };
            print_json(&snapshot(session.as_ref(), options.profile))
        }
        "begin" => begin(&arguments[1..]),
        "listen" => listen(&arguments[1..]),
        "join" => join(&arguments[1..]),
        "confirm" => confirm(&arguments[1..]),
        "cancel" => cancel(&arguments[1..]),
        other => Err(crate::Failure::Usage(format!(
            "unknown pairing command: {other}"
        ))),
    }
}

fn begin(arguments: &[String]) -> Result<(), crate::Failure> {
    let options = options(arguments, &["--workspace"])?;

    let workspace = match options.values.get("--workspace") {
        Some(path) => {
            let path = PathBuf::from(path);
            if !path.is_dir() {
                return Err(crate::Failure::Runtime(format!(
                    "the workspace is not a directory: {}",
                    path.display()
                )));
            }
            Some(path)
        }
        None => None,
    };
    let store = open_store().map_err(crate::Failure::Runtime)?;
    // Refuse to stack windows: an open session already owns the code and the budget.
    if let Some(current) = store.current_for(store::HOST).map_err(crate::Failure::Runtime)? {
        if !store::is_terminal(&current.state) {
            let value = snapshot(Some(&current), options.profile);
            return print_json(&value);
        }
    }
    let code = profile::random_code().map_err(crate::Failure::Runtime)?;
    let review_token = profile::hex(&profile::random(16).map_err(crate::Failure::Runtime)?);
    let session_id = profile::hex(&profile::random(16).map_err(crate::Failure::Runtime)?);
    let operation_id = profile::hex(&profile::random(16).map_err(crate::Failure::Runtime)?);
    let session = store
        .begin_host(
            workspace.as_deref(),
            &code,
            &review_token,
            &operation_id,
            &session_id,
        )
        .map_err(crate::Failure::Runtime)?;
    print_json(&snapshot(Some(&session), options.profile))
}

fn listen(arguments: &[String]) -> Result<(), crate::Failure> {
    let values = crate::parse_with_switches(
        arguments,
        &["--listen", "--session-id", "--seconds", "--profile"],
        &["--advertise"],
    )?;
    let active =
        profile::resolve(values.get("--profile").map(String::as_str) == Some("test-adapter"))
            .map_err(crate::Failure::Runtime)?;

    let address = crate::required(&values, "--listen")?.to_string();
    if address.len() > 512 || address.chars().any(char::is_whitespace) {
        return Err(crate::Failure::Usage("the listen address is not usable".into()));
    }
    let seconds: u64 = values
        .get("--seconds")
        .map(|raw| raw.parse::<u64>())
        .transpose()
        .map_err(|_| crate::Failure::Usage("--seconds must be a whole number".into()))?
        .unwrap_or(store::WINDOW_SECONDS as u64);
    let advertise = values.contains_key("--advertise");
    let store = std::sync::Arc::new(open_store().map_err(crate::Failure::Runtime)?);
    let session = match values.get("--session-id") {
        Some(id) => store.session(id).map_err(crate::Failure::Runtime)?,
        None => store.current_for(store::HOST).map_err(crate::Failure::Runtime)?,
    }
    .ok_or_else(|| crate::Failure::Runtime("start a pairing session first".into()))?;
    if !store::is_open(&session.state) {
        return print_json(&snapshot(Some(&session), active));
    }
    let local = profile::LocalIdentity::load().map_err(crate::Failure::Runtime)?;
    let mut listener =
        exchange::Listener::bind(&address, store.clone(), &session.session_id, local)
            .map_err(crate::Failure::Runtime)?;
    let port = listener.port().map_err(crate::Failure::Runtime)?;
    let identity = crate::load_identity().map_err(crate::Failure::Runtime)?;
    let _advertisement = if advertise {
        let ip = address
            .parse::<std::net::SocketAddr>()
            .map(|value| value.ip())
            .ok();
        match crate::discover::advertise_pairing(
            &identity.installation_id,
            &identity.fingerprint(),
            "odessy-link.local.",
            ip.unwrap_or(std::net::IpAddr::V4(std::net::Ipv4Addr::UNSPECIFIED)),
            port,
        ) {
            Ok((daemon, info)) => Some((daemon, info)),
            Err(error) => {
                eprintln!("mDNS advertisement unavailable: {error}");
                None
            }
        }
    } else {
        None
    };
    eprintln!("pairing listener on port {port} (experimental authenticated enrollment)");
    let deadline = store::now() + seconds as i64;
    let session_id = session.session_id.clone();
    let watch = store.clone();
    listener
        .run(move || {
            match watch.refresh(&session_id) {
                Ok(session) => !store::is_open(&session.state) || store::now() >= deadline,
                Err(_) => true,
            }
        })
        .map_err(crate::Failure::Runtime)?;
    let session = store
        .session(&session.session_id)
        .map_err(crate::Failure::Runtime)?;
    print_json(&snapshot(session.as_ref(), active))
}

/// Read the six-digit code from stdin as one bounded JSON line.
///
/// A command line is visible to other local users on some platforms, so the code is
/// accepted on standard input rather than as an argument wherever possible.
fn code_from_stdin() -> Result<String, crate::Failure> {
    use std::io::Read;
    let mut buffer = Vec::new();
    std::io::stdin()
        .lock()
        .take(512)
        .read_to_end(&mut buffer)
        .map_err(|_| crate::Failure::Runtime("cannot read the pairing code".into()))?;
    if buffer.len() >= 512 {
        return Err(crate::Failure::Usage(
            "the pairing code input is too large".into(),
        ));
    }
    let value: Value = serde_json::from_slice(&buffer)
        .map_err(|_| crate::Failure::Usage("the pairing code input is unreadable".into()))?;
    let code = value
        .get("code")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    if !store::valid_code(&code) {
        return Err(crate::Failure::Usage(
            "enter the six-digit code exactly as the other computer shows it".into(),
        ));
    }
    Ok(code)
}

fn join(arguments: &[String]) -> Result<(), crate::Failure> {
    let values = crate::parse_with_switches(arguments, &["--address", "--code", "--profile"], &["--code-stdin"])?;

    let address = crate::required(&values, "--address")?.to_string();
    if address.len() > 512 || address.chars().any(char::is_whitespace) {
        return Err(crate::Failure::Usage(
            "the address is not a socket address".into(),
        ));
    }
    let active = profile::Profile::Experimental;
    let code = match (values.get("--code"), values.contains_key("--code-stdin")) {
        (Some(code), false) => {
            if !store::valid_code(code) {
                return Err(crate::Failure::Usage(
                    "enter the six-digit code exactly as the other computer shows it".into(),
                ));
            }
            code.clone()
        }
        (None, true) => code_from_stdin()?,
        _ => {
            return Err(crate::Failure::Usage(
                "provide the code once, as --code or on stdin".into(),
            ))
        }
    };
    let store = open_store().map_err(crate::Failure::Runtime)?;
    let local = profile::LocalIdentity::load().map_err(crate::Failure::Runtime)?;
    let session_id = profile::hex(&profile::random(16).map_err(crate::Failure::Runtime)?);
    let operation_id = profile::hex(&profile::random(16).map_err(crate::Failure::Runtime)?);
    let session = store
        .begin_joiner(&address, &operation_id, &session_id)
        .map_err(crate::Failure::Runtime)?;
    let outcome = exchange::join(&store, &session, &address, &code, &local);
    let session = match outcome {
        Ok(_host) => {
            // The host identity is now *pending* locally. Enrolling it is an address
            // book entry with no capability: execution authority is unchanged, and the
            // operator must still review outbound consent.
            let committed=commit(&store, &session.session_id, None, false).map_err(crate::Failure::Runtime)?;
            use std::net::ToSocketAddrs;
            let mut endpoint=address.to_socket_addrs().map_err(|_|crate::Failure::Runtime("Cannot resolve execution address".into()))?.next().ok_or_else(||crate::Failure::Runtime("Execution address missing".into()))?;
            endpoint.set_port(7443);
            crate::open_registry()?.set_address(&committed.peer_alias.clone().unwrap_or_default(),&endpoint.to_string()).map_err(crate::Failure::Runtime)?;
            committed
        }
        Err(error) => {
            let current = store
                .session(&session.session_id)
                .map_err(crate::Failure::Runtime)?;
            if current.as_ref().map(|session| store::is_terminal(&session.state)) != Some(true) {
                let _ = store.mark_incomplete(&session.session_id, &error);
            }
            let session = store
                .session(&session.session_id)
                .map_err(crate::Failure::Runtime)?;
            let mut value = snapshot(session.as_ref(), active);
            value["message"] = json!(error);
            return print_json(&value);
        }
    };
    print_json(&snapshot(Some(&session), active))
}

/// Commit a pending peer: identity first, then the reviewed workspace grant.
///
/// Each stage is durable, so a crash mid-commit is reported as `incomplete` by
/// recovery instead of being silently completed or rolled back. Re-running with the
/// same review token is the *same* approved operation, and an already-committed
/// outcome is returned without touching the registry again (no grant replay).
pub(crate) fn commit(
    store: &store::Store,
    session_id: &str,
    workspace: Option<&std::path::Path>,
    replace: bool,
) -> Result<store::Session, String> {
    let session = store
        .session(session_id)?
        .ok_or_else(|| format!("unknown pairing session: {session_id}"))?;
    let (Some(alias), Some(fingerprint), Some(ca_pem), Some(certificate_pem)) = (
        session.peer_alias.clone(),
        session.peer_fingerprint.clone(),
        session.peer_ca_pem.clone(),
        session.peer_cert_pem.clone(),
    ) else {
        return Err("the session has no authenticated peer to commit".into());
    };
    if session.commit_stage == "done" {
        return Ok(session);
    }
    if let Some(path) = workspace {
        if !path.is_dir() {
            return Err(format!(
                "the workspace is not a directory: {}",
                path.display()
            ));
        }
    }
    let registry = crate::open_registry()?;
    if let Some(existing) = registry.peer(&alias)? {
        if existing.fingerprint_sha256 != fingerprint && !replace {
            return Err(format!(
                "{alias} is already enrolled with a different certificate; review the identity and approve a replacement explicitly"
            ));
        }
    }
    let bundle = crate::identity::EnrollmentBundle {
        kind: crate::identity::EnrollmentBundle::KIND.to_string(),
        version: crate::identity::EnrollmentBundle::VERSION,
        installation_id: session.peer_installation_id.clone().unwrap_or_default(),
        ca_pem,
        certificate_pem,
        fingerprint_sha256: fingerprint.clone(),
    };
    let bundle_text = serde_json::to_string(&bundle)
        .map_err(|error| format!("cannot prepare the enrollment: {error}"))?;
    let grant = workspace.map(|path| path.to_string_lossy().to_string());

    // Already committed by an earlier attempt: report it, do not write again.
    let satisfied = registry
        .peer(&alias)?
        .map(|peer| {
            peer.fingerprint_sha256 == fingerprint
                && match &grant {
                    Some(path) => {
                        peer.workspace.as_deref() == Some(path.as_str())
                            && peer.capability_mcp
                            && !peer.revoked
                    }
                    None => true,
                }
        })
        .unwrap_or(false);
    if satisfied {
        // The registry already holds this outcome; record the same terminal state the
        // first attempt would have produced, without touching trust again.
        return store.finish_commit(session_id);
    }

    registry
        .add_peer(&alias, &bundle_text, &fingerprint, None)
        .map_err(|error| format!("cannot enroll the peer: {error}"))?;
    store.set_commit_stage(session_id, "peer")?;
    if let Some(path) = &grant {
        registry
            .grant(&alias, crate::trust::CAPABILITY_MCP, path)
            .map_err(|error| {
                format!("the identity is enrolled but the workspace grant failed: {error}")
            })?;
        store.set_commit_stage(session_id, "grant")?;
    }
    store.finish_commit(session_id)
}

fn confirm(arguments: &[String]) -> Result<(), crate::Failure> {
    let options = options(arguments, &["--session-id", "--review-token", "--workspace"])?;

    let session_id = crate::required(&options.values, "--session-id")?.to_string();
    let token = crate::required(&options.values, "--review-token")?.to_string();
    let store = open_store().map_err(crate::Failure::Runtime)?;
    let session = store
        .session(&session_id)
        .map_err(crate::Failure::Runtime)?
        .ok_or_else(|| crate::Failure::Runtime(format!("unknown pairing session: {session_id}")))?;
    if session.role != store::HOST {
        return Err(crate::Failure::Runtime(
            "only the host approves an incoming device".into(),
        ));
    }
    // The approval token is bound to the request the operator was shown.
    if !profile::constant_time_eq(session.review_token.as_bytes(), token.as_bytes()) {
        let mut value = snapshot(Some(&session), options.profile);
        value["committed"] = json!(false);
        value["message"] = json!("the approval is stale; review the current request again");
        return print_json(&value);
    }
    if session.commit_stage == "done" {
        let mut value = snapshot(Some(&session), options.profile);
        value["committed"] = json!(true);
        value["message"] = json!("this enrollment was already committed");
        return print_json(&value);
    }
    // The reviewed workspace is recorded before the commit starts, so the commit
    // itself is one ordered sequence of durable stages.
    if let Some(path) = options.values.get("--workspace") {
        store
            .set_grant_workspace(&session_id, path)
            .map_err(crate::Failure::Runtime)?;
    }
    let session = store
        .begin_commit(&session_id, &token)
        .map_err(crate::Failure::Runtime)?;
    let grant = session.grant_workspace.clone().map(PathBuf::from);
    match commit(&store, &session_id, grant.as_deref(), false) {
        Ok(session) => {
            let mut value = snapshot(Some(&session), options.profile);
            value["committed"] = json!(true);
            print_json(&value)
        }
        Err(error) => {
            let session = store
                .mark_incomplete(&session_id, &error)
                .map_err(crate::Failure::Runtime)?;
            let mut value = snapshot(Some(&session), options.profile);
            value["committed"] = json!(false);
            value["message"] = json!(error);
            print_json(&value)
        }
    }
}

fn cancel(arguments: &[String]) -> Result<(), crate::Failure> {
    let options = options(arguments, &["--session-id"])?;
    let store = open_store().map_err(crate::Failure::Runtime)?;
    let session_id = match options.values.get("--session-id") {
        Some(id) => id.clone(),
        None => store
            .current_for(store::HOST)
            .map_err(crate::Failure::Runtime)?
            .map(|session| session.session_id)
            .ok_or_else(|| crate::Failure::Runtime("no pairing session to cancel".into()))?,
    };
    let (session, cancelled) = store.cancel(&session_id).map_err(crate::Failure::Runtime)?;
    let mut value = snapshot(Some(&session), options.profile);
    value["cancelled"] = json!(cancelled);
    value["message"] = json!(if cancelled {
        "Pairing cancelled. Nothing was enrolled by this session."
    } else if session.state == "enrolling" {
        "A commit was in progress; its outcome is unknown. Inspect the paired devices before retrying."
    } else {
        "Nothing was cancelled: the session had already finished."
    });
    print_json(&value)
}
