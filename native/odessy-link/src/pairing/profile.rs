//! Protocol profile selection and the primitives the enrollment exchange needs.
//!
//! **M0 is not approved.** No complete, reviewed pairing construction exists in this
//! repository, so the production profile is *unavailable*: it opens no listener and
//! dials nothing. The only executable exchange is an explicitly labelled test
//! adapter, present in this binary only when it is built with the `test-profile`
//! feature *and* the invoking process sets `ODESSY_LINK_TEST_PAIRING_ADAPTER=1`.
//! A release build cannot enable it by configuration alone, and every response it
//! produces says `profile: "test-adapter-unapproved"`.
//!
//! What the test adapter does prove is real: code comparison against a per-session
//! transcript, ECDSA P-256 possession of the exact certificate being enrolled, mutual
//! proof verification and mutual key confirmation. What it does **not** prove is the
//! M0 profile: there is no PAKE, so the code-derived value is a plain hash of a
//! 20-bit secret and offers no offline-guess resistance, and the confirmation value
//! is a transcript hash rather than a MAC under a negotiated key. Do not describe it
//! as approved enrollment.

use crate::identity::Identity;
use ring::rand::SystemRandom;
use ring::signature::{EcdsaKeyPair, KeyPair, UnparsedPublicKey, ECDSA_P256_SHA256_ASN1, ECDSA_P256_SHA256_ASN1_SIGNING};
use sha2::{Digest, Sha256};

/// Appears in every transcript so a message from another purpose cannot be replayed
/// into this exchange.
pub const PROFILE_DOMAIN: &[u8] = b"odessy-link/pairing/test-adapter-v1";
/// Label for the code-derived attempt value.
pub const CODE_LABEL: &[u8] = b"odessy-link/pairing/code";
/// Label for a possession proof.
pub const POSSESSION_LABEL: &[u8] = b"odessy-link/pairing/possession";
/// Label for the mutual key confirmation.
pub const CONFIRM_LABEL: &[u8] = b"odessy-link/pairing/confirm";
/// Environment variable that a test run must also set. It cannot enable the adapter
/// in a build without the feature, and the feature is off by default.
pub const TEST_ADAPTER_ENV: &str = "ODESSY_LINK_TEST_PAIRING_ADAPTER";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Profile {
    /// No reviewed construction: enrollment is closed.
    Production,
    /// Explicit, unapproved, test-only construction.
    TestOnly,
}

impl Profile {
    pub fn name(self) -> &'static str {
        match self {
            Profile::Production => "unavailable",
            Profile::TestOnly => "test-adapter-unapproved",
        }
    }
}

/// Choose a profile. `explicit` is `--profile test-adapter` on the command line.
///
/// Both gates are required: a build with the feature and a caller that asks for it.
/// Nothing promotes a profile implicitly, so a normal invocation is always closed.
pub fn resolve(explicit: bool) -> Result<Profile, String> {
    if !explicit {
        return Ok(Profile::Production);
    }
    if !cfg!(feature = "test-profile") {
        return Err(
            "this build contains no unapproved pairing profile; rebuild with --features test-profile for tests"
                .into(),
        );
    }
    if std::env::var(TEST_ADAPTER_ENV).as_deref() != Ok("1") {
        return Err(format!(
            "the test adapter requires {TEST_ADAPTER_ENV}=1 in the invoking process"
        ));
    }
    Ok(Profile::TestOnly)
}

pub fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

pub fn unhex(text: &str) -> Option<Vec<u8>> {
    if text.len() % 2 != 0 || text.len() > 4096 {
        return None;
    }
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(text.len() / 2);
    let value = |byte: u8| -> Option<u8> {
        match byte {
            b'0'..=b'9' => Some(byte - b'0'),
            b'a'..=b'f' => Some(byte - b'a' + 10),
            b'A'..=b'F' => Some(byte - b'A' + 10),
            _ => None,
        }
    };
    for pair in bytes.chunks(2) {
        out.push((value(pair[0])? << 4) | value(pair[1])?);
    }
    Some(out)
}

