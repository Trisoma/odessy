//! `serve` and `connect`: the authenticated MCP relay.
//!
//! Order of operations on both ends is deliberate: the TLS handshake completes
//! under ordinary Rustls verification, and only then is the presented leaf
//! certificate compared with the enrolled fingerprint. Nothing is forwarded to the
//! Node server, or to the local MCP client, before that comparison succeeds.

use crate::{node, relay, tls};
use crate::identity::{display_fingerprint, fingerprint_sha256};
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
    let recheck: relay::AdmitCheck = Arc::new(move || {
        let registry = Registry::open(&trust_path)?;
        match registry.peer_by_fingerprint(&expected)? {
            Some(peer) if peer.has_execution_grant() => Ok(()),
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
    let outcome: Result<(), String> = forward.await;
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

/// Diagnostic used by tests and by `doctor`-style reporting later: the fingerprint
/// this host would expect for a peer, computed from the enrolled certificate.
#[allow(dead_code)]
pub fn enrolled_fingerprint(peer: &crate::trust::Peer) -> Result<String, String> {
    let der = crate::trust::certificate_der_for(&peer.certificate_pem)?;
    Ok(fingerprint_sha256(&der))
}
