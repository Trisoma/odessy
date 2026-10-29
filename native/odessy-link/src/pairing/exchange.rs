//! Bounded experimental PAKE/Noise enrollment. Local approval remains mandatory.
use super::profile::{
    self, certificate_public_key, hex, random, unhex, unhex_exact, LocalIdentity,
    Transcript,
};
use super::store::{now, Session, Store};
use serde_json::{json, Value};
use std::io::{BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::Arc;
use std::time::Duration;

/// Per-frame ceiling, excluding the delimiter.
pub const MAX_FRAME: usize = 64 * 1024;
/// Aggregate ceiling per direction for one enrollment exchange.
pub const MAX_AGGREGATE: usize = 256 * 1024;
/// No message may take longer than this, and an idle peer is dropped.
pub const INACTIVITY: Duration = Duration::from_secs(5);
/// Largest certificate or CA block accepted from a peer.
const MAX_PEM: usize = 8 * 1024;
/// Largest proof signature accepted (P-256 ASN.1 DER is 70-72 bytes).
const MAX_SIGNATURE: usize = 80;

/// A framed connection with both ceilings enforced as bytes arrive.
pub struct Channel {
    stream: TcpStream,
    reader: BufReader<TcpStream>,
    read_total: usize,
    write_total: usize,
    protected: Option<snow::TransportState>,
}

impl Channel {
    pub fn wrap(stream: TcpStream) -> Result<Self, String> {
        // The listening socket polls with a short non-blocking accept; on Windows the
        // accepted socket inherits that mode, so it must be reset before the first
        // read or every receive would report inactivity immediately.
        stream
            .set_nonblocking(false)
            .map_err(|error| format!("cannot configure the pairing socket: {error}"))?;
        stream
            .set_read_timeout(Some(INACTIVITY))
            .map_err(|error| format!("cannot bound the pairing read: {error}"))?;
        stream
            .set_write_timeout(Some(INACTIVITY))
            .map_err(|error| format!("cannot bound the pairing write: {error}"))?;
        let _ = stream.set_nodelay(true);
        let reader = BufReader::new(stream.try_clone().map_err(|error| error.to_string())?);
        Ok(Self {
            stream,
            reader,
            read_total: 0,
            write_total: 0,
            protected: None,
        })
    }

    pub fn protect(&mut self,state:snow::TransportState){self.protected=Some(state);}
    pub fn send(&mut self,value:&Value)->Result<(),String>{
        let plain=serde_json::to_vec(value).map_err(|_|"Invalid pairing message")?;
        let state=self.protected.as_mut().ok_or("Unauthenticated pairing channel")?;
        if plain.len()>MAX_FRAME/2-64{return Err("Pairing payload too large".into());}
        let mut encrypted=vec![0u8;plain.len()+16];
        let count=state.write_message(&plain,&mut encrypted).map_err(|_|"Pairing encryption failed")?;
        self.send_raw(&json!({"encrypted":hex(&encrypted[..count])}))
    }
    pub fn receive(&mut self)->Result<Value,String>{
        let value=self.receive_raw()?;
        let encoded=value.get("encrypted").and_then(Value::as_str).ok_or("Unprotected pairing message rejected")?;
        if encoded.len()>MAX_FRAME{return Err("Pairing payload too large".into());}
        let encrypted=unhex(encoded).ok_or("Invalid encrypted pairing message")?;
        let state=self.protected.as_mut().ok_or("Unauthenticated pairing channel")?;
        let mut plain=vec![0u8;encrypted.len()];
        let count=state.read_message(&encrypted,&mut plain).map_err(|_|"Pairing authentication failed")?;
        serde_json::from_slice(&plain[..count]).map_err(|_|"Invalid protected pairing message".into())
    }

    pub fn send_raw(&mut self, value: &Value) -> Result<(), String> {
        let mut text =
            serde_json::to_vec(value).map_err(|_| "cannot encode a pairing message".to_string())?;
        if text.len() > MAX_FRAME {
            return Err("pairing message limit exceeded".into());
        }
        if self.write_total + text.len() + 1 > MAX_AGGREGATE {
            return Err("pairing exchange limit exceeded".into());
        }
        self.write_total += text.len() + 1;
        text.push(b'\n');
        self.stream
            .write_all(&text)
            .and_then(|()| self.stream.flush())
            .map_err(|error| format!("cannot write the pairing message: {error}"))
    }

    /// Read one frame. `WouldBlock`/`TimedOut` is reported as inactivity rather than
    /// a silent retry, so a stalled peer cannot hold a session open.
    pub fn receive_raw(&mut self) -> Result<Value, String> {
        let mut frame = Vec::new();
        let deadline=std::time::Instant::now()+INACTIVITY;
        loop {
            let remaining=deadline.saturating_duration_since(std::time::Instant::now());
            if remaining.is_zero(){return Err("Pairing frame deadline exceeded".into());}
            self.reader.get_mut().set_read_timeout(Some(remaining)).map_err(|e|e.to_string())?;
            let mut byte=[0u8;1];
            let count=self.reader.read(&mut byte).map_err(|_|"Pairing frame deadline exceeded")?;
            if count==0{break;}
            frame.push(byte[0]);
            if byte[0]==b'\n' || frame.len()>MAX_FRAME+1{break;}
        }
        let read=frame.len();
        self.read_total += read;
        if self.read_total > MAX_AGGREGATE {
            return Err("pairing exchange limit exceeded".into());
        }
        if read == 0 {
            return Err("the peer closed the pairing exchange".into());
        }
        if frame.last() != Some(&b'\n') {
            return Err(if frame.len() > MAX_FRAME {
                "pairing message limit exceeded".into()
            } else {
                "incomplete pairing message".into()
            });
        }
        frame.pop();
        if frame.len() > MAX_FRAME {
            return Err("pairing message limit exceeded".into());
        }
        serde_json::from_slice(&frame).map_err(|_| "unreadable pairing message".to_string())
    }
}

/// A reserved unauthenticated connection. Dropping it releases the slot, so a
/// finished or failed exchange never holds one of the two places open.
struct ConnectionSlot<'a> {
    store: &'a Store,
}

