//! Durable short-code enrollment sessions and the global attempt budget.
//!
//! Everything this module owns survives process termination. A session's identity,
//! its six-digit code, its window, the attempts left in the window and the exact
//! stage of a trust commit are rows, not memory. Recovery is deliberately
//! conservative: an interrupted commit is reported as `incomplete`, never completed
//! and never rolled back, so a committed peer cannot disappear because a process
//! died before it could say so.

use rusqlite::{params, Connection, OptionalExtension};
use std::path::Path;
use std::sync::Mutex;

/// Six digits including leading zeros, so `042137` is a valid code.
pub const CODE_DIGITS: usize = 6;
/// The pairing window, in seconds. A session past this instant is `expired`.
pub const WINDOW_SECONDS: i64 = 120;
/// Authentication attempts allowed across every session until a new window starts.
pub const MAX_AUTH_ATTEMPTS: i64 = 3;
/// Unauthenticated connections one window may hold *at once*. Releasing one frees
/// the slot; the attempt budget is the cumulative limit.
pub const MAX_UNAUTHENTICATED_CONNECTIONS: i64 = 2;
/// Sessions retained; older terminal sessions are pruned.
pub const MAX_SESSIONS: i64 = 16;
/// Lifecycle events retained per session.
pub const MAX_EVENTS: i64 = 24;
/// How long a commit may go without a stage update before a later recovery pass treats
/// it as interrupted. Recovery only runs on a session-owning command, but a second
/// window or a status reader must never be able to declare a live commit dead.
pub const LIVE_COMMIT_SECONDS: i64 = 60;

pub const HOST: &str = "host";
pub const JOINER: &str = "joiner";

/// Every state the typed client may receive. Kept identical to
/// `packages/odessy_client/lib/src/pairing.dart`.
pub const STATES: [&str; 14] = [
    "closed",
    "discoverable",
    "authenticating",
    "awaiting_approval",
    "enrolling",
    "paired_no_access",
    "authorizing_access",
    "connecting",
    "ready",
    "rejected",
    "expired",
    "cancelled",
    "failed",
    "incomplete",
];

/// States that no longer progress on their own. `incomplete` is terminal for
/// automation: only an explicit operator retry moves it.
pub fn is_terminal(state: &str) -> bool {
    matches!(
        state,
        "ready"
            | "paired_no_access"
            | "rejected"
            | "expired"
            | "cancelled"
            | "failed"
            | "incomplete"
    )
}

