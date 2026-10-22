//! Private state storage for identity material.
//!
//! The plan requires the private key to be protected by the operating system, not by
//! hope: owner-only modes on Unix, DPAPI plus an explicit non-inherited ACL on Windows.
//! Existing plaintext Windows identities are migrated deliberately and loudly rather
//! than silently left with weaker protection.

use std::path::{Path, PathBuf};

/// File the identity is written to. Protected on Windows, `0600` on Unix.
pub const IDENTITY_FILE: &str = "identity.json";
/// Windows-only protected form of the same document.
const PROTECTED_FILE: &str = "identity.dpapi";

/// Create the state directory with owner-only access if it does not exist.
pub fn ensure_state_dir(directory: &Path) -> Result<(), String> {
    std::fs::create_dir_all(directory)
        .map_err(|error| format!("cannot create {}: {error}", directory.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o700))
            .map_err(|error| format!("cannot restrict {}: {error}", directory.display()))?;
    }
    #[cfg(windows)]
    restrict_to_current_user(directory)?;
    Ok(())
}

/// Write identity bytes for `identity_dir`, protecting them at rest.
pub fn save_identity(identity_dir: &Path, bytes: &[u8]) -> Result<PathBuf, String> {
    ensure_state_dir(identity_dir)?;
    #[cfg(windows)]
    {
        let protected = identity_dir.join(PROTECTED_FILE);
        let sealed = dpapi::protect(bytes)?;
        write_new(&protected, &sealed)?;
        restrict_to_current_user(&protected)?;
        // A leftover plaintext copy would defeat the protection.
        let plaintext = identity_dir.join(IDENTITY_FILE);
        if plaintext.exists() {
            std::fs::remove_file(&plaintext).map_err(|error| {
                format!("cannot remove plaintext identity {}: {error}", plaintext.display())
            })?;
        }
        Ok(protected)
    }
    #[cfg(unix)]
    {
        let path = identity_dir.join(IDENTITY_FILE);
        write_new(&path, bytes)?;
        Ok(path)
    }
}

/// Read identity bytes, migrating a legacy plaintext Windows identity once.
pub fn load_identity(identity_dir: &Path) -> Result<Vec<u8>, String> {
    #[cfg(windows)]
    {
        let protected = identity_dir.join(PROTECTED_FILE);
        if protected.is_file() {
            return dpapi::unprotect(&std::fs::read(&protected).map_err(|error| {
                format!("cannot read {}: {error}", protected.display())
            })?);
        }
        let plaintext = identity_dir.join(IDENTITY_FILE);
        if plaintext.is_file() {
            let bytes = std::fs::read(&plaintext)
                .map_err(|error| format!("cannot read {}: {error}", plaintext.display()))?;
            // Migrate explicitly: protect, verify the round trip, then remove plaintext.
            let migrated = save_identity(identity_dir, &bytes)?;
            let check = dpapi::unprotect(&std::fs::read(&migrated).map_err(|error| {
                format!("cannot read {}: {error}", migrated.display())
            })?)?;
            if check != bytes {
                return Err(
                    "identity migration produced different bytes; refusing to continue".into(),
                );
            }
            eprintln!(
                "migrated identity at {} to DPAPI protection; the plaintext copy was removed",
                plaintext.display()
            );
            return Ok(bytes);
        }
        Err(format!(
            "no identity in {}; run `odessy-link init`",
            identity_dir.display()
        ))
    }
    #[cfg(unix)]
    {
        let path = identity_dir.join(IDENTITY_FILE);
        std::fs::read(&path).map_err(|error| {
            format!(
                "cannot read identity at {}: {error}; run `odessy-link init` first",
                path.display()
            )
        })
    }
}

/// Create a state file only the owner can read, even if the process umask is loose.
///
/// SQLite creates its file (and WAL sidecars) with the process default mode, which can
/// be group- or world-readable. Enrolment data names peers and grants; it is not a
/// secret, but it is authority state, so it gets the same owner-only treatment as the
/// identity.
pub fn restrict_file(path: &Path) -> Result<(), String> {
    if !path.exists() {
        return Ok(());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
            .map_err(|error| format!("cannot restrict {}: {error}", path.display()))?;
    }
    #[cfg(windows)]
    restrict_to_current_user(path)?;
    Ok(())
}

