//! `odessy-link` — authenticated host pairing for Odessy.
//!
//! Implements the pairing plan's local slice: identity and enrolment, mutual TLS
//! `serve`/`connect`, the MCP relay, and mDNS discovery as an address aid only.

mod commands;
mod discover;
mod framing;
mod identity;
mod journal;
mod node;
mod relay;
mod store;
mod tls;
mod trust;

use identity::{display_fingerprint, random_installation_id, EnrollmentBundle, Identity};
use std::path::{Path, PathBuf};
use trust::Registry;

const USAGE: &str = "\
odessy-link — authenticated host pairing for Odessy

  odessy-link init
  odessy-link identity export --out <bundle>
  odessy-link pair add --alias <name> --certificate <bundle> --expect-sha256 <fingerprint> [--address <host:port>]
  odessy-link peers list [--json]
  odessy-link peers grant <peer> --capability mcp --workspace <path>
  odessy-link peers revoke <peer>
  odessy-link serve --listen <address> --workspace <path> --node-server <entry> [--advertise]
  odessy-link connect --peer <alias> [--address <host:port>] [--discover]
  odessy-link discover [--seconds <n>]

State directory: $ODESSY_LINK_STATE_DIR or the per-user data directory.
";

enum Failure {
    Usage(String),
    Runtime(String),
}

impl From<String> for Failure {
    fn from(message: String) -> Self {
        Failure::Runtime(message)
    }
}

impl From<std::io::Error> for Failure {
    fn from(error: std::io::Error) -> Self {
        Failure::Runtime(error.to_string())
    }
}

fn main() -> std::process::ExitCode {
    match run(std::env::args().skip(1).collect()) {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(Failure::Usage(message)) => {
            eprintln!("error: {message}\n\n{USAGE}");
            std::process::ExitCode::from(2)
        }
        Err(Failure::Runtime(message)) => {
            eprintln!("error: {message}");
            std::process::ExitCode::from(1)
        }
    }
}

fn run(arguments: Vec<String>) -> Result<(), Failure> {
    let Some(command) = arguments.first().map(String::as_str) else {
        return Err(Failure::Usage("missing command".into()));
    };
    match command {
        "init" => {
            no_extra(&arguments[1..])?;
            let state = state_dir()?;
            // The whole state directory is private, not only the identity inside it.
            store::ensure_state_dir(&state)?;
            let directory = state.join("identity");
            store::ensure_state_dir(&directory)?;
            let existing = directory.join("identity.json");
            let protected = directory.join("identity.dpapi");
            if existing.exists() || protected.exists() {
                return Err(Failure::Runtime(format!(
                    "identity already exists in {}; rotation requires explicit re-enrollment",
                    directory.display()
                )));
            }
            let installation_id = random_installation_id()?;
            let identity = Identity::generate(&installation_id)?;
            let path = save_identity(&directory, &identity)?;
            println!("installation id: {installation_id}");
            println!("fingerprint: {}", display_fingerprint(&identity.fingerprint()));
            println!("identity: {}", path.display());
            println!(
                "next: share `odessy-link identity export --out <bundle>` with the other operator"
            );
            Ok(())
        }
        "identity" => match arguments.get(1).map(String::as_str) {
            Some("export") => export_identity(&arguments[2..]),
            Some(other) => Err(Failure::Usage(format!("unknown identity subcommand: {other}"))),
            None => Err(Failure::Usage("identity requires 'export'".into())),
        },
        "pair" => match arguments.get(1).map(String::as_str) {
            Some("add") => pair_add(&arguments[2..]),
            Some(other) => Err(Failure::Usage(format!("unknown pair subcommand: {other}"))),
            None => Err(Failure::Usage("pair requires 'add'".into())),
        },
        "peers" => match arguments.get(1).map(String::as_str) {
            Some("list") => peers_list(&arguments[2..]),
            Some("grant") => peers_grant(&arguments[2..]),
            Some("revoke") => peers_revoke(&arguments[2..]),
            Some(other) => Err(Failure::Usage(format!("unknown peers subcommand: {other}"))),
            None => Err(Failure::Usage("peers requires list, grant or revoke".into())),
        },
        "serve" => commands::serve(&arguments[1..]),
        "connect" => commands::connect(&arguments[1..]),
        "discover" => discover_peers(&arguments[1..]),
        "--help" | "-h" | "help" => {
            print!("{USAGE}");
            Ok(())
        }
        other => Err(Failure::Usage(format!("unknown command: {other}"))),
    }
}

