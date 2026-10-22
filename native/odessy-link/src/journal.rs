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

/// Sessions kept before rows for *finished* sessions are dropped. Bounded so the
/// journal cannot grow without limit on a long-lived host.
pub const RETAINED_SESSIONS: usize = 200;
/// Ids a single session may use. Reaching it does not evict anything: the session is
/// exhausted and the peer must start a fresh one, because forgetting a used id would
/// let the same call run twice.
pub const MAX_ITEMS_PER_SESSION: usize = 4096;

/// What recording a request id did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AdmitOutcome {
    /// The id had not been used in this session and direction.
    Fresh,
    /// The id was already used. Forwarding it again would be a duplicate execution.
    Duplicate,
    /// The session has used all the ids it is allowed. A fresh session is required.
    Exhausted,
}

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
        crate::store::restrict_file(path)?;
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
        for sidecar in ["-wal", "-shm"] {
            let mut name = path.as_os_str().to_owned();
            name.push(sidecar);
            crate::store::restrict_file(std::path::Path::new(&name))?;
        }
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

    /// Record a request id.
    ///
    /// Nothing is ever evicted to make room: an id stays used, and an outstanding
    /// operation stays unresolved, for as long as the session exists. When the session
    /// has used its allowance the caller must start a fresh session.
    /// Record a request id using the default session allowance. Tests use this; the
    /// relay passes the host's configured allowance explicitly.
    #[cfg(test)]
    pub fn admit(
        &self,
        session: &str,
        direction: &str,
        id: &RequestId,
    ) -> Result<AdmitOutcome, String> {
        self.admit_with_limit(session, direction, id, MAX_ITEMS_PER_SESSION)
    }

    /// The same rule with an explicit allowance, so the limit can be exercised without
    /// inserting thousands of rows.
    pub fn admit_with_limit(
        &self,
        session: &str,
        direction: &str,
        id: &RequestId,
        limit: usize,
    ) -> Result<AdmitOutcome, String> {
        let (kind, value) = id_columns(id);
        let mut connection = self.lock()?;
        let transaction = connection
            .transaction()
            .map_err(|error| format!("cannot start journal transaction: {error}"))?;
        let existing: i64 = transaction
            .query_row(
                "SELECT COUNT(*) FROM journal
                 WHERE session=?1 AND direction=?2 AND id_kind=?3 AND id_value=?4",
                params![session, direction, kind, value],
                |row| row.get(0),
            )
            .map_err(|error| format!("cannot read journal: {error}"))?;
        if existing > 0 {
            transaction
                .commit()
                .map_err(|error| format!("cannot close journal transaction: {error}"))?;
            return Ok(AdmitOutcome::Duplicate);
        }
        let used: i64 = transaction
            .query_row(
                "SELECT COUNT(*) FROM journal WHERE session=?1",
                params![session],
                |row| row.get(0),
            )
            .map_err(|error| format!("cannot read journal: {error}"))?;
        if used as usize >= limit {
            transaction
                .commit()
                .map_err(|error| format!("cannot close journal transaction: {error}"))?;
            return Ok(AdmitOutcome::Exhausted);
        }
        transaction
            .execute(
                "INSERT INTO journal(session, direction, id_kind, id_value, state, admitted_at)
                 VALUES(?1, ?2, ?3, ?4, 'admitted', ?5)",
                params![session, direction, kind, value, Self::now()],
            )
            .map_err(|error| format!("cannot record request: {error}"))?;
        transaction
            .commit()
            .map_err(|error| format!("cannot close journal transaction: {error}"))?;
        Ok(AdmitOutcome::Fresh)
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

    /// Drop rows for fully settled sessions other than the newest `keep`.
    ///
    /// A session with an `admitted` or `unresolved` row is never pruned: its used ids
    /// may still be needed to reject a duplicate, and an unresolved outcome must stay
    /// reportable rather than being forgotten.
    pub fn prune(&self, keep: usize) -> Result<usize, String> {
        let removed = self
            .lock()?
            .execute(
                "DELETE FROM journal WHERE session IN (
                     SELECT session FROM journal
                     GROUP BY session
                     HAVING SUM(CASE WHEN state='settled' THEN 1 ELSE 0 END) = COUNT(*)
                     ORDER BY MAX(rowid) DESC LIMIT -1 OFFSET ?1
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
        assert_eq!(
            fixture
                .journal
                .admit("session-1", "peer-to-node", &RequestId::Number(1))
                .expect("admit"),
            AdmitOutcome::Fresh
        );
        let reopened = Journal::open(&fixture.directory.join("journal.sqlite3")).expect("reopen");
        assert_eq!(
            reopened
                .admit("session-1", "peer-to-node", &RequestId::Number(1))
                .expect("admit"),
            AdmitOutcome::Duplicate,
            "the same id may not be reused after a restart"
        );
        // A different session starts clean.
        assert_eq!(
            reopened
                .admit("session-2", "peer-to-node", &RequestId::Number(1))
                .expect("admit"),
            AdmitOutcome::Fresh
        );
    }

    #[test]
    fn numeric_and_string_ids_are_stored_separately() {
        let fixture = TempJournal::new("ids");
        assert_eq!(
            fixture
                .journal
                .admit("s", "d", &RequestId::Number(1))
                .expect("numeric"),
            AdmitOutcome::Fresh
        );
        assert_eq!(
            fixture
                .journal
                .admit("s", "d", &RequestId::Text("1".into()))
                .expect("string"),
            AdmitOutcome::Fresh
        );
        assert_eq!(
            fixture
                .journal
                .admit("s", "d", &RequestId::Number(1))
                .expect("repeat"),
            AdmitOutcome::Duplicate
        );
    }

    #[test]
    fn equal_ids_in_opposite_directions_are_independent() {
        let fixture = TempJournal::new("directions");
        let id = RequestId::Number(7);
        assert_eq!(
            fixture.journal.admit("s", "peer-to-node", &id).expect("inbound"),
            AdmitOutcome::Fresh
        );
        assert_eq!(
            fixture.journal.admit("s", "node-to-peer", &id).expect("outbound"),
            AdmitOutcome::Fresh
        );
        fixture
            .journal
            .settle("s", "peer-to-node", &id)
            .expect("settle inbound");
        let connection = fixture.journal.lock().expect("lock");
        let states: Vec<String> = connection
            .prepare("SELECT direction || ':' || state FROM journal WHERE session='s'")
            .expect("prepare")
            .query_map([], |row| row.get(0))
            .expect("query")
            .map(|value| value.expect("row"))
            .collect();
        // The inbound request is answered; the outbound request with the same id is not.
        assert!(states.contains(&"peer-to-node:settled".to_string()));
        assert!(states.contains(&"node-to-peer:admitted".to_string()));
    }

    #[test]
    fn a_full_session_is_exhausted_rather_than_forgetting_used_ids() {
        let fixture = TempJournal::new("exhausted");
        for offset in 0..3 {
            assert_eq!(
                fixture
                    .journal
                    .admit_with_limit("s", "peer-to-node", &RequestId::Number(offset), 3)
                    .expect("admit"),
                AdmitOutcome::Fresh
            );
        }
        assert_eq!(
            fixture
                .journal
                .admit_with_limit("s", "peer-to-node", &RequestId::Number(99), 3)
                .expect("admit"),
            AdmitOutcome::Exhausted,
            "a full session must be refused, not silently forget an id"
        );
        // The first id is still known, so it can never be executed twice.
        assert_eq!(
            fixture
                .journal
                .admit_with_limit("s", "peer-to-node", &RequestId::Number(0), 3)
                .expect("admit"),
            AdmitOutcome::Duplicate
        );
        // A fresh session starts over.
        assert_eq!(
            fixture
                .journal
                .admit_with_limit("s2", "peer-to-node", &RequestId::Number(0), 3)
                .expect("admit"),
            AdmitOutcome::Fresh
        );
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
    }

    #[test]
    fn pruning_never_drops_a_session_with_outstanding_work() {
        let fixture = TempJournal::new("prune");
        // A live session: one request admitted and never answered.
        fixture
            .journal
            .admit("live", "peer-to-node", &RequestId::Number(1))
            .expect("live");
        for finished in ["old", "new"] {
            fixture
                .journal
                .admit(finished, "peer-to-node", &RequestId::Number(1))
                .expect("admit");
            fixture
                .journal
                .settle(finished, "peer-to-node", &RequestId::Number(1))
                .expect("settle");
        }
        fixture.journal.close_session("live").expect("close live");
        // The live session is now unresolved, which is still not prunable: its outcome
        // must remain reportable instead of being replayed.
        fixture.journal.prune(0).expect("prune");
        assert!(
            !fixture.journal.unresolved("live").expect("read").is_empty(),
            "an unresolved session keeps its rows"
        );
        assert_eq!(
            fixture
                .journal
                .admit("live", "peer-to-node", &RequestId::Number(1))
                .expect("admit"),
            AdmitOutcome::Duplicate,
            "a pruned candidate must never free a used id"
        );
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
