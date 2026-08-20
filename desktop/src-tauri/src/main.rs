// Buddy desktop shell.
//
// Thin by design: the UI is the existing React app and the logic is the
// existing FastAPI backend. This process exists to do three things a browser
// tab cannot.
//
// 1. Start the backend and know when it is ready. The backend imports pandas
//    and matplotlib, which takes seconds; a window shown before that renders
//    connection errors, so the splash stays up until /healthz answers.
//
// 2. Find out which port it chose. The backend binds an OS-assigned port
//    rather than a fixed 8000, and reports it on stdout as BUDDY_PORT=<n>.
//
// 3. Guarantee the backend dies with the window. An orphaned backend holds the
//    database and the SearXNG port, so the next launch fails in a way the user
//    cannot diagnose. Killing it in the window handler is not enough - Task
//    Manager, a crash, or a force-kill all skip that handler - so the child is
//    also placed in a job object the OS tears down with this process.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, WindowEvent};

/// Longest we wait for the backend to answer /healthz before giving up.
/// Generous because a cold first launch on a slow disk has to page in ~150 MB
/// of native libraries.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(90);

/// Backend executable name.
#[cfg(windows)]
const BACKEND_EXE: &str = "buddy-backend.exe";
#[cfg(not(windows))]
const BACKEND_EXE: &str = "buddy-backend";

/// Subdirectory holding the helper executables.
///
/// They live one level down so the install root contains only Buddy.exe and
/// its uninstaller. A folder full of loose helper binaries reads as an
/// extracted archive rather than an application.
const BIN_DIR: &str = "bin";

/// Locate the backend, preferring bin/ and falling back to alongside.
///
/// The fallback keeps a plain `cargo build` working, where the sidecar is
/// copied next to the shell rather than installed.
fn find_backend(exe_dir: &std::path::Path) -> std::path::PathBuf {
    let in_bin = exe_dir.join(BIN_DIR).join(BACKEND_EXE);
    if in_bin.exists() {
        return in_bin;
    }
    exe_dir.join(BACKEND_EXE)
}

/// Holds the child so it can be killed on exit.
struct Backend(Mutex<Option<Child>>);

/// Tie a child process's lifetime to this one at the OS level.
///
/// The window handler covers a normal close, but not a force-kill from Task
/// Manager and not a crash in this process - in both cases the handler never
/// runs and the backend is left holding the database and the SearXNG port.
///
/// A job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE closes that gap: the
/// handle is owned by this process, so when this process dies by any means the
/// kernel closes it and terminates everything in the job.
///
/// Deliberately best-effort. Failing to create a job is not a reason to refuse
/// to start - it only means falling back to the window handler, which covers
/// the ordinary case.
#[cfg(windows)]
fn attach_to_job(child: &Child) {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, SetInformationJobObject,
        JobObjectExtendedLimitInformation, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return;
        }

        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

        let ok = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const std::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if ok == 0 {
            return;
        }

        AssignProcessToJobObject(job, child.as_raw_handle() as _);

        // `job` is a raw HANDLE with no Drop, so letting it go out of scope
        // leaks it deliberately - exactly what is wanted here. The job must
        // stay open for this process's whole life, because closing it is the
        // event that kills the backend. The OS reclaims it when we exit.
    }
}

/// Kill the backend directly, for the ordinary window-close path.
///
/// The job object is the backstop for abnormal exits; this keeps a normal
/// close from waiting on process teardown to release the database.
fn kill_backend(state: &Backend) {
    if let Ok(mut guard) = state.0.lock() {
        if let Some(child) = guard.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        *guard = None;
    }
}

#[cfg(not(windows))]
fn attach_to_job(_child: &Child) {}

/// Block until the backend answers, or the deadline passes.
///
/// Publishing a port is not the same as serving on it: run_server prints
/// BUDDY_PORT before uvicorn binds, and importing pandas and matplotlib takes
/// seconds more. Showing the window on the port alone gives the user a
/// half-rendered app full of connection errors.
///
/// Deliberately a plain TCP connect rather than an HTTP request: it needs no
/// HTTP client in the shell, and "something is accepting connections on this
/// port" is exactly the condition being waited for.
fn wait_for_backend(port: u16, deadline: Instant) -> bool {
    use std::net::{Ipv4Addr, SocketAddr, TcpStream};

    let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    while Instant::now() < deadline {
        if TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    false
}

/// Spawn the backend and read the port it prints.
///
/// Returns the base URL. Reading stdout rather than scanning ports is what
/// makes this deterministic: the backend tells us, so there is no guessing and
/// no race with another application.
fn spawn_backend(exe: std::path::PathBuf) -> Result<(Child, String), String> {
    let mut command = Command::new(&exe);
    command
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        // Ask for an OS-assigned port; see run_server.py.
        .env("PORT", "0");

    // Without this the backend flashes a console window on Windows.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = command
        .spawn()
        .map_err(|e| format!("could not start the backend at {}: {e}", exe.display()))?;

    // Before anything else can fail: from here on the OS owns the guarantee
    // that this child does not outlive us.
    attach_to_job(&child);

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "backend produced no stdout".to_string())?;

    // Read on this thread until the port line arrives: nothing can proceed
    // without it, and the timeout below bounds the wait.
    let deadline = Instant::now() + STARTUP_TIMEOUT;
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();

    while Instant::now() < deadline {
        line.clear();
        match reader.read_line(&mut line) {
            // EOF: the backend exited without reporting a port.
            Ok(0) => break,
            Ok(_) => {
                if let Some(port) = line.trim().strip_prefix("BUDDY_PORT=") {
                    let base = format!("http://127.0.0.1:{port}");
                    // Drain the rest in the background; a full stdout pipe
                    // would block the backend's own logging forever.
                    std::thread::spawn(move || {
                        let mut sink = String::new();
                        while reader.read_line(&mut sink).unwrap_or(0) > 0 {
                            sink.clear();
                        }
                    });
                    return Ok((child, base));
                }
            }
            Err(e) => return Err(format!("could not read the backend's output: {e}")),
        }
    }

    let _ = child.kill();
    Err("the backend did not report a port in time".into())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            // Sidecar lives beside this executable in an installed build.
            let exe_dir = std::env::current_exe()
                .ok()
                .and_then(|p| p.parent().map(|d| d.to_path_buf()))
                .ok_or("could not locate the install directory")?;

            let (child, base) = spawn_backend(find_backend(&exe_dir))?;

            *app.state::<Backend>().0.lock().unwrap() = Some(child);

            // Point the webview at the backend, which serves the UI too, so the
            // app is same-origin and CORS never applies.
            // The port is in `base` as "http://127.0.0.1:<port>".
            let port: u16 = base
                .rsplit(':')
                .next()
                .and_then(|p| p.parse().ok())
                .ok_or("could not parse the backend port")?;

            if !wait_for_backend(port, Instant::now() + STARTUP_TIMEOUT) {
                return Err("the backend did not start listening in time".into());
            }

            let window = app.get_webview_window("main").ok_or("no main window")?;
            window.navigate(base.parse().map_err(|e| format!("bad backend url: {e}"))?)?;
            // Shown only now: the window starts hidden (see tauri.conf.json) so
            // the user never sees it render against a backend that is not up.
            window.show()?;
            window.set_focus()?;

            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window must take the backend with it.
            if let WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<Backend>() {
                    kill_backend(&state);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Buddy");
}
