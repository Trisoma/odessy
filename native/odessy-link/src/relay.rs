//! Bidirectional MCP relay with per-direction accounting.
//!
//! Each direction keeps its own used-id table and pending-request count. Requests
//! waiting for a response are capped at 32; notifications pass through but the
//! codec's 16 MiB ceiling still bounds buffering. A duplicate id or a 33rd
//! outstanding request closes the connection rather than being forwarded.

use crate::framing::{FrameCodec, FrameError, RequestId, UsedIds};
use crate::journal::Journal;
use serde_json::Value;
use std::sync::Arc;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

/// Outstanding requests awaiting a response, per direction.
pub const MAX_OUTSTANDING: usize = 32;
/// Used-id window retained per direction.
const USED_ID_CAPACITY: usize = 4096;
const READ_CHUNK: usize = 64 * 1024;

#[derive(Debug, PartialEq, Eq)]
pub enum RelayError {
    Framing(FrameError),
    DuplicateRequestId,
    TooManyOutstanding,
    Io(String),
    /// A frame that is not a JSON-RPC object cannot be accounted for, so it is
    /// refused instead of blindly forwarded.
    Malformed,
    /// The host's own authority check failed for this request: a revoked peer, a
    /// removed grant, or a registry that cannot be read.
    Refused(String),
}

impl From<FrameError> for RelayError {
    fn from(error: FrameError) -> Self {
        RelayError::Framing(error)
    }
}

/// Re-run the host's own admission check. Called immediately before each request is
/// forwarded, so a grant revoked during a live session stops the next request.
pub type AdmitCheck = Arc<dyn Fn() -> Result<(), String> + Send + Sync>;

pub struct DirectionState {
    used: Option<UsedIds>,
    pending: std::collections::HashSet<RequestId>,
    /// Durable, content-free record of used ids for this session and direction.
    journal: Option<(Arc<Journal>, String)>,
    direction: &'static str,
    recheck: Option<AdmitCheck>,
}

impl DirectionState {
    pub fn new() -> Self {
        Self {
            used: Some(UsedIds::new(USED_ID_CAPACITY)),
            pending: std::collections::HashSet::new(),
            journal: None,
            direction: "unknown",
            recheck: None,
        }
    }

    /// Record ids durably. Without a journal the in-memory table is authoritative;
    /// with one, a used id stays used across a restart of this process.
    pub fn with_journal(mut self, journal: Arc<Journal>, session: String, direction: &'static str) -> Self {
        self.journal = Some((journal, session));
        self.direction = direction;
        self
    }

    pub fn with_recheck(mut self, recheck: AdmitCheck) -> Self {
        self.recheck = Some(recheck);
        self
    }

    /// Account for one frame before forwarding it.
    pub fn admit(&mut self, frame: &str) -> Result<(), RelayError> {
        let value: Value = serde_json::from_str(frame).map_err(|_| RelayError::Malformed)?;
        if !value.is_object() {
            return Err(RelayError::Malformed);
        }
        let id = value.get("id").and_then(RequestId::from_json);
        let is_request = value.get("method").is_some();
        match (is_request, id) {
            (true, Some(id)) => {
                // Host authority first: a revoked grant must refuse the request even
                // though the connection authenticated successfully earlier.
                if let Some(recheck) = &self.recheck {
                    recheck().map_err(RelayError::Refused)?;
                }
                let fresh = match &self.journal {
                    Some((journal, session)) => journal
                        .admit(session, self.direction, &id)
                        .map_err(RelayError::Refused)?,
                    None => self
                        .used
                        .get_or_insert_with(|| UsedIds::new(USED_ID_CAPACITY))
                        .insert(id.clone()),
                };
                if !fresh {
                    return Err(RelayError::DuplicateRequestId);
                }
                if self.pending.len() >= MAX_OUTSTANDING {
                    return Err(RelayError::TooManyOutstanding);
                }
                self.pending.insert(id);
                Ok(())
            }
            // A response releases its slot. An unknown response id is tolerated:
            // it may answer a request from before a reconnect.
            (false, Some(id)) => {
                if let Some((journal, session)) = &self.journal {
                    journal
                        .settle(session, self.direction, &id)
                        .map_err(RelayError::Refused)?;
                }
                self.pending.remove(&id);
                Ok(())
            }
            // Notifications occupy no slot.
            _ => Ok(()),
        }
    }

    #[cfg(test)]
    pub fn outstanding(&self) -> usize {
        self.pending.len()
    }
}

