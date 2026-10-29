//! One owned instance of the existing mTLS host; no enrollment or policy logic.
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver};

pub struct Execution {
    child: Child,
    events: Receiver<String>,
    #[cfg(windows)]
    _job: Job,
}

impl Execution {
    pub fn start(root: &Path, workspace: &Path, listen: &str) -> Result<Self, String> {
        if !workspace.is_dir() { return Err("Choose an existing workspace folder.".into()); }
        let node=root.join("node").join(if cfg!(windows){"node.exe"}else{"node"});
        let server=root.join("tool-server/dist/src/server.js");
        if !node.is_file() || !server.is_file(){return Err("Bundled Node or the tool server is missing. Repair the executor package.".into());}
        let mut command=Command::new(super::link_binary(root)?);
        command.args(["serve","--listen",listen,"--workspace"])
            .arg(workspace).arg("--node-server").arg(server).arg("--advertise").arg("--activation-stdin")
            .env("ODESSY_LINK_NODE",node).stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::piped());
        #[cfg(windows)] {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        #[cfg(unix)] {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let mut child=command.spawn().map_err(|e|format!("Cannot start executor: {e}"))?;
        #[cfg(windows)]
        let job=match Job::assign(&child) {
            Ok(job)=>job,
            Err(e)=>{let _=child.kill();let _=child.wait();return Err(e);}
        };
        {
            use std::io::Write;
            let activation=child.stdin.take().ok_or("Executor activation channel unavailable")?
                .write_all(b"\n");
            if activation.is_err(){let _=child.kill();let _=child.wait();return Err("Executor activation failed".into());}
        }
        let stderr=child.stderr.take().ok_or("Executor error channel unavailable")?;
        let (sender,events)=mpsc::sync_channel(8);
        std::thread::spawn(move||{
            use std::io::Read;
            let mut stream=stderr;
            let mut buffer=[0u8;1024];
            let mut line=Vec::new();
            loop {
                match stream.read(&mut buffer) {
                    Ok(0)|Err(_)=>break,
                    Ok(n)=>for byte in &buffer[..n] {
                        if *byte==b'\n' {
                            let text=String::from_utf8_lossy(&line).to_string();
                            let _=sender.try_send(text);
                            line.clear();
                        } else if line.len()<4096 {line.push(*byte);}
                    }
                }
            }
        });
        Ok(Self{child,events,#[cfg(windows)] _job:job})
    }
    pub fn poll(&mut self)->Result<Option<String>,String>{
        // Preserve the listening event even when an advertisement follows it.
        let latest=self.events.try_recv().ok();
        if let Some(exit)=self.child.try_wait().map_err(|e|e.to_string())?{
            return Err(format!("Executor stopped ({exit}). {}",latest.unwrap_or_else(||"Check the workspace and port; another executor may already be listening.".into())));
        }
        Ok(latest)
    }
}
impl Drop for Execution {
    fn drop(&mut self){
        #[cfg(windows)] self._job.terminate();
        #[cfg(unix)] unsafe {libc::kill(-(self.child.id() as i32),libc::SIGKILL);}
        let _=self.child.kill();
        let _=self.child.wait();
    }
}

#[cfg(windows)]
struct Job(*mut std::ffi::c_void);
#[cfg(windows)]
impl Job {
    fn assign(child:&Child)->Result<Self,String>{
        use std::os::windows::io::AsRawHandle;
        #[repr(C)] #[derive(Default)]
        struct Basic {process_time:i64,job_time:i64,flags:u32,min_ws:usize,max_ws:usize,active:u32,affinity:usize,priority:u32,scheduling:u32}
        #[repr(C)] #[derive(Default)]
        struct Limits {basic:Basic,io:[u64;6],process_memory:usize,job_memory:usize,peak_process:usize,peak_job:usize}
        let handle=unsafe{CreateJobObjectW(std::ptr::null(),std::ptr::null())};
        if handle.is_null(){return Err("Cannot create owned executor process group.".into());}
        let job=Self(handle);
        let mut limits=Limits::default();limits.basic.flags=0x2000;
        if unsafe{SetInformationJobObject(handle,9,&limits as *const _ as *const _,std::mem::size_of::<Limits>() as u32)}==0 ||
            unsafe{AssignProcessToJobObject(handle,child.as_raw_handle())}==0{
            return Err("Cannot secure owned executor process cleanup; execution was not started.".into());
        }
        Ok(job)
    }
    fn terminate(&self){unsafe{TerminateJobObject(self.0,1);}}
}
#[cfg(windows)] impl Drop for Job{fn drop(&mut self){unsafe{CloseHandle(self.0);}}}
#[cfg(windows)]
#[link(name="kernel32")]
extern "system" {
    fn CreateJobObjectW(attributes:*const std::ffi::c_void,name:*const u16)->*mut std::ffi::c_void;
    fn SetInformationJobObject(job:*mut std::ffi::c_void,class:i32,info:*const std::ffi::c_void,length:u32)->i32;
    fn AssignProcessToJobObject(job:*mut std::ffi::c_void,process:*mut std::ffi::c_void)->i32;
    fn TerminateJobObject(job:*mut std::ffi::c_void,code:u32)->i32;
    fn CloseHandle(handle:*mut std::ffi::c_void)->i32;
}

#[cfg(test)] mod tests {
    #[test]fn absent_workspace_does_not_start_an_executor(){
        assert!(super::Execution::start(std::path::Path::new("missing-package"),std::path::Path::new("missing-workspace"),"127.0.0.1:0").is_err());
    }
}