fn write_new(path: &Path, bytes: &[u8]) -> Result<(), String> {
    if path.exists() {
        std::fs::remove_file(path)
            .map_err(|error| format!("cannot replace {}: {error}", path.display()))?;
    }
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(path)
        .map_err(|error| format!("cannot create {}: {error}", path.display()))?;
    use std::io::Write;
    file.write_all(bytes)
        .map_err(|error| format!("cannot write {}: {error}", path.display()))?;
    Ok(())
}

/// Remove inherited access and grant only the current user. Windows ACLs inherit from
/// the profile directory by default, which other local accounts can read.
#[cfg(windows)]
fn restrict_to_current_user(path: &Path) -> Result<(), String> {
    let user = std::env::var("USERNAME").unwrap_or_default();
    if user.is_empty() {
        return Err("cannot determine the current Windows user for ACL restriction".into());
    }
    // Capture icacls instead of inheriting its handles: its progress text would
    // otherwise land on our stdout and corrupt machine-readable output such as
    // `peers list --json`.
    let output = std::process::Command::new("icacls")
        .arg(path)
        .arg("/inheritance:r")
        .arg("/grant:r")
        .arg(format!("{user}:F"))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .output()
        .map_err(|error| format!("cannot run icacls: {error}"))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        let detail = detail.trim();
        return Err(format!(
            "icacls could not restrict {} (exit {:?}){}{detail}",
            path.display(),
            output.status.code(),
            if detail.is_empty() { "" } else { ": " }
        ));
    }
    Ok(())
}

#[cfg(windows)]
mod dpapi {
    use std::ffi::c_void;

    /// DPAPI entropy: binds the ciphertext to this application, so another program
    /// running as the same user cannot simply unprotect it by accident.
    const ENTROPY: &[u8] = b"odessy-link-identity-v1";
    const CRYPTPROTECT_UI_FORBIDDEN: u32 = 0x1;

    #[repr(C)]
    struct DataBlob {
        cb_data: u32,
        pb_data: *mut u8,
    }

    #[link(name = "crypt32")]
    extern "system" {
        fn CryptProtectData(
            data_in: *const DataBlob,
            description: *const u16,
            entropy: *const DataBlob,
            reserved: *mut c_void,
            prompt: *mut c_void,
            flags: u32,
            data_out: *mut DataBlob,
        ) -> i32;
        fn CryptUnprotectData(
            data_in: *const DataBlob,
            description: *mut *mut u16,
            entropy: *const DataBlob,
            reserved: *mut c_void,
            prompt: *mut c_void,
            flags: u32,
            data_out: *mut DataBlob,
        ) -> i32;
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn LocalFree(memory: *mut c_void) -> *mut c_void;
    }

    fn blob(bytes: &[u8]) -> DataBlob {
        DataBlob {
            cb_data: bytes.len() as u32,
            pb_data: bytes.as_ptr() as *mut u8,
        }
    }

    fn take(out: DataBlob) -> Vec<u8> {
        // Copy then free: the buffer belongs to the system allocator.
        let copy = unsafe { std::slice::from_raw_parts(out.pb_data, out.cb_data as usize) }.to_vec();
        unsafe {
            LocalFree(out.pb_data as *mut c_void);
        }
        copy
    }

    pub fn protect(plaintext: &[u8]) -> Result<Vec<u8>, String> {
        let input = blob(plaintext);
        let entropy = blob(ENTROPY);
        let mut out = DataBlob {
            cb_data: 0,
            pb_data: std::ptr::null_mut(),
        };
        let ok = unsafe {
            CryptProtectData(
                &input,
                std::ptr::null(),
                &entropy,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut out,
            )
        };
        if ok == 0 {
            return Err(format!(
                "CryptProtectData failed: {}",
                std::io::Error::last_os_error()
            ));
        }
        Ok(take(out))
    }

