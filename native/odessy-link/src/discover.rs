//! mDNS discovery, strictly as an address aid.
//!
//! An advertisement can tell us *where* a peer claims to be. It can never tell us
//! *who* it is: enrolment and permission still come from the local registry, and the
//! TLS layer still requires the exact enrolled certificate. A peer that advertises a
//! different fingerprint is reported, not trusted, and the saved manual address always
//! remains available when discovery is unavailable.

use mdns_sd::{ServiceDaemon, ServiceEvent, ServiceInfo};
use std::collections::BTreeMap;
use std::time::Duration;

/// Service type both ends advertise and browse.
pub const SERVICE_TYPE: &str = "_odessy-link._tcp.local.";
/// How long `discover` collects answers before reporting.
pub const BROWSE_WINDOW: Duration = Duration::from_secs(3);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Advertisement {
    /// Instance name, which is the advertising installation id.
    pub alias: String,
    pub host: String,
    pub port: u16,
    /// Advertised certificate fingerprint. Informational only: it is compared against
    /// the enrolled value to pick an address, never used to accept a peer.
    pub fingerprint: Option<String>,
}

impl Advertisement {
    pub fn address(&self) -> String {
        format!("{}:{}", self.host, self.port)
    }
}

/// Parse one resolved mDNS service into an advertisement. Missing or malformed
/// properties are tolerated: an advertisement is not authoritative.
pub fn from_service(
    instance: &str,
    host: &str,
    port: u16,
    properties: &BTreeMap<String, String>,
) -> Option<Advertisement> {
    if host.is_empty() || port == 0 {
        return None;
    }
    Some(Advertisement {
        alias: instance.to_string(),
        host: host.to_string(),
        port,
        fingerprint: properties.get("fingerprint").cloned(),
    })
}

/// Choose the address to use for `expected_fingerprint`.
///
/// Several peers may answer. A candidate whose advertised fingerprint equals the
/// enrolled one is preferred; that only selects an address, and the TLS handshake
/// still proves the peer holds the enrolled certificate. Ties are resolved by address
/// so a reconnect reaches the same peer deterministically.
pub fn select_address(
    candidates: &[Advertisement],
    expected_fingerprint: &str,
) -> Result<String, String> {
    let mut matching: Vec<&Advertisement> = candidates
        .iter()
        .filter(|candidate| {
            candidate
                .fingerprint
                .as_deref()
                .map(|value| value.eq_ignore_ascii_case(expected_fingerprint))
                .unwrap_or(false)
        })
        .collect();
    if matching.is_empty() {
        let seen: Vec<String> = candidates
            .iter()
            .map(|candidate| {
                format!(
                    "{} ({})",
                    candidate.alias,
                    candidate.fingerprint.as_deref().unwrap_or("no fingerprint")
                )
            })
            .collect();
        return Err(format!(
            "no advertisement matched the enrolled certificate; seen: {}",
            if seen.is_empty() {
                "none".to_string()
            } else {
                seen.join(", ")
            }
        ));
    }
    matching.sort_by_key(|candidate| candidate.address());
    Ok(matching[0].address())
}

/// Collect advertisements for a short window. Discovery being unavailable is an
/// error the caller reports; it is never silently treated as "no peer".
pub fn browse(window: Duration) -> Result<Vec<Advertisement>, String> {
    let daemon = ServiceDaemon::new().map_err(|error| format!("cannot start mDNS: {error}"))?;
    let receiver = daemon
        .browse(SERVICE_TYPE)
        .map_err(|error| format!("cannot browse {SERVICE_TYPE}: {error}"))?;
    let deadline = std::time::Instant::now() + window;
    let mut found = Vec::new();
    while std::time::Instant::now() < deadline {
        match receiver.recv_timeout(Duration::from_millis(200)) {
            Ok(ServiceEvent::ServiceResolved(info)) => {
                let properties: BTreeMap<String, String> = info
                    .get_properties()
                    .iter()
                    .map(|property| (property.key().to_string(), property.val_str().to_string()))
                    .collect();
                let host = info
                    .get_addresses()
                    .iter()
                    .map(|address| address.to_string())
                    .next()
                    .unwrap_or_default();
                if let Some(advertisement) = from_service(
                    info.get_fullname(),
                    &host,
                    info.get_port(),
                    &properties,
                ) {
                    found.push(advertisement);
                }
            }
            Ok(_) => {}
            Err(_) => {}
        }
    }
    let _ = daemon.shutdown();
    Ok(found)
}

