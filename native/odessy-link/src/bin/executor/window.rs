//! The executor's single window: workspace, pairing actions, code and status.
//!
//! The window owns every child process it starts. Closing it asks whether to keep
//! serving in the background or quit; either way the children stop with the window
//! that owns them. Nothing here reads or writes trust state: it renders what
//! `odessy-link` reports and forwards the operator's explicit decisions.

use super::{link_binary, package_root, run_component, Status};
use std::ffi::c_void;
use std::process::{Child, Stdio};

type Handle = *mut c_void;
type Hwnd = Handle;
type Hinstance = Handle;
type Hicon = Handle;
type Hcursor = Handle;
type Hbrush = Handle;
type Hmenu = Handle;
type Wparam = usize;
type Lparam = isize;
type Lresult = isize;

const WS_OVERLAPPEDWINDOW: u32 = 0x00CF_0000;
const WS_CHILD: u32 = 0x4000_0000;
const WS_VISIBLE: u32 = 0x1000_0000;
const WS_TABSTOP: u32 = 0x0001_0000;
const WS_BORDER: u32 = 0x0080_0000;
const BS_PUSHBUTTON: u32 = 0;
const SS_LEFT: u32 = 0;
const SW_HIDE: i32 = 0;
const SW_SHOW: i32 = 5;
const SW_RESTORE: i32 = 9;
const WM_CREATE: u32 = 1;
const WM_NCCREATE: u32 = 0x0081;
const WM_DESTROY: u32 = 2;
const WM_CLOSE: u32 = 0x0010;
const WM_COMMAND: u32 = 0x0111;
const WM_TIMER: u32 = 0x0113;
const WM_SETFONT: u32 = 0x0030;
const MB_YESNOCANCEL: u32 = 0x0000_0003;
const MB_ICONQUESTION: u32 = 0x0000_0020;
const MB_ICONERROR: u32 = 0x0000_0010;
const IDYES: i32 = 6;
const IDNO: i32 = 7;
const ERROR_ALREADY_EXISTS: u32 = 183;
const BIF_RETURNONLYFSDIRS: u32 = 0x0001;
const BIF_NEWDIALOGSTYLE: u32 = 0x0040;
const DEFAULT_GUI_FONT: i32 = 17;
/// `COLOR_WINDOW + 1`: the way a window class names a system colour brush.
const COLOR_WINDOW_BRUSH: usize = 6;
const IDC_ARROW: usize = 32512;
const GWLP_USERDATA: i32 = -21;

const ID_WORKSPACE: usize = 101;
const ID_BROWSE: usize = 102;
const ID_ALLOW: usize = 103;
const ID_APPROVE: usize = 104;
const ID_CANCEL: usize = 105;
const ID_CODE: usize = 106;
const ID_HEADLINE: usize = 107;
const ID_NOTE: usize = 108;
const ID_START: usize=109;
const ID_STOP: usize=110;

#[repr(C)]
struct WndClassW {
    style: u32,
    lpfn_wnd_proc: Option<extern "system" fn(Hwnd, u32, Wparam, Lparam) -> Lresult>,
    cb_cls_extra: i32,
    cb_wnd_extra: i32,
    h_instance: Hinstance,
    h_icon: Hicon,
    h_cursor: Hcursor,
    hbr_background: Hbrush,
    lpsz_menu_name: *const u16,
    lpsz_class_name: *const u16,
}

#[repr(C)]
struct BrowseInfoW {
    hwnd_owner: Hwnd,
    pidl_root: *mut c_void,
    psz_display_name: *mut u16,
    lpsz_title: *const u16,
    ul_flags: u32,
    lpfn: Option<extern "system" fn(Hwnd, u32, Lparam, Lparam) -> i32>,
    l_param: Lparam,
    i_image: i32,
}

#[repr(C)]
struct Msg {
    hwnd: Hwnd,
    message: u32,
    w_param: Wparam,
    l_param: Lparam,
    time: u32,
    point_x: i32,
    point_y: i32,
}

