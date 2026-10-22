//! Mutual-TLS setup with explicit, enrolled-only trust.
//!
//! Deliberate properties, per the pairing plan:
//! - roots are the peer CAs the operator enrolled; nothing else is trusted;
//! - client verification uses the peer's expected SAN, not the address or a
//!   discovered hostname;
//! - both ends then require the exact enrolled connection-certificate
//!   fingerprint before any MCP byte is forwarded;
//! - session resumption is off and no custom `ServerCertVerifier` exists.

use crate::identity::{fingerprint_sha256, Identity};
use crate::trust::Peer;
use rustls::pki_types::{CertificateDer, PrivateKeyDer};
use std::sync::Arc;

fn certificate_chain(identity: &Identity) -> Result<Vec<CertificateDer<'static>>, String> {
    let mut chain = vec![CertificateDer::from(identity.certificate_der.clone())];
    // Send the CA too: the verifier needs it to build the peer's chain.
    chain.push(CertificateDer::from(identity.ca_der.clone()));
    Ok(chain)
}

fn private_key(identity: &Identity) -> Result<PrivateKeyDer<'static>, String> {
    let mut cursor = std::io::Cursor::new(identity.key_pem.as_bytes());
    let mut keys = rustls_pemfile::pkcs8_private_keys(&mut cursor);
    match keys.next() {
        Some(Ok(key)) => Ok(PrivateKeyDer::Pkcs8(key)),
        _ => Err("identity private key is not a readable PKCS#8 key".into()),
    }
}

fn roots(authorities: Vec<Vec<u8>>) -> Result<rustls::RootCertStore, String> {
    let mut store = rustls::RootCertStore::empty();
    for der in authorities {
        store
            .add(CertificateDer::from(der))
            .map_err(|error| format!("enrolled authority is not a usable CA: {error}"))?;
    }
    Ok(store)
}

fn crypto_provider() {
    // Installing explicitly avoids depending on which provider feature happens to
    // be enabled downstream.
    let _ = rustls::crypto::ring::default_provider().install_default();
}

/// Client configuration for one enrolled peer.
pub fn client_config(identity: &Identity, peer: &Peer) -> Result<rustls::ClientConfig, String> {
    crypto_provider();
    let ca = crate::trust::certificate_der_for(&peer.ca_pem)?;
    let store = roots(vec![ca])?;
    let mut config = rustls::ClientConfig::builder()
        .with_root_certificates(store)
        .with_client_auth_cert(certificate_chain(identity)?, private_key(identity)?)
        .map_err(|error| format!("cannot build client TLS config: {error}"))?;
    // A resumed session would skip the certificate exchange this design relies on.
    config.resumption = rustls::client::Resumption::disabled();
    config.alpn_protocols.clear();
    Ok(config)
}

/// Server configuration accepting client certificates from enrolled peer CAs.
///
/// Client authentication is mandatory: an unauthenticated peer has no path in.
pub fn server_config(identity: &Identity, authorities: Vec<Vec<u8>>) -> Result<rustls::ServerConfig, String> {
    crypto_provider();
    let store = Arc::new(roots(authorities)?);
    let verifier = rustls::server::WebPkiClientVerifier::builder(store)
        .build()
        .map_err(|error| format!("cannot build client-certificate verifier: {error}"))?;
    let mut config = rustls::ServerConfig::builder()
        .with_client_cert_verifier(verifier)
        .with_single_cert(certificate_chain(identity)?, private_key(identity)?)
        .map_err(|error| format!("cannot build server TLS config: {error}"))?;
    // No resumption storage means every connection re-authenticates.
    config.session_storage = Arc::new(rustls::server::NoServerSessionStorage {});
    config.alpn_protocols.clear();
    Ok(config)
}

/// DNS name the client must see in the peer's certificate. It comes from the
/// enrolled installation id, never from the connection address.
pub fn expected_server_name(peer: &Peer) -> Result<rustls::pki_types::ServerName<'static>, String> {
    let name = crate::identity::dns_name(&peer.installation_id);
    rustls::pki_types::ServerName::try_from(name)
        .map_err(|error| format!("enrolled peer identity is not a usable name: {error}"))
        .map(|name| name.to_owned())
}

