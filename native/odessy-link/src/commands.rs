//! `serve` and `connect`: the authenticated MCP relay.
//!
//! Order of operations on both ends is deliberate: the TLS handshake completes
//! under ordinary Rustls verification, and only then is the presented leaf
//! certificate compared with the enrolled fingerprint. Nothing is forwarded to the
//! Node server, or to the local MCP client, before that comparison succeeds.

use crate::{node, relay, tls};
use crate::identity::display_fingerprint;
use crate::trust::Registry;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::io::AsyncWriteExt;
use tokio::net::{TcpListener, TcpStream};
use tokio_rustls::{TlsAcceptor, TlsConnector};

use crate::{journal, load_identity, open_registry, state_dir, Failure};

/// Connection and handshake ceiling. Operation deadlines are the caller's.
const HANDSHAKE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(10);
/// How long a host waits for the node server to finish answering after the peer
/// half-closes. Bounded so a wedged child cannot hold the session forever.
const SESSION_DRAIN: std::time::Duration = std::time::Duration::from_secs(5);
/// How often a live session rechecks its grant so a revocation can end it.
const REVOCATION_POLL: std::time::Duration = std::time::Duration::from_secs(1);

/// Node server errors terminate the whole relay, so a dead execution backend can
/// never look like a stalled peer.
fn relay_error_text(context: &str, error: relay::RelayError) -> String {
    format!("{context}: {error:?}")
}

pub fn serve(arguments: &[String]) -> Result<(), Failure> {
    let options = crate::parse(arguments, &["--listen", "--workspace", "--node-server"])?;
    let listen = crate::required(&options, "--listen")?.to_string();
    let workspace = PathBuf::from(crate::required(&options, "--workspace")?);
    let node_server = PathBuf::from(crate::required(&options, "--node-server")?);
    if !workspace.is_dir() {
        return Err(Failure::Runtime(format!(
            "workspace is not a directory: {}",
            workspace.display()
        )));
    }
    if !node_server.is_file() {
        return Err(Failure::Runtime(format!(
            "node server entry does not exist: {}",
            node_server.display()
        )));
    }
    let identity = load_identity()?;
    let registry = open_registry()?;
    let authorities = registry.enrolled_authorities()?;
    if authorities.is_empty() {
        return Err(Failure::Runtime(
            "no enrolled peers: enroll the other host before serving".into(),
        ));
    }
    let config = tls::server_config(&identity, authorities)?;
    drop(registry);

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .map_err(|error| Failure::Runtime(format!("cannot start runtime: {error}")))?;
    runtime.block_on(async move {
        let listener = TcpListener::bind(&listen)
            .await
            .map_err(|error| Failure::Runtime(format!("cannot listen on {listen}: {error}")))?;
        let local = listener
            .local_addr()
            .map_err(|error| Failure::Runtime(format!("cannot read listen address: {error}")))?;
        eprintln!("listening on {local} (mTLS, enrolled peers only)");
        let acceptor = TlsAcceptor::from(Arc::new(config));
        loop {
            let (stream, address) = match listener.accept().await {
                Ok(accepted) => accepted,
                Err(error) => {
                    eprintln!("accept failed: {error}");
                    continue;
                }
            };
            let acceptor = acceptor.clone();
            let workspace = workspace.clone();
            let node_server = node_server.clone();
            tokio::spawn(async move {
                if let Err(error) =
                    handle_connection(acceptor, stream, address, workspace, node_server).await
                {
                    eprintln!("refused {address}: {error}");
                }
            });
        }
    })
}