#[repr(C)]
struct CreateStructW {
    params: *mut c_void,
    instance: Hinstance,
    menu: Hmenu,
    parent: Hwnd,
    cy: i32,
    cx: i32,
    y: i32,
    x: i32,
    style: i32,
    name: *const u16,
    class: *const u16,
    ex_style: u32,
}

#[link(name = "user32")]
extern "system" {
    fn RegisterClassW(class: *const WndClassW) -> u16;
    fn CreateWindowExW(
        ex_style: u32,
        class_name: *const u16,
        window_name: *const u16,
        style: u32,
        x: i32,
        y: i32,
        width: i32,
        height: i32,
        parent: Hwnd,
        menu: Hmenu,
        instance: Hinstance,
        param: *mut c_void,
    ) -> Hwnd;
    fn DefWindowProcW(hwnd: Hwnd, message: u32, w: Wparam, l: Lparam) -> Lresult;
    fn GetMessageW(message: *mut Msg, hwnd: Hwnd, min: u32, max: u32) -> i32;
    fn TranslateMessage(message: *const Msg) -> i32;
    fn DispatchMessageW(message: *const Msg) -> Lresult;
    fn PostQuitMessage(code: i32);
    fn DestroyWindow(hwnd: Hwnd) -> i32;
    fn ShowWindow(hwnd: Hwnd, command: i32) -> i32;
    fn UpdateWindow(hwnd: Hwnd) -> i32;
    fn SetWindowTextW(hwnd: Hwnd, text: *const u16) -> i32;
    fn SendMessageW(hwnd: Hwnd, message: u32, w: Wparam, l: Lparam) -> Lresult;
    fn MessageBoxW(hwnd: Hwnd, text: *const u16, caption: *const u16, kind: u32) -> i32;
    fn SetTimer(hwnd: Hwnd, id: usize, milliseconds: u32, callback: *mut c_void) -> usize;
    fn KillTimer(hwnd: Hwnd, id: usize) -> i32;
    fn FindWindowW(class_name: *const u16, window_name: *const u16) -> Hwnd;
    fn SetForegroundWindow(hwnd: Hwnd) -> i32;
    fn EnableWindow(hwnd: Hwnd, enable: i32) -> i32;
    fn GetDlgItem(hwnd: Hwnd, id: i32) -> Hwnd;
    fn GetSystemMetrics(index: i32) -> i32;
    fn LoadCursorW(instance: Hinstance, name: *const u16) -> Hcursor;
    #[cfg(target_pointer_width = "64")]
    fn SetWindowLongPtrW(hwnd: Hwnd, index: i32, value: isize) -> isize;
    #[cfg(target_pointer_width = "32")]
    fn SetWindowLongPtrW(hwnd: Hwnd, index: i32, value: i32) -> i32;
    #[cfg(target_pointer_width = "64")]
    fn GetWindowLongPtrW(hwnd: Hwnd, index: i32) -> isize;
    #[cfg(target_pointer_width = "32")]
    fn GetWindowLongPtrW(hwnd: Hwnd, index: i32) -> i32;
}

#[link(name = "kernel32")]
extern "system" {
    fn GetModuleHandleW(name: *const u16) -> Hinstance;
    fn CreateMutexW(attributes: *mut c_void, owner: i32, name: *const u16) -> Handle;
    fn GetLastError() -> u32;
    fn CloseHandle(handle: Handle) -> i32;
}

#[link(name = "shell32")]
extern "system" {
    fn SHBrowseForFolderW(info: *const BrowseInfoW) -> *mut c_void;
    fn SHGetPathFromIDListW(id_list: *const c_void, path: *mut u16) -> i32;
}

#[link(name = "ole32")]
extern "system" {
    fn OleInitialize(reserved: *mut c_void) -> i32;
    fn CoTaskMemFree(block: *mut c_void);
}

#[link(name = "gdi32")]
extern "system" {
    fn GetStockObject(index: i32) -> Handle;
}

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}

fn set_text(hwnd: Hwnd, text: &str) {
    let value = wide(text);
    unsafe { SetWindowTextW(hwnd, value.as_ptr()) };
}

