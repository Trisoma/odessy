//! Per-user trust registry backed by SQLite.
//!
//! The registry holds only enrollment material and grants. It never stores tool
//! payloads, prompts, or credentials, and it never stores private keys: identity
//! keys live in their own files with owner-only permissions.

use crate::identity::{display_fingerprint, fingerprint_sha256, EnrollmentBundle};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};

/// Capabilities a peer can be granted. Unknown capability strings are rejected
/// rather than stored, so a typo cannot silently widen access.
pub const CAPABILITY_MCP: &str = "mcp";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Peer {
    pub alias: String,
    pub installation_id: String,
    pub fingerprint_sha256: String,
    pub address: Option<String>,
    pub capability_mcp: bool,
    pub capability_telemetry: bool,
    pub revoked: bool,
    pub workspace: Option<String>,
    pub certificate_pem: String,
    pub ca_pem: String,
}

impl Peer {
    pub fn has_execution_grant(&self) -> bool {
        self.capability_mcp && self.workspace.is_some()
    }
}

pub struct Registry {
    connection: Connection,
}

impl Registry {
    pub fn open(path: &std::path::Path) -> Result<Self, String> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|error| format!("cannot create state directory: {error}"))?;
        }
        let connection =
            Connection::open(path).map_err(|error| format!("cannot open registry: {error}"))?;
        // Authority state, so owner-only like the identity itself: WAL sidecars are
        // created lazily, hence the second call after the pragma below.
        crate::store::restrict_file(path)?;
        // WAL keeps a reader (peers list) from blocking a grant update, and
        // `secure_delete` clears revoked bytes rather than leaving them in pages.
        connection
            .execute_batch(
                "PRAGMA journal_mode=WAL; PRAGMA secure_delete=ON; PRAGMA foreign_keys=ON;",
            )
            .map_err(|error| format!("cannot configure registry: {error}"))?;
        for sidecar in ["-wal", "-shm"] {
            let mut name = path.as_os_str().to_owned();
            name.push(sidecar);
            crate::store::restrict_file(std::path::Path::new(&name))?;
        }
        let registry = Self { connection };
        registry.init_schema()?;
        Ok(registry)
    }

    fn init_schema(&self) -> Result<(), String> {
        self.connection
            .execute_batch(
                "CREATE TABLE IF NOT EXISTS peers(
                     alias TEXT PRIMARY KEY,
                     installation_id TEXT NOT NULL,
                     fingerprint_sha256 TEXT NOT NULL,
                     certificate_pem TEXT NOT NULL,
                     ca_pem TEXT NOT NULL,
                     address TEXT,
                     capability_mcp INTEGER NOT NULL DEFAULT 0,
                     workspace TEXT
                 );
                 CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);",
            )
            .map_err(|error| format!("cannot create schema: {error}"))?;

        // Additive migration: previously enrolled peers get NO telemetry grant.
        let has_telemetry = self.connection.prepare("PRAGMA table_info(peers)")
            .map_err(|e| e.to_string())?.query_map([], |row| row.get::<_, String>(1))
            .map_err(|e| e.to_string())?.collect::<Result<Vec<_>, _>>()
            .map_err(|e| e.to_string())?.iter().any(|name| name == "capability_telemetry");
        if !has_telemetry {
            self.connection.execute("ALTER TABLE peers ADD COLUMN capability_telemetry INTEGER NOT NULL DEFAULT 0", [])
                .map_err(|e| format!("cannot migrate telemetry grant: {e}"))?;
        }
        self.connection
            .execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('grant_revision', '0')",
                [],
            )
            .map_err(|error| format!("cannot create schema: {error}"))?;
        let has_revoked = self.connection.prepare("PRAGMA table_info(peers)").map_err(|e| e.to_string())?
            .query_map([], |row| row.get::<_, String>(1)).map_err(|e| e.to_string())?
            .collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())?.iter().any(|name| name == "revoked");
        if !has_revoked {self.connection.execute("ALTER TABLE peers ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0", []).map_err(|e|e.to_string())?;}
        Ok(())
    }

    /// Monotonic local grant revision. Bumped by every enrolment, grant and revoke so
    /// a coordinator can bind an approval to the permissions it saw.
    pub fn revision(&self) -> Result<i64, String> {
        self.connection
            .query_row("SELECT value FROM meta WHERE key='grant_revision'", [], |row| {
                row.get::<_, String>(0)
            })
            .map_err(|error| format!("cannot read revision: {error}"))?
            .parse::<i64>()
            .map_err(|error| format!("revision is not a number: {error}"))
    }

    /// Bump the revision inside the caller's transaction, so a permission change and
    /// the revision that describes it commit together or not at all.
    fn bump_revision(&self, transaction: &rusqlite::Transaction<'_>) -> Result<(), String> {
        transaction
            .execute(
                "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key='grant_revision'",
                [],
            )
            .map_err(|error| format!("cannot update revision: {error}"))?;
        Ok(())
    }

    /// Enroll a peer from an exported bundle.
    ///
    /// The operator must pass the fingerprint they compared out of band; a bundle
    /// whose certificate does not hash to that value is refused. Nothing here is
    /// authenticated by the bundle itself, which is why the comparison is
    /// mandatory rather than optional.
    pub fn add_peer(
        &self,
        alias: &str,
        bundle_text: &str,
        expected_sha256: &str,
        address: Option<&str>,
    ) -> Result<Peer, String> {
        validate_alias(alias)?;
        let bundle = EnrollmentBundle::from_json(bundle_text)?;
        let expected = normalize_fingerprint(expected_sha256)?;
        if expected != bundle.fingerprint_sha256 {
            return Err(format!(
                "certificate fingerprint mismatch: the bundle is {} but you expected {}",
                display_fingerprint(&bundle.fingerprint_sha256),
                display_fingerprint(&expected)
            ));
        }
        let der = certificate_der(&bundle.certificate_pem)?;
        if fingerprint_sha256(&der) != expected {
            return Err("bundle fingerprint does not match its certificate".into());
        }
        // One transaction: the peer row, its dropped grant, and the revision commit
        // together. A revision that describes a state the peers table never had would
        // make a coordinator's approval binding meaningless.
        let transaction = self
            .connection
            .unchecked_transaction()
            .map_err(|error| format!("cannot start trust transaction: {error}"))?;
        transaction
            .execute(
                "INSERT INTO peers(alias, installation_id, fingerprint_sha256, certificate_pem, ca_pem, address)
                 VALUES(?1, ?2, ?3, ?4, ?5, ?6)
                 ON CONFLICT(alias) DO UPDATE SET
                     installation_id=excluded.installation_id,
                     fingerprint_sha256=excluded.fingerprint_sha256,
                     certificate_pem=excluded.certificate_pem,
                     ca_pem=excluded.ca_pem,
                     address=excluded.address,
                     capability_mcp=0, capability_telemetry=0, revoked=0,
                     workspace=NULL",
                params![
                    alias,
                    bundle.installation_id,
                    expected,
                    bundle.certificate_pem,
                    bundle.ca_pem,
                    address
                ],
            )
            .map_err(|error| format!("cannot save peer: {error}"))?;
        // Re-enrolling the same alias always drops existing grants: a replaced
        // certificate must not inherit the old peer's execution access.
        self.bump_revision(&transaction)?;
        transaction
            .commit()
            .map_err(|error| format!("cannot commit trust transaction: {error}"))?;
        self.peer(alias)?
            .ok_or_else(|| "peer disappeared while saving".to_string())
    }

    pub fn peer(&self, alias: &str) -> Result<Option<Peer>, String> {
        self.connection
            .query_row(
                "SELECT alias, installation_id, fingerprint_sha256, address, capability_mcp, workspace,
                        certificate_pem, ca_pem, capability_telemetry, revoked
                 FROM peers WHERE alias = ?1",
                params![alias],
                row_to_peer,
            )
            .optional()
            .map_err(|error| format!("cannot read peer: {error}"))
    }

    /// Identify an admitted connection by the exact fingerprint of the certificate
    /// it presented. A valid chain alone is not enough: the leaf must be enrolled.
    pub fn peer_by_fingerprint(&self, fingerprint: &str) -> Result<Option<Peer>, String> {
        self.connection
            .query_row(
                "SELECT alias, installation_id, fingerprint_sha256, address, capability_mcp, workspace,
                        certificate_pem, ca_pem, capability_telemetry, revoked
                 FROM peers WHERE fingerprint_sha256 = ?1",
                params![fingerprint],
                row_to_peer,
            )
            .optional()
            .map_err(|error| format!("cannot read peer: {error}"))
    }

    /// Every enrolled peer CA, as DER, for the TLS verifiers. Only explicitly
    /// enrolled authorities are trusted: no system roots, no accept-any verifier.
    pub fn enrolled_authorities(&self) -> Result<Vec<Vec<u8>>, String> {
        let mut statement = self
            .connection
            .prepare("SELECT ca_pem FROM peers")
            .map_err(|error| format!("cannot list authorities: {error}"))?;
        let rows = statement
            .query_map([], |row| row.get::<_, String>(0))
            .map_err(|error| format!("cannot list authorities: {error}"))?;
        let mut authorities = Vec::new();
        for row in rows {
            let pem = row.map_err(|error| format!("cannot list authorities: {error}"))?;
            authorities.push(certificate_der(&pem)?);
        }
        Ok(authorities)
    }

    pub fn peers(&self) -> Result<Vec<Peer>, String> {
        let mut statement = self
            .connection
            .prepare(
                "SELECT alias, installation_id, fingerprint_sha256, address, capability_mcp, workspace,
                        certificate_pem, ca_pem, capability_telemetry, revoked
                 FROM peers ORDER BY alias",
            )
            .map_err(|error| format!("cannot list peers: {error}"))?;
        let rows = statement
            .query_map([], row_to_peer)
            .map_err(|error| format!("cannot list peers: {error}"))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("cannot list peers: {error}"))
    }

    /// Grant a capability. Enrollment alone never implies execution access.
    pub fn grant(&self, alias: &str, capability: &str, workspace: &str) -> Result<Peer, String> {
        if capability != CAPABILITY_MCP && capability != "telemetry.read" {
            return Err(format!("unsupported capability: {capability}"));
        }
        if capability == CAPABILITY_MCP && workspace.trim().is_empty() {
            return Err("workspace must be a non-empty path".into());
        }
        let transaction = self
            .connection
            .unchecked_transaction()
            .map_err(|error| format!("cannot start trust transaction: {error}"))?;
        let changed = transaction
            .execute(
                if capability == CAPABILITY_MCP {
                    "UPDATE peers SET capability_mcp=1, revoked=0, workspace=?2 WHERE alias=?1"
                } else {
                    "UPDATE peers SET capability_telemetry=1, revoked=0 WHERE alias=?1 AND ?2 IS NOT NULL"
                },
                params![alias, workspace],
            )
            .map_err(|error| format!("cannot grant capability: {error}"))?;
        if changed == 0 {
            return Err(format!("unknown peer: {alias}"));
        }
        self.bump_revision(&transaction)?;
        transaction
            .commit()
            .map_err(|error| format!("cannot commit trust transaction: {error}"))?;
        self.peer(alias)?
            .ok_or_else(|| "peer disappeared while granting".to_string())
    }

    /// Revoke every capability for a peer. Returns whether the peer existed, so a
    /// caller can distinguish "already gone" from "revoked now".
    pub fn revoke(&self, alias: &str) -> Result<bool, String> {
        let transaction = self
            .connection
            .unchecked_transaction()
            .map_err(|error| format!("cannot start trust transaction: {error}"))?;
        let changed = transaction
            .execute(
                "UPDATE peers SET capability_mcp=0, capability_telemetry=0, revoked=1, workspace=NULL WHERE alias=?1",
                params![alias],
            )
            .map_err(|error| format!("cannot revoke peer: {error}"))?;
        if changed > 0 {
            self.bump_revision(&transaction)?;
        }
        transaction
            .commit()
            .map_err(|error| format!("cannot commit trust transaction: {error}"))?;
        Ok(changed > 0)
    }
}

