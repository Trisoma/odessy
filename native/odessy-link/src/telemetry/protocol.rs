use serde::Deserialize;
use serde_json::Value;
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncWrite, AsyncWriteExt};

pub const MAX_FRAME: usize = 256 * 1024;
pub const GROUPS: &[&str] = &[
    "cpu", "memory", "disk_io", "network", "disks", "gpu", "thermal", "power", "services",
];

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub schema_version: u8,
    pub id: String,
    pub op: String,
    pub host_id: Option<String>,
    pub generation: Option<String>,
    #[serde(default)]
    pub groups: Vec<String>,
    pub target_id: Option<String>,
}

impl Request {
    pub fn parse(bytes: &[u8]) -> Result<Self, String> {
        let request: Self =
            serde_json::from_slice(bytes).map_err(|_| "invalid request".to_string())?;
        if request.schema_version != 1
            || request.id.is_empty()
            || request.id.len() > 64
            || !["inventory", "heartbeat", "sample", "cancel"].contains(&request.op.as_str())
            || request.groups.len() > 9
            || request.groups.iter().any(|g| !GROUPS.contains(&g.as_str()))
            || request.host_id.as_ref().is_some_and(|v| v.len() > 128)
            || request.generation.as_ref().is_some_and(|v| v.len() > 128)
        {
            return Err("invalid request".into());
        }
        Ok(request)
    }
}

// Bound BEFORE extending the buffer. Partial frames and EOF are handled explicitly.
pub async fn read_frame<R: AsyncBufRead + Unpin>(
    reader: &mut R,
) -> Result<Option<Vec<u8>>, String> {
    let mut frame = Vec::new();
    loop {
        let chunk = reader.fill_buf().await.map_err(|_| "read failed")?;
        if chunk.is_empty() {
            return if frame.is_empty() {
                Ok(None)
            } else {
                Err("partial frame".into())
            };
        }
        let end = chunk.iter().position(|b| *b == b'\n');
        let take = end.map_or(chunk.len(), |n| n + 1);
        if frame.len() + take > MAX_FRAME {
            return Err("frame exceeds limit".into());
        }
        frame.extend_from_slice(&chunk[..take]);
        reader.consume(take);
        if end.is_some() {
            return Ok(Some(frame));
        }
    }
}

pub async fn write_frame<W: AsyncWrite + Unpin>(
    writer: &mut W,
    value: &Value,
) -> Result<(), String> {
    let mut bytes = serde_json::to_vec(value).map_err(|_| "encode failed")?;
    if bytes.len() >= MAX_FRAME {
        return Err("frame exceeds limit".into());
    }
    bytes.push(b'\n');
    writer.write_all(&bytes).await.map_err(|_| "write failed")?;
    writer.flush().await.map_err(|_| "flush failed".into())
}

pub fn failure(id: &str, code: &str) -> Value {
    serde_json::json!({"schema_version":1,"id":id,"ok":false,"error":{"code":code,"message":code,"retryable":false}})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn request_never_accepts_shell_or_unknown_fields() {
        assert!(Request::parse(br#"{"schema_version":1,"id":"1","op":"inventory"}"#).is_ok());
        for raw in [
            br#"{"schema_version":1,"id":"1","op":"shell"}"#.as_slice(),
            br#"{"schema_version":1,"id":"1","op":"inventory","command":"x"}"#,
        ] {
            assert!(Request::parse(raw).is_err());
        }
    }
    #[tokio::test]
    async fn oversized_and_partial_frames_are_refused() {
        let mut reader = tokio::io::BufReader::new(&b"partial"[..]);
        assert!(read_frame(&mut reader).await.is_err());
        let bytes = vec![b'x'; MAX_FRAME + 1];
        let mut reader = tokio::io::BufReader::new(bytes.as_slice());
        assert!(read_frame(&mut reader).await.is_err());
    }
}