/// Fixed-length hex field: exactly `bytes` worth of hex characters.
pub fn unhex_exact(text: &str, bytes: usize) -> Option<Vec<u8>> {
    if text.len() != bytes * 2 {
        return None;
    }
    unhex(text)
}

pub fn sha256(parts: &[&[u8]]) -> [u8; 32] {
    let mut digest = Sha256::new();
    for part in parts {
        // Length prefixes keep `("ab","c")` distinct from `("a","bc")`.
        digest.update((part.len() as u64).to_be_bytes());
        digest.update(part);
    }
    let out = digest.finalize();
    let mut value = [0u8; 32];
    value.copy_from_slice(&out);
    value
}

/// Octal comparison of two byte strings without an early exit.
pub fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    let mut difference = 0u8;
    for (a, b) in left.iter().zip(right.iter()) {
        difference |= a ^ b;
    }
    difference == 0
}

/// A private key for the installation certificate, used only to prove possession
/// during enrollment. It is never sent.
pub struct Signer {
    pair: EcdsaKeyPair,
}

impl Signer {
    pub fn from_pem(pem: &str) -> Result<Self, String> {
        let der = pem_der(pem, "PRIVATE KEY")?;
        let pair = EcdsaKeyPair::from_pkcs8(&ECDSA_P256_SHA256_ASN1_SIGNING, &der, &SystemRandom::new())
            .map_err(|_| "the identity private key is not a usable P-256 key".to_string())?;
        Ok(Self { pair })
    }

    pub fn public_key(&self) -> Vec<u8> {
        self.pair.public_key().as_ref().to_vec()
    }

    pub fn sign(&self, message: &[u8]) -> Result<Vec<u8>, String> {
        self.pair
            .sign(&SystemRandom::new(), message)
            .map(|signature| signature.as_ref().to_vec())
            .map_err(|_| "cannot produce the possession proof".to_string())
    }
}

/// Verify a possession proof with the key inside a certificate.
pub fn verify(public_key: &[u8], message: &[u8], signature: &[u8]) -> bool {
    if public_key.len() != 65 || public_key[0] != 0x04 || signature.is_empty() || signature.len() > 80 {
        return false;
    }
    UnparsedPublicKey::new(&ECDSA_P256_SHA256_ASN1, public_key)
        .verify(message, signature)
        .is_ok()
}

/// The uncompressed P-256 point from a certificate's SubjectPublicKeyInfo.
///
/// The scan is narrow on purpose: it accepts only the exact
/// `id-ecPublicKey`/`prime256v1` AlgorithmIdentifier followed by a 65-byte BIT
/// STRING point, and requires that the key verify under ring, which rejects a point
/// that is not on the curve. A certificate is not otherwise parsed here, and the
/// exact leaf fingerprint remains the value that is enrolled.
pub fn certificate_public_key(der: &[u8]) -> Result<Vec<u8>, String> {
    const PREFIX: &[u8] = &[
        0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02, 0x01, // id-ecPublicKey
        0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07, // prime256v1
        0x03, 0x42, 0x00, 0x04, // BIT STRING, 66 bytes, no unused bits, uncompressed
    ];
    let position = der
        .windows(PREFIX.len())
        .position(|window| window == PREFIX)
        .ok_or_else(|| "the certificate has no P-256 public key".to_string())?;
    let start = position + PREFIX.len();
    let end = start + 64;
    if end > der.len() {
        return Err("the certificate public key is truncated".into());
    }
    let mut key = Vec::with_capacity(65);
    key.push(0x04);
    key.extend_from_slice(&der[start..end]);
    Ok(key)
}

