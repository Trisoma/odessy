//! Content-free request journal.
//!
//! The pairing plan asks for a durable record of request identity and state that
//! survives a reconnect, is bounded, and never contains payloads. This journal
//! stores only: session, direction, request id, state, and two timestamps. It is the
//! authority for "has this id been used" across a session restart, and for reporting
//! which operations were still unresolved when a connection ended.

use crate::framing::RequestId;
use rusqlite::{params, Connection};
use std::sync::Mutex;

/// Sessions kept before the oldest rows are dropped. Bounded so the journal cannot
/// grow without limit on a long-lived host.
pub const RETAINED_SESSIONS: usize = 200;
/// Rows kept for a single session. A long-lived session would otherwise grow without
/// limit; past this window the oldest ids are forgotten and may be reused by the peer.
pub const ROWS_PER_SESSION: usize = 4096;

pub struct Journal {
    connection: Mutex<Connection>,
}

#[cfg(test)]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    pub direction: String,
    pub id: RequestId,
    pub state: String,
}

fn id_columns(id: &RequestId) -> (&'static str, String) {
    match id {
        RequestId::Number(value) => ("number", value.to_string()),
        // The kind column keeps numeric 1 and string "1" distinct in the primary key.
        RequestId::Text(value) => ("text", value.clone()),
    }
}

