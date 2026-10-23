//! Newline-delimited JSON-RPC framing with hard limits.
//!
//! MCP over stdio is UTF-8, newline-delimited JSON-RPC. The plan forbids an
//! unbounded line reader, so this codec accumulates bytes and enforces the 8 MiB
//! frame ceiling while data arrives rather than after a full line exists.

/// 8 MiB per frame, excluding the newline delimiter.
pub const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
/// 16 MiB of application data per direction; a stalled consumer must not grow it.
pub const MAX_BUFFERED_BYTES: usize = 16 * 1024 * 1024;

#[derive(Debug, PartialEq, Eq)]
pub enum FrameError {
    /// A frame exceeded the per-frame ceiling before its delimiter arrived.
    FrameTooLarge,
    /// Buffered data exceeded the per-direction ceiling.
    BufferFull,
    /// A frame was not valid UTF-8.
    NotUtf8,
    /// The stream ended inside a frame; the plan requires discarding it.
    IncompleteFrame,
}

#[derive(Default)]
pub struct FrameCodec {
    buffer: Vec<u8>,
}

impl FrameCodec {
    pub fn new() -> Self {
        Self::default()
    }

    #[cfg(test)]
    pub fn buffered_len(&self) -> usize {
        self.buffer.len()
    }

    /// Accept bytes from a read. Frames may arrive split across any number of calls.
    pub fn push(&mut self, chunk: &[u8]) -> Result<(), FrameError> {
        if self.buffer.len() + chunk.len() > MAX_BUFFERED_BYTES {
            return Err(FrameError::BufferFull);
        }
        self.buffer.extend_from_slice(chunk);
        // Enforce the frame limit as soon as an unterminated frame is too large,
        // so a peer cannot force us to hold more than one frame's worth.
        let unterminated = self.buffer.len() - self.buffer.iter().rposition(|b| *b == b'\n').map_or(0, |index| index + 1);
        if unterminated > MAX_FRAME_BYTES {
            return Err(FrameError::FrameTooLarge);
        }
        Ok(())
    }

    /// Take the next complete frame, without its delimiter.
    pub fn next_frame(&mut self) -> Result<Option<String>, FrameError> {
        let Some(index) = self.buffer.iter().position(|byte| *byte == b'\n') else {
            return Ok(None);
        };
        let mut frame: Vec<u8> = self.buffer.drain(..=index).collect();
        frame.pop();
        if frame.len() > MAX_FRAME_BYTES {
            return Err(FrameError::FrameTooLarge);
        }
        String::from_utf8(frame)
            .map(Some)
            .map_err(|_| FrameError::NotUtf8)
    }

    /// Called at EOF. Any remaining bytes are an incomplete frame and must be
    /// discarded rather than forwarded as a truncated message.
    pub fn finish(&mut self) -> Result<(), FrameError> {
        if self.buffer.is_empty() {
            return Ok(());
        }
        self.buffer.clear();
        Err(FrameError::IncompleteFrame)
    }
}

/// A JSON-RPC request id, keeping numeric `1` and string `"1"` distinct.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum RequestId {
    Number(i64),
    Text(String),
}

impl RequestId {
    pub fn from_json(value: &serde_json::Value) -> Option<Self> {
        match value {
            serde_json::Value::Number(number) => number.as_i64().map(RequestId::Number),
            serde_json::Value::String(text) if !text.is_empty() => Some(RequestId::Text(text.clone())),
            _ => None,
        }
    }
}

/// Bounded used-id table. The plan requires retaining used ids in a bounded
/// structure instead of an ever-growing set, so the oldest entries are evicted
/// once the window is full.
pub struct UsedIds {
    seen: std::collections::HashSet<RequestId>,
    order: std::collections::VecDeque<RequestId>,
    capacity: usize,
}

impl UsedIds {
    pub fn new(capacity: usize) -> Self {
        Self {
            seen: std::collections::HashSet::with_capacity(capacity.min(4096)),
            order: std::collections::VecDeque::with_capacity(capacity.min(4096)),
            capacity: capacity.max(1),
        }
    }

    /// Record an id. Returns `false` when this id was already used in the session
    /// and the request must not be forwarded.
    pub fn insert(&mut self, id: RequestId) -> bool {
        if !self.seen.insert(id.clone()) {
            return false;
        }
        self.order.push_back(id.clone());
        while self.order.len() > self.capacity {
            if let Some(oldest) = self.order.pop_front() {
                self.seen.remove(&oldest);
            }
        }
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn frames_split_across_reads_are_reassembled() {
        let mut codec = FrameCodec::new();
        codec.push(b"{\"jsonrpc\":\"2.0\",\"id\":1}").expect("push");
        assert_eq!(codec.next_frame().expect("frame"), None);
        codec.push(b"\n{\"id\":2}").expect("push");
        assert_eq!(codec.next_frame().expect("frame").as_deref(), Some("{\"jsonrpc\":\"2.0\",\"id\":1}"));
        assert_eq!(codec.next_frame().expect("frame"), None);
        codec.push(b"\n").expect("push");
        assert_eq!(codec.next_frame().expect("frame").as_deref(), Some("{\"id\":2}"));
        codec.finish().expect("clean eof");
    }

    #[test]
    fn an_incomplete_frame_at_eof_is_an_error_and_is_discarded() {
        let mut codec = FrameCodec::new();
        codec.push(b"{\"id\":").expect("push");
        assert_eq!(codec.finish(), Err(FrameError::IncompleteFrame));
        // The bytes are gone: a later frame cannot be spliced onto them.
        assert_eq!(codec.buffered_len(), 0);
        codec.push(b"{\"id\":1}\n").expect("push");
        assert_eq!(codec.next_frame().expect("frame").as_deref(), Some("{\"id\":1}"));
    }

    #[test]
    fn oversized_frames_are_refused_while_arriving() {
        let mut codec = FrameCodec::new();
        let chunk = vec![b'a'; 1024 * 1024];
        for _ in 0..8 {
            codec.push(&chunk).expect("within limit");
        }
        // 8 MiB exactly is allowed; one more byte without a delimiter is not.
        assert_eq!(codec.push(b"a"), Err(FrameError::FrameTooLarge));
    }

    #[test]
    fn invalid_utf8_is_reported_rather_than_forwarded() {
        let mut codec = FrameCodec::new();
        codec.push(b"\xff\xfe\n").expect("push");
        assert_eq!(codec.next_frame(), Err(FrameError::NotUtf8));
    }

    #[test]
    fn numeric_and_string_ids_are_distinct() {
        let mut ids = UsedIds::new(8);
        assert!(ids.insert(RequestId::from_json(&json!(1)).unwrap()));
        assert!(!ids.insert(RequestId::from_json(&json!(1)).unwrap()));
        assert!(ids.insert(RequestId::from_json(&json!("1")).unwrap()));
        assert!(ids.insert(RequestId::from_json(&json!("2")).unwrap()));
    }

    #[test]
    fn the_used_id_table_is_bounded() {
        let mut ids = UsedIds::new(4);
        for value in 0..10 {
            assert!(ids.insert(RequestId::Number(value)));
        }
        assert!(ids.seen.len() <= 4, "table must stay bounded");
        assert!(ids.insert(RequestId::Number(100)), "new ids still accepted");
    }
}