impl<'a> ConnectionSlot<'a> {
    fn reserve(store: &'a Store, session: &str) -> Result<Self, String> {
        if !store.reserve_connection(session)? {
            return Err("the pairing window already has its unauthenticated connections".into());
        }
        Ok(Self { store })
    }
}

impl Drop for ConnectionSlot<'_> {
    fn drop(&mut self) {
        let _ = self.store.release_connection();
    }
}

/// A peer that authenticated but is not trusted yet.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PendingPeer {
    pub alias: String,
    pub installation_id: String,
    pub fingerprint_sha256: String,
    pub ca_pem: String,
    pub certificate_pem: String,
}

fn text<'a>(value: &'a Value, name: &str) -> Result<&'a str, String> {
    value
        .get(name)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("pairing message has no {name}"))
}

fn alias_of(value: &Value) -> Result<String, String> {
    let alias = text(value, "alias")?;
    if alias.is_empty()
        || alias.len() > 64
        || !alias
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    {
        return Err("the peer name is not usable".into());
    }
    Ok(alias.to_string())
}

fn pem_of(value: &Value, name: &str) -> Result<String, String> {
    let pem = text(value, name)?;
    if pem.len() > MAX_PEM {
        return Err(format!("the peer {name} block is too large"));
    }
    Ok(pem.to_string())
}