fn message_box(owner: Hwnd, text: &str, kind: u32) -> i32 {
    let text = wide(text);
    let caption = wide("Odessy Executor");
    unsafe { MessageBoxW(owner, text.as_ptr(), caption.as_ptr(), kind) }
}

/// Everything the window owns. It lives exactly as long as the window and is dropped
/// after the message loop ends.
struct State {
    root: std::path::PathBuf,
    workspace: String,
    status: Status,
    listener: Option<Child>,
    /// The mTLS relay serving the approved workspace, owned by this window.
    serve: Option<super::execution::Execution>,
    /// A job is running off the window thread; further requests wait for its answer.
    busy: bool,
    worker: Option<std::sync::mpsc::Receiver<(Job, Result<Status, String>)>>,
    last_poll: std::time::Instant,
    /// The session this window opened. Cancellation names it, so a stale window cannot
    /// cancel some newer session and call that success.
    owned_session: Option<String>,
    note: String,
}

impl State {
    /// The command for a job, or the reason it cannot run yet.
    fn arguments(&self, job: Job) -> Result<Vec<String>, String> {
        match job {
            Job::Refresh => Ok(vec!["pairing".into(), "status".into()]),
            Job::Allow => {
                if self.workspace.is_empty() {
                    return Err("Choose the folder this computer should share first.".into());
                }
                Ok(vec![
                    "pairing".into(),
                    "begin".into(),
                    "--workspace".into(),
                    self.workspace.clone(),
                ])
            }
            Job::Approve => self.approve_arguments().ok_or_else(|| {
                if self.workspace.is_empty() {
                    "Choose the folder this computer shares before approving access.".to_string()
                } else {
                    "There is no authenticated request to approve yet.".to_string()
                }
            }),
            Job::Cancel => {
                // Name the session this window opened, so a stale window cannot cancel
                // a newer one and report that as success.
                let mut arguments = vec!["pairing".into(), "cancel".into()];
                let session=self.owned_session.clone().ok_or("This window owns no pairing session. Inspect the current request before acting.")?;
                arguments.push("--session-id".into());
                arguments.push(session);
                Ok(arguments)
            }
        }
    }

    fn stop_listener(&mut self) {
        if let Some(mut listener) = self.listener.take() {
            let _ = listener.kill();
            let _ = listener.wait();
        }
    }