/// Copy frames from `reader` to `writer` until EOF.
///
/// On EOF the frame codec is finished: a trailing partial frame is a protocol
/// failure and is discarded rather than forwarded truncated.
pub async fn pump<R, W>(
    mut reader: R,
    mut writer: W,
    mut state: DirectionState,
) -> Result<(), RelayError>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    let mut codec = FrameCodec::new();
    let mut chunk = vec![0u8; READ_CHUNK];
    loop {
        let read = reader
            .read(&mut chunk)
            .await
            .map_err(|error| RelayError::Io(error.to_string()))?;
        if read == 0 {
            codec.finish()?;
            writer
                .shutdown()
                .await
                .map_err(|error| RelayError::Io(error.to_string()))?;
            return Ok(());
        }
        codec.push(&chunk[..read])?;
        while let Some(frame) = codec.next_frame()? {
            state.admit(&frame)?;
            let mut line = frame.into_bytes();
            // The delimiter is re-added here; it is not part of the 8 MiB budget.
            line.push(b'\n');
            writer
                .write_all(&line)
                .await
                .map_err(|error| RelayError::Io(error.to_string()))?;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn state() -> DirectionState {
        DirectionState::new()
    }

    #[test]
    fn requests_and_responses_are_accounted_for() {
        let mut state = state();
        state
            .admit(&json!({"jsonrpc":"2.0","id":1,"method":"tools/list"}).to_string())
            .expect("request");
        assert_eq!(state.outstanding(), 1);
        state
            .admit(&json!({"jsonrpc":"2.0","id":1,"result":{}}).to_string())
            .expect("response");
        assert_eq!(state.outstanding(), 0);
        // The id stays used: replaying the same request is refused.
        assert_eq!(
            state.admit(&json!({"jsonrpc":"2.0","id":1,"method":"tools/list"}).to_string()),
            Err(RelayError::DuplicateRequestId)
        );
    }

    #[test]
    fn notifications_do_not_consume_slots() {
        let mut state = state();
        for _ in 0..MAX_OUTSTANDING * 2 {
            state
                .admit(&json!({"jsonrpc":"2.0","method":"notifications/progress"}).to_string())
                .expect("notification");
        }
        assert_eq!(state.outstanding(), 0);
    }

    #[test]
    fn the_thirty_third_outstanding_request_is_refused() {
        let mut state = state();
        for id in 0..MAX_OUTSTANDING {
            state
                .admit(&json!({"jsonrpc":"2.0","id":id,"method":"tools/call"}).to_string())
                .expect("within limit");
        }
        assert_eq!(
            state.admit(&json!({"jsonrpc":"2.0","id":999,"method":"tools/call"}).to_string()),
            Err(RelayError::TooManyOutstanding)
        );
    }

    #[test]
    fn numeric_and_string_ids_are_separate_slots() {
        let mut state = state();
        state
            .admit(&json!({"jsonrpc":"2.0","id":1,"method":"tools/call"}).to_string())
            .expect("numeric");
        state
            .admit(&json!({"jsonrpc":"2.0","id":"1","method":"tools/call"}).to_string())
            .expect("string is a different id");
        assert_eq!(state.outstanding(), 2);
    }

    #[test]
    fn non_object_frames_are_refused() {
        let mut state = state();
        assert_eq!(state.admit("[1,2,3]"), Err(RelayError::Malformed));
        assert_eq!(state.admit("not json"), Err(RelayError::Malformed));
    }

    #[tokio::test]
    async fn frames_are_forwarded_verbatim_including_split_reads() {
        let (mut client, server) = tokio::io::duplex(4096);
        let (out_rx, out_tx) = tokio::io::duplex(4096);
        let task = tokio::spawn(pump(server, out_tx, DirectionState::new()));
        let frames = [
            json!({"jsonrpc":"2.0","id":1,"method":"tools/list"}).to_string(),
            json!({"jsonrpc":"2.0","method":"notifications/progress","params":{}}).to_string(),
        ];
        for frame in &frames {
            // Split mid-frame to prove reassembly across reads.
            let (head, tail) = frame.as_bytes().split_at(frame.len() / 2);
            client.write_all(head).await.expect("write head");
            client.flush().await.expect("flush");
            client.write_all(tail).await.expect("write tail");
            client.write_all(b"\n").await.expect("delimiter");
        }
        client.shutdown().await.expect("shutdown");
        task.await.expect("join").expect("pump");

        let mut received = String::new();
        let mut out_rx = out_rx;
        out_rx.read_to_string(&mut received).await.expect("read");
        let lines: Vec<&str> = received.lines().collect();
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[0], frames[0]);
        assert_eq!(lines[1], frames[1]);
    }

    #[tokio::test]
    async fn a_duplicate_id_closes_the_relay_instead_of_forwarding() {
        let (mut client, server) = tokio::io::duplex(4096);
        let (out_rx, out_tx) = tokio::io::duplex(4096);
        let task = tokio::spawn(pump(server, out_tx, DirectionState::new()));
        let frame = json!({"jsonrpc":"2.0","id":7,"method":"tools/list"}).to_string();
        client.write_all(format!("{frame}\n{frame}\n").as_bytes()).await.expect("write");
        let error = task.await.expect("join").expect_err("duplicate must fail");
        assert_eq!(error, RelayError::DuplicateRequestId);
        let mut out_rx = out_rx;
        let mut received = String::new();
        out_rx.read_to_string(&mut received).await.expect("read");
        assert_eq!(received.lines().count(), 1, "only the first frame was forwarded");
    }

    #[tokio::test]
    async fn an_incomplete_frame_at_eof_fails_the_relay() {
        let (mut client, server) = tokio::io::duplex(4096);
        let (_out_rx, out_tx) = tokio::io::duplex(4096);
        let task = tokio::spawn(pump(server, out_tx, DirectionState::new()));
        client.write_all(b"{\"jsonrpc\":\"2.0\",").await.expect("write");
        client.shutdown().await.expect("shutdown");
        let error = task.await.expect("join").expect_err("partial frame must fail");
        assert_eq!(error, RelayError::Framing(FrameError::IncompleteFrame));
    }

    fn journal_fixture(name: &str) -> (std::path::PathBuf, Arc<Journal>) {
        let directory = std::env::temp_dir().join(format!(
            "odessy-link-relay-journal-{name}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let journal = Arc::new(Journal::open(&directory.join("journal.sqlite3")).expect("journal"));
        (directory, journal)
    }

    #[test]
    fn a_journal_makes_a_used_id_durable_for_the_session() {
        let (directory, journal) = journal_fixture("durable");
        let mut first = DirectionState::new().with_journal(journal.clone(), "s1".into(), "peer-to-node");
        first
            .admit(&json!({"jsonrpc":"2.0","id":5,"method":"tools/list"}).to_string())
            .expect("first use");
        // A fresh direction state stands in for a reconnected session: the journal,
        // not the in-memory table, decides that the id was already used.
        let mut second = DirectionState::new().with_journal(journal.clone(), "s1".into(), "peer-to-node");
        assert_eq!(
            second.admit(&json!({"jsonrpc":"2.0","id":5,"method":"tools/list"}).to_string()),
            Err(RelayError::DuplicateRequestId)
        );
        let _ = std::fs::remove_dir_all(directory);
    }

    #[test]
    fn a_failing_recheck_refuses_before_accounting_or_forwarding() {
        let mut state = DirectionState::new().with_recheck(Arc::new(|| {
            Err("capability revoked for this peer".to_string())
        }));
        let error = state
            .admit(&json!({"jsonrpc":"2.0","id":9,"method":"tools/call"}).to_string())
            .expect_err("a revoked peer must be refused");
        assert_eq!(
            error,
            RelayError::Refused("capability revoked for this peer".to_string())
        );
        assert_eq!(state.outstanding(), 0, "a refused request never occupies a slot");
        // Notifications are not authority-bearing requests, so they still pass.
        state
            .admit(&json!({"jsonrpc":"2.0","method":"notifications/progress"}).to_string())
            .expect("notifications pass");
    }

    #[tokio::test]
    async fn a_refused_request_is_never_forwarded() {
        let (mut client, server) = tokio::io::duplex(4096);
        let (out_rx, out_tx) = tokio::io::duplex(4096);
        let state = DirectionState::new().with_recheck(Arc::new(|| Err("revoked".to_string())));
        let task = tokio::spawn(pump(server, out_tx, state));
        client
            .write_all(b"{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\"}\n")
            .await
            .expect("write");
        let error = task.await.expect("join").expect_err("refusal ends the session");
        assert_eq!(error, RelayError::Refused("revoked".to_string()));
        let mut out_rx = out_rx;
        let mut received = String::new();
        out_rx.read_to_string(&mut received).await.expect("read");
        assert!(received.is_empty(), "nothing may reach the node server after a refusal");
    }
}