/// Host side of one unauthenticated connection. Returns the pending peer after the
/// peer has proved code knowledge, certificate possession and key confirmation.
pub fn accept(
    channel: &mut Channel,
    store: &Store,
    session: &Session,
    local: &LocalIdentity,
) -> Result<PendingPeer, String> {
    let _slot=ConnectionSlot::reserve(store,&session.session_id)?;
    if !store.reserve_attempt()? {return Err("The pairing window has no authentication attempts left".into());}
    if let Err(error)=super::secure_channel::host(channel,&session.session_id,&session.code){
        if store.selection()?.attempts_remaining()==0 {
            let _=store.transition(&session.session_id,&["discoverable","authenticating"],"rejected","attempts exhausted");
        }
        return Err(error);
    }
    let hello = channel.receive()?;
    if hello.get("op").and_then(Value::as_str) != Some("hello") {
        return Err("the peer did not start the exchange".into());
    }
    if hello.get("v").and_then(Value::as_u64) != Some(1) {
        return Err("unsupported pairing message version".into());
    }
    let alias = alias_of(&hello)?;
    let installation_id = text(&hello, "installation_id")?.to_string();
    crate::identity::validate_installation_id(&installation_id)?;
    let certificate_pem = pem_of(&hello, "certificate_pem")?;
    let ca_pem = pem_of(&hello, "ca_pem")?;
    let peer_nonce = unhex_exact(text(&hello, "nonce")?, 32)
        .ok_or_else(|| "the peer nonce is not 32 bytes".to_string())?;

    // One of the window's unauthenticated connections, reserved before any work. The
    // limit is a *concurrency* bound: a released connection frees its slot, so the
    // three-attempt budget — not two sockets — is what ends a hostile window.

    // Only one exchange may hold the session at a time; the state transition is the
    // lock, and it is durable.
    let session = store.transition(
        &session.session_id,
        &["discoverable"],
        "authenticating",
        "a device connected",
    )?;

    let peer_der = crate::trust::certificate_der_for(&certificate_pem)?;
    let peer_fingerprint = crate::identity::fingerprint_sha256(&peer_der);
    let peer_public_key = certificate_public_key(&peer_der)?;

    let transcript = Transcript {
        domain: b"host".to_vec(),
        session: session.session_id.clone().into_bytes(),
        host_fingerprint: local.identity.fingerprint().into_bytes(),
        peer_fingerprint: peer_fingerprint.clone().into_bytes(),
        peer_nonce,
    };
    let binding = transcript.binding();
    channel.send(&json!({
        "v":1,
        "op":"challenge",
        "session_id":session.session_id,
        "binding":hex(&binding),
        "expires_in":(session.expires_at - now()).max(0),
    }))?;

    let authenticate = channel.receive()?;
    if authenticate.get("op").and_then(Value::as_str) != Some("authenticate") {
        return Err("the peer did not authenticate".into());
    }
    let proof = unhex(text(&authenticate, "proof")?)
        .ok_or_else(|| "the peer proof is not hexadecimal".to_string())?;
    if proof.len() > MAX_SIGNATURE {
        return Err("the peer proof is too large".into());
    }
    let signature_ok = profile::verify(
        &peer_public_key,
        &transcript.possession(&binding, "joiner"),
        &proof,
    );
    if !signature_ok {
        store.transition(
            &session.session_id,
            &["authenticating", "discoverable"],
            "discoverable",
            "peer possession rejected",
        )?;
        let left = store.selection()?.attempts_remaining();
        channel.send(&json!({
            "v":1,
            "op":"reject",
            "reason":"possession_rejected",
            "attempts_remaining":left,
        }))?;
        if left == 0 {
            store.transition(&session.session_id, &["discoverable"], "rejected", "attempts exhausted")?;
        }
        return Err("The peer could not prove possession of its certificate".into());
    }

    let host_proof = local
        .signer
        .sign(&transcript.possession(&binding, "host"))?;
    let bundle = local.identity.bundle();
    channel.send(&json!({
        "v":1,
        "op":"accept",
        "binding":hex(&binding),
        "proof":hex(&host_proof),
        "bundle":serde_json::to_value(&bundle).map_err(|_| "cannot encode the identity".to_string())?,
    }))?;

    let complete = channel.receive()?;
    if complete.get("op").and_then(Value::as_str) != Some("complete") {
        return Err("the peer did not confirm the session".into());
    }

    if now()>=session.expires_at{return Err("Pairing window expired".into());}
    store.record_pending_peer(
        &session.session_id,
        &alias,
        &installation_id,
        &peer_fingerprint,
        &ca_pem,
        &certificate_pem,
    )?;
    channel.send(&json!({"v":1,"op":"awaiting_approval","session_id":session.session_id}))?;
    Ok(PendingPeer {
        alias,
        installation_id,
        fingerprint_sha256: peer_fingerprint,
        ca_pem,
        certificate_pem,
    })
}

/// What the joining side learned. The host identity is returned so the caller can
/// enroll it, but nothing here is authority to execute on it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostPeer {
    pub peer: PendingPeer,
    pub session_done: bool,
}