async fn handle_connection(
    acceptor: TlsAcceptor,
    stream: TcpStream,
    address: std::net::SocketAddr,
    workspace: PathBuf,
    node_server: PathBuf,
) -> Result<(), String> {
    let stream = tokio::time::timeout(HANDSHAKE_TIMEOUT, acceptor.accept(stream))
        .await
        .map_err(|_| "handshake timed out".to_string())?
        .map_err(|error| format!("handshake failed: {error}"))?;
    // Exact leaf identity, checked before any Node process exists.
    let fingerprint = tls::presented_fingerprint(stream.get_ref().1.peer_certificates())?;
    let registry = Registry::open(&state_dir()?.join("trust.sqlite3"))
        .map_err(|error| format!("trust registry unavailable: {error}"))?;
    let enrolled = registry
        .peer_by_fingerprint(&fingerprint)?
        .ok_or_else(|| {
            format!(
                "presented certificate {} is not an enrolled peer",
                display_fingerprint(&fingerprint)
            )
        })?;
    if !enrolled.has_execution_grant() {
        return Err(format!(
            "{} is enrolled but has no mcp capability for a workspace",
            enrolled.alias
        ));
    }
    // The grant names a workspace. Serving a different directory is the host
    // escalating its own authority, so the paths must agree.
    match enrolled.workspace.as_deref() {
        Some(granted) if workspace_matches(granted, &workspace) => {}
        Some(granted) => {
            return Err(format!(
                "{} holds a grant for {}, which is not the served workspace {}",
                enrolled.alias,
                granted,
                workspace.display()
            ))
        }
        None => return Err(format!("{} has no granted workspace", enrolled.alias)),
    }
    // The host's own workspace configuration wins over anything the peer says.
    if !workspace.is_dir() {
        return Err("configured workspace disappeared".into());
    }
    eprintln!(
        "admitted {} from {address} (fingerprint {})",
        enrolled.alias,
        display_fingerprint(&fingerprint)
    );

    // Content-free journal: ids and states only, durable across a reconnect so a used
    // id can never be replayed in a later process.
    let journal = Arc::new(journal::Journal::open(
        &state_dir()?.join("journal.sqlite3"),
    )?);
    let _ = journal.prune(journal::RETAINED_SESSIONS);
    let session = format!(
        "{}-{}",
        &fingerprint[..16.min(fingerprint.len())],
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0)
    );
    // Per-request admission: a grant revoked during the session stops the next request,
    // even though this connection authenticated when it was admitted.
    let trust_path = state_dir()?.join("trust.sqlite3");
    let expected = fingerprint.clone();
    let served_workspace = workspace.clone();
    let watch_path = trust_path.clone();
    let watch_fingerprint = expected.clone();
    let watch_workspace = workspace.clone();
    let recheck: relay::AdmitCheck = Arc::new(move || {
        let registry = Registry::open(&trust_path)?;
        match registry.peer_by_fingerprint(&expected)? {
            Some(peer) if peer.has_execution_grant() => match peer.workspace.as_deref() {
                Some(granted) if workspace_matches(granted, &served_workspace) => Ok(()),
                Some(granted) => Err(format!(
                    "grant moved to {granted}, which is not the served workspace"
                )),
                None => Err("the peer's workspace grant was removed".to_string()),
            },
            Some(_) => Err("capability revoked for this peer".to_string()),
            None => Err("peer is no longer enrolled".to_string()),
        }
    });

    let mut session_child = node::spawn(&node_server, &workspace)?;
    let child_stdin = session_child.take_stdin()?;
    let mut child_stdout = session_child.take_stdout()?;
    let (mut tls_reader, mut tls_writer) = tokio::io::split(stream);
    let outbound = relay::DirectionState::new()
        .with_journal(journal.clone(), session.clone(), "node-to-peer");
    let inbound = relay::DirectionState::new()
        .with_journal(journal.clone(), session.clone(), "peer-to-node")
        .with_recheck(recheck);
    // Linked: an answer on the outbound stream settles the inbound request and frees
    // its slot, so a long session is not capped at 32 answered requests.
    let outbound = outbound.answering(&inbound);
    // The peer finishing its request half-closes the child's stdin, but the child
    // keeps its chance to answer: killing it at peer EOF would drop the response.
    let back = tokio::spawn(async move {
        let result = relay::pump(&mut child_stdout, &mut tls_writer, outbound).await;
        let _ = tls_writer.shutdown().await;
        result.map_err(|error| relay_error_text("node to peer", error))
    });
    let forward = async move {
        let mut stdin = child_stdin;
        let result = relay::pump(&mut tls_reader, &mut stdin, inbound).await;
        // Drop, not shutdown: dropping the handle closes the pipe, so the child sees
        // EOF and exits on its own instead of being killed after the drain window.
        drop(stdin);
        result.map_err(|error| relay_error_text("peer to node", error))
    };
    let outcome: Result<(), String> = tokio::select! {
        result = forward => result,
        _ = watch_grant(watch_path, watch_fingerprint, watch_workspace) => {
            // The plan requires revocation to end an affected session, not only to
            // block its next request.
            Err("grant revoked or moved; session terminated".to_string())
        }
    };
    // A revoked session must stop forwarding immediately, in both directions.
    back.abort();
    // A child that keeps running after the peer finished is reported, then killed. It
    // is not a transport failure: the peer's own half already completed.
    let received = match tokio::time::timeout(SESSION_DRAIN, back).await {
        Ok(joined) => joined.map_err(|error| format!("relay task failed: {error}"))?,
        Err(_) => {
            eprintln!(
                "session {session}: node server still running {}s after the peer finished; terminating it",
                SESSION_DRAIN.as_secs()
            );
            Ok(())
        }
    };
    let cleanup = session_child.shutdown().await;
    // Unresolved requests stay unknown; nothing is replayed and no effect is claimed.
    match journal.close_session(&session) {
        Ok(0) => {}
        Ok(count) => eprintln!(
            "session {session} ended with {count} unresolved request(s); their outcome is unknown"
        ),
        Err(error) => eprintln!("cannot close journal session {session}: {error}"),
    }
    match (outcome.and(received), cleanup) {
        (Ok(()), Ok(())) => Ok(()),
        (Err(error), _) => Err(error),
        (Ok(()), Err(error)) => Err(format!("session ended but cleanup was incomplete: {error}")),
    }
}

