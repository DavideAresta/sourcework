//! The backend's lifecycle: find a Python that has SourceWork, start it on an
//! ephemeral port, wait until it answers, and stop it again.
//!
//! The shell owns the process so a window close or the tray's Quit can end it;
//! the backend itself (`sourcework app`) is unchanged and still runs the mesh
//! and the web UI, browser-mode included.

use std::io::Write;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use serde_json::Value;
use tauri::path::BaseDirectory;
use tauri::{AppHandle, Manager};

/// How long the backend has to answer `/healthz` before the shell calls it
/// failed. Generous: first-run imports and the model probe are not instant.
const START_TIMEOUT: Duration = Duration::from_secs(90);

pub struct Backend {
    /// `http://127.0.0.1:<port>`, no trailing slash.
    pub base: String,
    child: Child,
}

/// Where the backend's stdout/stderr go, so a failed start has somewhere to
/// point the reader. The workspace log holds the application's own records;
/// this holds everything the child said before it could write one.
pub fn log_path() -> PathBuf {
    std::env::temp_dir().join("sourcework-desktop.log")
}

/// Append one of the shell's own lines to that log. The child writes its stdout
/// and stderr to the same file; these lines are what the shell knows and the
/// child cannot say - which interpreter was chosen, and why a start-up ended
/// before there was a child at all. Best-effort: a shell that cannot write its
/// log still starts the app.
fn note(log: Option<&std::fs::File>, detail: &str) {
    if let Some(mut file) = log {
        let _ = writeln!(file, "[sourcework-desktop] {detail}");
    }
}

fn which(program: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(program))
        .find(|candidate| candidate.is_file())
}

/// The interpreter of a virtualenv in the current directory or any parent.
///
/// This is the "run from a checkout" case: `cargo tauri dev` starts the shell
/// inside the repository, and the backend lives in `.venv`, not on PATH. The
/// installed app has no virtualenv to find and falls through to the rest.
fn venv_python() -> Option<PathBuf> {
    let mut dir = std::env::current_dir().ok()?;
    loop {
        for name in [".venv", "venv"] {
            let candidate = if cfg!(windows) {
                dir.join(name).join("Scripts").join("python.exe")
            } else {
                dir.join(name).join("bin").join("python")
            };
            if candidate.is_file() {
                return Some(candidate);
            }
        }
        if !dir.pop() {
            return None;
        }
    }
}