/// Join a host that is waiting with a six-digit code.
///
/// Every failure is recorded on the joiner's own session, so `status` can report what
/// happened after the process is gone.
pub fn join(
    store: &Store,
    session: &Session,
    address: &str,
    code: &str,
    local: &LocalIdentity,
) -> Result<HostPeer, String> {
    use std::net::ToSocketAddrs;
    let endpoint=address.to_socket_addrs().map_err(|_|"Cannot resolve pairing address")?.next().ok_or("Missing pairing address")?;
    let stream=TcpStream::connect_timeout(&endpoint,INACTIVITY).map_err(|_|"Cannot reach pairing device within five seconds")?;
    let mut channel = Channel::wrap(stream)?;
    super::secure_channel::join(&mut channel,code)?;
    let nonce = random(32)?;
    channel.send(&json!({
        "v":1,
        "op":"hello",
        "alias":host_alias(&local.identity.installation_id),
        "installation_id":local.identity.installation_id,
        "certificate_pem":local.identity.certificate_pem,
        "ca_pem":local.identity.ca_pem,
        "nonce":hex(&nonce),
    }))?;
    let challenge = channel.receive()?;
    match challenge.get("op").and_then(Value::as_str) {
        Some("challenge") => {}
        Some("reject") => {
            let reason = challenge
                .get("reason")
                .and_then(Value::as_str)
                .unwrap_or("rejected");
            store.transition(
                &session.session_id,
                &["authenticating"],
                "rejected",
                reason,
            )?;
            return Err(host_reason(reason));
        }
        _ => return Err("the host did not send a challenge".into()),
    }
    // The host's session id differs from this machine's, so it is not compared. The
    // binding the host returned already commits it to that session's transcript.
    let binding = unhex_exact(text(&challenge, "binding")?, 32)
        .ok_or_else(|| "the host binding is not 32 bytes".to_string())?;
    // The host derives the attempt and proof values from the binding it returned, so
    // the joiner uses that value rather than reconstructing a transcript it cannot see.
    let proof = local
        .signer
        .sign(&profile::sha256(&[profile::POSSESSION_LABEL, &binding, b"joiner"]))?;
    channel.send(&json!({
        "v":1,
        "op":"authenticate",
        "proof":hex(&proof),
    }))?;
    let accept = channel.receive()?;
    match accept.get("op").and_then(Value::as_str) {
        Some("accept") => {}
        Some("reject") => {
            let reason = accept
                .get("reason")
                .and_then(Value::as_str)
                .unwrap_or("rejected");
            store.transition(&session.session_id, &["authenticating"], "rejected", reason)?;
            return Err(host_reason(reason));
        }
        _ => return Err("the host did not accept the pairing".into()),
    }
    let host_proof = unhex(text(&accept, "proof")?)
        .ok_or_else(|| "the host proof is not hexadecimal".to_string())?;
    let bundle = accept
        .get("bundle")
        .cloned()
        .ok_or_else(|| "the host sent no identity".to_string())?;
    let bundle: crate::identity::EnrollmentBundle = serde_json::from_value(bundle)
        .map_err(|_| "the host identity is unreadable".to_string())?;
    crate::identity::validate_installation_id(&bundle.installation_id)?;
    let host_der = crate::trust::certificate_der_for(&bundle.certificate_pem)?;
    if crate::identity::fingerprint_sha256(&host_der) != bundle.fingerprint_sha256 {
        return Err("the host identity does not match its certificate".into());
    }
    let host_public_key = certificate_public_key(&host_der)?;
    if !profile::verify(
        &host_public_key,
        &profile::sha256(&[profile::POSSESSION_LABEL, &binding, b"host"]),
        &host_proof,
    ) {
        return Err("the host could not prove possession of its certificate".into());
    }
    channel.send(&json!({"v":1,"op":"complete"}))?;
    let final_message = channel.receive()?;
    if final_message.get("op").and_then(Value::as_str) != Some("awaiting_approval") {
        return Err("the host did not confirm the enrollment".into());
    }
    // The host's challenge proves the host holds its key; the final confirmation
    // proves both ends saw the same transcript. Record the host as *pending*: it is
    // an address book entry, not authority to run anything.
    store.record_pending_peer(
        &session.session_id,
        &host_alias(&bundle.installation_id),
        &bundle.installation_id,
        &bundle.fingerprint_sha256,
        &bundle.ca_pem,
        &bundle.certificate_pem,
    )?;
    Ok(HostPeer {
        peer: PendingPeer {
            alias: host_alias(&bundle.installation_id),
            installation_id: bundle.installation_id,
            fingerprint_sha256: bundle.fingerprint_sha256,
            ca_pem: bundle.ca_pem,
            certificate_pem: bundle.certificate_pem,
        },
        session_done: true,
    })
}