    /// The listener is a child of this window; it stops when the window stops.
    fn start_listener(&mut self, link: &std::path::Path) {
        if self.listener.is_some() {
            return;
        }
        let child = super::component_command(link)
            .args([
                "pairing",
                "listen",
                "--listen",
                "0.0.0.0:0",
                "--advertise",
                "--seconds",
                "120",
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
        match child {
            Ok(child) => self.listener = Some(child),
            Err(error) => {
                self.note = format!(
                    "Cannot start the pairing listener: {error}. Check that the firewall allows \
                     this program on a private network; do not disable it."
                )
            }
        }
    }

    /// Approve exactly the request on screen, for exactly the chosen folder.
    fn approve_arguments(&self) -> Option<Vec<String>> {
        if self.status.state != "awaiting_approval"
            || self.status.session_id.is_empty()
            || self.status.review_token.is_empty()
            || self.workspace.is_empty()
        {
            return None;
        }
        Some(vec![
            "pairing".into(),
            "confirm".into(),
            "--session-id".into(),
            self.status.session_id.clone(),
            "--review-token".into(),
            self.status.review_token.clone(),
            "--workspace".into(),
            self.workspace.clone(),
        ])
    }

    /// Apply a completed job. Always on the window thread.
    fn apply(&mut self, job: Job, result: Result<Status, String>) {
        self.busy = false;
        match result {
            Ok(status) => {
                if job == Job::Allow && !status.session_id.is_empty() {
                    self.owned_session = Some(status.session_id.clone());
                }
                let discoverable = status.state == "discoverable";
                let supported = status.supported;
                let ready = status.state == "ready";
                self.status = status;
                if job != Job::Refresh {self.note.clear();}
                if discoverable && job == Job::Allow {
                    if let Ok(link) = link_binary(&self.root) {
                        self.start_listener(&link);
                    }
                }
                if !supported && job == Job::Allow {
                    self.note = "This build cannot open a pairing window: the protocol review is not complete.".into();
                }
                if ready && job==Job::Approve {
                    self.start_serve();
                }
            }
            Err(error) => self.note = error,
        }
    }

    /// Start the existing mTLS relay as a child of this window.
    ///
    /// The workspace and the Node entry come from verified package paths; the user
    /// never types a path or a command. A failure here is reported, never retried into
    /// a state that looks connected.
    fn start_serve(&mut self) {
        if self.serve.is_some() || self.workspace.is_empty() {
            return;
        }
        match super::execution::Execution::start(&self.root, std::path::Path::new(&self.workspace), "0.0.0.0:7443") {
            Ok(host)=>{self.serve=Some(host);self.note="Starting executor; waiting for the local listener. No remote connection is verified.".into();}
            Err(error)=>self.note=error,
        }
    }
    fn stop_serve(&mut self) { self.serve=None; }

    fn choose_workspace(&mut self, owner: Hwnd) {
        let title = wide("Choose the folder this computer shares");
        let info = BrowseInfoW {
            hwnd_owner: owner,
            pidl_root: std::ptr::null_mut(),
            psz_display_name: std::ptr::null_mut(),
            lpsz_title: title.as_ptr(),
            ul_flags: BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE,
            lpfn: None,
            l_param: 0,
            i_image: 0,
        };
        let id_list = unsafe { SHBrowseForFolderW(&info) };
        if id_list.is_null() {
            return;
        }
        let mut buffer = vec![0u16; 1024];
        let ok = unsafe { SHGetPathFromIDListW(id_list, buffer.as_mut_ptr()) } != 0;
        unsafe { CoTaskMemFree(id_list) };
        if !ok {
            self.note = "That folder could not be read. Choose another one.".into();
            return;
        }
        let length = buffer.iter().position(|unit| *unit == 0).unwrap_or(0);
        self.workspace = String::from_utf16_lossy(&buffer[..length]);
        self.note.clear();
    }

    fn stop_children(&mut self) {
        self.stop_listener();
        self.stop_serve();
    }

    /// Buttons are enabled from the observed state, so the window cannot offer an
    /// action the component would refuse.
    fn enable_controls(&self, hwnd: Hwnd) {
        let open = matches!(
            self.status.state.as_str(),
            "discoverable" | "authenticating" | "awaiting_approval" | "enrolling"
        );
        unsafe {
            if let Some(control) = non_null(GetDlgItem(hwnd, ID_ALLOW as i32)) {
                EnableWindow(control, i32::from(!self.busy && !open));
            }
            if let Some(control) = non_null(GetDlgItem(hwnd, ID_APPROVE as i32)) {
                EnableWindow(control, i32::from(!self.busy && self.status.state == "awaiting_approval"));
            }
            if let Some(control) = non_null(GetDlgItem(hwnd, ID_CANCEL as i32)) {
                EnableWindow(control, i32::from(!self.busy && open && self.owned_session.is_some()));
            }
            EnableWindow(GetDlgItem(hwnd, ID_START as i32),i32::from(!self.busy && !open && !self.workspace.is_empty() && self.serve.is_none()));
            EnableWindow(GetDlgItem(hwnd, ID_STOP as i32),i32::from(self.serve.is_some()));
            EnableWindow(GetDlgItem(hwnd, ID_BROWSE as i32), i32::from(!self.busy && !open && self.serve.is_none()));
        }
    }

    fn paint(&self, hwnd: Hwnd) {
        set_text_control(hwnd, ID_HEADLINE, &self.status.headline());
        let code = if self.status.code.is_empty() {
            String::new()
        } else {
            format!("Code: {}", self.status.code)
        };
        set_text_control(hwnd, ID_CODE, &code);
        let workspace = if self.workspace.is_empty() {
            "No folder chosen".to_string()
        } else {
            self.workspace.clone()
        };
        set_text_control(hwnd, ID_WORKSPACE, &workspace);
        set_text_control(hwnd, ID_NOTE, &self.note);
        self.enable_controls(hwnd);
    }
}

/// Work the window asks for. Every command runs on a worker thread so the window keeps
/// painting and responding while the component answers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Job {
    Refresh,
    Allow,
    Approve,
    Cancel,
}

/// A bounded channel owns results; workers never retain native window handles.
fn start_job(hwnd: Hwnd, job: Job) {
    let state = state_of(hwnd);
    if state.busy {
        return;
    }
    let arguments = match state.arguments(job) {
        Ok(arguments) => arguments,
        Err(note) => {
            state.note = note;
            state.paint(hwnd);
            return;
        }
    };
    if job == Job::Cancel {
        // Stop what this window started before the durable session is closed, so a
        // reconnect cannot reach a listener that is still accepting.
        state.stop_children();
    }
    state.busy = true;
    state.paint(hwnd);
    let root = state.root.clone();
    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    state.worker = Some(receiver);
    std::thread::spawn(move || {
        let timeout = if job == Job::Approve { 30 } else { 15 };
        let result = link_binary(&root)
            .and_then(|link| {
                let borrowed: Vec<&str> = arguments.iter().map(String::as_str).collect();
                run_component(&link, &borrowed, std::time::Duration::from_secs(timeout))
            })
            .and_then(|output| Status::parse(&output));
        let _ = sender.send((job, result));
    });
}

fn non_null(handle: Hwnd) -> Option<Hwnd> {
    if handle.is_null() {
        None
    } else {
        Some(handle)
    }
}

fn set_text_control(hwnd: Hwnd, id: usize, text: &str) {
    if let Some(control) = non_null(unsafe { GetDlgItem(hwnd, id as i32) }) {
        set_text(control, text);
    }
}

fn state_of(hwnd: Hwnd) -> &'static mut State {
    let pointer = unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) } as *mut State;
    unsafe { &mut *pointer }
}