/// Whether `python` can actually import SourceWork. This is what keeps the
/// shell from picking an unrelated interpreter that happens to be first on PATH.
fn can_import(python: &Path) -> bool {
    python_command(python)
        .args(["-c", "import sourcework"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// A `Command` for an interpreter we chose ourselves, with the environment's
/// Python variables cleared.
///
/// The AppImage is why this exists. linuxdeploy's `AppRun` exports
/// `PYTHONHOME=$APPDIR/usr` and `PYTHONPATH=$APPDIR/usr/share/pyshared` for its
/// own bundled-Python case, and every child of the shell inherits them. They
/// point at a prefix that holds no standard library, so *any* interpreter we
/// launch - the runtime we ship, or a Python of the user's that has SourceWork
/// installed - dies in `init_fs_encoding` with `No module named 'encodings'`
/// before it can import anything. The shell always names the interpreter it
/// wants by full path, so an inherited `PYTHONHOME` is never the right prefix
/// for it.
fn python_command(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut command = Command::new(program);
    command.env_remove("PYTHONHOME").env_remove("PYTHONPATH");
    command
}

/// The interpreter shipped inside the installer, resolved from Tauri's resource
/// directory.
///
/// `None` in a development build, where the runtime was never fetched and the
/// checkout's `.venv` is the backend. Tauri puts `python/` next to the
/// executable on Windows, under `Contents/Resources` on macOS, and under
/// `/usr/lib/<exe>` (or the AppImage's `$APPDIR`) on Linux.
fn bundled_python(app: &AppHandle) -> Option<PathBuf> {
    let relative = if cfg!(windows) {
        "python/python.exe"
    } else {
        "python/bin/python3"
    };
    let path = app.path().resolve(relative, BaseDirectory::Resource).ok()?;
    path.is_file().then_some(path)
}

/// `(program, args)` that run SourceWork's CLI.
///
/// Order matters: an explicit `SOURCEWORK_BACKEND_CMD` wins, then the runtime
/// embedded in the installer, then a checkout's virtualenv, then a Python that
/// can actually `import sourcework`, then the console script. The bundled
/// runtime is preferred over the machine's because that is the whole point of
/// shipping one: an installed app must not depend on a Python the user has.
fn backend_command(app: &AppHandle) -> Result<(String, Vec<String>), String> {
    if let Ok(custom) = std::env::var("SOURCEWORK_BACKEND_CMD") {
        let mut parts = custom.split_whitespace().map(str::to_string);
        if let Some(program) = parts.next() {
            return Ok((program, parts.collect()));
        }
    }

    if let Some(python) = bundled_python(app).filter(|path| can_import(path)) {
        return Ok((
            python.to_string_lossy().into_owned(),
            vec!["-m".to_string(), "sourcework".to_string()],
        ));
    }

    if let Some(python) = venv_python() {
        if can_import(&python) {
            return Ok((
                python.to_string_lossy().into_owned(),
                vec!["-m".to_string(), "sourcework".to_string()],
            ));
        }
    }

    for (program, module_args) in [
        ("python3", vec!["-m", "sourcework"]),
        ("python", vec!["-m", "sourcework"]),
        ("py", vec!["-3", "-m", "sourcework"]),
    ] {
        if which(program).is_none() {
            continue;
        }
        if can_import(Path::new(program)) {
            return Ok((program.to_string(), module_args.into_iter().map(String::from).collect()));
        }
    }

    if which("sourcework").is_some() {
        return Ok(("sourcework".to_string(), Vec::new()));
    }

    Err("No Python with SourceWork installed was found. Install it with \
         `pip install sourcework`, or set SOURCEWORK_BACKEND_CMD to the command \
         that starts it."
        .to_string())
}

/// A port the OS says is free right now. Not a reservation: the backend binds
/// it a moment later, and the gap is the same small race every launcher has.
fn free_port() -> Result<u16, String> {
    let listener =
        TcpListener::bind(("127.0.0.1", 0)).map_err(|e| format!("no free port: {e}"))?;
    listener
        .local_addr()
        .map(|addr| addr.port())
        .map_err(|e| format!("no local address: {e}"))
}

fn agent(timeout: Duration) -> ureq::Agent {
    ureq::AgentBuilder::new().timeout(timeout).build()
}

impl Backend {
    pub fn start(app: &AppHandle) -> Result<Self, String> {
        // The log is opened before the first thing that can fail, not after.
        // Resolving the interpreter is itself a failure point - "no Python with
        // SourceWork installed was found" is the one a broken install hits -
        // and opening the file only once there was a command to run left that
        // error with nowhere to be read: the window pointed at a log that did
        // not exist, which looks like a shell that never tried.
        let log_path = log_path();
        let log = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .ok();
        let record = |detail: &str| note(log.as_ref(), detail);

        let port = free_port().inspect_err(|detail| record(detail))?;
        let (program, mut args) = backend_command(app).inspect_err(|detail| record(detail))?;
        let base = format!("http://127.0.0.1:{port}");

        let stdout = log.as_ref().and_then(|f| f.try_clone().ok());
        let stderr = log.as_ref().and_then(|f| f.try_clone().ok());

        args.push("app".to_string());
        args.push("--no-browser".to_string());
        args.push("--port".to_string());
        args.push(port.to_string());

        // Which interpreter won, in the log the reader is already being sent
        // to: the bundled runtime and a fallback fail in different ways, and
        // the message alone does not say which one was tried.
        record(&format!("starting `{program} {}`", args.join(" ")));

        let mut child = python_command(&program)
            .args(&args)
            .stdin(Stdio::null())
            .stdout(stdout.map(Stdio::from).unwrap_or_else(Stdio::null))
            .stderr(stderr.map(Stdio::from).unwrap_or_else(Stdio::null))
            .spawn()
            .map_err(|e| {
                let detail = format!("could not start `{program} {}`: {e}", args.join(" "));
                record(&detail);
                detail
            })?;

        let http = agent(Duration::from_millis(800));
        let deadline = Instant::now() + START_TIMEOUT;
        loop {
            if let Ok(Some(status)) = child.try_wait() {
                record(&format!("the backend exited during start-up ({status})"));
                return Err(format!(
                    "the backend exited during start-up ({status}). See {}",
                    log_path.display()
                ));
            }
            match http.get(&format!("{base}/healthz")).call() {
                Ok(response) if response.status() == 200 => break,
                _ => {}
            }
            if Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                record(&format!(
                    "the backend did not answer within {}s; killed it",
                    START_TIMEOUT.as_secs()
                ));
                return Err(format!(
                    "the backend did not answer within {}s. See {}",
                    START_TIMEOUT.as_secs(),
                    log_path.display()
                ));
            }
            std::thread::sleep(Duration::from_millis(250));
        }

        Ok(Self { base, child })
    }

    /// `Some(status)` once the backend has exited on its own, so the shell can
    /// say so instead of polling a port nothing answers on.
    pub fn exited(&mut self) -> Option<std::process::ExitStatus> {
        self.child.try_wait().ok().flatten()
    }

    /// Ask nicely, then insist. The endpoint stops the mesh and the UI; the
    /// kill is for when it does not, so no orphan holds the ports.
    pub fn shutdown(&mut self) {
        let _ = agent(Duration::from_secs(2))
            .post(&format!("{}/api/shutdown", self.base))
            .call();
        let deadline = Instant::now() + Duration::from_secs(6);
        while Instant::now() < deadline {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// Runs as `(id, title, status)`, newest first, for the notifier.
pub fn runs(base: &str) -> Result<Vec<(String, String, String)>, String> {
    let response = agent(Duration::from_secs(3))
        .get(&format!("{base}/api/runs"))
        .call()
        .map_err(|e| e.to_string())?;
    let body: Value = response.into_json().map_err(|e| e.to_string())?;
    let list = body.as_array().ok_or("runs is not a list")?;
    Ok(list
        .iter()
        .filter_map(|run| {
            let id = run.get("id")?.as_str()?.to_string();
            let title = run.get("title").and_then(Value::as_str).unwrap_or("").to_string();
            let status = run.get("status").and_then(Value::as_str).unwrap_or("").to_string();
            Some((id, title, status))
        })
        .collect())
}