/// Base64-decode a single PEM block body. Reuses the registry decoder so there is
/// one implementation of that transform.
fn pem_der(pem: &str, label: &str) -> Result<Vec<u8>, String> {
    let begin = format!("-----BEGIN {label}-----");
    let end = format!("-----END {label}-----");
    let mut inside = false;
    let mut body = String::new();
    for line in pem.lines() {
        let line = line.trim();
        if line == begin {
            inside = true;
            continue;
        }
        if line == end {
            return crate::trust::decode_base64_for_pairing(&body);
        }
        if inside {
            body.push_str(line);
        }
    }
    Err(format!("no complete {label} block"))
}

/// Transcript helpers shared by both ends, so the two sides cannot drift.
pub struct Transcript {
    pub domain: Vec<u8>,
    pub session: Vec<u8>,
    pub host_fingerprint: Vec<u8>,
    pub peer_fingerprint: Vec<u8>,
    pub peer_nonce: Vec<u8>,
}

impl Transcript {
    pub fn binding(&self) -> Vec<u8> {
        sha256(&[
            PROFILE_DOMAIN,
            &self.domain,
            &self.session,
            &self.host_fingerprint,
            &self.peer_fingerprint,
            &self.peer_nonce,
        ])
        .to_vec()
    }

    pub fn code_attempt(&self, binding: &[u8], code: &str) -> Vec<u8> {
        sha256(&[CODE_LABEL, binding, code.as_bytes()]).to_vec()
    }

    pub fn possession(&self, binding: &[u8], role: &str) -> Vec<u8> {
        sha256(&[POSSESSION_LABEL, binding, role.as_bytes()]).to_vec()
    }

    pub fn confirmation(&self, binding: &[u8], peer_proof: &[u8], host_proof: &[u8]) -> Vec<u8> {
        sha256(&[CONFIRM_LABEL, binding, peer_proof, host_proof]).to_vec()
    }
}

/// Random bytes from the OS source.
pub fn random(bytes: usize) -> Result<Vec<u8>, String> {
    let mut buffer = vec![0u8; bytes];
    crate::identity::fill_random(&mut buffer)?;
    Ok(buffer)
}

/// A fresh six-digit code, including leading zeros.
///
/// Rejection sampling, not a modulo: 10^6 does not divide 2^32, so `value % 1_000_000`
/// would make some codes slightly more likely. Every code is equally probable here.
pub fn random_code() -> Result<String, String> {
    const CODES: u32 = 1_000_000;
    // Largest value that is a whole number of code ranges; anything above is redrawn.
    let limit = u32::MAX - (u32::MAX % CODES);
    for _ in 0..16 {
        let bytes = random(4)?;
        let value = u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        if value < limit {
            return Ok(super::store::format_code(value % CODES));
        }
    }
    Err("cannot draw a pairing code; try again".into())
}

/// Identity material plus the signer, for the exchange.
pub struct LocalIdentity {
    pub identity: Identity,
    pub signer: Signer,
    pub public_key: Vec<u8>,
}