extern "system" fn wnd_proc(hwnd: Hwnd, message: u32, w: Wparam, l: Lparam) -> Lresult {
    match message {
        WM_NCCREATE => {
            let creation = unsafe { &*(l as *const CreateStructW) };
            unsafe { SetWindowLongPtrW(hwnd, GWLP_USERDATA, creation.params as isize); }
            1
        }
        WM_CREATE => {
            // The state pointer arrives as the creation parameter: GWLP_USERDATA
            // cannot be written before the window exists.
            unsafe {
                let font = GetStockObject(DEFAULT_GUI_FONT);
                let left = 18;
                let mut y = 18;
                let create = |class: &str,
                                  text: &str,
                                  style: u32,
                                  x: i32,
                                  y: i32,
                                  width: i32,
                                  height: i32,
                                  id: usize| {
                    let class = wide(class);
                    let text = wide(text);
                    let handle = CreateWindowExW(
                        0,
                        class.as_ptr(),
                        text.as_ptr(),
                        WS_CHILD | WS_VISIBLE | style,
                        x,
                        y,
                        width,
                        height,
                        hwnd,
                        id as Hmenu,
                        GetModuleHandleW(std::ptr::null()),
                        std::ptr::null_mut(),
                    );
                    SendMessageW(handle, WM_SETFONT, font as Wparam, 1);
                    handle
                };
                create("STATIC", "Folder this computer shares", SS_LEFT, left, y, 380, 18, 0);
                y += 22;
                create("STATIC", "No folder chosen", WS_BORDER, left, y, 380, 22, ID_WORKSPACE);
                create("BUTTON", "Browse…", BS_PUSHBUTTON | WS_TABSTOP, 410, y - 2, 116, 26, ID_BROWSE);
                y += 40;
                create("BUTTON", "Allow pairing", BS_PUSHBUTTON | WS_TABSTOP, left, y, 140, 30, ID_ALLOW);
                create("BUTTON", "Approve request", BS_PUSHBUTTON | WS_TABSTOP, left + 150, y, 140, 30, ID_APPROVE);
                create("BUTTON", "Cancel", BS_PUSHBUTTON | WS_TABSTOP, left + 300, y, 100, 30, ID_CANCEL);
                y += 38;
                create("BUTTON", "Start executor", BS_PUSHBUTTON | WS_TABSTOP, left, y, 140, 30, ID_START);
                create("BUTTON", "Stop executor", BS_PUSHBUTTON | WS_TABSTOP, left+150, y, 140, 30, ID_STOP);
                y += 38;
                create("STATIC", "", SS_LEFT, left, y, 380, 24, ID_CODE);
                y += 30;
                create("STATIC", "Starting…", SS_LEFT, left, y, 500, 66, ID_HEADLINE);
                y += 74;
                create(
                    "STATIC",
                    "No certificate, fingerprint or command line is needed. The other device types \
                     the code shown above; you approve the request here.",
                    SS_LEFT,
                    left,
                    y,
                    500,
                    44,
                    0,
                );
                y += 50;
                create("STATIC", "", SS_LEFT, left, y, 500, 40, ID_NOTE);
                SetTimer(hwnd, 1, 100, std::ptr::null_mut());
            }
            0
        }
        WM_TIMER => {
            // Polling is a subprocess too, so it also runs off the window thread and
            // waits for any job already in flight.
            let state=state_of(hwnd);
            if let Some(host)=&mut state.serve {
                match host.poll() {
                    Ok(Some(message))=>{
                        state.note=if message.starts_with("listening on ") {
                            "Executor listening over mTLS. Only enrolled peers with a workspace grant are admitted. Remote connection health remains unknown.".into()
                        } else {message};state.paint(hwnd);
                    }
                    Err(error)=>{state.note=error;state.serve=None;state.paint(hwnd);}
                    _=>{}
                }
            }
            if let Some(receiver)=&state.worker {
                if let Ok((job,result))=receiver.try_recv() {
                    state.worker=None;
                    state.apply(job,result);
                    state.paint(hwnd);
                }
            }
            if !state.busy && state.last_poll.elapsed()>=std::time::Duration::from_secs(2) {
                state.last_poll=std::time::Instant::now();
                start_job(hwnd, Job::Refresh);
            }
            0
        }
        WM_COMMAND => {
            let identifier = w & 0xffff;
            match identifier {
                ID_BROWSE => {state_of(hwnd).choose_workspace(hwnd);state_of(hwnd).paint(hwnd);},
                ID_START => {state_of(hwnd).start_serve();state_of(hwnd).paint(hwnd);},
                ID_STOP => {state_of(hwnd).stop_serve();state_of(hwnd).note="Executor stopped. Saved identities and grants were retained.".into();state_of(hwnd).paint(hwnd);},
                ID_ALLOW => start_job(hwnd, Job::Allow),
                ID_APPROVE => start_job(hwnd, Job::Approve),
                ID_CANCEL => start_job(hwnd, Job::Cancel),
                _ => {}
            }
            0
        }
        WM_CLOSE => {
            if state_of(hwnd).busy {
                state_of(hwnd).note="Finishing the current request. Its outcome will be shown before closing; completed grants are not undone.".into();
                state_of(hwnd).paint(hwnd);
                return 0;
            }
            let answer = message_box(
                hwnd,
                "Keep this computer available for pairing in the background?\n\n\
                 Yes: keep running (window hidden)\n\
                 No: stop the executor now\n\
                 Cancel: return to the window",
                MB_YESNOCANCEL | MB_ICONQUESTION,
            );
            match answer {
                IDYES => {
                    unsafe {
                        ShowWindow(hwnd, SW_HIDE);
                    }
                }
                IDNO => {
                    unsafe {
                        DestroyWindow(hwnd);
                    }
                }
                _ => {}
            }
            0
        }
        WM_DESTROY => {
            let state = state_of(hwnd);
            state.stop_children();
            unsafe {
                KillTimer(hwnd, 1);
                PostQuitMessage(0);
            }
            0
        }
        _ => unsafe { DefWindowProcW(hwnd, message, w, l) },
    }
}

