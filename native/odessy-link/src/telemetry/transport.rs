use super::{
    collectors::Collectors,
    generation, host_id,
    protocol::{self, Request},
    ALPN,
};
use serde_json::{json, Value};
use std::{sync::Arc, time::Duration};
use tokio::{io::BufReader, net::TcpStream, sync::Mutex};
use tokio_rustls::{server::TlsStream, TlsConnector};

fn admitted(fingerprint: &str) -> Result<i64, String> {
    let registry = crate::open_registry().map_err(|_| "TRUST_UNAVAILABLE")?;
    let peer = registry
        .peer_by_fingerprint(fingerprint)?
        .ok_or("HOST_ACCESS_DENIED")?;
    if !peer.capability_telemetry || peer.revoked {
        return Err("HOST_ACCESS_DENIED".into());
    }
    registry.revision()
}

pub async fn serve_session(
    stream: TlsStream<TcpStream>,
    collector: Arc<Mutex<Collectors>>,
) -> Result<(), String> {
    let fingerprint = crate::tls::presented_fingerprint(stream.get_ref().1.peer_certificates())?;
    admitted(&fingerprint)?;
    let mut stream = BufReader::new(stream);
    loop {
        // Keep the partial-frame future alive across revocation ticks.
        let bytes = {
            let read = protocol::read_frame(&mut stream);
            tokio::pin!(read);
            let mut tick = tokio::time::interval(Duration::from_secs(1));
            loop {
                tokio::select! {
                    value=&mut read=>break value?,
                    _=tick.tick()=>{admitted(&fingerprint)?;}
                }
            }
        };
        let Some(bytes) = bytes else {
            return Ok(());
        };
        let revision = admitted(&fingerprint)?;
        let req = Request::parse(&bytes)?;
        let identity = crate::load_identity().map_err(|_| "IDENTITY_UNAVAILABLE")?;
        if req.host_id.as_deref()
            != Some(host_id(&identity.installation_id, &identity.fingerprint()).as_str())
        {
            return Err("HOST_NOT_FOUND".into());
        }
        let result = match req.op.as_str() {
            "heartbeat" => super::heartbeat(&req.host_id, &req.generation),
            "sample" => {
                let mut sample = collector.lock().await.sample(&req.groups);
                super::gpu::enrich(&mut sample, &req.groups).await;
                sample
            }
            _ => return Err("INVALID_REQUEST".into()),
        };
        if admitted(&fingerprint)? != revision {
            return Err("STALE_GENERATION".into());
        }
        protocol::write_frame(
            stream.get_mut(),
            &json!({"schema_version":1,"id":req.id,"ok":true,"result":result}),
        )
        .await?;
    }
}

pub async fn request(req: &Request) -> Result<Value, String> {
    let registry = crate::open_registry().map_err(|_| "TRUST_UNAVAILABLE")?;
    let peer = registry
        .peers()?
        .into_iter()
        .find(|peer| {
            Some(host_id(&peer.installation_id, &peer.fingerprint_sha256)).as_deref()
                == req.host_id.as_deref()
        })
        .ok_or("HOST_NOT_FOUND")?;
    if peer.revoked || !peer.capability_telemetry {
        return Err("HOST_ACCESS_DENIED".into());
    }
    if Some(generation(registry.revision()?, &peer.fingerprint_sha256)).as_deref()
        != req.generation.as_deref()
    {
        return Err("STALE_GENERATION".into());
    }
    let slot=connection_slot(req.host_id.as_deref().ok_or("HOST_NOT_FOUND")?).await;
    let mut guard=slot.lock().await;
    // Taking the stream makes cancellation fail closed: partial replies are never reused.
    let cached=guard.take();
    let binding=format!("{}:{}",req.generation.as_deref().unwrap_or(""),peer.address.as_deref().unwrap_or(""));
    let mut stream=match cached {
        Some((saved,stream)) if saved==binding => stream,
        _ => {
    let mut config = crate::tls::client_config(
        &crate::load_identity().map_err(|_| "IDENTITY_UNAVAILABLE")?,
        &peer,
    )?;
    config.alpn_protocols = vec![ALPN.to_vec()];
    let server_name = crate::tls::expected_server_name(&peer)?;
    let address = peer.address.as_deref().ok_or("ADDRESS_UNAVAILABLE")?;
    let socket = TcpStream::connect(address)
        .await
        .map_err(|_| "HOST_OFFLINE")?;
    let connector = TlsConnector::from(Arc::new(config));
    let stream = connector
        .connect(server_name, socket)
        .await
        .map_err(|_| "TLS_FAILED")?;
    if stream.get_ref().1.alpn_protocol() != Some(ALPN)
        || crate::tls::presented_fingerprint(stream.get_ref().1.peer_certificates())?
            != peer.fingerprint_sha256
    {
        return Err("IDENTITY_MISMATCH".into());
    }
    let stream = BufReader::new(stream);
    stream

        }
    };
    let message = json!({"schema_version":1,"id":req.id,"op":req.op,"host_id":req.host_id,"generation":req.generation,"groups":req.groups});
    protocol::write_frame(stream.get_mut(), &message).await?;
    let bytes = protocol::read_frame(&mut stream)
        .await?
        .ok_or("HOST_OFFLINE")?;
    let reply: Value = serde_json::from_slice(&bytes).map_err(|_| "INVALID_REPLY")?;
    if reply["id"] != req.id || reply["schema_version"] != 1 || reply["ok"] != true {
        return Err("INVALID_REPLY".into());
    }
    // The caller checks its registry again after the awaited response.
    let result=reply["result"].clone();
    *guard=Some((binding,stream));
    Ok(result)
}

type Connection=BufReader<tokio_rustls::client::TlsStream<TcpStream>>;
type Slot=Arc<Mutex<Option<(String,Connection)>>>;
async fn connection_slot(host:&str)->Slot {
    use std::{collections::HashMap,sync::OnceLock};
    static POOL:OnceLock<Mutex<HashMap<String,Slot>>>=OnceLock::new();
    let mut pool=POOL.get_or_init(||Mutex::new(HashMap::new())).lock().await;
    if let Some(slot)=pool.get(host){return slot.clone()}
    if pool.len()>=32 {if let Some(key)=pool.keys().next().cloned(){pool.remove(&key);}}
    let slot=Arc::new(Mutex::new(None));pool.insert(host.to_string(),slot.clone());slot
}
#[cfg(test)]
mod pooling_tests {
    #[tokio::test]
    async fn same_host_reuses_slot_without_sharing_another_hosts_connection() {
        let first=super::connection_slot("one").await;
        let second=super::connection_slot("one").await;
        let other=super::connection_slot("two").await;
        assert!(std::sync::Arc::ptr_eq(&first,&second));
        assert!(!std::sync::Arc::ptr_eq(&first,&other));
    }
}