/// A stable, readable peer name derived from an installation id. The local operator
/// can rename it later; it carries no authority.
pub fn host_alias(installation_id: &str) -> String {
    let take = installation_id.len().min(8);
    format!("peer-{}", &installation_id[..take])
}

fn host_reason(reason: &str) -> String {
    match reason {
        "code_rejected" => "the pairing code did not match".into(),
        "possession_rejected" => "the host rejected the certificate proof".into(),
        "attempts_exhausted" => "the pairing window has no attempts left; start a new one".into(),
        "connections_exhausted" => "the pairing window has already been used".into(),
        other => format!("the host refused the pairing: {other}"),
    }
}

/// Accept enrollment connections until the session stops being open or the window
/// closes. Each connection is handled on its own thread with its own deadline.
pub struct Listener {
    listener: TcpListener,
    local: LocalIdentity,
    store: Arc<Store>,
    session_id: String,
    /// Number of connections accepted so far, reported so a caller can distinguish
    /// "nobody tried" from "the limit was reached".
    pub accepted: usize,
}

impl Listener {
    pub fn bind(address: &str, store: Arc<Store>, session_id: &str, local: LocalIdentity) -> Result<Self, String> {
        let listener =
            TcpListener::bind(address).map_err(|error| format!("cannot listen on {address}: {error}"))?;
        listener
            .set_nonblocking(true)
            .map_err(|error| format!("cannot configure the pairing listener: {error}"))?;
        Ok(Self {
            listener,
            local,
            store,
            session_id: session_id.to_string(),
            accepted: 0,
        })
    }

    pub fn port(&self) -> Result<u16, String> {
        self.listener
            .local_addr()
            .map(|address| address.port())
            .map_err(|error| format!("cannot read the pairing address: {error}"))
    }

