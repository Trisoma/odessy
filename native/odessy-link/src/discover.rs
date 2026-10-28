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
    /// `host:port` in the form a connector expects: IPv4 as-is, IPv6 bracketed.
    pub fn address(&self) -> Result<String, String> {
        connect_address(&self.host, self.port)
    }
}

/// Turn a discovered host and port into something dialable.
///
/// An address advertisement supplies an `IpAddr`, and formatting that as `host:port`
/// is wrong for IPv6: `fe80::1:7443` parses as a different address, so the connection
/// would go nowhere. Standard socket formatting brackets the address instead. A
/// link-local address is only reachable through a specific interface, and discovery
/// carries no scope, so such a candidate is reported as unusable rather than returned
/// as an address that can never connect.
pub fn connect_address(host: &str, port: u16) -> Result<String, String> {
    if port == 0 {
        return Err("an advertisement without a port is not an address".into());
    }
    let host = host.trim();
    let (address, scope) = match host.split_once('%') {
        Some((address, scope)) => (address, Some(scope)),
        None => (host, None),
    };
    let address: std::net::IpAddr = address
        .parse()
        .map_err(|_| format!("advertisement names {host}, which is not an IP address"))?;
    match address {
        std::net::IpAddr::V4(v4) => match scope {
            None => Ok(std::net::SocketAddr::from((v4, port)).to_string()),
            Some(scope) => Err(format!(
                "IPv4 address {host} cannot carry an interface scope ({scope})"
            )),
        },
        std::net::IpAddr::V6(v6) => {
            let scope_id = match scope {
                Some(scope) => scope.parse::<u32>().map_err(|_| {
                    format!("interface scope {scope} in {host} is not an interface index")
                })?,
                // fe80::/10: link-local, which is scoped by definition. No scope means
                // no usable destination.
                None if (v6.segments()[0] & 0xffc0) == 0xfe80 => {
                    return Err(format!(
                        "link-local address {host} has no interface scope; the candidate is unusable"
                    ))
                }
                None => 0,
            };
            Ok(std::net::SocketAddr::V6(std::net::SocketAddrV6::new(
                v6, port, 0, scope_id,
            ))
            .to_string())
        }
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
        alias: properties.get("name").filter(|name| !name.is_empty()).cloned().unwrap_or_else(|| instance.to_string()),
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
    // Deterministic order so a reconnect reaches the same peer, independent of the
    // order the daemon reported them in.
    matching.sort_by(|left, right| {
        (&left.alias, &left.host, left.port).cmp(&(&right.alias, &right.host, right.port))
    });
    // A candidate that cannot be dialled is not an address. Reporting it beats handing
    // the caller a string that fails only after the TLS timeout.
    let mut unusable: Vec<String> = Vec::new();
    for candidate in matching {
        match candidate.address() {
            Ok(address) => return Ok(address),
            Err(reason) => unusable.push(format!("{} ({})", candidate.alias, reason)),
        }
    }
    Err(format!(
        "every advertisement for the enrolled certificate is unusable: {}",
        unusable.join(", ")
    ))
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
                for host in info.get_addresses().iter().filter(|ip| !ip.is_unspecified() && !ip.is_loopback() && !ip.is_multicast()) {
                if let Some(advertisement) = from_service(
                    info.get_fullname(),
                    &host.to_string(),
                    info.get_port(),
                    &properties,
                ) {
                    if !found.contains(&advertisement) { found.push(advertisement); }
                }
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
    let name=sysinfo::System::host_name().unwrap_or_else(|| installation_id.to_string());
    let properties = [("fingerprint", fingerprint), ("name", name.as_str())];
    let info = ServiceInfo::new(
        SERVICE_TYPE,
        installation_id,
        host_name,
        if address.is_unspecified() { String::new() } else { address.to_string() },
        port,
        &properties[..],
    )
    .map_err(|error| format!("cannot build mDNS advertisement: {error}"))?;
    let info = if address.is_unspecified() { info.enable_addr_auto() } else { info };
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
        assert_eq!(advertisement.address().expect("usable"), "10.0.0.5:7443");
        assert_eq!(advertisement.fingerprint, None);
    }

    #[test]
    fn addresses_are_formatted_as_socket_addresses() {
        // IPv4 is unchanged; IPv6 must be bracketed or the port reads as part of it.
        assert_eq!(connect_address("10.0.0.5", 7443).expect("v4"), "10.0.0.5:7443");
        assert_eq!(connect_address("::1", 7443).expect("v6"), "[::1]:7443");
        assert_eq!(
            connect_address("2001:db8::10", 7443).expect("v6"),
            "[2001:db8::10]:7443"
        );
        assert_eq!(
            connect_address("fe80::1%7", 7443).expect("scoped"),
            "[fe80::1%7]:7443",
            "a scope that identifies the interface is kept"
        );
        // The unbracketed form is not equivalent, so formatting it would have dialled
        // a different address entirely.
        assert_ne!(connect_address("2001:db8::10", 7443).expect("v6"), "2001:db8::10:7443");
        // What a connector does with the string: parse it back as a socket address.
        for host in ["10.0.0.5", "::1", "2001:db8::10", "fe80::1%7"] {
            let text = connect_address(host, 7443).expect("usable");
            let parsed: std::net::SocketAddr = text
                .parse()
                .unwrap_or_else(|error| panic!("{text} must parse as a socket address: {error}"));
            assert_eq!(parsed.port(), 7443, "{text}");
        }
        match connect_address("fe80::1%7", 7443).expect("scoped").parse::<std::net::SocketAddr>() {
            Ok(std::net::SocketAddr::V6(v6)) => assert_eq!(v6.scope_id(), 7, "the scope survives"),
            other => panic!("expected a scoped IPv6 address, got {other:?}"),
        }
    }

    #[test]
    fn a_link_local_address_without_an_interface_scope_is_unusable() {
        let error = connect_address("fe80::1", 7443).expect_err("no scope");
        assert!(error.contains("no interface scope"), "{error}");
        assert!(error.contains("unusable"), "{error}");
        // A named scope is not an interface index, so it is refused rather than guessed.
        let error = connect_address("fe80::1%Wi-Fi", 7443).expect_err("named scope");
        assert!(error.contains("not an interface index"), "{error}");
        // IPv4 has no scope concept at all.
        let error = connect_address("10.0.0.5%7", 7443).expect_err("v4 scope");
        assert!(error.contains("cannot carry an interface scope"), "{error}");
        // A host name is not dialable by this path.
        let error = connect_address("peer.local", 7443).expect_err("hostname");
        assert!(error.contains("not an IP address"), "{error}");
    }

    #[test]
    fn selection_prefers_a_usable_address_over_an_unusable_one() {
        let candidates = [
            advertisement("peer", "fe80::1", 7443, Some("ab")),
            advertisement("peer", "2001:db8::10", 7443, Some("ab")),
        ];
        assert_eq!(
            select_address(&candidates, "ab").expect("usable"),
            "[2001:db8::10]:7443"
        );
    }

    #[test]
    fn a_match_that_cannot_be_dialled_is_reported_rather_than_returned() {
        let candidates = [advertisement("peer", "fe80::1", 7443, Some("ab"))];
        let error = select_address(&candidates, "ab").expect_err("unusable");
        assert!(error.contains("unusable"), "{error}");
        assert!(error.contains("fe80::1"), "the candidate is named: {error}");
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
        let rendered = format!(
            "{} {:?}",
            advertisement.address().expect("usable"),
            advertisement.fingerprint
        );
        assert!(rendered.contains("10.0.0.5:7443"));
        assert!(!rendered.to_lowercase().contains("grant"));
    }
}