fn export_identity(arguments: &[String]) -> Result<(), Failure> {
    let options = parse(arguments, &["--out"])?;
    let out = options
        .get("--out")
        .ok_or_else(|| Failure::Usage("identity export requires --out".into()))?;
    let identity = load_identity()?;
    let bundle = identity.bundle();
    let json = serde_json::to_string_pretty(&bundle)
        .map_err(|error| Failure::Runtime(format!("cannot encode bundle: {error}")))?;
    std::fs::write(out, format!("{json}\n"))
        .map_err(|error| Failure::Runtime(format!("cannot write {out}: {error}")))?;
    println!("wrote enrollment bundle: {out}");
    println!(
        "compare this fingerprint out of band: {}",
        display_fingerprint(&bundle.fingerprint_sha256)
    );
    Ok(())
}

fn pair_add(arguments: &[String]) -> Result<(), Failure> {
    let options = parse(
        arguments,
        &["--alias", "--certificate", "--expect-sha256", "--address"],
    )?;
    let alias = required(&options, "--alias")?;
    let certificate = required(&options, "--certificate")?;
    let expected = required(&options, "--expect-sha256")?;
    let address = options.get("--address").map(String::as_str);
    let text = std::fs::read_to_string(certificate)
        .map_err(|error| Failure::Runtime(format!("cannot read {certificate}: {error}")))?;
    let registry = open_registry()?;
    let peer = registry.add_peer(alias, &text, expected, address)?;
    println!("enrolled {} ({})", peer.alias, peer.installation_id);
    println!("fingerprint: {}", display_fingerprint(&peer.fingerprint_sha256));
    println!("no capability granted yet: enrollment alone allows no execution");
    Ok(())
}

fn peers_list(arguments: &[String]) -> Result<(), Failure> {
    // `--json` is a bare switch: the coordinator parses it to bind approvals.
    let json = arguments.iter().any(|argument| argument == "--json");
    if let Some(extra) = arguments
        .iter()
        .find(|argument| *argument != "--json")
    {
        return Err(Failure::Usage(format!("unexpected argument: {extra}")));
    }
    let registry = open_registry()?;
    let peers = registry.peers()?;
    if json {
        let revision = registry.revision()?;
        let entries: Vec<serde_json::Value> = peers
            .iter()
            .map(|peer| {
                serde_json::json!({
                    "alias": peer.alias,
                    "installation_id": peer.installation_id,
                    "fingerprint_sha256": peer.fingerprint_sha256,
                    "address": peer.address,
                    "capability_mcp": peer.capability_mcp,
                    "workspace": peer.workspace,
                    "grant_revision": revision,
                })
            })
            .collect();
        println!(
            "{}",
            serde_json::json!({"peers": entries, "grant_revision": revision})
        );
        return Ok(());
    }
    if peers.is_empty() {
        println!("no enrolled peers");
        return Ok(());
    }
    for peer in peers {
        println!(
            "{}  {}{}  {}",
            peer.alias,
            peer.installation_id,
            if peer.has_execution_grant() {
                "  [mcp granted]"
            } else {
                "  [no execution access]"
            },
            peer.address.as_deref().unwrap_or("-")
        );
        println!("    fingerprint: {}", display_fingerprint(&peer.fingerprint_sha256));
        if let Some(workspace) = peer.workspace {
            println!("    workspace: {workspace}");
        }
    }
    Ok(())
}

fn discover_peers(arguments: &[String]) -> Result<(), Failure> {
    let options = parse(arguments, &["--seconds"])?;
    let seconds = options
        .get("--seconds")
        .map(|raw| raw.parse::<u64>())
        .transpose()
        .map_err(|_| Failure::Usage("--seconds must be a whole number".into()))?
        .unwrap_or(discover::BROWSE_WINDOW.as_secs());
    println!(
        "discovering {SERVICE} for {seconds}s (bindings are not affected: advertisements \
         are only an address aid and never enroll a peer)",
        SERVICE = discover::SERVICE_TYPE
    );
    let found = discover::browse(std::time::Duration::from_secs(seconds.max(1)))?;
    if found.is_empty() {
        println!("no advertisements found; use the address saved by `pair add --address`");
        return Ok(());
    }
    for advertisement in found {
        let address = advertisement
            .address()
            .unwrap_or_else(|reason| format!("unusable ({reason})"));
        println!(
            "{}  {}  advertised fingerprint: {} (unverified; compare with `peers list`)",
            advertisement.alias,
            address,
            advertisement.fingerprint.as_deref().unwrap_or("none")
        );
    }
    Ok(())
}