    pub fn unprotect(ciphertext: &[u8]) -> Result<Vec<u8>, String> {
        let input = blob(ciphertext);
        let entropy = blob(ENTROPY);
        let mut out = DataBlob {
            cb_data: 0,
            pb_data: std::ptr::null_mut(),
        };
        let ok = unsafe {
            CryptUnprotectData(
                &input,
                std::ptr::null_mut(),
                &entropy,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut out,
            )
        };
        if ok == 0 {
            return Err(format!(
                "CryptUnprotectData failed: {}",
                std::io::Error::last_os_error()
            ));
        }
        Ok(take(out))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Temp {
        directory: PathBuf,
    }

    impl Temp {
        fn new(name: &str) -> Self {
            let directory = std::env::temp_dir().join(format!(
                "odessy-link-store-{name}-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            Self { directory }
        }
    }

    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.directory);
        }
    }

    #[test]
    fn identity_bytes_round_trip() {
        let temp = Temp::new("roundtrip");
        let bytes = br#"{"installation_id":"abc","key_pem":"PRIVATE KEY"}"#;
        let path = save_identity(&temp.directory, bytes).expect("save");
        assert!(path.is_file());
        assert_eq!(load_identity(&temp.directory).expect("load"), bytes);
    }

    #[cfg(unix)]
    #[test]
    fn the_identity_file_is_owner_only_from_the_first_write() {
        use std::os::unix::fs::PermissionsExt;
        let temp = Temp::new("mode");
        let path = save_identity(&temp.directory, b"secret").expect("save");
        let mode = std::fs::metadata(&path).expect("metadata").permissions().mode();
        assert_eq!(mode & 0o077, 0, "no group or other access: {mode:o}");
    }

    #[cfg(unix)]
    #[test]
    fn a_state_file_created_with_a_loose_umask_is_narrowed_to_the_owner() {
        use std::os::unix::fs::PermissionsExt;
        let temp = Temp::new("restrict");
        std::fs::create_dir_all(&temp.directory).expect("directory");
        let path = temp.directory.join("trust.sqlite3");
        std::fs::write(&path, b"registry").expect("write");
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).expect("mode");
        restrict_file(&path).expect("restrict");
        let mode = std::fs::metadata(&path).expect("metadata").permissions().mode();
        assert_eq!(mode & 0o077, 0, "no group or other access: {mode:o}");
        // A file that is not there yet is not an error: WAL sidecars appear lazily.
        restrict_file(&temp.directory.join("absent-wal")).expect("missing is fine");
    }

    #[cfg(windows)]
    #[test]
    fn the_identity_is_not_stored_as_plaintext() {
        let temp = Temp::new("dpapi");
        let bytes = br#"{"key_pem":"-----BEGIN PRIVATE KEY-----"}"#;
        let path = save_identity(&temp.directory, bytes).expect("save");
        assert_eq!(
            path.file_name().and_then(|name| name.to_str()),
            Some(PROTECTED_FILE)
        );
        let stored = std::fs::read(&path).expect("read");
        assert!(
            !stored.windows(bytes.len()).any(|window| window == &bytes[..]),
            "the protected file must not contain the plaintext"
        );
        assert_eq!(load_identity(&temp.directory).expect("load"), bytes);
    }

    #[cfg(windows)]
    #[test]
    fn an_existing_plaintext_identity_is_migrated_and_removed() {
        let temp = Temp::new("migrate");
        std::fs::create_dir_all(&temp.directory).expect("dir");
        let plaintext = temp.directory.join(IDENTITY_FILE);
        std::fs::write(&plaintext, b"legacy identity").expect("write");
        let loaded = load_identity(&temp.directory).expect("migrate");
        assert_eq!(loaded, b"legacy identity");
        assert!(!plaintext.exists(), "the plaintext copy must be removed");
        assert!(temp.directory.join(PROTECTED_FILE).is_file());
        // And the migrated form still loads.
        assert_eq!(load_identity(&temp.directory).expect("load"), b"legacy identity");
    }
}