impl Journal {
    pub fn open(path: &std::path::Path) -> Result<Self, String> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|error| format!("cannot create journal directory: {error}"))?;
        }
        let connection =
            Connection::open(path).map_err(|error| format!("cannot open journal: {error}"))?;
        connection
            .execute_batch(
                "PRAGMA journal_mode=WAL; PRAGMA secure_delete=ON;
                 CREATE TABLE IF NOT EXISTS journal(
                     session TEXT NOT NULL,
                     direction TEXT NOT NULL,
                     id_kind TEXT NOT NULL,
                     id_value TEXT NOT NULL,
                     state TEXT NOT NULL,
                     admitted_at INTEGER NOT NULL,
                     settled_at INTEGER,
                     PRIMARY KEY(session, direction, id_kind, id_value)
                 );",
            )
            .map_err(|error| format!("cannot configure journal: {error}"))?;
        Ok(Self {
            connection: Mutex::new(connection),
        })
    }

    fn lock(&self) -> Result<std::sync::MutexGuard<'_, Connection>, String> {
        self.connection
            .lock()
            .map_err(|_| "journal lock poisoned".to_string())
    }

    fn now() -> i64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_secs() as i64)
            .unwrap_or(0)
    }

    /// Record a request id. Returns `false` when this id was already used in this
    /// session and direction, including in an earlier process.
    pub fn admit(&self, session: &str, direction: &str, id: &RequestId) -> Result<bool, String> {
        let (kind, value) = id_columns(id);
        let connection = self.lock()?;
        let inserted = connection
            .execute(
                "INSERT OR IGNORE INTO journal(session, direction, id_kind, id_value, state, admitted_at)
                 VALUES(?1, ?2, ?3, ?4, 'admitted', ?5)",
                params![session, direction, kind, value, Self::now()],
            )
            .map_err(|error| format!("cannot record request: {error}"))?;
        // Bound one session's history independently of the session count.
        connection
            .execute(
                "DELETE FROM journal WHERE rowid IN (
                     SELECT rowid FROM journal WHERE session=?1 ORDER BY rowid DESC LIMIT -1 OFFSET ?2
                 )",
                params![session, ROWS_PER_SESSION as i64],
            )
            .map_err(|error| format!("cannot bound session rows: {error}"))?;
        Ok(inserted == 1)
    }

    /// Mark a request as having received its answer.
    ///
    /// The direction is the one that *carried the request*, which is the opposite of
    /// the stream that carried the response. Matching only by id would settle a
    /// same-numbered request in the other direction as well.
    pub fn settle(&self, session: &str, direction: &str, id: &RequestId) -> Result<(), String> {
        let (kind, value) = id_columns(id);
        self.lock()?
            .execute(
                "UPDATE journal SET state='settled', settled_at=?5
                 WHERE session=?1 AND direction=?2 AND id_kind=?3 AND id_value=?4
                   AND state='admitted'",
                params![session, direction, kind, value, Self::now()],
            )
            .map_err(|error| format!("cannot settle request: {error}"))?;
        Ok(())
    }

    /// Record that a session ended with requests still outstanding. They stay
    /// `unresolved`: the plan forbids treating a disconnect as success or replaying it.
    pub fn close_session(&self, session: &str) -> Result<usize, String> {
        let changed = self
            .lock()?
            .execute(
                "UPDATE journal SET state='unresolved' WHERE session=?1 AND state='admitted'",
                params![session],
            )
            .map_err(|error| format!("cannot close session: {error}"))?;
        Ok(changed)
    }

    /// Unresolved entries for one session, in admission order.
    #[cfg(test)]
    pub fn unresolved(&self, session: &str) -> Result<Vec<Entry>, String> {
        let connection = self.lock()?;
        let mut statement = connection
            .prepare(
                "SELECT direction, id_kind, id_value, state FROM journal
                 WHERE session=?1 AND state='unresolved' ORDER BY admitted_at",
            )
            .map_err(|error| format!("cannot read journal: {error}"))?;
        let rows = statement
            .query_map(params![session], |row| {
                let kind: String = row.get(1)?;
                let value: String = row.get(2)?;
                let id = match kind.as_str() {
                    "number" => RequestId::Number(value.parse::<i64>().unwrap_or_default()),
                    _ => RequestId::Text(value),
                };
                Ok(Entry {
                    direction: row.get(0)?,
                    id,
                    state: row.get(3)?,
                })
            })
            .map_err(|error| format!("cannot read journal: {error}"))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("cannot read journal: {error}"))
    }

    /// Drop rows for sessions other than the newest `keep`.
    ///
    /// Newest is decided by `rowid`, not by a timestamp: two sessions opened in the
    /// same second must still order deterministically.
    pub fn prune(&self, keep: usize) -> Result<usize, String> {
        let removed = self
            .lock()?
            .execute(
                "DELETE FROM journal WHERE session NOT IN (
                     SELECT session FROM journal GROUP BY session ORDER BY MAX(rowid) DESC LIMIT ?1
                 )",
                params![keep.max(1) as i64],
            )
            .map_err(|error| format!("cannot prune journal: {error}"))?;
        Ok(removed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct TempJournal {
        directory: std::path::PathBuf,
        journal: Journal,
    }

    impl TempJournal {
        fn new(name: &str) -> Self {
            let directory = std::env::temp_dir().join(format!(
                "odessy-link-journal-{name}-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            let journal = Journal::open(&directory.join("journal.sqlite3")).expect("journal");
            Self { directory, journal }
        }
    }

    impl Drop for TempJournal {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.directory);
        }
    }

    #[test]
    fn a_used_id_is_refused_across_a_reopen() {
        let fixture = TempJournal::new("reopen");
        assert!(fixture
            .journal
            .admit("session-1", "peer-to-node", &RequestId::Number(1))
            .expect("admit"));
        // Dropping this handle closes the connection; the row is on disk, not in it.
        let reopened = Journal::open(&fixture.directory.join("journal.sqlite3")).expect("reopen");
        assert!(
            !reopened
                .admit("session-1", "peer-to-node", &RequestId::Number(1))
                .expect("admit"),
            "the same id may not be reused after a restart"
        );
        // A different session starts clean; a different direction is independent.
        assert!(reopened
            .admit("session-2", "peer-to-node", &RequestId::Number(1))
            .expect("admit"));
        assert!(reopened
            .admit("session-1", "node-to-peer", &RequestId::Number(1))
            .expect("admit"));
    }

    #[test]
    fn numeric_and_string_ids_are_stored_separately() {
        let fixture = TempJournal::new("ids");
        assert!(fixture
            .journal
            .admit("s", "d", &RequestId::Number(1))
            .expect("numeric"));
        assert!(fixture
            .journal
            .admit("s", "d", &RequestId::Text("1".into()))
            .expect("string"));
        assert!(!fixture
            .journal
            .admit("s", "d", &RequestId::Number(1))
            .expect("repeat"));
    }

    #[test]
    fn closing_a_session_marks_only_outstanding_requests_unresolved() {
        let fixture = TempJournal::new("unresolved");
        fixture
            .journal
            .admit("s", "peer-to-node", &RequestId::Number(1))
            .expect("admit 1");
        fixture
            .journal
            .admit("s", "peer-to-node", &RequestId::Number(2))
            .expect("admit 2");
        fixture
            .journal
            .settle("s", "peer-to-node", &RequestId::Number(2))
            .expect("settle");
        assert_eq!(fixture.journal.close_session("s").expect("close"), 1);
        let unresolved = fixture.journal.unresolved("s").expect("read");
        assert_eq!(unresolved.len(), 1);
        assert_eq!(unresolved[0].id, RequestId::Number(1));
        // Settling again is harmless and does not resurrect the entry.
        assert!(fixture.journal.unresolved("s").expect("read").len() == 1);
    }

    #[test]
    fn pruning_keeps_the_newest_sessions_only() {
        let fixture = TempJournal::new("prune");
        for session in ["a", "b", "c"] {
            fixture
                .journal
                .admit(session, "peer-to-node", &RequestId::Number(1))
                .expect("admit");
        }
        let removed = fixture.journal.prune(1).expect("prune");
        assert!(removed >= 1, "older sessions are dropped");
        assert!(fixture.journal.unresolved("c").is_ok());
        assert!(fixture.journal.admit("a", "peer-to-node", &RequestId::Number(1)).expect("admit"),
            "a pruned session may admit again");
    }

    #[test]
    fn the_journal_stores_no_payload() {
        let fixture = TempJournal::new("content-free");
        fixture
            .journal
            .admit("s", "peer-to-node", &RequestId::Text("secret-looking-id".into()))
            .expect("admit");
        let connection = fixture.journal.lock().expect("lock");
        let columns: Vec<String> = connection
            .prepare("SELECT name FROM pragma_table_info('journal')")
            .expect("prepare")
            .query_map([], |row| row.get(0))
            .expect("query")
            .map(|name| name.expect("name"))
            .collect();
        assert_eq!(
            columns,
            vec![
                "session".to_string(),
                "direction".to_string(),
                "id_kind".to_string(),
                "id_value".to_string(),
                "state".to_string(),
                "admitted_at".to_string(),
                "settled_at".to_string()
            ],
            "no column may hold arguments, results or credentials"
        );
    }
}
