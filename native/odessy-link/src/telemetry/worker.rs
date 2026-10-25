use super::{
    collectors::Collectors,
    generation, host_id,
    protocol::{self, Request},
};
use serde_json::{json, Value};
use std::{collections::HashMap, sync::Arc};
use tokio::{io::BufReader, sync::Mutex};

pub fn inventory() -> Result<Value, String> {
    let identity = crate::load_identity().map_err(|_| "identity unavailable")?;
    let registry = crate::open_registry().map_err(|_| "registry unavailable")?;
    let revision = registry.revision()?;
    let fp = identity.fingerprint();
    let mut hosts = vec![
        json!({"host_id":host_id(&identity.installation_id,&fp),"installation_id":identity.installation_id,
        "fingerprint_sha256":fp,"alias":"This computer","platform":{"os":std::env::consts::OS,"arch":std::env::consts::ARCH},
        "trust_state":"local","monitoring_access":"allowed","generation":generation(revision,&fp),"certificate_expires_at":null}),
    ];
    for peer in registry.peers()? {
        hosts.push(json!({"host_id":host_id(&peer.installation_id,&peer.fingerprint_sha256),
            "installation_id":peer.installation_id,"fingerprint_sha256":peer.fingerprint_sha256,
            "alias":peer.alias,"platform":null,"trust_state":if peer.revoked {"revoked"}else{"paired"},
            "monitoring_access":if peer.capability_telemetry {"allowed"}else{"denied"},
            "generation":generation(revision,&peer.fingerprint_sha256),"certificate_expires_at":null}));
    }
    Ok(json!({"revision":revision,"hosts":hosts}))
}

pub async fn run() -> Result<(), String> {
    let collector = Arc::new(Mutex::new(Collectors::new()));
    let writer = Arc::new(Mutex::new(tokio::io::stdout()));
    let mut reader = BufReader::new(tokio::io::stdin());
    let mut tasks: HashMap<String, tokio::task::JoinHandle<()>> = HashMap::new();
    while let Some(bytes) = protocol::read_frame(&mut reader).await? {
        tasks.retain(|_, task| !task.is_finished());
        let req = match Request::parse(&bytes) {
            Ok(req) => req,
            Err(_) => {
                protocol::write_frame(
                    &mut *writer.lock().await,
                    &protocol::failure("", "INVALID_REQUEST"),
                )
                .await?;
                continue;
            }
        };
        if req.op == "cancel" {
            let found = req.target_id.as_ref().and_then(|id| tasks.remove(id));
            let state = if let Some(task) = found {
                task.abort();
                "cancelled"
            } else {
                "unknown"
            };
            protocol::write_frame(
                &mut *writer.lock().await,
                &json!({"schema_version":1,"id":req.id,"ok":true,"result":{"state":state}}),
            )
            .await?;
            continue;
        }
        if tasks.contains_key(&req.id) || (tasks.len() >= 4 && req.op != "inventory") {
            protocol::write_frame(
                &mut *writer.lock().await,
                &protocol::failure(&req.id, "RESOURCE_LIMIT"),
            )
            .await?;
            continue;
        }
        // Inventory/control have reserved admission, bounded separately by serial input.
        if req.op == "inventory" {
            let reply = match inventory() {
                Ok(value) => json!({"schema_version":1,"id":req.id,"ok":true,"result":value}),
                Err(_) => protocol::failure(&req.id, "TRUST_UNAVAILABLE"),
            };
            protocol::write_frame(&mut *writer.lock().await, &reply).await?;
            continue;
        }
        let id = req.id.clone();
        let output = writer.clone();
        let collector = collector.clone();
        tasks.insert(
            id,
            tokio::spawn(async move {
                let reply = match tokio::time::timeout(
                    std::time::Duration::from_secs(3),
                    handle(&req, collector),
                )
                .await
                {
                    Ok(Ok(value)) => {
                        json!({"schema_version":1,"id":req.id,"ok":true,"result":value})
                    }
                    Ok(Err(code)) => protocol::failure(&req.id, &code),
                    Err(_) => protocol::failure(&req.id, "TIMEOUT"),
                };
                let _ = protocol::write_frame(&mut *output.lock().await, &reply).await;
            }),
        );
    }
    for (_, task) in tasks {
        task.abort();
        let _ = task.await;
    }
    Ok(())
}

async fn handle(req: &Request, collector: Arc<Mutex<Collectors>>) -> Result<Value, String> {
    let inv = inventory()?;
    let target = inv["hosts"]
        .as_array()
        .unwrap()
        .iter()
        .find(|host| host["host_id"].as_str() == req.host_id.as_deref())
        .ok_or("HOST_NOT_FOUND")?;
    if target["generation"].as_str() != req.generation.as_deref() {
        return Err("STALE_GENERATION".into());
    }
    if target["monitoring_access"] != "allowed" {
        return Err("HOST_ACCESS_DENIED".into());
    }
    if target["trust_state"] == "local" {
        if req.op == "heartbeat" {
            return Ok(super::heartbeat(&req.host_id, &req.generation));
        }
        let mut sample = collector.lock().await.sample(&req.groups);
        super::gpu::enrich(&mut sample, &req.groups).await;
        return Ok(sample);
    }
    let value = super::transport::request(req).await?;
    let fresh = inventory()?;
    if !fresh["hosts"].as_array().unwrap().iter().any(|host| {
        host["host_id"].as_str() == req.host_id.as_deref()
            && host["generation"].as_str() == req.generation.as_deref()
            && host["monitoring_access"] == "allowed"
    }) {
        return Err("STALE_GENERATION".into());
    }
    Ok(value)
}