fn peers_grant(arguments: &[String]) -> Result<(), Failure> {    let Some(alias) = arguments.first() else {
        return Err(Failure::Usage("peers grant requires a peer".into()));
    };
    let options = parse(&arguments[1..], &["--capability", "--workspace"])?;
    let capability = required(&options, "--capability")?;
    let workspace = required(&options, "--workspace")?;
    let registry = open_registry()?;
    let peer = registry.grant(alias, capability, workspace)?;
    println!("granted {capability} to {} for {workspace}", peer.alias);
    Ok(())
}

fn peers_revoke(arguments: &[String]) -> Result<(), Failure> {
    let Some(alias) = arguments.first() else {
        return Err(Failure::Usage("peers revoke requires a peer".into()));
    };
    no_extra(&arguments[1..])?;
    let registry = open_registry()?;
    if registry.revoke(alias)? {
        println!("revoked all capabilities for {alias}");
    } else {
        println!("no such peer: {alias}");
    }
    Ok(())
}

pub(crate) fn required<'a>(
    options: &'a std::collections::BTreeMap<String, String>,
    name: &str,
) -> Result<&'a str, Failure> {
    options
        .get(name)
        .map(String::as_str)
        .ok_or_else(|| Failure::Usage(format!("missing {name}")))
}
/// Accept only the flags a subcommand documents. An unknown flag is an error, so a
/// misspelled `--expect-sha256` cannot be silently ignored.
pub(crate) fn parse(
    arguments: &[String],
    allowed: &[&str],
) -> Result<std::collections::BTreeMap<String, String>, Failure> {
    parse_with_switches(arguments, allowed, &[])
}

pub(crate) fn parse_with_switches(
    arguments: &[String],
    allowed: &[&str],
    switches: &[&str],
) -> Result<std::collections::BTreeMap<String, String>, Failure> {
    let mut options = std::collections::BTreeMap::new();
    let mut index = 0;
    while index < arguments.len() {
        let name = &arguments[index];
        if !name.starts_with("--") {
            return Err(Failure::Usage(format!("unexpected argument: {name}")));
        }
        if switches.contains(&name.as_str()) {
            if options.insert(name.clone(), String::new()).is_some() {
                return Err(Failure::Usage(format!("{name} was given more than once")));
            }
            index += 1;
            continue;
        }
        if !allowed.contains(&name.as_str()) {
            return Err(Failure::Usage(format!("unsupported option: {name}")));
        }
        let value = arguments
            .get(index + 1)
            .filter(|value| !value.starts_with("--"))
            .ok_or_else(|| Failure::Usage(format!("{name} requires a value")))?;
        if options.insert(name.clone(), value.clone()).is_some() {
            return Err(Failure::Usage(format!("{name} was given more than once")));
        }
        index += 2;
    }
    Ok(options)
}

#[cfg(test)]
mod cli_switch_tests {
    #[test]
    fn switches_preserve_values_and_reject_invalid_input() {
        for arguments in [
            vec!["--discover", "--peer", "laptop"],
            vec!["--peer", "laptop", "--discover"],
        ] {
            let arguments: Vec<String> = arguments.into_iter().map(String::from).collect();
            let options = crate::parse_with_switches(&arguments, &["--peer"], &["--discover"])
                .unwrap_or_else(|_| panic!("valid switch rejected"));
            assert_eq!(options.get("--peer").map(String::as_str), Some("laptop"));
            assert!(options.contains_key("--discover"));
        }
        for arguments in [
            vec!["--discover", "--discover"],
            vec!["--discover", "true"],
            vec!["--peer", "--discover"],
            vec!["--unknown"],
            vec!["--peer", "one", "--peer", "two"],
        ] {
            let arguments: Vec<String> = arguments.into_iter().map(String::from).collect();
            assert!(crate::parse_with_switches(&arguments, &["--peer"], &["--discover"]).is_err());
        }
        assert!(crate::parse(&["--discover".into()], &[]).is_err());
    }