/// Create the window, prove every control exists, then tear it down.
///
/// This is an automated check of the window/control wiring. It is **not** evidence
/// that a person exercised the flow: no button is clicked and no dialog is shown.
pub fn smoke() -> i32 {
    let class_name = wide("OdessyExecutorWindow");
    let root = match package_root() {
        Ok(root) => root,
        Err(error) => {
            eprintln!("executor: {error}");
            return 1;
        }
    };
    unsafe {
        OleInitialize(std::ptr::null_mut());
        let instance = GetModuleHandleW(std::ptr::null());
        let class = WndClassW {
            style: 0,
            lpfn_wnd_proc: Some(wnd_proc),
            cb_cls_extra: 0,
            cb_wnd_extra: 0,
            h_instance: instance,
            h_icon: std::ptr::null_mut(),
            h_cursor: LoadCursorW(std::ptr::null_mut(), IDC_ARROW as *const u16),
            hbr_background: COLOR_WINDOW_BRUSH as Hbrush,
            lpsz_menu_name: std::ptr::null(),
            lpsz_class_name: class_name.as_ptr(),
        };
        if RegisterClassW(&class) == 0 {
            eprintln!("executor: the window class could not be registered");
            return 1;
        }
        let state = Box::into_raw(Box::new(State {
            root,
            workspace: String::new(),
            status: Status::default(),
            listener: None,
            serve: None,
            busy: false,
            worker: None,
            last_poll: std::time::Instant::now(),
            owned_session: None,
            note: String::new(),
        }));
        let title = wide("Odessy Executor (smoke test)");
        let window = CreateWindowExW(
            0,
            class_name.as_ptr(),
            title.as_ptr(),
            WS_OVERLAPPEDWINDOW,
            0,
            0,
            560,
            510,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            instance,
            state as *mut c_void,
        );
        if window.is_null() {
            eprintln!("executor: the window could not be created");
            drop(Box::from_raw(state));
            return 1;
        }
        // This must be the user pointer, not the CREATESTRUCTW address supplied
        // by Windows to WM_CREATE. Do not dispatch any action on invalid state.
        if GetWindowLongPtrW(window, GWLP_USERDATA) != state as isize {
            eprintln!("executor: window state was not bound to its owner");
            SetWindowLongPtrW(window, GWLP_USERDATA, state as isize);
            DestroyWindow(window);
            drop(Box::from_raw(state));
            return 1;
        }
        let mut missing = Vec::new();
        for (id, name) in [
            (ID_WORKSPACE, "workspace"),
            (ID_BROWSE, "browse"),
            (ID_ALLOW, "allow"),
            (ID_APPROVE, "approve"),
            (ID_CANCEL, "cancel"),
            (ID_CODE, "code"),
            (ID_HEADLINE, "headline"),
            (ID_NOTE, "note"),
            (ID_START, "start executor"),
            (ID_STOP, "stop executor"),
        ] {
            if GetDlgItem(window, id as i32).is_null() {
                missing.push(name);
            }
        }
        // Give the timer one turn so the status line is written from the component.
        state_of(window).paint(window);
        DestroyWindow(window);
        drop(Box::from_raw(state));
        if !missing.is_empty() {
            eprintln!("executor: missing controls: {missing:?}");
            return 1;
        }
        println!("window: 10 controls created, owner binding checked, status painted, window destroyed");
    }
    0
}