    /// Serve until `should_stop` returns true. Blocking accept with a short poll so a
    /// cancelled session stops within one poll interval.
    pub fn run(&mut self, mut should_stop: impl FnMut() -> bool) -> Result<(), String> {
        while !should_stop() {
            match self.listener.accept() {
                Ok((stream, _)) => {
                    self.accepted += 1;
                    let store = self.store.clone();
                    let local = LocalIdentity {
                        identity: self.local.identity.clone(),
                        signer: profile::Signer::from_pem(&self.local.identity.key_pem)?,
                        public_key: self.local.public_key.clone(),
                    };
                    let session_id = self.session_id.clone();
                    std::thread::spawn(move || {
                        let session = match store.refresh(&session_id) {
                            Ok(session) => session,
                            Err(_) => return,
                        };
                        let mut channel = match Channel::wrap(stream) {
                            Ok(channel) => channel,
                            Err(_) => return,
                        };
                        if let Err(error) = accept(&mut channel, &store, &session, &local) {
                            eprintln!("pairing exchange refused: {error}");
                        }
                    });
                }
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(100));
                }
                Err(error) => return Err(format!("pairing listener failed: {error}")),
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::identity::Identity;
    use crate::pairing::store::Store;

    struct Fixture {
        directory: std::path::PathBuf,
    }

    impl Fixture {
        fn new(name: &str) -> Self {
            let directory = std::env::temp_dir().join(format!(
                "odessy-link-exchange-{name}-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            Self { directory }
        }
        fn store(&self, name: &str) -> Store {
            Store::open(&self.directory.join(name)).expect("store")
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.directory);
        }
    }

    fn local(installation_id: &str) -> LocalIdentity {
        let identity = Identity::generate(installation_id).expect("identity");
        LocalIdentity {
            signer: profile::Signer::from_pem(&identity.key_pem).expect("signer"),
            public_key: certificate_public_key(&identity.certificate_der).expect("spki"),
            identity,
        }
    }

    #[test]
    fn a_wrong_code_is_refused_before_enrollment() {
        let fixture = Fixture::new("signature");
        let host_store = fixture.store("host.sqlite3");
        let joiner_store = fixture.store("joiner.sqlite3");
        let host = local("exchangefixture01");
        let joiner = local("exchangefixture02");
        let session = host_store
            .begin_host(None, "042137", "token", "op", "00112233445566778899aabbccddeeff")
            .expect("host session");
        let mut listener = Listener::bind(
            "127.0.0.1:0",
            Arc::new(fixture.store("host.sqlite3")),
            &session.session_id,
            host,
        )
        .expect("listener");
        let port = listener.port().expect("port");
        let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let flag = stop.clone();
        let thread = std::thread::spawn(move || {
            listener
                .run(|| flag.load(std::sync::atomic::Ordering::Relaxed))
                .expect("serve");
        });
        let joiner_session = joiner_store
            .begin_joiner("127.0.0.1", "jop", "joiner-session")
            .expect("joiner session");
        let error = join(
            &joiner_store,
            &joiner_session,
            &format!("127.0.0.1:{port}"),
            "999999",
            &joiner,
        )
        .expect_err("a wrong code must be refused");
        assert!(!error.is_empty());
        assert!(host_store.current().unwrap().unwrap().peer_alias.is_none());
        let left = host_store.selection().expect("budget");
        assert_eq!(left.attempts_used, 1, "the attempt is consumed");
        stop.store(true, std::sync::atomic::Ordering::Relaxed);
        let _ = thread.join();
    }

    #[test]
    fn a_proof_bound_to_one_session_does_not_verify_in_another() {
        let identity = Identity::generate("exchangereplay1").expect("identity");
        let signer = profile::Signer::from_pem(&identity.key_pem).expect("signer");
        let public_key = certificate_public_key(&identity.certificate_der).expect("spki");
        let transcript = |session: &str| Transcript {
            domain: b"host".to_vec(),
            session: session.as_bytes().to_vec(),
            host_fingerprint: b"host".to_vec(),
            peer_fingerprint: b"peer".to_vec(),
            peer_nonce: vec![7; 32],
        };
        let first = transcript("session-one");
        let second = transcript("session-two");
        let message = first.possession(&first.binding(), "joiner");
        let signature = signer.sign(&message).expect("sign");
        assert!(profile::verify(&public_key, &message, &signature));
        // The same signature replayed into a different session does not verify.
        assert!(!profile::verify(
            &public_key,
            &second.possession(&second.binding(), "joiner"),
            &signature
        ));
        // Nor does a host-role proof verify as a joiner-role proof.
        assert!(!profile::verify(
            &public_key,
            &first.possession(&first.binding(), "host"),
            &signature
        ));
    }

    #[test]
    fn a_proof_made_with_another_identity_than_the_certificate_is_refused() {
        // The host verifies against the key extracted from the certificate it will
        // enroll, so presenting a certificate whose private key is not held fails.
        let presented = Identity::generate("exchangecert01").expect("certificate");
        let attacker = Identity::generate("exchangecert02").expect("attacker");
        let presented_key = certificate_public_key(&presented.certificate_der).expect("spki");
        let attacker_signer = profile::Signer::from_pem(&attacker.key_pem).expect("signer");
        let transcript = Transcript {
            domain: b"host".to_vec(),
            session: b"session".to_vec(),
            host_fingerprint: b"host".to_vec(),
            peer_fingerprint: presented.fingerprint().into_bytes(),
            peer_nonce: vec![9; 32],
        };
        let message = transcript.possession(&transcript.binding(), "joiner");
        let signature = attacker_signer.sign(&message).expect("sign");
        assert!(
            !profile::verify(&presented_key, &message, &signature),
            "a substituted certificate must not pass possession"
        );
        assert!(profile::verify(&attacker_signer.public_key(), &message, &signature));
    }
}