    #[test]
    fn advertise_is_a_bare_serve_switch() {
        let error = crate::commands::serve(&["--advertise".into()]).unwrap_err();
        assert!(matches!(error, crate::Failure::Usage(message) if message == "missing --listen"));
    }

    #[test]
    fn discover_is_a_bare_connect_switch() {
        let error = crate::commands::connect(&["--discover".into()]).unwrap_err();
        assert!(matches!(error, crate::Failure::Usage(message) if message == "missing --peer"));
    }
}

fn no_extra(arguments: &[String]) -> Result<(), Failure> {
    match arguments.first() {
        Some(extra) => Err(Failure::Usage(format!("unexpected argument: {extra}"))),
        None => Ok(()),
    }
}

pub(crate) fn state_dir() -> Result<PathBuf, String> {
    if let Some(configured) = std::env::var_os("ODESSY_LINK_STATE_DIR") {
        return Ok(PathBuf::from(configured));
    }
    #[cfg(windows)]
    {
        let base = std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .ok_or_else(|| "APPDATA is not set".to_string())?;
        Ok(base.join("odessy-link"))
    }
    #[cfg(unix)]
    {
        if let Some(base) = std::env::var_os("XDG_DATA_HOME") {
            return Ok(PathBuf::from(base).join("odessy-link"));
        }
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .ok_or_else(|| "HOME is not set".to_string())?;
        return Ok(home.join(".local/share/odessy-link"));
    }
}

pub(crate) fn load_identity() -> Result<Identity, String> {
    let identity_dir = state_dir()?.join("identity");
    let text = String::from_utf8(store::load_identity(&identity_dir)?)
        .map_err(|error| format!("identity is not valid UTF-8: {error}"))?;
    let stored: StoredIdentity = serde_json::from_str(&text)
        .map_err(|error| format!("identity is unreadable: {error}"))?;
    let bundle = EnrollmentBundle {
        kind: EnrollmentBundle::KIND.to_string(),
        version: EnrollmentBundle::VERSION,
        installation_id: stored.installation_id.clone(),
        ca_pem: stored.ca_pem.clone(),
        certificate_pem: stored.certificate_pem.clone(),
        fingerprint_sha256: identity::fingerprint_sha256(&stored.certificate_der()?),
    };
    if bundle.fingerprint_sha256 != stored.fingerprint_sha256 {
        return Err("stored identity fingerprint does not match its certificate".into());
    }
    let certificate_der = stored.certificate_der()?;
    Ok(Identity {
        installation_id: stored.installation_id,
        ca_pem: stored.ca_pem,
        ca_der: Vec::new(),
        certificate_der,
        certificate_pem: stored.certificate_pem,
        key_pem: stored.key_pem,
    })
}

fn save_identity(identity_dir: &Path, identity: &Identity) -> Result<PathBuf, String> {
    let stored = StoredIdentity {
        installation_id: identity.installation_id.clone(),
        ca_pem: identity.ca_pem.clone(),
        certificate_pem: identity.certificate_pem.clone(),
        key_pem: identity.key_pem.clone(),
        fingerprint_sha256: identity.fingerprint(),
    };
    let json = serde_json::to_string_pretty(&stored)
        .map_err(|error| format!("cannot encode identity: {error}"))?;
    store::save_identity(identity_dir, format!("{json}\n").as_bytes())
}

#[derive(serde::Serialize, serde::Deserialize)]
struct StoredIdentity {
    installation_id: String,
    ca_pem: String,
    certificate_pem: String,
    key_pem: String,
    fingerprint_sha256: String,
}

impl StoredIdentity {
    fn certificate_der(&self) -> Result<Vec<u8>, String> {
        // Reuse the registry's PEM decoding so there is one implementation.
        trust::certificate_der_for(&self.certificate_pem)
    }
}

pub(crate) fn open_registry() -> Result<Registry, String> {
    let path = state_dir()?.join("trust.sqlite3");
    store::ensure_state_dir(&state_dir()?)?;
    Registry::open(&path)
}