impl LocalIdentity {
    pub fn load() -> Result<Self, String> {
        let identity = crate::load_identity()?;
        let signer = Signer::from_pem(&identity.key_pem)?;
        let public_key = signer.public_key();
        // The proof and the certificate must describe the same key, or the
        // possession proof would say nothing about the enrolled identity.
        let expected = certificate_public_key(&identity.certificate_der)?;
        if expected != public_key {
            return Err("the identity private key does not match its certificate".into());
        }
        Ok(Self {
            identity,
            signer,
            public_key,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codes_keep_leading_zeros_and_reject_hostile_text() {
        assert_eq!(super::super::store::format_code(0), "000000");
        assert_eq!(super::super::store::format_code(42), "000042");
        assert_eq!(super::super::store::format_code(1_000_000), "000000");
        assert!(super::super::store::valid_code("000042"));
        assert!(!super::super::store::valid_code("42"));
        assert!(!super::super::store::valid_code("00042a"));
        assert!(!super::super::store::valid_code("００００４２"));
        let code = random_code().expect("code");
        assert!(super::super::store::valid_code(&code), "{code}");
    }

    #[test]
    fn hex_round_trips_and_bounds_length() {
        assert_eq!(hex(&[0x00, 0x7f, 0xff]), "007fff");
        assert_eq!(unhex("007fff"), Some(vec![0x00, 0x7f, 0xff]));
        assert_eq!(unhex("0"), None);
        assert_eq!(unhex("zz"), None);
        assert_eq!(unhex_exact("0000", 2), Some(vec![0, 0]));
        assert_eq!(unhex_exact("0000", 1), None);
        assert_eq!(unhex(&"0".repeat(4098)), None);
    }

    #[test]
    fn the_transcript_binds_every_component() {
        let base = Transcript {
            domain: b"d".to_vec(),
            session: b"s".to_vec(),
            host_fingerprint: vec![1; 32],
            peer_fingerprint: vec![2; 32],
            peer_nonce: vec![3; 32],
        };
        let binding = base.binding();
        let changed = Transcript {
            peer_nonce: vec![4; 32],
            ..Transcript {
                domain: b"d".to_vec(),
                session: b"s".to_vec(),
                host_fingerprint: vec![1; 32],
                peer_fingerprint: vec![2; 32],
                peer_nonce: vec![3; 32],
            }
        };
        assert_ne!(binding, changed.binding());
        // A different code must produce a different attempt value.
        assert_ne!(
            base.code_attempt(&binding, "000042"),
            base.code_attempt(&binding, "000043")
        );
        // Roles are separated, so a host proof cannot be replayed as a peer proof.
        assert_ne!(
            base.possession(&binding, "host"),
            base.possession(&binding, "joiner")
        );
    }

    #[test]
    fn possession_proofs_verify_only_with_the_matching_certificate_key() {
        let identity = Identity::generate("pairprofile01").expect("identity");
        let signer = Signer::from_pem(&identity.key_pem).expect("signer");
        let public_key = certificate_public_key(&identity.certificate_der).expect("spki");
        assert_eq!(public_key, signer.public_key());
        let message = sha256(&[b"message"]);
        let signature = signer.sign(&message).expect("sign");
        assert!(verify(&public_key, &message, &signature));
        assert!(!verify(&public_key, b"other", &signature));

        let other = Identity::generate("pairprofile02").expect("identity");
        let other_key = certificate_public_key(&other.certificate_der).expect("spki");
        assert!(!verify(&other_key, &message, &signature));
        // A certificate that is not DER, or has no P-256 key, is refused.
        assert!(certificate_public_key(b"not a certificate").is_err());
        assert!(certificate_public_key(&identity.certificate_der[..60]).is_err());
    }

    #[test]
    fn an_identity_whose_key_does_not_match_its_certificate_is_refused() {
        let identity = Identity::generate("pairprofile03").expect("identity");
        let other = Identity::generate("pairprofile04").expect("identity");
        let signer = Signer::from_pem(&other.key_pem).expect("signer");
        assert_ne!(
            certificate_public_key(&identity.certificate_der).expect("spki"),
            signer.public_key()
        );
    }

    #[test]
    fn constant_time_comparison_rejects_length_and_content_differences() {
        assert!(constant_time_eq(b"abc", b"abc"));
        assert!(!constant_time_eq(b"abc", b"abd"));
        assert!(!constant_time_eq(b"abc", b"ab"));
        assert!(!constant_time_eq(b"", b"a"));
    }

    #[test]
    fn a_normal_invocation_never_selects_the_test_profile() {
        assert_eq!(resolve(false).expect("production"), Profile::Production);
        // Without the feature, asking for the adapter is an error rather than a
        // silent fallback to something unreviewed.
        if !cfg!(feature = "test-profile") {
            assert!(resolve(true).is_err());
        }
    }
}
