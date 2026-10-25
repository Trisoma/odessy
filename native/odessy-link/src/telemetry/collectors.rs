use serde_json::{json, Value};
use std::collections::HashMap;
use std::time::Instant;
use sysinfo::{Disks, Networks, System};

pub fn now() -> String {
    {
        let t = time::OffsetDateTime::now_utc();
        format!(
            "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
            t.year(),
            t.month() as u8,
            t.day(),
            t.hour(),
            t.minute(),
            t.second()
        )
    }
}

pub struct Collectors {
    system: System,
    networks: Networks,
    previous: HashMap<String, (u64, u64)>,
    last: Option<Instant>,
    network_last: Option<Instant>,
    cpu_initialized: bool,
    boot: String,
    sequence: u64,
    disk_cache: std::sync::Arc<std::sync::Mutex<Option<(Instant, Value)>>>,
    disk_running: std::sync::Arc<std::sync::atomic::AtomicBool>,
}

impl Collectors {
    pub fn new() -> Self {
        Self {
            system: System::new(),
            networks: Networks::new(),
            previous: HashMap::new(),
            last: None,
            network_last: None,
            cpu_initialized: false,
            boot: crate::identity::random_installation_id().unwrap_or_else(|_| "unknown".into()),
            sequence: 0,
            disk_cache: Default::default(),
            disk_running: Default::default(),
        }
    }

    pub fn sample(&mut self, groups: &[String]) -> Value {
        let started = Instant::now();
        let elapsed = self.last.map(|last| last.elapsed().as_secs_f64());
        let timestamp = now();
        self.sequence += 1;
        let mut result = json!({"schema_version":1,"id":format!("{}:{}",self.boot,self.sequence),
            "boot_id":self.boot,"sequence":self.sequence,"observed_at":timestamp,
            "interval_ms":elapsed.map(|v| ((v*1000.0) as u64).max(1)).unwrap_or(10000),"scope":"host"});
        for group in groups {
            if group == "services" {
                continue;
            }
            let mut value = match group.as_str() {
                "cpu" => {
                    self.system.refresh_cpu_usage();
                    let initialized = self.cpu_initialized;
                    self.cpu_initialized = true;
                    json!({"status":if initialized {"ok"} else {"warming_up"},
                        "utilization_percent":if initialized {Some(self.system.global_cpu_usage().clamp(0.0,100.0))} else {None}})
                }
                "memory" => {
                    self.system.refresh_memory();
                    if self.system.total_memory() == 0 {
                        unsupported()
                    } else {
                        json!({"status":"ok","total_bytes":self.system.total_memory(),
                            "available_bytes":self.system.available_memory(),"used_bytes":self.system.used_memory(),
                            "swap_total_bytes":self.system.total_swap(),"swap_used_bytes":self.system.used_swap()})
                    }
                }
                "disks" => {
                    use std::sync::atomic::Ordering;
                    let cached = self.disk_cache.lock().unwrap().clone();
                    if cached
                        .as_ref()
                        .is_none_or(|(at, _)| at.elapsed().as_secs() >= 60)
                        && !self.disk_running.swap(true, Ordering::SeqCst)
                    {
                        let cache = self.disk_cache.clone();
                        let running = self.disk_running.clone();
                        // One bounded background collector: an unavailable drive never stalls heartbeats/basic metrics.
                        std::thread::spawn(move || {
                            let disks = Disks::new_with_refreshed_list();
                            let devices:Vec<Value>=disks.iter().take(32).map(|disk|json!({
                                "id":crate::identity::fingerprint_sha256(disk.mount_point().to_string_lossy().as_bytes()),
                                "total_bytes":disk.total_space(),"available_bytes":disk.available_space()})).collect();
                            *cache.lock().unwrap() = Some((
                                Instant::now(),
                                json!({"status":"ok","observed_at":now(),"devices":devices,"truncated":disks.len()>32}),
                            ));
                            running.store(false, Ordering::SeqCst);
                        });
                    }
                    match cached {
                        Some((at, mut data)) => {
                            if at.elapsed().as_secs() > 120 {
                                data["status"] = json!("stale");
                            }
                            data
                        }
                        None => {
                            json!({"status":"warming_up","reason":"collection_pending","devices":[]})
                        }
                    }
                }
                "network" => {
                    let network_elapsed = self.network_last.map(|t| t.elapsed().as_secs_f64());
                    self.networks.refresh(true);
                    let mut next = HashMap::new();
                    let mut devices = Vec::new();
                    for (name, data) in &self.networks {
                        if devices.len() == 64 {
                            break;
                        }
                        let rx = data.total_received();
                        let tx = data.total_transmitted();
                        let previous = self.previous.get(name);
                        let rates = previous.and_then(|(r, t)| {
                            network_elapsed.and_then(|dt| {
                                if dt > 0.0 && rx >= *r && tx >= *t {
                                    Some(((rx - r) as f64 / dt, (tx - t) as f64 / dt))
                                } else {
                                    None
                                }
                            })
                        });
                        devices.push(json!({"id":name,"rx_bytes":rx,"tx_bytes":tx,
                            "rx_bytes_per_second":rates.map(|v|v.0),"tx_bytes_per_second":rates.map(|v|v.1)}));
                        next.insert(name.clone(), (rx, tx));
                    }
                    self.previous = next;
                    self.network_last = Some(Instant::now());
                    json!({"status":if network_elapsed.is_some(){"ok"}else{"warming_up"},"devices":devices,"truncated":self.networks.len()>64})
                }
                // Vendor helpers are opt-in; unsupported is never a measured zero.
                _ => unsupported(),
            };
            if value.get("observed_at").is_none() {
                value["observed_at"] = json!(timestamp);
            }
            value["collection_duration_ms"] = json!(started.elapsed().as_secs_f64() * 1000.0);
            result[group] = value;
        }
        self.last = Some(Instant::now());
        result
    }
}

fn unsupported() -> Value {
    json!({"status":"unsupported","reason":"collector_unavailable","devices":[]})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unrelated_group_does_not_complete_cpu_or_network_warmup() {
        let mut collector = Collectors::new();
        collector.sample(&["memory".into()]);
        let cpu = collector.sample(&["cpu".into()]);
        assert_eq!(cpu["cpu"]["status"], "warming_up");
        let network = collector.sample(&["network".into()]);
        assert_eq!(network["network"]["status"], "warming_up");
    }
    #[test]
    fn cpu_warms_up_and_gpu_does_not_fabricate_memory() {
        let mut collector = Collectors::new();
        let sample = collector.sample(&["cpu".into(), "gpu".into(), "memory".into()]);
        assert_eq!(sample["cpu"]["status"], "warming_up");
        assert!(sample["cpu"]["utilization_percent"].is_null());
        assert_eq!(sample["gpu"]["status"], "unsupported");
        assert!(sample["gpu"].get("memory_total_bytes").is_none());
    }
}