/// Advertise this host while `serve` runs. The instance name is the installation id
/// and the TXT record repeats the public fingerprint so a coordinator can pick the
/// right address out of several; neither is trusted.
pub fn advertise(
    installation_id: &str,
    fingerprint: &str,
    host_name: &str,
    address: std::net::IpAddr,
    port: u16,
) -> Result<(ServiceDaemon, ServiceInfo), String> {
    let properties = [("fingerprint", fingerprint)];
    let info = ServiceInfo::new(
        SERVICE_TYPE,
        installation_id,
        host_name,
        address.to_string(),
        port,
        &properties[..],
    )
    .map_err(|error| format!("cannot build mDNS advertisement: {error}"))?;
    let daemon = ServiceDaemon::new().map_err(|error| format!("cannot start mDNS: {error}"))?;
    daemon
        .register(info.clone())
        .map_err(|error| format!("cannot advertise {SERVICE_TYPE}: {error}"))?;
    Ok((daemon, info))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn advertisement(alias: &str, host: &str, port: u16, fingerprint: Option<&str>) -> Advertisement {
        Advertisement {
            alias: alias.to_string(),
            host: host.to_string(),
            port,
            fingerprint: fingerprint.map(str::to_string),
        }
    }

    #[test]
    fn a_service_without_a_host_or_port_is_not_an_address() {
        let empty = BTreeMap::new();
        assert!(from_service("id", "", 7443, &empty).is_none());
        assert!(from_service("id", "10.0.0.5", 0, &empty).is_none());
        let advertisement = from_service("id", "10.0.0.5", 7443, &empty).expect("address");
        assert_eq!(advertisement.address(), "10.0.0.5:7443");
        assert_eq!(advertisement.fingerprint, None);
    }

    #[test]
    fn the_enrolled_fingerprint_picks_the_address_out_of_several_advertisements() {
        let candidates = [
            advertisement("other", "10.0.0.9", 7443, Some("ff")),
            advertisement("peer", "10.0.0.7", 7443, Some("AB")),
            advertisement("peer", "10.0.0.5", 7443, Some("ab")),
        ];
        let chosen = select_address(&candidates, "ab").expect("match");
        assert_eq!(chosen, "10.0.0.5:7443", "ties resolve deterministically");
    }

    #[test]
    fn an_unmatched_advertisement_is_never_used() {
        let candidates = [advertisement("impostor", "10.0.0.9", 7443, Some("ff"))];
        let error = select_address(&candidates, "ab").expect_err("must not match");
        assert!(error.contains("no advertisement matched"), "{error}");
        assert!(error.contains("impostor"), "the rejected candidate is named: {error}");

        let error = select_address(&[], "ab").expect_err("nothing to match");
        assert!(error.contains("none"), "{error}");
    }

    #[test]
    fn an_advertisement_cannot_supply_identity() {
        // The struct exposes only an address and an informational fingerprint: there is
        // nowhere to put a CA, a certificate, a capability or a workspace grant.
        let advertisement = advertisement("peer", "10.0.0.5", 7443, Some("ab"));
        let rendered = format!("{} {:?}", advertisement.address(), advertisement.fingerprint);
        assert!(rendered.contains("10.0.0.5:7443"));
        assert!(!rendered.to_lowercase().contains("grant"));
    }
}