/// A state that may still accept a new authenticated connection.
pub fn is_open(state: &str) -> bool {
    matches!(state, "discoverable" | "authenticating")
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Session {
    pub session_id: String,
    pub role: String,
    pub state: String,
    pub workspace: Option<String>,
    /// Never leaves the host process except to the host's own status window.
    pub code: String,
    pub created_at: i64,
    pub expires_at: i64,
    pub connections: i64,
    pub review_token: String,
    pub operation_id: String,
    /// none | peer | grant | done
    pub commit_stage: String,
    pub peer_alias: Option<String>,
    pub peer_installation_id: Option<String>,
    pub peer_fingerprint: Option<String>,
    pub peer_ca_pem: Option<String>,
    pub peer_cert_pem: Option<String>,
    pub grant_workspace: Option<String>,
    pub error: Option<String>,
    pub updated_at: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Selection {
    pub attempts_used: i64,
    pub connections_used: i64,
}

impl Selection {
    pub fn attempts_remaining(&self) -> i64 {
        (MAX_AUTH_ATTEMPTS - self.attempts_used).max(0)
    }
    pub fn connections_remaining(&self) -> i64 {
        (MAX_UNAUTHENTICATED_CONNECTIONS - self.connections_used).max(0)
    }
}

pub fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or(0)
}

/// A code in `000000..=999999`, always six characters with leading zeros.
pub fn format_code(value: u32) -> String {
    format!("{:06}", value % 1_000_000)
}

/// Six ASCII digits, exactly. `0`-`9` only: a Unicode digit is not a digit here.
pub fn valid_code(code: &str) -> bool {
    code.len() == CODE_DIGITS && code.bytes().all(|byte| byte.is_ascii_digit())
}

pub struct Store {
    connection: Mutex<Connection>,
}

impl Store {
    /// Open for an operation that owns sessions, running recovery.
    pub fn open(path: &Path) -> Result<Self, String> {
        let store = Self::open_without_recovery(path)?;
        store.recover()?;
        Ok(store)
    }

    /// Open for inspection only.
    ///
    /// `pairing status` must be able to look at a session while another command is
    /// committing it; recovery here would report a live commit as interrupted.
    pub fn open_readonly(path: &Path) -> Result<Self, String> {
        Self::open_without_recovery(path)
    }

    fn open_without_recovery(path: &Path) -> Result<Self, String> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|error| format!("cannot create pairing state: {error}"))?;
        }
        let connection = Connection::open(path)
            .map_err(|error| format!("cannot open pairing state: {error}"))?;
        crate::store::restrict_file(path)?;
        connection
            .execute_batch(
                "PRAGMA journal_mode=WAL; PRAGMA secure_delete=ON; PRAGMA busy_timeout=5000;
                 CREATE TABLE IF NOT EXISTS session(
                     session_id TEXT PRIMARY KEY,
                     role TEXT NOT NULL,
                     state TEXT NOT NULL,
                     workspace TEXT,
                     code TEXT NOT NULL,
                     created_at INTEGER NOT NULL,
                     expires_at INTEGER NOT NULL,
                     connections INTEGER NOT NULL DEFAULT 0,
                     review_token TEXT NOT NULL,
                     operation_id TEXT NOT NULL,
                     commit_stage TEXT NOT NULL DEFAULT 'none',
                     peer_alias TEXT,
                     peer_installation_id TEXT,
                     peer_fingerprint TEXT,
                     peer_ca_pem TEXT,
                     peer_cert_pem TEXT,
                     grant_workspace TEXT,
                     error TEXT,
                     updated_at INTEGER NOT NULL
                 );
                 CREATE TABLE IF NOT EXISTS event(
                     session_id TEXT NOT NULL,
                     seq INTEGER NOT NULL,
                     at INTEGER NOT NULL,
                     state TEXT NOT NULL,
                     detail TEXT NOT NULL,
                     PRIMARY KEY(session_id, seq)
                 );
                 CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);",
            )
            .map_err(|error| format!("cannot configure pairing state: {error}"))?;
        for sidecar in ["-wal", "-shm"] {
            let mut name = path.as_os_str().to_owned();
            name.push(sidecar);
            crate::store::restrict_file(std::path::Path::new(&name))?;
        }
        let store = Self {
            connection: Mutex::new(connection),
        };
        {
            let connection = store.lock()?;
            for key in ["auth_attempts", "connections"] {
                connection
                    .execute(
                        "INSERT OR IGNORE INTO meta(key, value) VALUES(?1, '0')",
                        params![key],
                    )
                    .map_err(|error| format!("cannot create pairing budget: {error}"))?;
            }
        }
        Ok(store)
    }

    fn lock(&self) -> Result<std::sync::MutexGuard<'_, Connection>, String> {
        self.connection
            .lock()
            .map_err(|_| "pairing state lock poisoned".to_string())
    }

    /// Report every transition so a client can show what happened without a
    /// second, unbounded log. The newest [`MAX_EVENTS`] events are kept.
    fn event(
        connection: &Connection,
        session: &str,
        state: &str,
        detail: &str,
        at: i64,
    ) -> Result<(), String> {
        let seq: i64 = connection
            .query_row(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM event WHERE session_id=?1",
                params![session],
                |row| row.get(0),
            )
            .map_err(|error| format!("cannot read pairing events: {error}"))?;
        let detail: String = detail.chars().take(120).collect();
        connection
            .execute(
                "INSERT INTO event(session_id, seq, at, state, detail) VALUES(?1, ?2, ?3, ?4, ?5)",
                params![session, seq, at, state, detail],
            )
            .map_err(|error| format!("cannot record pairing event: {error}"))?;
        connection
            .execute(
                "DELETE FROM event WHERE session_id=?1 AND seq <= ?2",
                params![session, seq - MAX_EVENTS],
            )
            .map_err(|error| format!("cannot trim pairing events: {error}"))?;
        Ok(())
    }

    /// Recovery runs at open so a restart never leaves a stale session claiming to
    /// be live, and never completes or reverses a trust commit on its own.
    fn recover(&self) -> Result<usize, String> {
        let connection = self.lock()?;
        let at = now();
        let interrupted = connection
            .execute(
                "UPDATE session SET state='incomplete', error=COALESCE(error,'commit_interrupted'), updated_at=?1
                 WHERE state IN ('enrolling','authorizing_access','connecting') AND updated_at <= ?2",
                params![at, at - LIVE_COMMIT_SECONDS],
            )
            .map_err(|error| format!("cannot recover pairing sessions: {error}"))?;
        let expired = connection
            .execute(
                "UPDATE session SET state='expired', updated_at=?1
                 WHERE state IN ('discoverable','authenticating') AND expires_at<=?1",
                params![at],
            )
            .map_err(|error| format!("cannot expire pairing sessions: {error}"))?;
        let rows: Vec<(String, String)> = {
            let mut statement = connection
                .prepare("SELECT session_id, state FROM session WHERE updated_at=?1")
                .map_err(|error| format!("cannot read recovered sessions: {error}"))?;
            let rows = statement
                .query_map(params![at], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
                })
                .map_err(|error| format!("cannot read recovered sessions: {error}"))?;
            rows.collect::<Result<Vec<_>, _>>()
                .map_err(|error| format!("cannot read recovered sessions: {error}"))?
        };
        for (session, state) in &rows {
            let detail = if state == "incomplete" {
                "recovered: an interrupted commit is reported, not completed"
            } else {
                "recovered: the pairing window had passed"
            };
            Self::event(&connection, session, state, detail, at)?;
        }
        Ok(interrupted + expired)
    }

    /// Start a host session. The attempt and connection budgets belong to the
    /// window, so starting a new one resets them; the attacker cannot start one.
    pub fn begin_host(
        &self,
        workspace: Option<&Path>,
        code: &str,
        review_token: &str,
        operation_id: &str,
        session_id: &str,
    ) -> Result<Session, String> {
        if !valid_code(code) {
            return Err("a pairing code is exactly six digits".into());
        }
        let connection = self.lock()?;
        let at = now();
        let transaction = connection
            .unchecked_transaction()
            .map_err(|error| format!("cannot start pairing transaction: {error}"))?;
        transaction
            .execute(
                "UPDATE meta SET value='0' WHERE key IN ('auth_attempts','connections')",
                [],
            )
            .map_err(|error| format!("cannot reset pairing budget: {error}"))?;
        transaction
            .execute(
                "INSERT INTO session(session_id, role, state, workspace, code, created_at, expires_at,
                     connections, review_token, operation_id, commit_stage, updated_at)
                 VALUES(?1, ?2, 'discoverable', ?3, ?4, ?5, ?6, 0, ?7, ?8, 'none', ?5)",
                params![
                    session_id,
                    HOST,
                    workspace.map(|value| value.to_string_lossy().to_string()),
                    code,
                    at,
                    at + WINDOW_SECONDS,
                    review_token,
                    operation_id
                ],
            )
            .map_err(|error| format!("cannot create pairing session: {error}"))?;
        Self::event(&transaction, session_id, "discoverable", "window opened", at)?;
        Self::prune(&transaction)?;
        transaction
            .commit()
            .map_err(|error| format!("cannot commit pairing session: {error}"))?;
        drop(connection);
        self.session(session_id)?
            .ok_or_else(|| "pairing session disappeared while opening".to_string())
    }

    /// Start a joiner session: the record exists before the first packet, so a
    /// crash during the exchange leaves a truthful `incomplete` row.
    pub fn begin_joiner(
        &self,
        address: &str,
        operation_id: &str,
        session_id: &str,
    ) -> Result<Session, String> {
        let connection = self.lock()?;
        let at = now();
        connection
            .execute(
                "INSERT INTO session(session_id, role, state, workspace, code, created_at, expires_at,
                     connections, review_token, operation_id, commit_stage, updated_at)
                 VALUES(?1, ?2, 'authenticating', ?3, '', ?4, ?5, 0, '', ?6, 'none', ?4)",
                params![session_id, JOINER, address, at, at + WINDOW_SECONDS, operation_id],
            )
            .map_err(|error| format!("cannot create pairing attempt: {error}"))?;
        Self::event(&connection, session_id, "authenticating", "dialling the host", at)?;
        Self::prune(&connection)?;
        drop(connection);
        self.session(session_id)?
            .ok_or_else(|| "pairing attempt disappeared while opening".to_string())
    }

    fn prune(connection: &Connection) -> Result<(), String> {
        connection
            .execute(
                "DELETE FROM session WHERE session_id IN (
                     SELECT session_id FROM session
                     WHERE state IN ('ready','paired_no_access','rejected','expired','cancelled','failed','incomplete')
                     ORDER BY updated_at DESC LIMIT -1 OFFSET ?1
                 )",
                params![MAX_SESSIONS],
            )
            .map_err(|error| format!("cannot prune pairing sessions: {error}"))?;
        Ok(())
    }

    pub fn session(&self, id: &str) -> Result<Option<Session>, String> {
        let connection = self.lock()?;
        connection
            .query_row(
                "SELECT session_id, role, state, workspace, code, created_at, expires_at, connections,
                        review_token, operation_id, commit_stage, peer_alias, peer_installation_id,
                        peer_fingerprint, peer_ca_pem, peer_cert_pem, grant_workspace, error, updated_at
                 FROM session WHERE session_id=?1",
                params![id],
                row_to_session,
            )
            .optional()
            .map_err(|error| format!("cannot read pairing session: {error}"))
    }

    /// The newest session, whatever its state. A client that only knows "the
    /// pairing screen" can always report what actually happened last.
    pub fn current(&self) -> Result<Option<Session>, String> {
        let connection = self.lock()?;
        connection
            .query_row(
                "SELECT session_id, role, state, workspace, code, created_at, expires_at, connections,
                        review_token, operation_id, commit_stage, peer_alias, peer_installation_id,
                        peer_fingerprint, peer_ca_pem, peer_cert_pem, grant_workspace, error, updated_at
                 FROM session ORDER BY created_at DESC, rowid DESC LIMIT 1",
                [],
                row_to_session,
            )
            .optional()
            .map_err(|error| format!("cannot read pairing session: {error}"))
    }

    /// The newest session on this machine for `role`.
    pub fn current_for(&self, role: &str) -> Result<Option<Session>, String> {
        let connection = self.lock()?;
        connection
            .query_row(
                "SELECT session_id, role, state, workspace, code, created_at, expires_at, connections,
                        review_token, operation_id, commit_stage, peer_alias, peer_installation_id,
                        peer_fingerprint, peer_ca_pem, peer_cert_pem, grant_workspace, error, updated_at
                 FROM session WHERE role=?1 ORDER BY created_at DESC, rowid DESC LIMIT 1",
                params![role],
                row_to_session,
            )
            .optional()
            .map_err(|error| format!("cannot read pairing session: {error}"))
    }

    pub fn selection(&self) -> Result<Selection, String> {
        let connection = self.lock()?;
        let read = |key: &str| -> Result<i64, String> {
            let value: String = connection
                .query_row("SELECT value FROM meta WHERE key=?1", params![key], |row| {
                    row.get(0)
                })
                .map_err(|error| format!("cannot read pairing budget: {error}"))?;
            value
                .parse::<i64>()
                .map_err(|error| format!("pairing budget is not a number: {error}"))
        };
        Ok(Selection {
            attempts_used: read("auth_attempts")?,
            connections_used: read("connections")?,
        })
    }

    /// Reserve one authentication attempt before the code is compared.
    ///
    /// The UPDATE is the reservation: it succeeds at most [`MAX_AUTH_ATTEMPTS`]
    /// times no matter how many processes or threads try at once, so two
    /// concurrent guesses cannot both see "one attempt left".
    pub fn reserve_attempt(&self) -> Result<bool, String> {
        let connection = self.lock()?;
        let changed = connection
            .execute(
                "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)
                 WHERE key='auth_attempts' AND CAST(value AS INTEGER) < ?1",
                params![MAX_AUTH_ATTEMPTS],
            )
            .map_err(|error| format!("cannot reserve an attempt: {error}"))?;
        Ok(changed == 1)
    }

    /// Reserve one unauthenticated connection for the window.
    pub fn reserve_connection(&self, session: &str) -> Result<bool, String> {
        let connection = self.lock()?;
        let transaction = connection
            .unchecked_transaction()
            .map_err(|error| format!("cannot start pairing transaction: {error}"))?;
        let changed = transaction
            .execute(
                "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)
                 WHERE key='connections' AND CAST(value AS INTEGER) < ?1",
                params![MAX_UNAUTHENTICATED_CONNECTIONS],
            )
            .map_err(|error| format!("cannot reserve a connection: {error}"))?;
        if changed != 1 {
            transaction
                .commit()
                .map_err(|error| format!("cannot close pairing transaction: {error}"))?;
            return Ok(false);
        }
        transaction
            .execute(
                "UPDATE session SET connections = connections + 1, updated_at=?2 WHERE session_id=?1",
                params![session, now()],
            )
            .map_err(|error| format!("cannot record the connection: {error}"))?;
        transaction
            .commit()
            .map_err(|error| format!("cannot close pairing transaction: {error}"))?;
        Ok(true)
    }

    /// Release a connection slot when an exchange finishes or fails.
    pub fn release_connection(&self) -> Result<(), String> {
        let connection = self.lock()?;
        connection
            .execute(
                "UPDATE meta SET value = CAST(MAX(CAST(value AS INTEGER) - 1, 0) AS TEXT) WHERE key='connections'",
                [],
            )
            .map_err(|error| format!("cannot release the connection slot: {error}"))?;
        Ok(())
    }

    /// Expire a session whose window has passed, then return it.
    pub fn refresh(&self, id: &str) -> Result<Session, String> {
        let at = now();
        {
            let connection = self.lock()?;
            let changed = connection
                .execute(
                    "UPDATE session SET state='expired', updated_at=?2
                     WHERE session_id=?1 AND state IN ('discoverable','authenticating') AND expires_at<=?2",
                    params![id, at],
                )
                .map_err(|error| format!("cannot expire pairing session: {error}"))?;
            if changed == 1 {
                Self::event(&connection, id, "expired", "the pairing window passed", at)?;
            }
        }
        self.session(id)?
            .ok_or_else(|| format!("unknown pairing session: {id}"))
    }

    /// Move a session forward, refusing any transition from a state it no longer
    /// holds. `from` is a list of states the caller believes it is in.
    pub fn transition(
        &self,
        id: &str,
        from: &[&str],
        to: &str,
        detail: &str,
    ) -> Result<Session, String> {
        if !STATES.contains(&to) {
            return Err(format!("unsupported pairing state: {to}"));
        }
        let at = now();
        {
            let connection = self.lock()?;
            let placeholders = vec!["?"; from.len()].join(",");
            let sql = format!(
                "UPDATE session SET state=?2, updated_at=?3 WHERE session_id=?1 AND state IN ({placeholders})"
            );
            let mut statement = connection
                .prepare(&sql)
                .map_err(|error| format!("cannot prepare pairing transition: {error}"))?;
            let mut values: Vec<Box<dyn rusqlite::ToSql>> = vec![
                Box::new(id.to_string()),
                Box::new(to.to_string()),
                Box::new(at),
            ];
            for state in from {
                values.push(Box::new(state.to_string()));
            }
            let references: Vec<&dyn rusqlite::ToSql> =
                values.iter().map(|value| value.as_ref()).collect();
            let changed = statement
                .execute(references.as_slice())
                .map_err(|error| format!("cannot update pairing session: {error}"))?;
            if changed != 1 {
                return Err(format!(
                    "pairing session {id} is not in a state that allows {to}"
                ));
            }
            Self::event(&connection, id, to, detail, at)?;
        }
        self.session(id)?
            .ok_or_else(|| format!("unknown pairing session: {id}"))
    }

    /// Record authenticated peer material as *pending*: it is not trust until the
    /// local operator confirms, and `awaiting_approval` is not an enrollment.
    #[allow(clippy::too_many_arguments)]
    pub fn record_pending_peer(
        &self,
        id: &str,
        alias: &str,
        installation_id: &str,
        fingerprint: &str,
        ca_pem: &str,
        certificate_pem: &str,
    ) -> Result<Session, String> {
        let at = now();
        {
            let connection = self.lock()?;
            let changed = connection
                .execute(
                    "UPDATE session SET state='awaiting_approval', peer_alias=?2, peer_installation_id=?3,
                         peer_fingerprint=?4, peer_ca_pem=?5, peer_cert_pem=?6, updated_at=?7
                     WHERE session_id=?1 AND state='authenticating'",
                    params![id, alias, installation_id, fingerprint, ca_pem, certificate_pem, at],
                )
                .map_err(|error| format!("cannot record the authenticated peer: {error}"))?;
            if changed != 1 {
                return Err("the pairing session cannot accept an authenticated peer".into());
            }
            Self::event(
                &connection,
                id,
                "awaiting_approval",
                "authenticated; waiting for the local operator",
                at,
            )?;
        }
        self.session(id)?
            .ok_or_else(|| format!("unknown pairing session: {id}"))
    }

    pub fn set_grant_workspace(&self, id: &str, workspace: &str) -> Result<Session, String> {
        let at = now();
        {
            let connection = self.lock()?;
            let changed = connection
                .execute(
                    "UPDATE session SET grant_workspace=?2, updated_at=?3
                     WHERE session_id=?1 AND state IN ('awaiting_approval','incomplete')",
                    params![id, workspace, at],
                )
                .map_err(|error| format!("cannot record the reviewed workspace: {error}"))?;
            if changed != 1 {
                return Err("the pairing session is not awaiting approval".into());
            }
        }
        self.session(id)?
            .ok_or_else(|| format!("unknown pairing session: {id}"))
    }

    /// Begin the commit. The operation id is durable, so a retry after a crash is
    /// the same approved operation rather than a second, different one.
    pub fn begin_commit(&self, id: &str, review_token: &str) -> Result<Session, String> {
        let at = now();
        {
            let connection = self.lock()?;
            let changed = connection
                .execute(
                    "UPDATE session SET state='enrolling', updated_at=?3
                     WHERE session_id=?1 AND review_token=?2
                       AND state IN ('awaiting_approval','incomplete') AND commit_stage != 'done'",
                    params![id, review_token, at],
                )
                .map_err(|error| format!("cannot start the enrollment commit: {error}"))?;
            if changed != 1 {
                return Err(
                    "the approval token is stale, already used, or the session is not awaiting approval"
                        .into(),
                );
            }
            Self::event(&connection, id, "enrolling", "committing the approved peer", at)?;
        }
        self.session(id)?
            .ok_or_else(|| format!("unknown pairing session: {id}"))
    }

    pub fn set_commit_stage(&self, id: &str, stage: &str) -> Result<(), String> {
        let connection = self.lock()?;
        connection
            .execute(
                "UPDATE session SET commit_stage=?2, updated_at=?3 WHERE session_id=?1",
                params![id, stage, now()],
            )
            .map_err(|error| format!("cannot record the commit stage: {error}"))?;
        Ok(())
    }

    /// Record the committed outcome. `ready` means identity enrolled *and* the
    /// reviewed workspace granted; `paired_no_access` means identity only.
    pub fn finish_commit(&self, id: &str) -> Result<Session, String> {
        let at = now();
        {
            let connection = self.lock()?;
            let granted: Option<Option<String>> = connection
                .query_row(
                    "SELECT grant_workspace FROM session WHERE session_id=?1",
                    params![id],
                    |row| row.get(0),
                )
                .optional()
                .map_err(|error| format!("cannot read the reviewed workspace: {error}"))?;
            let state = match granted.flatten() {
                Some(_) => "ready",
                None => "paired_no_access",
            };
            connection
                .execute(
                    "UPDATE session SET state=?2, commit_stage='done', error=NULL, updated_at=?3
                     WHERE session_id=?1 AND state IN ('enrolling','awaiting_approval')",
                    params![id, state, at],
                )
                .map_err(|error| format!("cannot finish the enrollment commit: {error}"))?;
            Self::event(&connection, id, state, "trust committed locally", at)?;
        }
        self.session(id)?
            .ok_or_else(|| format!("unknown pairing session: {id}"))
    }

    /// Report an unknown or partial commit without inventing an outcome.
    pub fn mark_incomplete(&self, id: &str, reason: &str) -> Result<Session, String> {
        let at = now();
        {
            let connection = self.lock()?;
            connection
                .execute(
                    "UPDATE session SET state='incomplete', error=?2, updated_at=?3
                     WHERE session_id=?1 AND state NOT IN ('cancelled')",
                    params![id, reason.chars().take(120).collect::<String>(), at],
                )
                .map_err(|error| format!("cannot record the incomplete commit: {error}"))?;
            Self::event(&connection, id, "incomplete", reason, at)?;
        }
        self.session(id)?
            .ok_or_else(|| format!("unknown pairing session: {id}"))
    }


    /// Cancel a session.
    ///
    /// A commit is not cancellable: once `enrolling`, the answer is "unknown" —
    /// the caller must inspect rather than be told the change was undone.
    pub fn cancel(&self, id: &str) -> Result<(Session, bool), String> {
        let at = now();
        let state: String = {
            let connection = self.lock()?;
            connection
                .query_row("SELECT state FROM session WHERE session_id=?1", params![id], |row| {
                    row.get(0)
                })
                .optional()
                .map_err(|error| format!("cannot read pairing session: {error}"))?
                .ok_or_else(|| format!("unknown pairing session: {id}"))?
        };
        // A commit already in progress has an unknown outcome. Saying "cancelled"
        // would claim a rollback that never happened.
        if state == "enrolling" {
            return Ok((
                self.session(id)?
                    .ok_or_else(|| format!("unknown pairing session: {id}"))?,
                false,
            ));
        }
        {
            let connection = self.lock()?;
            let changed = connection
                .execute(
                    "UPDATE session SET state='cancelled', updated_at=?2
                     WHERE session_id=?1 AND state IN ('discoverable','authenticating','awaiting_approval')",
                    params![id, at],
                )
                .map_err(|error| format!("cannot cancel pairing session: {error}"))?;
            if changed == 1 {
                Self::event(&connection, id, "cancelled", "the operator cancelled", at)?;
            }
            drop(connection);
            let session = self
                .session(id)?
                .ok_or_else(|| format!("unknown pairing session: {id}"))?;
            // Report what this call did, not what the session happens to be: cancelling
            // an already-finished session changed nothing.
            Ok((session, changed == 1))
        }
    }

    /// Lifecycle events for a session, oldest first.
    pub fn events(&self, id: &str) -> Result<Vec<(i64, String, String)>, String> {
        let connection = self.lock()?;
        let mut statement = connection
            .prepare("SELECT at, state, detail FROM event WHERE session_id=?1 ORDER BY seq")
            .map_err(|error| format!("cannot read pairing events: {error}"))?;
        let rows = statement
            .query_map(params![id], |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                ))
            })
            .map_err(|error| format!("cannot read pairing events: {error}"))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("cannot read pairing events: {error}"))
    }
}

fn row_to_session(row: &rusqlite::Row<'_>) -> rusqlite::Result<Session> {
    Ok(Session {
        session_id: row.get(0)?,
        role: row.get(1)?,
        state: row.get(2)?,
        workspace: row.get(3)?,
        code: row.get(4)?,
        created_at: row.get(5)?,
        expires_at: row.get(6)?,
        connections: row.get(7)?,
        review_token: row.get(8)?,
        operation_id: row.get(9)?,
        commit_stage: row.get(10)?,
        peer_alias: row.get(11)?,
        peer_installation_id: row.get(12)?,
        peer_fingerprint: row.get(13)?,
        peer_ca_pem: row.get(14)?,
        peer_cert_pem: row.get(15)?,
        grant_workspace: row.get(16)?,
        error: row.get(17)?,
        updated_at: row.get(18)?,
    })
}