pub fn connect(arguments: &[String]) -> Result<(), Failure> {
    let options = crate::parse(arguments, &["--peer", "--address"])?;
    let alias = crate::required(&options, "--peer")?;
    let registry = open_registry()?;
    let peer = registry
        .peer(alias)?
        .ok_or_else(|| Failure::Runtime(format!("unknown peer: {alias}")))?;
    if !peer.has_execution_grant() {
        // The host decides admission; this is the coordinator's own view only.
        eprintln!("note: no local mcp grant recorded for {alias}");
    }
    let address = options
        .get("--address")
        .cloned()
        .or_else(|| peer.address.clone())
        .ok_or_else(|| {
            Failure::Runtime(format!(
                "no address for {alias}: enroll with --address or pass --address"
            ))
        })?;
    let identity = load_identity()?;
    let config = tls::client_config(&identity, &peer)?;
    let server_name = tls::expected_server_name(&peer)?;
    let expected = peer.fingerprint_sha256.clone();
    drop(registry);

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .map_err(|error| Failure::Runtime(format!("cannot start runtime: {error}")))?;
    runtime.block_on(async move {
        let stream = tokio::time::timeout(HANDSHAKE_TIMEOUT, TcpStream::connect(&address))
            .await
            .map_err(|_| Failure::Runtime(format!("connect to {address} timed out")))?
            .map_err(|error| Failure::Runtime(format!("cannot connect to {address}: {error}")))?;
        let connector = TlsConnector::from(Arc::new(config));
        let stream = tokio::time::timeout(HANDSHAKE_TIMEOUT, connector.connect(server_name, stream))
            .await
            .map_err(|_| Failure::Runtime("TLS handshake timed out".into()))?
            .map_err(|error| Failure::Runtime(format!("TLS handshake failed: {error}")))?;
        let presented = tls::presented_fingerprint(stream.get_ref().1.peer_certificates())?;
        if presented != expected {
            return Err(Failure::Runtime(format!(
                "peer certificate mismatch: expected {} but {} presented {}; refusing to forward MCP traffic",
                display_fingerprint(&expected),
                alias,
                display_fingerprint(&presented)
            )));
        }
        eprintln!(
            "connected to {alias} with verified certificate {}",
            display_fingerprint(&presented)
        );

        let (mut tls_reader, mut tls_writer) = tokio::io::split(stream);
        // Stdin EOF half-closes the connection, then remaining replies are still
        // drained. A `select!` here would drop the last response whenever the local
        // caller finished writing before the peer finished answering.
        let send = async {
            let result = relay::pump(
                tokio::io::stdin(),
                &mut tls_writer,
                relay::DirectionState::new(),
            )
            .await;
            let _ = tls_writer.shutdown().await;
            result.map_err(|error| relay_error_text("local to peer", error))
        };
        let receive = relay::pump(
            &mut tls_reader,
            tokio::io::stdout(),
            relay::DirectionState::new(),
        );
        let (sent, received) = tokio::join!(send, receive);
        sent?;
        received.map_err(|error| relay_error_text("peer to local", error))?;
        Ok(())
    })
}