fn row_to_peer(row: &rusqlite::Row<'_>) -> rusqlite::Result<Peer> {
    Ok(Peer {
        alias: row.get(0)?,
        installation_id: row.get(1)?,
        fingerprint_sha256: row.get(2)?,
        address: row.get(3)?,
        capability_mcp: row.get::<_, i64>(4)? != 0,
        workspace: row.get(5)?,
        certificate_pem: row.get(6)?,
        ca_pem: row.get(7)?,
        capability_telemetry: row.get::<_, i64>(8)? != 0,
        revoked: row.get::<_, i64>(9)? != 0,
    })
}

/// Accept a grouped or bare fingerprint, and normalise to lowercase bare hex so a
/// pasted display value cannot fail a comparison by formatting alone.
pub fn normalize_fingerprint(text: &str) -> Result<String, String> {
    let bare: String = text
        .chars()
        .filter(|character| !character.is_ascii_whitespace() && *character != ':')
        .collect::<String>()
        .to_ascii_lowercase();
    if bare.len() != 64 || !bare.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("fingerprint must be a full 64-character SHA-256 hex value".into());
    }
    Ok(bare)
}

fn validate_alias(alias: &str) -> Result<(), String> {
    if alias.is_empty() || alias.len() > 64 {
        return Err("alias must be 1-64 characters".into());
    }
    if !alias
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    {
        return Err("alias must be ASCII letters, digits, '-' or '_'".into());
    }
    Ok(())
}

