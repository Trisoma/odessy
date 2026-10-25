//! Vendor adapters: opt-in, fixed executables, no shell/elevation or inference.
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncReadExt};

pub fn parse_tegrastats(line: &str) -> Result<Value, String> {
    let words: Vec<&str> = line.split_whitespace().collect();
    let usage = words
        .iter()
        .position(|v| *v == "GR3D_FREQ")
        .and_then(|i| words.get(i + 1))
        .and_then(|v| v.trim_end_matches('%').parse::<f64>().ok())
        .filter(|v| v.is_finite() && *v >= 0.0 && *v <= 100.0)
        .ok_or("missing GPU usage")?;
    let mut rails = Vec::new();
    let mut sensors = Vec::new();
    for (i, word) in words.iter().enumerate() {
        if word.starts_with("VDD_") {
            if let Some(watts) = words
                .get(i + 1)
                .and_then(|v| v.split('/').next())
                .and_then(|v| v.strip_suffix("mW"))
                .and_then(|v| v.parse::<f64>().ok())
            {
                if watts.is_finite() && watts >= 0.0 {
                    rails.push(json!({"id":word,"domain":word,"watts":watts/1000.0}));
                }
            }
        }
        if let Some((name, temp)) = word.split_once('@') {
            if let Some(value) = temp
                .strip_suffix('C')
                .and_then(|v| v.parse::<f64>().ok())
                .filter(|v| v.is_finite())
            {
                sensors.push(json!({"id":name,"temperature_celsius":value}));
            }
        }
    }
    Ok(
        json!({"gpu":{"status":"ok","devices":[{"id":"jetson-gpu","utilization_percent":usage,"memory_kind":"shared"}]},
        "thermal":{"status":if sensors.is_empty(){"unsupported"}else{"ok"},"devices":sensors},
        "power":{"status":if rails.is_empty(){"unsupported"}else{"ok"},"devices":rails}}),
    )
}

pub fn parse_nvidia(text: &str) -> Result<Value, String> {
    let mut devices = Vec::new();
    for line in text.lines().take(16) {
        let parts: Vec<&str> = line.split(',').map(str::trim).collect();
        if parts.len() != 6 {
            return Err("invalid NVIDIA sample".into());
        }
        let number = |i: usize| {
            parts[i]
                .parse::<f64>()
                .ok()
                .filter(|v| v.is_finite() && *v >= 0.0)
        };
        devices.push(
            json!({"id":parts[0],"utilization_percent":number(1).filter(|v|*v<=100.0),
            "memory_used_bytes":number(2).map(|v|(v*1048576.0) as u64),
            "memory_total_bytes":number(3).map(|v|(v*1048576.0) as u64),
            "temperature_celsius":number(4),"power_watts":number(5),"memory_kind":"dedicated"}),
        );
    }
    if devices.is_empty() {
        return Err("no NVIDIA devices".into());
    }
    Ok(json!({"status":"ok","devices":devices}))
}

pub async fn enrich(sample: &mut Value, groups: &[String]) {
    if std::env::var("ODESSY_TELEMETRY_GPU").as_deref() != Ok("on")
        || !groups
            .iter()
            .any(|g| ["gpu", "thermal", "power"].contains(&g.as_str()))
    {
        return;
    }
    let tegra = std::path::Path::new("/usr/bin/tegrastats").is_file();
    let executable = if tegra {
        "/usr/bin/tegrastats"
    } else if cfg!(windows) {
        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
    } else {
        "/usr/bin/nvidia-smi"
    };
    if !std::path::Path::new(executable).is_file() {
        return;
    }
    let mut command = tokio::process::Command::new(executable);
    if tegra {
        command.args(["--interval", "1000"]);
    } else {
        command.args([
            "--query-gpu=uuid,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]);
    }
    #[cfg(windows)]
    {
        command.creation_flags(0x08000000);
    }
    command
        .kill_on_drop(true)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null());
    let Ok(mut child) = command.spawn() else {
        return;
    };
    let output = child.stdout.take().unwrap();
    let read = async {
        let mut data = Vec::new();
        let mut limited = tokio::io::BufReader::new(output).take(65537);
        if tegra {
            limited.read_until(b'\n', &mut data).await?;
        } else {
            limited.read_to_end(&mut data).await?;
        }
        Ok::<_, std::io::Error>(data)
    };
    let data = tokio::time::timeout(std::time::Duration::from_secs(2), read).await;
    let _ = child.kill().await;
    let _ = child.wait().await;
    if let Ok(Ok(data)) = data {
        if data.len() > 65536 {
            return;
        }
        if let Ok(text) = std::str::from_utf8(&data) {
            let parsed = if tegra {
                parse_tegrastats(text)
            } else {
                parse_nvidia(text).map(|gpu| json!({"gpu":gpu}))
            };
            if let Ok(value) = parsed {
                for group in ["gpu", "thermal", "power"] {
                    if groups.iter().any(|g| g == group) && value.get(group).is_some() {
                        sample[group] = value[group].clone();
                        sample[group]["observed_at"] = json!(super::collectors::now());
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn jetson_ram_and_power_are_shared_not_dedicated_gpu_metrics() {
        let value = super::parse_tegrastats(
            "RAM 1529/3602MB GR3D_FREQ 18% gpu@54.5C VDD_CPU_GPU_CV 432mW/432mW",
        )
        .unwrap();
        assert_eq!(value["gpu"]["devices"][0]["memory_kind"], "shared");
        assert!(value["gpu"]["devices"][0]
            .get("memory_total_bytes")
            .is_none());
        assert_eq!(value["gpu"]["devices"][0]["utilization_percent"], 18.0);
        assert_eq!(value["power"]["devices"][0]["domain"], "VDD_CPU_GPU_CV");
        assert_eq!(value["power"]["devices"][0]["watts"], 0.432);
        assert!(super::parse_tegrastats("no metrics").is_err());
    }
    #[test]
    fn nvidia_missing_power_is_null_and_mib_is_converted() {
        let value = super::parse_nvidia("GPU-abc, 20, 100, 1000, 45, N/A").unwrap();
        assert_eq!(value["devices"][0]["memory_used_bytes"], 100 * 1024 * 1024);
        assert!(value["devices"][0]["power_watts"].is_null());
    }
}
