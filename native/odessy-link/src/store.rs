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
#[cfg(windows)]
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
    // State written by an older build (or an operator's own directory) is tightened on
    // first use, not only when it is written again.
    if identity_dir.is_dir() {
        ensure_state_dir(identity_dir)?;
    }
    #[cfg(windows)]
    {
        let protected = identity_dir.join(PROTECTED_FILE);
        if protected.is_file() {
            restrict_file(&protected)?;
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
        if path.is_file() {
            restrict_file(&path)?;
        }
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

/// Enforce an owner-only DACL on `path` and prove it.
///
/// Removing inherited entries is not enough on its own: an *explicit* grant such as
/// `Everyone: FullControl` survives `/inheritance:r`, and so does an identity file that
/// was created before the directory was restricted. The sequence below discards every
/// explicit entry, removes inheritance, grants only this user, and then reads the
/// resulting DACL back. If anything but this user's SID ends up with access, the call
/// fails instead of reporting the state as protected.
#[cfg(windows)]
fn restrict_to_current_user(path: &Path) -> Result<(), String> {
    win_security::restrict(path)
}

/// Windows token, DACL and icacls plumbing.
#[cfg(windows)]
mod win_security {
    use std::ffi::c_void;
    use std::path::Path;

    type Handle = *mut c_void;

    const TOKEN_QUERY: u32 = 0x0008;
    const TOKEN_USER_CLASS: u32 = 1;
    const DACL_SECURITY_INFORMATION: u32 = 0x0000_0004;
    const ACL_SIZE_INFORMATION_CLASS: u32 = 2;
    const SE_FILE_OBJECT: u32 = 1;
    const ACCESS_ALLOWED_ACE_TYPE: u8 = 0;
    /// `/reset` rewrites the ACL from the parent's inheritable entries, which is the
    /// only icacls operation that discards explicit entries we did not create.
    const RESET: &str = "/reset";

    #[repr(C)]
    struct SidAndAttributes {
        sid: *mut c_void,
        attributes: u32,
    }

    #[repr(C)]
    struct AclSizeInformation {
        ace_count: u32,
        acl_bytes_in_use: u32,
        acl_bytes_free: u32,
    }

    /// `ACE_HEADER`: type, flags and size, in that order.
    #[repr(C)]
    struct AceHeader {
        ace_type: u8,
        ace_flags: u8,
        ace_size: u16,
    }

    #[link(name = "advapi32")]
    extern "system" {
        fn OpenProcessToken(process: Handle, access: u32, token: *mut Handle) -> i32;
        fn GetTokenInformation(
            token: Handle,
            class: u32,
            buffer: *mut c_void,
            length: u32,
            needed: *mut u32,
        ) -> i32;
        fn ConvertSidToStringSidW(sid: *mut c_void, text: *mut *mut u16) -> i32;
        fn GetNamedSecurityInfoW(
            object: *const u16,
            object_type: u32,
            info: u32,
            owner: *mut *mut c_void,
            group: *mut *mut c_void,
            dacl: *mut *mut c_void,
            sacl: *mut *mut c_void,
            descriptor: *mut *mut c_void,
        ) -> u32;
        fn GetAclInformation(
            acl: *const c_void,
            info: *mut c_void,
            length: u32,
            class: u32,
        ) -> i32;
        fn GetAce(acl: *const c_void, index: u32, ace: *mut *mut c_void) -> i32;
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn GetCurrentProcess() -> Handle;
        fn CloseHandle(handle: Handle) -> i32;
        fn LocalFree(memory: *mut c_void) -> *mut c_void;
    }

    /// One access control entry, reduced to what a protection check needs: its type and
    /// the SID it names.
    #[derive(Debug, Clone, PartialEq, Eq)]
    pub struct Ace {
        pub ace_type: u8,
        pub sid: String,
    }

    /// The current user's SID from this process's token.
    ///
    /// Never `USERNAME`: a user name is not unique, can be a group, and does not match
    /// the trustee recorded in a DACL.
    pub fn current_sid() -> Result<String, String> {
        unsafe {
            let mut token: Handle = std::ptr::null_mut();
            if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
                return Err(format!(
                    "cannot open this process's token: {}",
                    std::io::Error::last_os_error()
                ));
            }
            let result = token_user_sid(token);
            CloseHandle(token);
            result
        }
    }

    unsafe fn token_user_sid(token: Handle) -> Result<String, String> {
        let mut needed: u32 = 0;
        GetTokenInformation(
            token,
            TOKEN_USER_CLASS,
            std::ptr::null_mut(),
            0,
            &mut needed,
        );
        if needed == 0 {
            return Err(format!(
                "cannot size the token user: {}",
                std::io::Error::last_os_error()
            ));
        }
        let mut buffer = vec![0u8; needed as usize];
        if GetTokenInformation(
            token,
            TOKEN_USER_CLASS,
            buffer.as_mut_ptr() as *mut c_void,
            needed,
            &mut needed,
        ) == 0
        {
            return Err(format!(
                "cannot read the token user: {}",
                std::io::Error::last_os_error()
            ));
        }
        let user = &*(buffer.as_ptr() as *const SidAndAttributes);
        if user.sid.is_null() {
            return Err("this process's token carries no user SID".into());
        }
        sid_to_string(user.sid)
    }

    unsafe fn sid_to_string(sid: *mut c_void) -> Result<String, String> {
        let mut text: *mut u16 = std::ptr::null_mut();
        if ConvertSidToStringSidW(sid, &mut text) == 0 {
            return Err(format!(
                "cannot format a SID: {}",
                std::io::Error::last_os_error()
            ));
        }
        let mut length = 0usize;
        while *text.add(length) != 0 {
            length += 1;
        }
        let value = String::from_utf16_lossy(std::slice::from_raw_parts(text, length));
        LocalFree(text as *mut c_void);
        Ok(value)
    }

    /// Every entry of `path`'s DACL, in order. A missing DACL is an error: a null DACL
    /// grants everyone full access, which is the opposite of what is wanted here.
    pub fn entries(path: &Path) -> Result<Vec<Ace>, String> {
        let wide = wide(path);
        unsafe {
            let mut dacl: *mut c_void = std::ptr::null_mut();
            let mut descriptor: *mut c_void = std::ptr::null_mut();
            let status = GetNamedSecurityInfoW(
                wide.as_ptr(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut dacl,
                std::ptr::null_mut(),
                &mut descriptor,
            );
            if status != 0 {
                return Err(format!(
                    "cannot read the ACL of {} (Windows error {status})",
                    path.display()
                ));
            }
            let result = read_aces(dacl);
            LocalFree(descriptor);
            result
        }
    }

    unsafe fn read_aces(dacl: *mut c_void) -> Result<Vec<Ace>, String> {
        if dacl.is_null() {
            return Err("the object has a null DACL, which grants everyone full access".into());
        }
        let mut info = AclSizeInformation {
            ace_count: 0,
            acl_bytes_in_use: 0,
            acl_bytes_free: 0,
        };
        if GetAclInformation(
            dacl,
            &mut info as *mut AclSizeInformation as *mut c_void,
            std::mem::size_of::<AclSizeInformation>() as u32,
            ACL_SIZE_INFORMATION_CLASS,
        ) == 0
        {
            return Err("cannot read the size of the DACL".into());
        }
        let mut aces = Vec::with_capacity(info.ace_count as usize);
        for index in 0..info.ace_count {
            let mut ace: *mut c_void = std::ptr::null_mut();
            if GetAce(dacl, index, &mut ace) == 0 || ace.is_null() {
                return Err("cannot read an access control entry".into());
            }
            let header = &*(ace as *const AceHeader);
            if header.ace_type != ACCESS_ALLOWED_ACE_TYPE {
                aces.push(Ace {
                    ace_type: header.ace_type,
                    sid: String::new(),
                });
                continue;
            }
            // ACCESS_ALLOWED_ACE is the header, then the access mask, then the SID.
            let sid = (ace as *mut u8).add(std::mem::size_of::<AceHeader>() + 4) as *mut c_void;
            aces.push(Ace {
                ace_type: header.ace_type,
                sid: sid_to_string(sid)?,
            });
        }
        Ok(aces)
    }

    /// Make `path` reachable only by this user, then verify the DACL says so.
    pub fn restrict(path: &Path) -> Result<(), String> {
        let sid = current_sid()?;
        let rights = if path.is_dir() {
            // Children created later (SQLite's WAL and shared-memory files, the
            // protected identity) have to inherit this grant.
            format!("*{sid}:(OI)(CI)F")
        } else {
            format!("*{sid}:F")
        };
        run_icacls(path, &[RESET])?;
        run_icacls(path, &["/inheritance:r", "/grant:r", rights.as_str()])?;
        verify_owner_only(path, &sid)
    }

    /// Fail closed unless every entry of `path`'s DACL grants this user and no one else.
    pub fn verify_owner_only(path: &Path, sid: &str) -> Result<(), String> {
        let entries = entries(path)?;
        if entries.is_empty() {
            return Err(format!(
                "{} ended up with an empty DACL and cannot be used",
                path.display()
            ));
        }
        for entry in &entries {
            if entry.ace_type != ACCESS_ALLOWED_ACE_TYPE {
                // A deny entry would also be surprising here: the owner must be able to
                // use its own state.
                return Err(format!(
                    "{} carries a non-grant access control entry (type {})",
                    path.display(),
                    entry.ace_type
                ));
            }
            if entry.sid != sid {
                return Err(format!(
                    "{} still grants access to {}; refusing to treat it as protected",
                    path.display(),
                    entry.sid
                ));
            }
        }
        Ok(())
    }

    fn run_icacls(path: &Path, arguments: &[&str]) -> Result<(), String> {
        // Capture icacls instead of inheriting its handles: its progress text would
        // otherwise land on our stdout and corrupt machine-readable output such as
        // `peers list --json`.
        let output = std::process::Command::new("icacls")
            .arg(path)
            .args(arguments)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::piped())
            .output()
            .map_err(|error| format!("cannot run icacls: {error}"))?;
        if !output.status.success() {
            let detail = String::from_utf8_lossy(&output.stderr);
            let detail = detail.trim();
            return Err(format!(
                "icacls {} could not restrict {} (exit {:?}){}{detail}",
                arguments.join(" "),
                path.display(),
                output.status.code(),
                if detail.is_empty() { "" } else { ": " }
            ));
        }
        Ok(())
    }

    fn wide(path: &Path) -> Vec<u16> {
        use std::os::windows::ffi::OsStrExt;
        path.as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
    }
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
    fn an_explicit_everyone_grant_is_removed_and_the_state_stays_usable() {
        let everyone = "S-1-1-0";
        let temp = Temp::new("dacl");
        std::fs::create_dir_all(&temp.directory).expect("directory");
        // The reported case: an explicit grant on the directory itself. `/inheritance:r`
        // alone leaves it in place, so the DACL has to be reset rather than trimmed.
        let added = std::process::Command::new("icacls")
            .arg(&temp.directory)
            .arg("/grant")
            .arg(format!("*{everyone}:(OI)(CI)F"))
            .output()
            .expect("icacls");
        assert!(
            added.status.success(),
            "{}",
            String::from_utf8_lossy(&added.stderr)
        );
        let before = win_security::entries(&temp.directory).expect("acl");
        assert!(
            before.iter().any(|ace| ace.sid == everyone),
            "precondition: an explicit Everyone grant must be present, found {before:?}"
        );

        ensure_state_dir(&temp.directory).expect("restrict");

        let sid = win_security::current_sid().expect("sid");
        let after = win_security::entries(&temp.directory).expect("acl");
        assert_eq!(
            after,
            vec![win_security::Ace {
                ace_type: 0,
                sid: sid.clone(),
            }],
            "only the current user's SID may remain, found {after:?}"
        );

        // Protected must still mean usable: the DPAPI identity and the registry both
        // have to be created, reopened, and read by their owner.
        let bytes = br#"{"installation_id":"owner-only"}"#;
        let path = save_identity(&temp.directory, bytes).expect("save");
        assert!(path.is_file());
        assert_eq!(load_identity(&temp.directory).expect("load"), bytes);
        let registry =
            crate::trust::Registry::open(&temp.directory.join("trust.sqlite3")).expect("registry");
        assert_eq!(registry.revision().expect("revision"), 0);
        // The database file itself is owner-only too, after it exists.
        let database = win_security::entries(&temp.directory.join("trust.sqlite3")).expect("acl");
        assert!(
            database.iter().all(|ace| ace.sid == sid),
            "the registry file must not grant anyone else, found {database:?}"
        );
    }

    #[cfg(windows)]
    #[test]
    fn existing_state_with_a_loosened_acl_is_tightened_when_it_is_read() {
        let everyone = "S-1-1-0";
        let temp = Temp::new("existing");
        let bytes = br#"{"installation_id":"existing"}"#;
        let path = save_identity(&temp.directory, bytes).expect("save");
        // Someone widened the state after it was created, directory and file alike.
        for target in [&temp.directory, &path] {
            let added = std::process::Command::new("icacls")
                .arg(target)
                .arg("/grant")
                .arg(format!("*{everyone}:F"))
                .output()
                .expect("icacls");
            assert!(
                added.status.success(),
                "{}",
                String::from_utf8_lossy(&added.stderr)
            );
            assert!(win_security::entries(target)
                .expect("acl")
                .iter()
                .any(|ace| ace.sid == everyone));
        }

        assert_eq!(load_identity(&temp.directory).expect("load"), bytes);

        let sid = win_security::current_sid().expect("sid");
        for target in [&temp.directory, &path] {
            let entries = win_security::entries(target).expect("acl");
            assert!(
                entries.iter().all(|ace| ace.sid == sid),
                "{} must be owner-only after a read, found {entries:?}",
                target.display()
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn a_dacl_that_still_grants_someone_else_fails_closed() {
        let everyone = "S-1-1-0";
        let temp = Temp::new("failclosed");
        std::fs::create_dir_all(&temp.directory).expect("directory");
        ensure_state_dir(&temp.directory).expect("restrict");
        let sid = win_security::current_sid().expect("sid");
        win_security::verify_owner_only(&temp.directory, &sid).expect("clean after restrict");

        // Widening it again must be *detected*, not ignored: this is the branch that
        // makes "could not protect" fail loudly instead of silently continuing.
        let added = std::process::Command::new("icacls")
            .arg(&temp.directory)
            .arg("/grant")
            .arg(format!("*{everyone}:(OI)(CI)F"))
            .output()
            .expect("icacls");
        assert!(added.status.success());
        let error = win_security::verify_owner_only(&temp.directory, &sid)
            .expect_err("a foreign grant must be reported");
        assert!(error.contains(everyone), "the offending SID is named: {error}");
        assert!(error.contains("refusing"), "{error}");
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