/// Decode a PEM certificate body to DER without pulling in a PEM parser. Only the
/// single CERTIFICATE block an enrollment bundle contains is accepted.
pub fn certificate_der_for(pem: &str) -> Result<Vec<u8>, String> {
    certificate_der(pem)
}

fn certificate_der(pem: &str) -> Result<Vec<u8>, String> {
    let mut base64 = String::new();
    let mut inside = false;
    for line in pem.lines() {
        let line = line.trim();
        if line == "-----BEGIN CERTIFICATE-----" {
            inside = true;
            continue;
        }
        if line == "-----END CERTIFICATE-----" {
            return decode_base64(&base64);
        }
        if inside {
            base64.push_str(line);
        }
    }
    Err("bundle has no complete certificate block".into())
}

fn decode_base64(text: &str) -> Result<Vec<u8>, String> {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = Vec::with_capacity(text.len() / 4 * 3);
    let mut accumulator = 0u32;
    let mut bits = 0u32;
    for byte in text.bytes() {
        if byte == b'=' || byte.is_ascii_whitespace() {
            continue;
        }
        let value = TABLE
            .iter()
            .position(|candidate| *candidate == byte)
            .ok_or_else(|| "certificate PEM is not valid base64".to_string())?
            as u32;
        accumulator = (accumulator << 6) | value;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((accumulator >> bits) as u8);
        }
    }
    if out.is_empty() {
        return Err("certificate PEM is empty".into());
    }
    Ok(out)
}