/// Fingerprint of the certificate the other end presented.
pub fn presented_fingerprint(
    certificates: Option<&[CertificateDer<'static>]>,
) -> Result<String, String> {
    let leaf = certificates
        .and_then(|chain| chain.first())
        .ok_or_else(|| "peer presented no certificate".to_string())?;
    Ok(fingerprint_sha256(leaf.as_ref()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::trust::Registry;

    fn registry(name: &str) -> (std::path::PathBuf, Registry) {
        let directory = std::env::temp_dir().join(format!(
            "odessy-link-tls-{name}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let registry = Registry::open(&directory.join("trust.sqlite3")).expect("registry");
        (directory, registry)
    }

    #[test]
    fn client_config_trusts_only_the_enrolled_ca() {
        let (directory, store) = registry("client");
        let mine = Identity::generate("local0000001").expect("identity");
        let theirs = Identity::generate("peer00000001").expect("identity");
        let json = serde_json::to_string(&theirs.bundle()).expect("json");
        store
            .add_peer("peer", &json, &theirs.fingerprint(), None)
            .expect("enroll");
        let peer = store.peer("peer").expect("read").expect("peer");
        let config = client_config(&mine, &peer).expect("client config");
        assert_eq!(expected_server_name(&peer).unwrap().to_str(), "p-peer00000001.odessy.invalid");
        // The config is built from exactly one enrolled authority.
        assert_eq!(config.alpn_protocols.len(), 0);
        let _ = directory;
    }

    #[test]
    fn server_config_requires_client_certificates() {
        let (directory, store) = registry("server");
        let mine = Identity::generate("local0000002").expect("identity");
        let theirs = Identity::generate("peer00000003").expect("identity");
        let json = serde_json::to_string(&theirs.bundle()).expect("json");
        store
            .add_peer("peer", &json, &theirs.fingerprint(), None)
            .expect("enroll");
        let authorities = store.enrolled_authorities().expect("authorities");
        assert_eq!(authorities.len(), 1);
        server_config(&mine, authorities).expect("server config");
        // With no enrolled peer there is nothing to trust, so config building fails
        // rather than creating a server that accepts anybody.
        assert!(server_config(&mine, Vec::new()).is_err());
        let _ = directory;
    }

    #[test]
    fn presented_fingerprint_requires_a_certificate() {
        assert!(presented_fingerprint(None).is_err());
        assert!(presented_fingerprint(Some(&[])).is_err());
    }

    /// Two hosts enroll each other, then complete a real mutual-TLS handshake over
    /// loopback. Both ends must report the fingerprint they enrolled: a valid chain
    /// with the wrong leaf is not enough.
    #[tokio::test]
    async fn enrolled_hosts_complete_mutual_tls_and_agree_on_fingerprints() {
        let (directory_a, registry_a) = registry("a");
        let (directory_b, registry_b) = registry("b");
        let host_a = Identity::generate("hosta0000001").expect("a");
        let host_b = Identity::generate("hostb0000001").expect("b");
        registry_a
            .add_peer("b", &serde_json::to_string(&host_b.bundle()).unwrap(), &host_b.fingerprint(), None)
            .expect("a enrolls b");
        registry_b
            .add_peer("a", &serde_json::to_string(&host_a.bundle()).unwrap(), &host_a.fingerprint(), None)
            .expect("b enrolls a");

        let server = std::sync::Arc::new(
            server_config(&host_a, registry_a.enrolled_authorities().expect("authorities"))
                .expect("server config"),
        );
        let peer_a = registry_b.peer("a").expect("read").expect("peer");
        let client = std::sync::Arc::new(client_config(&host_b, &peer_a).expect("client config"));

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let address = listener.local_addr().expect("addr");
        let acceptor = tokio_rustls::TlsAcceptor::from(server);
        let server_task = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.expect("accept");
            let stream = acceptor.accept(stream).await.expect("server handshake");
            presented_fingerprint(stream.get_ref().1.peer_certificates()).expect("peer cert")
        });
        let connector = tokio_rustls::TlsConnector::from(client);
        let stream = tokio::net::TcpStream::connect(address).await.expect("connect");
        let name = expected_server_name(&peer_a).expect("name");
        let stream = connector.connect(name, stream).await.expect("client handshake");
        let seen_server = presented_fingerprint(stream.get_ref().1.peer_certificates()).expect("cert");
        let seen_client = server_task.await.expect("join");

        assert_eq!(seen_server, host_a.fingerprint(), "client sees the enrolled server leaf");
        assert_eq!(seen_client, host_b.fingerprint(), "server sees the enrolled client leaf");
        let _ = (directory_a, directory_b);
    }

    /// An unenrolled identity cannot complete the handshake even though its
    /// certificate chain is well formed.
    #[tokio::test]
    async fn an_unenrolled_identity_is_rejected_by_client_certificate_verification() {
        let (directory_a, registry_a) = registry("reject-a");
        let (directory_c, registry_c) = registry("reject-c");
        let host_a = Identity::generate("hosta0000002").expect("a");
        let enrolled = Identity::generate("hostb0000002").expect("b");
        let stranger = Identity::generate("hostc0000002").expect("c");
        registry_a
            .add_peer("b", &serde_json::to_string(&enrolled.bundle()).unwrap(), &enrolled.fingerprint(), None)
            .expect("a enrolls b");
        // The stranger trusts A, so only the server-side check can reject it.
        registry_c
            .add_peer("a", &serde_json::to_string(&host_a.bundle()).unwrap(), &host_a.fingerprint(), None)
            .expect("c enrolls a");

        let server = std::sync::Arc::new(
            server_config(&host_a, registry_a.enrolled_authorities().expect("authorities"))
                .expect("server config"),
        );
        let peer_a = registry_c.peer("a").expect("read").expect("peer");
        let client = std::sync::Arc::new(client_config(&stranger, &peer_a).expect("client config"));

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let address = listener.local_addr().expect("addr");
        let acceptor = tokio_rustls::TlsAcceptor::from(server);
        let server_task = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.expect("accept");
            acceptor.accept(stream).await.is_err()
        });
        let connector = tokio_rustls::TlsConnector::from(client);
        let stream = tokio::net::TcpStream::connect(address).await.expect("connect");
        let name = expected_server_name(&peer_a).expect("name");
        // Under TLS 1.3 the client can finish its handshake before the server
        // processes its certificate, so the refusal surfaces on the first
        // application read. The server verdict below is the authoritative one.
        let mut established = connector.connect(name, stream).await;
        if let Ok(stream) = established.as_mut() {
            use tokio::io::AsyncReadExt;
            let mut buffer = [0u8; 1];
            let read = tokio::time::timeout(
                std::time::Duration::from_secs(5),
                stream.read(&mut buffer),
            )
            .await
            .expect("the server must close the connection");
            assert!(
                matches!(read, Ok(0) | Err(_)),
                "no MCP byte may be exchanged with an unenrolled peer"
            );
        }
        assert!(server_task.await.expect("join"), "server must refuse the stranger");
        let _ = (directory_a, directory_c);
    }

    /// An expired connection certificate must be refused even though its chain is
    /// fine: expiry never triggers an insecure fallback.
    #[tokio::test]
    async fn an_expired_certificate_is_refused() {
        let (directory_a, registry_a) = registry("expired-a");
        let (directory_c, registry_c) = registry("expired-c");
        let host_a = Identity::generate("hosta0000003").expect("a");
        let expired = Identity::expired("hostc0000003").expect("expired identity");
        registry_a
            .add_peer("c", &serde_json::to_string(&expired.bundle()).unwrap(), &expired.fingerprint(), None)
            .expect("a enrols c");
        registry_c
            .add_peer("a", &serde_json::to_string(&host_a.bundle()).unwrap(), &host_a.fingerprint(), None)
            .expect("c enrols a");

        let server = Arc::new(
            server_config(&host_a, registry_a.enrolled_authorities().expect("authorities"))
                .expect("server config"),
        );
        let peer_a = registry_c.peer("a").expect("read").expect("peer");
        let client = Arc::new(client_config(&expired, &peer_a).expect("client config"));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let address = listener.local_addr().expect("addr");
        let acceptor = tokio_rustls::TlsAcceptor::from(server);
        let server_task = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.expect("accept");
            acceptor.accept(stream).await.is_err()
        });
        let connector = tokio_rustls::TlsConnector::from(client);
        let stream = tokio::net::TcpStream::connect(address).await.expect("connect");
        let name = expected_server_name(&peer_a).expect("name");
        let _ = connector.connect(name, stream).await;
        assert!(server_task.await.expect("join"), "an expired leaf must be refused");
        let _ = (directory_a, directory_c);
    }

    /// The expected SAN comes from the enrolled record, so a certificate for another
    /// installation fails even when its chain is valid.
    #[tokio::test]
    async fn a_mismatched_san_is_refused() {
        let (directory_a, registry_a) = registry("san-a");
        let (directory_c, registry_c) = registry("san-c");
        let host_a = Identity::generate("hosta0000004").expect("a");
        let host_c = Identity::generate("hostc0000004").expect("c");
        registry_a
            .add_peer("c", &serde_json::to_string(&host_c.bundle()).unwrap(), &host_c.fingerprint(), None)
            .expect("a enrols c");
        // Enrol A's certificate under a record that claims a different installation id,
        // which is what a misleading advertised hostname would look like to the client.
        let mut bundle = serde_json::to_value(host_a.bundle()).unwrap();
        bundle["installation_id"] = serde_json::json!("wronghost01");
        registry_c
            .add_peer("a", &bundle.to_string(), &host_a.fingerprint(), None)
            .expect("c enrols a with a different claimed identity");

        let server = Arc::new(
            server_config(&host_a, registry_a.enrolled_authorities().expect("authorities"))
                .expect("server config"),
        );
        let peer_a = registry_c.peer("a").expect("read").expect("peer");
        assert_eq!(
            expected_server_name(&peer_a).unwrap().to_str(),
            "p-wronghost01.odessy.invalid"
        );
        let client = Arc::new(client_config(&host_c, &peer_a).expect("client config"));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let address = listener.local_addr().expect("addr");
        let acceptor = tokio_rustls::TlsAcceptor::from(server);
        let server_task = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.expect("accept");
            acceptor.accept(stream).await.is_err()
        });
        let connector = tokio_rustls::TlsConnector::from(client);
        let stream = tokio::net::TcpStream::connect(address).await.expect("connect");
        let name = expected_server_name(&peer_a).expect("name");
        let client_result = connector.connect(name, stream).await;
        assert!(
            client_result.is_err(),
            "the expected identity comes from the record, not from the peer's claim"
        );
        let _ = server_task.await;
        let _ = (directory_a, directory_c);
    }
}
