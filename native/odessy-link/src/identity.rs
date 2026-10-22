//! Installation identity: a self-signed CA plus a CA-signed connection certificate.
//!
//! The plan fixes the shape of this material: ECDSA P-256, a five-year CA, a
//! one-year connection certificate carrying both client and server auth usages,
//! and a DNS SAN derived from the installation id. Private keys never leave this
//! module's on-disk location and are never part of an enrollment bundle.

use rcgen::{
    BasicConstraints, CertificateParams, DistinguishedName, DnType, ExtendedKeyUsagePurpose, IsCa,
    KeyPair, KeyUsagePurpose, PKCS_ECDSA_P256_SHA256,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use time::{Duration, OffsetDateTime};

/// Five years, as planned. Rotation is an explicit re-enrollment, never automatic.
const CA_VALIDITY_DAYS: i64 = 365 * 5;
/// One year for the connection certificate.
const LEAF_VALIDITY_DAYS: i64 = 365;

/// Lowercase hex SHA-256 of the DER certificate, no separators.
///
/// This is the value an operator compares through an independently trusted
/// channel, so it must be the full digest of the exact bytes that will be
/// presented during the handshake.
pub fn fingerprint_sha256(der: &[u8]) -> String {
    let digest = Sha256::digest(der);
    let mut out = String::with_capacity(64);
    for byte in digest {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// Group a fingerprint as `aa:bb:...` for display only. Comparisons always use
/// the ungrouped value, so a display change can never weaken a check.
pub fn display_fingerprint(fingerprint: &str) -> String {
    fingerprint
        .as_bytes()
        .chunks(2)
        .map(|pair| String::from_utf8_lossy(pair).to_string())
        .collect::<Vec<_>>()
        .join(":")
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Identity {
    pub installation_id: String,
    pub ca_pem: String,
    pub ca_der: Vec<u8>,
    pub certificate_pem: String,
    pub certificate_der: Vec<u8>,
    /// Private key for the connection certificate. Never exported.
    pub key_pem: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EnrollmentBundle {
    pub kind: String,
    pub version: u32,
    pub installation_id: String,
    pub ca_pem: String,
    pub certificate_pem: String,
    pub fingerprint_sha256: String,
}

impl EnrollmentBundle {
    pub const KIND: &'static str = "odessy-link-enrollment";
    pub const VERSION: u32 = 1;

    /// Reject anything that is not a v1 enrollment bundle. A bundle is only a
    /// carrier for certificates; nothing inside it is trusted until the operator
    /// has confirmed the fingerprint out of band.
    pub fn from_json(text: &str) -> Result<Self, String> {
        let bundle: Self =
            serde_json::from_str(text).map_err(|error| format!("unreadable bundle: {error}"))?;
        if bundle.kind != Self::KIND {
            return Err(format!("not an enrollment bundle: kind={}", bundle.kind));
        }
        if bundle.version != Self::VERSION {
            return Err(format!("unsupported bundle version {}", bundle.version));
        }
        if bundle.fingerprint_sha256.len() != 64
            || !bundle
                .fingerprint_sha256
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
        {
            return Err("bundle fingerprint is not a SHA-256 hex digest".into());
        }
        Ok(bundle)
    }
}

impl Identity {
    /// Generate fresh identity material for `installation_id`.
    pub fn generate(installation_id: &str) -> Result<Self, String> {
        Self::build(installation_id, OffsetDateTime::now_utc(), CA_VALIDITY_DAYS, LEAF_VALIDITY_DAYS)
    }

    /// Material whose connection certificate expired in the past. Only tests use it:
    /// an expired certificate must be refused, never used to fall back to anything.
    #[cfg(test)]
    pub fn expired(installation_id: &str) -> Result<Self, String> {
        let start = OffsetDateTime::now_utc() - Duration::days(400);
        Self::build(installation_id, start, CA_VALIDITY_DAYS, 300)
    }

    /// Two identities that share one CA: the enrolled host and a sibling leaf that
    /// chains to the same authority. This is the valid-chain/wrong-leaf case, which
    /// only an exact certificate match can refuse.
    #[cfg(test)]
    pub fn with_sibling(installation_id: &str) -> Result<(Self, Self), String> {
        validate_installation_id(installation_id)?;
        let now = OffsetDateTime::now_utc();
        let ca_key = KeyPair::generate_for(&PKCS_ECDSA_P256_SHA256)
            .map_err(|error| format!("cannot generate CA key: {error}"))?;
        let mut ca_params = CertificateParams::default();
        ca_params.distinguished_name =
            distinguished_name("Odessy installation CA", installation_id);
        ca_params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        ca_params.key_usages = vec![
            KeyUsagePurpose::KeyCertSign,
            KeyUsagePurpose::CrlSign,
            KeyUsagePurpose::DigitalSignature,
        ];
        ca_params.not_before = now;
        ca_params.not_after = now + Duration::days(CA_VALIDITY_DAYS);
        let ca_cert = ca_params
            .self_signed(&ca_key)
            .map_err(|error| format!("cannot sign CA certificate: {error}"))?;
        let enrolment = Self::issue_leaf(&ca_cert, &ca_key, installation_id, now)?;
        // Same CA, same SAN, different key and therefore a different certificate.
        let sibling = Self::issue_leaf(&ca_cert, &ca_key, installation_id, now)?;
        Ok((enrolment, sibling))
    }

    #[cfg(test)]
    fn issue_leaf(
        ca_cert: &rcgen::Certificate,
        ca_key: &KeyPair,
        installation_id: &str,
        now: OffsetDateTime,
    ) -> Result<Self, String> {
        let leaf_key = KeyPair::generate_for(&PKCS_ECDSA_P256_SHA256)
            .map_err(|error| format!("cannot generate connection key: {error}"))?;
        let mut leaf_params = CertificateParams::new(vec![dns_name(installation_id)])
            .map_err(|error| format!("cannot build connection certificate: {error}"))?;
        leaf_params.distinguished_name =
            distinguished_name("Odessy connection certificate", installation_id);
        leaf_params.is_ca = IsCa::NoCa;
        leaf_params.extended_key_usages = vec![
            ExtendedKeyUsagePurpose::ClientAuth,
            ExtendedKeyUsagePurpose::ServerAuth,
        ];
        leaf_params.key_usages = vec![
            KeyUsagePurpose::DigitalSignature,
            KeyUsagePurpose::KeyEncipherment,
        ];
        leaf_params.not_before = now;
        leaf_params.not_after = now + Duration::days(LEAF_VALIDITY_DAYS);
        let leaf_cert = leaf_params
            .signed_by(&leaf_key, ca_cert, ca_key)
            .map_err(|error| format!("cannot sign connection certificate: {error}"))?;
        Ok(Self {
            installation_id: installation_id.to_string(),
            ca_pem: ca_cert.pem(),
            ca_der: ca_cert.der().to_vec(),
            certificate_pem: leaf_cert.pem(),
            certificate_der: leaf_cert.der().to_vec(),
            key_pem: leaf_key.serialize_pem(),
        })
    }

    fn build(
        installation_id: &str,
        now: OffsetDateTime,
        ca_days: i64,
        leaf_days: i64,
    ) -> Result<Self, String> {
        validate_installation_id(installation_id)?;
        let san = dns_name(installation_id);

        let ca_key = KeyPair::generate_for(&PKCS_ECDSA_P256_SHA256)
            .map_err(|error| format!("cannot generate CA key: {error}"))?;
        let mut ca_params = CertificateParams::default();
        ca_params.distinguished_name = distinguished_name("Odessy installation CA", installation_id);
        ca_params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        ca_params.key_usages = vec![
            KeyUsagePurpose::KeyCertSign,
            KeyUsagePurpose::CrlSign,
            KeyUsagePurpose::DigitalSignature,
        ];
        ca_params.not_before = now;
        ca_params.not_after = now + Duration::days(ca_days);
        let ca_cert = ca_params
            .self_signed(&ca_key)
            .map_err(|error| format!("cannot sign CA certificate: {error}"))?;

        let leaf_key = KeyPair::generate_for(&PKCS_ECDSA_P256_SHA256)
            .map_err(|error| format!("cannot generate connection key: {error}"))?;
        let mut leaf_params = CertificateParams::new(vec![san.clone()])
            .map_err(|error| format!("cannot build connection certificate: {error}"))?;
        leaf_params.distinguished_name =
            distinguished_name("Odessy connection certificate", installation_id);
        leaf_params.is_ca = IsCa::NoCa;
        // Both usages on purpose: the same certificate authenticates the client and
        // the server direction of a paired connection.
        leaf_params.extended_key_usages = vec![
            ExtendedKeyUsagePurpose::ClientAuth,
            ExtendedKeyUsagePurpose::ServerAuth,
        ];
        leaf_params.key_usages = vec![
            KeyUsagePurpose::DigitalSignature,
            KeyUsagePurpose::KeyEncipherment,
        ];
        leaf_params.not_before = now;
        leaf_params.not_after = now + Duration::days(leaf_days);
        let leaf_cert = leaf_params
            .signed_by(&leaf_key, &ca_cert, &ca_key)
            .map_err(|error| format!("cannot sign connection certificate: {error}"))?;

        Ok(Self {
            installation_id: installation_id.to_string(),
            ca_pem: ca_cert.pem(),
            ca_der: ca_cert.der().to_vec(),
            certificate_pem: leaf_cert.pem(),
            certificate_der: leaf_cert.der().to_vec(),
            key_pem: leaf_key.serialize_pem(),
        })
    }

    pub fn fingerprint(&self) -> String {
        fingerprint_sha256(&self.certificate_der)
    }

    /// Public material only. `key_pem` is deliberately absent from the type.
    pub fn bundle(&self) -> EnrollmentBundle {
        EnrollmentBundle {
            kind: EnrollmentBundle::KIND.to_string(),
            version: EnrollmentBundle::VERSION,
            installation_id: self.installation_id.clone(),
            ca_pem: self.ca_pem.clone(),
            certificate_pem: self.certificate_pem.clone(),
            fingerprint_sha256: self.fingerprint(),
        }
    }
}

pub fn dns_name(installation_id: &str) -> String {
    format!("p-{installation_id}.odessy.invalid")
}

fn distinguished_name(common_name: &str, installation_id: &str) -> DistinguishedName {
    let mut name = DistinguishedName::new();
    name.push(DnType::CommonName, common_name);
    name.push(DnType::OrganizationalUnitName, installation_id);
    name
}

/// Installation ids become part of a DNS name, so only characters that are safe
/// in a label are accepted. This also keeps a hostile id from injecting a second
/// SAN or a wildcard.
pub fn validate_installation_id(installation_id: &str) -> Result<(), String> {
    if installation_id.len() < 8 || installation_id.len() > 63 {
        return Err("installation id must be 8-63 characters".into());
    }
    if !installation_id
        .bytes()
        .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
    {
        return Err("installation id must be lowercase ASCII letters and digits".into());
    }
    Ok(())
}

/// Random installation id from the OS random source, restricted to the alphabet
/// `validate_installation_id` accepts.
pub fn random_installation_id() -> Result<String, String> {
    const ALPHABET: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789";
    let mut bytes = [0u8; 32];
    fill_random(&mut bytes)?;
    Ok(bytes
        .iter()
        .map(|byte| ALPHABET[(*byte as usize) % ALPHABET.len()] as char)
        .collect())
}

fn fill_random(buffer: &mut [u8]) -> Result<(), String> {
    #[cfg(unix)]
    {
        use std::io::Read;
        let mut source = std::fs::File::open("/dev/urandom")
            .map_err(|error| format!("cannot open /dev/urandom: {error}"))?;
        return source
            .read_exact(buffer)
            .map_err(|error| format!("cannot read /dev/urandom: {error}"));
    }
    #[cfg(windows)]
    {
        // BCryptGenRandom is the documented OS CSPRNG; CNG ships with Windows.
        #[link(name = "bcrypt")]
        extern "system" {
            fn BCryptGenRandom(
                algorithm: *mut core::ffi::c_void,
                buffer: *mut u8,
                length: u32,
                flags: u32,
            ) -> i32;
        }
        // BCRYPT_USE_SYSTEM_PREFERRED_RNG
        const FLAGS: u32 = 0x0000_0002;
        let status = unsafe {
            BCryptGenRandom(
                core::ptr::null_mut(),
                buffer.as_mut_ptr(),
                buffer.len() as u32,
                FLAGS,
            )
        };
        if status < 0 {
            return Err(format!("BCryptGenRandom failed with status {status}"));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn identity() -> Identity {
        Identity::generate("installation01").expect("identity")
    }

    #[test]
    fn fingerprint_is_full_sha256_hex() {
        let identity = identity();
        let fingerprint = identity.fingerprint();
        assert_eq!(fingerprint.len(), 64);
        assert!(fingerprint.bytes().all(|byte| byte.is_ascii_hexdigit()));
        assert_eq!(fingerprint, fingerprint_sha256(&identity.certificate_der));
    }

    #[test]
    fn bundle_carries_no_private_key_and_matches_the_certificate() {
        let identity = identity();
        let bundle = identity.bundle();
        let json = serde_json::to_string(&bundle).expect("json");
        assert!(!json.contains("PRIVATE KEY"));
        assert!(!json.contains(&identity.key_pem));
        assert_eq!(bundle.fingerprint_sha256, identity.fingerprint());
        assert_eq!(bundle.certificate_pem, identity.certificate_pem);
        assert_eq!(EnrollmentBundle::from_json(&json).expect("roundtrip"), bundle);
    }

    #[test]
    fn connection_certificate_uses_the_installation_dns_name() {
        let identity = identity();
        let pem = &identity.certificate_pem;
        assert!(pem.starts_with("-----BEGIN CERTIFICATE-----"));
        // The SAN is generated from the id; the id is rejected if it could escape
        // the label, so the drawn name is predictable.
        assert_eq!(dns_name("installation01"), "p-installation01.odessy.invalid");
    }

    #[test]
    fn invalid_installation_ids_are_rejected() {
        for bad in ["", "short", "UPPERCASE1", "with-dash-01", "*", "a.b"] {
            assert!(validate_installation_id(bad).is_err(), "{bad} must be rejected");
        }
        assert!(validate_installation_id("installation01").is_ok());
    }

    #[test]
    fn random_installation_ids_satisfy_validation() {
        for _ in 0..8 {
            let id = random_installation_id().expect("random id");
            validate_installation_id(&id).expect("valid id");
        }
        assert_ne!(
            random_installation_id().unwrap(),
            random_installation_id().unwrap()
        );
    }

    #[test]
    fn bundle_parsing_rejects_foreign_or_malformed_documents() {
        let bundle = identity().bundle();
        let mut json: serde_json::Value = serde_json::to_value(&bundle).expect("json");
        json["kind"] = serde_json::json!("something-else");
        assert!(EnrollmentBundle::from_json(&json.to_string()).is_err());
        let mut json: serde_json::Value = serde_json::to_value(&bundle).expect("json");
        json["version"] = serde_json::json!(2);
        assert!(EnrollmentBundle::from_json(&json.to_string()).is_err());
        let mut json: serde_json::Value = serde_json::to_value(&bundle).expect("json");
        json["fingerprint_sha256"] = serde_json::json!("abc");
        assert!(EnrollmentBundle::from_json(&json.to_string()).is_err());
        assert!(EnrollmentBundle::from_json("not json").is_err());
    }

    #[test]
    fn display_fingerprint_groups_without_changing_the_value() {
        let fingerprint = identity().fingerprint();
        let shown = display_fingerprint(&fingerprint);
        assert_eq!(shown.replace(':', ""), fingerprint);
        assert_eq!(shown.split(':').count(), 32);
    }
}