#[cfg(test)]
#[allow(dead_code)]
/// Expose the PEM decoder to the identity tests without a second implementation.
pub fn certificate_der_for_tests(pem: &str) -> Result<Vec<u8>, String> {
    certificate_der(pem)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::identity::Identity;
    use std::path::PathBuf;

    struct TempRegistry {
        directory: PathBuf,
        registry: Registry,
    }

    impl TempRegistry {
        fn new(name: &str) -> Self {
            let directory = std::env::temp_dir().join(format!(
                "odessy-link-registry-{name}-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            let registry = Registry::open(&directory.join("registry.sqlite3")).expect("registry");
            Self {
                directory,
                registry,
            }
        }
    }

    impl Drop for TempRegistry {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.directory);
        }
    }

    fn bundle_json(identity: &Identity) -> String {
        serde_json::to_string(&identity.bundle()).expect("json")
    }

    #[test]
    fn enrollment_requires_the_operator_confirmed_fingerprint() {
        let fixture = TempRegistry::new("fingerprint");
        let peer_identity = Identity::generate("peer00000001").expect("identity");
        let json = bundle_json(&peer_identity);
        let wrong = "0".repeat(64);
        let error = fixture
            .registry
            .add_peer("jetson", &json, &wrong, Some("10.0.0.5:7443"))
            .expect_err("wrong fingerprint must be refused");
        assert!(error.contains("fingerprint mismatch"), "{error}");
        assert!(fixture.registry.peers().expect("peers").is_empty());

        let peer = fixture
            .registry
            .add_peer(
                "jetson",
                &json,
                &display_fingerprint(&peer_identity.fingerprint()),
                Some("10.0.0.5:7443"),
            )
            .expect("enrollment");
        assert_eq!(peer.fingerprint_sha256, peer_identity.fingerprint());
        assert_eq!(peer.address.as_deref(), Some("10.0.0.5:7443"));
        assert!(!peer.has_execution_grant(), "enrollment grants nothing");
    }

    #[test]
    fn telemetry_grant_is_independent_and_reenrollment_drops_it() {
        let fixture = TempRegistry::new("telemetry");
        let identity = Identity::generate("peertelemetry").unwrap();
        fixture.registry.add_peer("peer", &bundle_json(&identity), &identity.fingerprint(), None).unwrap();
        assert!(!fixture.registry.peer("peer").unwrap().unwrap().capability_telemetry);
        let peer = fixture.registry.grant("peer", "telemetry.read", "").expect("telemetry without workspace");
        assert!(peer.capability_telemetry);
        assert!(!peer.has_execution_grant());
        assert!(peer.workspace.is_none());
        fixture.registry.grant("peer", CAPABILITY_MCP, "workspace").unwrap();
        assert!(fixture.registry.peer("peer").unwrap().unwrap().capability_telemetry);
        fixture.registry.revoke("peer").unwrap();
        let peer = fixture.registry.peer("peer").unwrap().unwrap();
        assert!(!peer.capability_telemetry && !peer.has_execution_grant());
        fixture.registry.grant("peer", "telemetry.read", "").unwrap();
        fixture.registry.add_peer("peer", &bundle_json(&identity), &identity.fingerprint(), None).unwrap();
        assert!(!fixture.registry.peer("peer").unwrap().unwrap().capability_telemetry);
    }

    #[test]
    fn grants_and_revocations_change_admission_state() {
        let fixture = TempRegistry::new("grants");
        let peer_identity = Identity::generate("peer00000002").expect("identity");
        fixture
            .registry
            .add_peer(
                "jetson",
                &bundle_json(&peer_identity),
                &peer_identity.fingerprint(),
                None,
            )
            .expect("enrollment");
        assert!(fixture
            .registry
            .grant("jetson", CAPABILITY_MCP, r"C:\work")
            .expect("grant")
            .has_execution_grant());
        assert!(fixture
            .registry
            .grant("jetson", "wildcard", r"C:\work")
            .is_err());
        assert!(fixture.registry.grant("missing", CAPABILITY_MCP, r"C:\work").is_err());
        assert!(fixture.registry.revoke("jetson").expect("revoke"));
        assert!(!fixture.registry.peer("jetson").expect("peer").unwrap().has_execution_grant());
        assert!(!fixture.registry.revoke("missing").expect("revoke"));
    }

    #[test]
    fn re_enrolling_a_peer_drops_previous_grants() {
        let fixture = TempRegistry::new("reenroll");
        let first = Identity::generate("peer00000003").expect("identity");
        fixture
            .registry
            .add_peer("jetson", &bundle_json(&first), &first.fingerprint(), None)
            .expect("enrollment");
        fixture
            .registry
            .grant("jetson", CAPABILITY_MCP, r"C:\work")
            .expect("grant");
        let second = Identity::generate("peer00000004").expect("identity");
        let peer = fixture
            .registry
            .add_peer("jetson", &bundle_json(&second), &second.fingerprint(), None)
            .expect("re-enrollment");
        assert_eq!(peer.fingerprint_sha256, second.fingerprint());
        assert!(!peer.has_execution_grant(), "a new certificate must not inherit grants");
    }

    #[test]
    fn every_enrolment_grant_and_revoke_bumps_the_revision() {
        let fixture = TempRegistry::new("revision");
        let first = Identity::generate("peer00000020").expect("identity");
        assert_eq!(fixture.registry.revision().expect("revision"), 0);
        fixture
            .registry
            .add_peer("jetson", &bundle_json(&first), &first.fingerprint(), None)
            .expect("enrol");
        assert_eq!(fixture.registry.revision().expect("revision"), 1);
        fixture
            .registry
            .grant("jetson", CAPABILITY_MCP, r"C:\work")
            .expect("grant");
        assert_eq!(fixture.registry.revision().expect("revision"), 2);
        fixture.registry.revoke("jetson").expect("revoke");
        assert_eq!(fixture.registry.revision().expect("revision"), 3);
        // Revoking nothing changes no permission, so it must not move the revision.
        fixture.registry.revoke("missing").expect("noop");
        assert_eq!(fixture.registry.revision().expect("revision"), 3);
    }

    #[test]
    fn a_refused_change_does_not_move_the_revision() {
        let fixture = TempRegistry::new("atomic");
        let peer = Identity::generate("peer00000021").expect("identity");
        // Refused enrolment: fingerprint mismatch.
        let error = fixture
            .registry
            .add_peer("jetson", &bundle_json(&peer), &"0".repeat(64), None)
            .expect_err("mismatch must be refused");
        assert!(error.contains("mismatch"));
        assert_eq!(fixture.registry.revision().expect("revision"), 0);
        // Refused grant: unknown peer.
        assert!(fixture
            .registry
            .grant("missing", CAPABILITY_MCP, r"C:\work")
            .is_err());
        assert_eq!(fixture.registry.revision().expect("revision"), 0);
        // A successful enrolment moves it exactly once.
        fixture
            .registry
            .add_peer("jetson", &bundle_json(&peer), &peer.fingerprint(), None)
            .expect("enrol");
        assert_eq!(fixture.registry.revision().expect("revision"), 1);
    }

    #[test]
    fn aliases_and_fingerprints_are_validated() {
        for bad in ["", "with space", "semi;colon", &"a".repeat(65)] {
            assert!(validate_alias(bad).is_err(), "{bad} must be rejected");
        }
        assert!(validate_alias("jetson-01").is_ok());
        let fingerprint = "AB".repeat(32);
        assert_eq!(normalize_fingerprint(&fingerprint).unwrap(), "ab".repeat(32));
        assert!(normalize_fingerprint("abcd").is_err());
        assert!(normalize_fingerprint(&"zz".repeat(32)).is_err());
    }

    #[test]
    fn base64_decoder_matches_a_known_certificate_length() {
        let identity = Identity::generate("peer00000005").expect("identity");
        let der = certificate_der(&identity.certificate_pem).expect("pem");
        assert_eq!(der, identity.certificate_der);
        assert!(certificate_der("-----BEGIN CERTIFICATE-----\n").is_err());
    }
}
