//! Local pairing boundary. Real short-code enrollment is deliberately unavailable
//! until M0 approves an authenticated protocol/channel profile. No network listener,
//! identity rotation, trust write or capability grant is reachable from this module.
use serde_json::{json, Value};
use std::io::{BufRead, Write};

const MAX_FRAME: usize = 64 * 1024;

pub fn capabilities() -> Value {
    json!({"schema_version":1,"supported":false,"state":"closed",
        "reason":"protocol_review_required",
        "message":"Short-code pairing is unavailable until the protocol security review is complete. Existing paired devices remain usable.",
        "limits":{"code_digits":6,"window_seconds":120,"failed_attempts":3,
            "unauthenticated_connections":2,"handshake_inactivity_seconds":5,
            "frame_bytes":MAX_FRAME,"connection_bytes":256*1024}})
}

pub fn serve(mut input: impl BufRead, mut output: impl Write) -> Result<(), String> {
    let mut total = 0;
    loop {
        let mut frame = Vec::new();
        loop {
            let available = input.fill_buf().map_err(|_| "pairing input unavailable")?;
            if available.is_empty() {
                if frame.is_empty() { return Ok(()); }
                return Err("incomplete pairing frame".into());
            }
            let count = available.iter().position(|byte| *byte == b'\n').map_or(available.len(), |end|end+1);
            if frame.len()+count > MAX_FRAME || total+count > 256*1024 {
                return Err("pairing input limit exceeded".into());
            }
            let complete = available[count-1] == b'\n';
            frame.extend_from_slice(&available[..count]);
            input.consume(count);
            total += count;
            if complete { break; }
        }
        let request: Value = serde_json::from_slice(&frame).map_err(|_| "invalid pairing request")?;
        let id = request.get("id").and_then(Value::as_str)
            .filter(|value| !value.is_empty() && value.len()<=80 && value.bytes().all(|b|b.is_ascii_alphanumeric() || b==b'-' || b==b'_'));
        let mut reply=capabilities();
        reply["id"]=id.map_or(Value::Null, |value|json!(value));
        // Commands never cross the M0 gate. In particular a caller cannot provide
        // authenticated=true, a claimed fingerprint, or an approval to bypass it.
        serde_json::to_writer(&mut output,&reply).map_err(|_|"pairing reply unavailable")?;
        output.write_all(b"\n").map_err(|_|"pairing reply unavailable")?;
        output.flush().map_err(|_|"pairing reply unavailable")?;
    }
}