/// Poll the local registry and complete when the peer's grant no longer admits this
/// session. One second is the plan's termination target for responsive processes.
async fn watch_grant(
    trust_path: std::path::PathBuf,
    fingerprint: String,
    workspace: std::path::PathBuf,
) {
    loop {
        tokio::time::sleep(REVOCATION_POLL).await;
        let admitted = Registry::open(&trust_path)
            .and_then(|registry| registry.peer_by_fingerprint(&fingerprint))
            .ok()
            .flatten()
            .map(|peer| {
                peer.has_execution_grant()
                    && peer
                        .workspace
                        .as_deref()
                        .map(|granted| workspace_matches(granted, &workspace))
                        .unwrap_or(false)
            })
            .unwrap_or(false);
        if !admitted {
            return;
        }
    }
}

/// Compare the workspace an operator granted with the one being served.
///
/// Both sides are canonicalised, so a relative grant, a symlink, or an 8.3 alias
/// cannot name a directory the grant was never meant to cover. Windows compares
/// case-insensitively, as its filesystem does.
pub fn workspace_matches(granted: &str, served: &std::path::Path) -> bool {
    let (Ok(granted), Ok(served)) = (
        std::fs::canonicalize(granted),
        std::fs::canonicalize(served),
    ) else {
        return false;
    };
    if cfg!(windows) {
        granted.to_string_lossy().eq_ignore_ascii_case(&served.to_string_lossy())
    } else {
        granted == served
    }
}

#[cfg(test)]
mod tests {
    use super::{watch_grant, workspace_matches};
    use crate::identity::Identity;
    use crate::trust::Registry;
    use std::time::Duration;

    #[test]
    fn a_grant_only_covers_the_exact_directory_it_names() {
        let root = std::env::temp_dir().join(format!("odessy-link-ws-{}", std::process::id()));
        let granted = root.join("granted");
        let other = root.join("other");
        std::fs::create_dir_all(&granted).expect("granted dir");
        std::fs::create_dir_all(&other).expect("other dir");

        assert!(workspace_matches(&granted.to_string_lossy(), &granted));
        assert!(
            !workspace_matches(&granted.to_string_lossy(), &other),
            "a grant for one directory must not admit another"
        );
        assert!(!workspace_matches(&root.to_string_lossy(), &granted), "nor a parent");
        assert!(!workspace_matches(&root.join("missing").to_string_lossy(), &granted));
        let _ = std::fs::remove_dir_all(root);
    }

    /// Revocation must end a live session, not merely block the next request.
    #[tokio::test]
    async fn the_revocation_watcher_fires_after_a_revoke() {
        let root = std::env::temp_dir().join(format!("odessy-link-watch-{}", std::process::id()));
        std::fs::create_dir_all(root.join("workspace")).expect("workspace");
        let workspace = root.join("workspace");
        let path = root.join("trust.sqlite3");
        let registry = Registry::open(&path).expect("registry");
        let peer = Identity::generate("peer00000010").expect("identity");
        registry
            .add_peer("peer", &serde_json::to_string(&peer.bundle()).unwrap(), &peer.fingerprint(), None)
            .expect("enrol");
        registry
            .grant("peer", "mcp", &workspace.to_string_lossy())
            .expect("grant");

        let mut watcher = Box::pin(watch_grant(path, peer.fingerprint(), workspace.clone()));
        assert!(
            tokio::time::timeout(Duration::from_millis(1200), &mut watcher).await.is_err(),
            "an admitted session must keep running"
        );
        registry.revoke("peer").expect("revoke");
        tokio::time::timeout(Duration::from_secs(4), watcher)
            .await
            .expect("the watcher must notice a revocation");
        let _ = std::fs::remove_dir_all(root);
    }
}

