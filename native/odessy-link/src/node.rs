//! Owned Node execution server for one authenticated connection.
//!
//! The executable, environment and workspace come from the host's local
//! configuration only. A remote caller can never substitute a different binary,
//! environment or working directory, and the child is killed when its session
//! ends so an abandoned peer cannot leave a process behind.

use std::path::Path;
use tokio::process::{Child, ChildStdin, ChildStdout, Command};

/// Environment names passed through to the Node server. Nothing else is inherited,
/// so a caller cannot influence execution through ambient variables.
const ALLOWED_ENV: &[&str] = &[
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "LANG",
    "LC_ALL",
];

#[derive(Debug)]
pub struct NodeSession {
    child: Child,
    stdin: Option<ChildStdin>,
    stdout: Option<ChildStdout>,
}

impl NodeSession {
    /// Take the stdin handle so a caller can half-close it at peer EOF while the
    /// child is still allowed to finish answering.
    pub fn take_stdin(&mut self) -> Result<ChildStdin, String> {
        self.stdin.take().ok_or_else(|| "node stdin was already taken".to_string())
    }

    pub fn take_stdout(&mut self) -> Result<ChildStdout, String> {
        self.stdout.take().ok_or_else(|| "node stdout was already taken".to_string())
    }

    /// Terminate the child **and its descendants** where the platform supports it, then
    /// reap it. Cleanup that cannot be confirmed is reported instead of assumed.
    pub async fn shutdown(mut self) -> Result<(), String> {
        let pid = self.child.id();
        let tree = match pid {
            Some(pid) => kill_tree(pid),
            None => Ok(()),
        };
        let _ = self.child.start_kill();
        let reaped = match tokio::time::timeout(std::time::Duration::from_secs(5), self.child.wait()).await {
            Ok(Ok(_)) => Ok(()),
            Ok(Err(error)) => Err(format!("node server could not be reaped: {error}")),
            Err(_) => Err("node server did not exit within 5s of termination".into()),
        };
        // A tree that could not be terminated is the more serious failure, so it wins.
        tree?;
        reaped
    }
}

/// Best-effort whole-tree termination. Windows uses `taskkill /T`, which also reports
/// descendants it could not reach; Unix signals the process group created at spawn.
fn kill_tree(pid: u32) -> Result<(), String> {
    #[cfg(windows)]
    {
        let output = std::process::Command::new("taskkill")
            .arg("/T")
            .arg("/F")
            .arg("/PID")
            .arg(pid.to_string())
            .output()
            .map_err(|error| format!("cannot run taskkill for {pid}: {error}"))?;
        if output.status.success() {
            return Ok(());
        }
        let message = String::from_utf8_lossy(&output.stderr).trim().to_string();
        // "not found" means the tree is already gone, which is the desired end state.
        if message.to_lowercase().contains("not found") || message.contains("没有找到") {
            return Ok(());
        }
        Err(format!("taskkill could not confirm termination of {pid}: {message}"))
    }
    #[cfg(unix)]
    {
        // The group id is the child's pid because spawn set `process_group(0)`.
        let status = unsafe { libc::kill(-(pid as i32), libc::SIGKILL) };
        if status == 0 {
            return Ok(());
        }
        let error = std::io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::ESRCH) {
            return Ok(());
        }
        Err(format!("cannot signal process group {pid}: {error}"))
    }
}

/// Spawn the host-configured Node server inside the granted workspace.
pub fn spawn(node_script: &Path, workspace: &Path) -> Result<NodeSession, String> {
    if !node_script.is_file() {
        return Err(format!(
            "node server entry does not exist: {}",
            node_script.display()
        ));
    }
    if !workspace.is_dir() {
        return Err(format!(
            "granted workspace is not a directory: {}",
            workspace.display()
        ));
    }
    let node = std::env::var("ODESSY_LINK_NODE").unwrap_or_else(|_| "node".to_string());
    let mut command = Command::new(node);
    command
        .arg(node_script)
        .current_dir(workspace)
        .env_clear()
        .kill_on_drop(true)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        // stderr is inherited where the host redirects it; MCP traffic is stdout.
        .stderr(std::process::Stdio::inherit());
    for name in ALLOWED_ENV {
        if let Some(value) = std::env::var_os(name) {
            command.env(name, value);
        }
    }
    // Fixed by the host, never by the caller: a served Node server names its command
    // tool `laptop.terminal.run`, which is the name Odessy's split-mode contracts and
    // policy already know. A remote peer cannot change this.
    command.env("ODESSY_DEPLOYMENT", "split");
    #[cfg(unix)]
    {
        // A new process group, so termination can reach grandchildren too.
        // `process_group` is inherent on `Command` in current stable, so no
        // `unix::process::CommandExt` import is needed here.
        command.process_group(0);
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("cannot start node server: {error}"))?;
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "node server has no stdin".to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "node server has no stdout".to_string())?;
    Ok(NodeSession {
        child,
        stdin: Some(stdin),
        stdout: Some(stdout),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_missing_entry_or_workspace_is_refused_before_spawning() {
        let directory = std::env::temp_dir();
        let missing = directory.join("odessy-link-missing-node-server.js");
        let error = spawn(&missing, &directory).expect_err("entry must exist");
        assert!(error.contains("does not exist"), "{error}");

        let script = directory.join(format!("odessy-link-node-script-{}.js", std::process::id()));
        std::fs::write(&script, "process.exit(0)").expect("write script");
        let error = spawn(&script, &directory.join("odessy-link-missing-workspace"))
            .expect_err("workspace must exist");
        assert!(error.contains("not a directory"), "{error}");
        let _ = std::fs::remove_file(&script);
    }
}
