//! Bidirectional MCP relay with per-direction accounting.
//!
//! Each direction keeps its own used-id table and pending-request count. Requests
//! waiting for a response are capped at 32; notifications pass through but the
//! codec's 16 MiB ceiling still bounds buffering. A duplicate id or a 33rd
//! outstanding request closes the connection rather than being forwarded.

use crate::framing::{FrameCodec, FrameError, RequestId, UsedIds};
use crate::journal::{AdmitOutcome, Journal, MAX_ITEMS_PER_SESSION};
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
    /// This session has used all the request ids it may use. Nothing is forgotten to
    /// make room, so the peer must open a fresh session.
    SessionExhausted,
}

impl From<FrameError> for RelayError {
    fn from(error: FrameError) -> Self {
        RelayError::Framing(error)
    }
}

/// Re-run the host's own admission check. Called immediately before each request is
/// forwarded, so a grant revoked during a live session stops the next request.
pub type AdmitCheck = Arc<dyn Fn() -> Result<(), String> + Send + Sync>;

/// Requests awaiting a matching response. Shared between the two pumps of one
/// session so a response clears the *requesting* direction's slot, no matter which
/// stream carried the answer.
#[derive(Default)]
pub struct Pending {
    ids: std::sync::Mutex<std::collections::HashSet<RequestId>>,
}

impl Pending {
    fn insert(&self, id: RequestId) -> Result<(), RelayError> {
        let mut ids = self.ids.lock().map_err(|_| RelayError::Io("pending lock poisoned".into()))?;
        if ids.len() >= MAX_OUTSTANDING {
            return Err(RelayError::TooManyOutstanding);
        }
        ids.insert(id);
        Ok(())
    }

    fn remove(&self, id: &RequestId) {
        if let Ok(mut ids) = self.ids.lock() {
            ids.remove(id);
        }
    }

    #[cfg(test)]
    fn len(&self) -> usize {
        self.ids.lock().map(|ids| ids.len()).unwrap_or(0)
    }
}

pub struct DirectionState {
    used: Option<UsedIds>,
    /// Requests carried by this direction and awaiting an answer.
    own: Arc<Pending>,
    /// The counterpart's pending set, cleared when this direction carries responses.
    answers: Option<Arc<Pending>>,
    /// Durable, content-free record of used ids for this session and direction.
    journal: Option<(Arc<Journal>, String)>,
    /// Ids this session may use before it must be replaced.
    journal_limit: usize,
    direction: &'static str,
    /// Direction whose journal rows a response in this stream settles.
    request_direction: &'static str,
    recheck: Option<AdmitCheck>,
}

impl DirectionState {
    pub fn new() -> Self {
        Self {
            used: Some(UsedIds::new(USED_ID_CAPACITY)),
            own: Arc::new(Pending::default()),
            answers: None,
            journal: None,
            journal_limit: MAX_ITEMS_PER_SESSION,
            direction: "unknown",
            request_direction: "unknown",
            recheck: None,
        }
    }

    /// Record ids durably. Without a journal the in-memory table is authoritative;
    /// with one, a used id stays used across a restart of this process.
    pub fn with_journal(mut self, journal: Arc<Journal>, session: String, direction: &'static str) -> Self {
        self.journal = Some((journal, session));
        self.direction = direction;
        self.request_direction = direction;
        self
    }

    /// Override how many ids this session may use. The host sets it from local
    /// configuration; reaching it requires a fresh session rather than forgetting ids.
    pub fn with_journal_limit(mut self, limit: usize) -> Self {
        self.journal_limit = limit.max(1);
        self
    }

    pub fn with_recheck(mut self, recheck: AdmitCheck) -> Self {
        self.recheck = Some(recheck);
        self
    }