pub fn run() -> i32 {
    let class_name = wide("OdessyExecutorWindow");
    let single = wide("Local\\OdessyExecutorSingleInstance");
    unsafe {
        let mutex = CreateMutexW(std::ptr::null_mut(), 1, single.as_ptr());
        if GetLastError() == ERROR_ALREADY_EXISTS {
            // One instance only: focus the running window instead of starting a second
            // one that could race on the same pairing state.
            let existing = FindWindowW(class_name.as_ptr(), std::ptr::null());
            if !existing.is_null() {
                ShowWindow(existing, SW_RESTORE);
                SetForegroundWindow(existing);
            }
            if !mutex.is_null() {
                CloseHandle(mutex);
            }
            return 0;
        }
        OleInitialize(std::ptr::null_mut());
        let root = match package_root() {
            Ok(root) => root,
            Err(error) => {
                message_box(std::ptr::null_mut(), &error, MB_ICONERROR);
                return 1;
            }
        };
        // A packaged build refuses to start from an unverified directory: an
        // unverified binary pair must not look like a working executor. A development
        // build has no manifest, so it starts and says so in the window.
        let mut startup_note = String::new();
        if let Err(error) = super::verify_package(&root) {
            if !cfg!(debug_assertions) {
                let note = format!(
                    "{error}\n\nThis package has not been verified, so the executor will not start. \
                     Reinstall it from the published package."
                );
                message_box(std::ptr::null_mut(), &note, MB_ICONERROR);
                return 1;
            }
            startup_note = format!("Development build: {error}");
        }
        let instance = GetModuleHandleW(std::ptr::null());
        let class = WndClassW {
            style: 0,
            lpfn_wnd_proc: Some(wnd_proc),
            cb_cls_extra: 0,
            cb_wnd_extra: 0,
            h_instance: instance,
            h_icon: std::ptr::null_mut(),
            h_cursor: LoadCursorW(std::ptr::null_mut(), IDC_ARROW as *const u16),
            hbr_background: COLOR_WINDOW_BRUSH as Hbrush,
            lpsz_menu_name: std::ptr::null(),
            lpsz_class_name: class_name.as_ptr(),
        };
        if RegisterClassW(&class) == 0 {
            message_box(
                std::ptr::null_mut(),
                "The executor window could not be created.",
                MB_ICONERROR,
            );
            return 1;
        }
        let state = Box::into_raw(Box::new(State {
            root,
            workspace: String::new(),
            status: Status::default(),
            listener: None,
            serve: None,
            busy: false,
            worker: None,
            last_poll: std::time::Instant::now(),
            owned_session: None,
            note: startup_note,
        }));
        let title = wide("Odessy Executor");
        let screen_width = GetSystemMetrics(0).max(640);
        let screen_height = GetSystemMetrics(1).max(480);
        let window = CreateWindowExW(
            0,
            class_name.as_ptr(),
            title.as_ptr(),
            WS_OVERLAPPEDWINDOW,
            (screen_width - 560) / 2,
            (screen_height - 510) / 2,
            560,
            510,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            instance,
            state as *mut c_void,
        );
        if window.is_null() {
            drop(Box::from_raw(state));
            return 1;
        }
        ShowWindow(window, SW_SHOW);
        UpdateWindow(window);
        let mut message = std::mem::zeroed::<Msg>();
        while GetMessageW(&mut message, std::ptr::null_mut(), 0, 0) > 0 {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        drop(Box::from_raw(state));
        if !mutex.is_null() {
            CloseHandle(mutex);
        }
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn refresh_never_adopts_another_session_or_restarts_a_stopped_executor(){
        let mut state=State{
            root:std::env::temp_dir(),workspace:"previous-workspace".into(),
            status:Status::default(),listener:None,serve:None,busy:false,
            worker:None,last_poll:std::time::Instant::now(),
            owned_session:Some("owned-session".into()),note:String::new(),
        };
        let response=Status::parse(r#"{"schema_version":1,"supported":true,"state":"ready","session_id":"another-session"}"#).unwrap();
        state.apply(Job::Refresh,Ok(response));
        assert_eq!(state.owned_session.as_deref(),Some("owned-session"));
        assert!(state.serve.is_none());
        assert!(state.note.is_empty(),"refresh tried to start an executor");
        let cancel=state.arguments(Job::Cancel).unwrap();
        assert_eq!(cancel.last().map(String::as_str),Some("owned-session"));
    }
}
