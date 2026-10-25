//! Read-only telemetry, deliberately separate from MCP execution.
pub mod collectors;
pub mod protocol;
pub mod transport;
pub mod worker;

pub const ALPN: &[u8] = b"odessy-telemetry/1";

pub fn host_id(installation: &str, fingerprint: &str) -> String {
    format!(
        "host_{}",
        crate::identity::fingerprint_sha256(format!("{installation}\n{fingerprint}").as_bytes())
    )
}

pub fn generation(revision: i64, fingerprint: &str) -> String {
    format!("{revision}:{fingerprint}")
}

pub fn run(arguments: &[String]) -> Result<(), crate::Failure> {
    if arguments != ["worker"] {
        return Err(crate::Failure::Usage("telemetry requires worker".into()));
    }
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let result = runtime.block_on(worker::run());
    runtime.shutdown_timeout(std::time::Duration::from_millis(250));
    result.map_err(crate::Failure::Runtime)
}

pub fn shared_collectors() -> std::sync::Arc<tokio::sync::Mutex<collectors::Collectors>> {
    static CACHE: std::sync::OnceLock<std::sync::Arc<tokio::sync::Mutex<collectors::Collectors>>> =
        std::sync::OnceLock::new();
    CACHE
        .get_or_init(|| std::sync::Arc::new(tokio::sync::Mutex::new(collectors::Collectors::new())))
        .clone()
}
pub mod gpu;

pub fn heartbeat(host: &Option<String>, generation: &Option<String>) -> serde_json::Value {
    use std::sync::atomic::{AtomicU64, Ordering};
    static SEQUENCE: AtomicU64 = AtomicU64::new(0);
    static BOOT: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    let boot = BOOT.get_or_init(|| {
        crate::identity::random_installation_id().unwrap_or_else(|_| "unknown".into())
    });
    serde_json::json!({"host_id":host,"generation":generation,"boot_id":boot,
        "sequence":SEQUENCE.fetch_add(1,Ordering::Relaxed)+1,"observed_at":collectors::now()})
}