    /// Link this direction to the one whose requests it answers. A response here then
    /// releases the requesting direction's slot and settles its journal row.
    pub fn answering(mut self, other: &DirectionState) -> Self {
        self.answers = Some(other.own.clone());
        self.request_direction = other.direction;
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
                // Reserve the slot before recording anything: a 33rd outstanding request
                // is refused here, and a journal row claiming it was admitted would make
                // the session report work it never accepted.
                self.own.insert(id.clone())?;
                let outcome = match &self.journal {
                    Some((journal, session)) => journal
                        .admit_with_limit(session, self.direction, &id, self.journal_limit)
                        .map_err(RelayError::Refused),
                    None => Ok(
                        if self
                            .used
                            .get_or_insert_with(|| UsedIds::new(USED_ID_CAPACITY))
                            .insert(id.clone())
                        {
                            AdmitOutcome::Fresh
                        } else {
                            AdmitOutcome::Duplicate
                        },
                    ),
                };
                match outcome {
                    Ok(AdmitOutcome::Fresh) => Ok(()),
                    // The slot was only reserved; nothing was forwarded.
                    Ok(AdmitOutcome::Duplicate) => {
                        self.own.remove(&id);
                        Err(RelayError::DuplicateRequestId)
                    }
                    Ok(AdmitOutcome::Exhausted) => {
                        self.own.remove(&id);
                        Err(RelayError::SessionExhausted)
                    }
                    Err(error) => {
                        self.own.remove(&id);
                        Err(error)
                    }
                }
            }
            // A response releases the requesting direction's slot. An unknown response
            // id is tolerated: it may answer a request from before a reconnect.
            (false, Some(id)) => {
                if let Some((journal, session)) = &self.journal {
                    journal
                        .settle(session, self.request_direction, &id)
                        .map_err(RelayError::Refused)?;
                }
                match &self.answers {
                    Some(pending) => pending.remove(&id),
                    None => self.own.remove(&id),
                }
                Ok(())
            }
            // Notifications occupy no slot.
            _ => Ok(()),
        }
    }

    #[cfg(test)]
    pub fn outstanding(&self) -> usize {
        self.own.len()
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

    #[test]
    fn a_request_refused_for_the_outstanding_limit_leaves_no_journal_row() {
        let directory = std::env::temp_dir().join(format!(
            "odessy-link-relay-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let journal = Arc::new(
            crate::journal::Journal::open(&directory.join("journal.sqlite3")).expect("journal"),
        );
        let mut state = DirectionState::new().with_journal(journal.clone(), "s".into(), "peer-to-node");
        for id in 0..MAX_OUTSTANDING {
            state
                .admit(&json!({"jsonrpc":"2.0","id":id,"method":"tools/call"}).to_string())
                .expect("within limit");
        }
        assert_eq!(
            state.admit(&json!({"jsonrpc":"2.0","id":999,"method":"tools/call"}).to_string()),
            Err(RelayError::TooManyOutstanding)
        );
        // The refused request must not look like work the session accepted.
        journal.close_session("s").expect("close");
        let unresolved = journal.unresolved("s").expect("unresolved");
        assert_eq!(unresolved.len(), MAX_OUTSTANDING, "only admitted requests are recorded");
        let _ = std::fs::remove_dir_all(&directory);
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

    /// Requests answered in the reverse stream must release their own slot. Before
    /// the two directions were linked, the second batch failed at 33 even though
    /// every earlier request had been answered.
    #[test]
    fn answers_release_the_requesting_directions_slot() {
        let requests = DirectionState::new();
        let mut answers = DirectionState::new().answering(&requests);
        let mut requests = requests;

        for round in 0..3i64 {
            for offset in 0..MAX_OUTSTANDING as i64 {
                let id = round * MAX_OUTSTANDING as i64 + offset;
                requests
                    .admit(&json!({"jsonrpc":"2.0","id":id,"method":"tools/call"}).to_string())
                    .unwrap_or_else(|error| panic!("round {round} id {id}: {error:?}"));
            }
            assert_eq!(requests.outstanding(), MAX_OUTSTANDING);
            for offset in 0..MAX_OUTSTANDING as i64 {
                let id = round * MAX_OUTSTANDING as i64 + offset;
                answers
                    .admit(&json!({"jsonrpc":"2.0","id":id,"result":{}}).to_string())
                    .expect("answer");
            }
            assert_eq!(
                requests.outstanding(),
                0,
                "round {round}: answered requests must release their slots"
            );
        }
    }
}
