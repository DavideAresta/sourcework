//! The backend's lifecycle: find a Python that has SourceWork, start it on an
//! ephemeral port, wait until it answers, and stop it again.
//!
//! The shell owns the process so a window close or the tray's Quit can end it;
//! the backend itself (`sourcework app`) is unchanged and still runs the mesh
//! and the web UI, browser-mode included.

use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use serde_json::Value;

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

/// `(program, args)` that run SourceWork's CLI.
///
/// Order matters: an explicit `SOURCEWORK_BACKEND_CMD` wins, then a checkout's
/// virtualenv, then a Python that can actually `import sourcework`, then the
/// console script. The import test is what keeps the shell from picking an
/// unrelated interpreter that happens to be first on PATH.
fn backend_command() -> Result<(String, Vec<String>), String> {
    if let Ok(custom) = std::env::var("SOURCEWORK_BACKEND_CMD") {
        let mut parts = custom.split_whitespace().map(str::to_string);
        if let Some(program) = parts.next() {
            return Ok((program, parts.collect()));
        }
    }

    if let Some(python) = venv_python() {
        let importable = Command::new(&python)
            .args(["-c", "import sourcework"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        if importable {
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
        let importable = Command::new(program)
            .args(["-c", "import sourcework"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        if importable {
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
    pub fn start() -> Result<Self, String> {
        let port = free_port()?;
        let (program, mut args) = backend_command()?;
        let base = format!("http://127.0.0.1:{port}");
        let log_path = log_path();

        let log = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .ok();
        let stderr = log.as_ref().and_then(|f| f.try_clone().ok());

        args.push("app".to_string());
        args.push("--no-browser".to_string());
        args.push("--port".to_string());
        args.push(port.to_string());

        let mut child = Command::new(&program)
            .args(&args)
            .stdin(Stdio::null())
            .stdout(log.map(Stdio::from).unwrap_or_else(Stdio::null))
            .stderr(stderr.map(Stdio::from).unwrap_or_else(Stdio::null))
            .spawn()
            .map_err(|e| format!("could not start `{program} {}`: {e}", args.join(" ")))?;

        let http = agent(Duration::from_millis(800));
        let deadline = Instant::now() + START_TIMEOUT;
        loop {
            if let Ok(Some(status)) = child.try_wait() {
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
