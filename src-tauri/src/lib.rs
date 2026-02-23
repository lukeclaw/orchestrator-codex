use std::net::TcpListener;
use std::sync::Mutex;
use std::time::Duration;
use tauri::Manager;

const SERVER_PORT: u16 = 8093;

/// Shared state to hold the sidecar child process handle.
struct SidecarState {
    child: Mutex<Option<std::process::Child>>,
}

/// Poll the health endpoint until the Python server is ready.
fn wait_for_server(url: &str, timeout: Duration) -> bool {
    let start = std::time::Instant::now();
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();

    while start.elapsed() < timeout {
        if let Ok(resp) = client.get(url).send() {
            if resp.status().is_success() {
                return true;
            }
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

/// Resolve the sidecar binary path.
/// The sidecar is a PyInstaller onedir bundle (no extraction needed at runtime).
///
/// In a macOS .app bundle: Contents/Resources/binaries/orchestrator-server-sidecar/orchestrator-server
/// In dev: src-tauri/binaries/orchestrator-server-sidecar/orchestrator-server
fn resolve_sidecar_path() -> std::path::PathBuf {
    let exe_path = std::env::current_exe().expect("failed to get current exe path");
    let exe_dir = exe_path.parent().expect("exe has no parent directory");

    const SIDECAR_DIR: &str = "binaries/orchestrator-server-sidecar";
    const SIDECAR_BIN: &str = "orchestrator-server";

    // Bundled mode: Contents/MacOS/../Resources/<sidecar_dir>/<binary>
    let resources_dir = exe_dir.join("..").join("Resources");
    let bundled = resources_dir.join(SIDECAR_DIR).join(SIDECAR_BIN);
    if bundled.exists() {
        ensure_executable(&bundled);
        return bundled;
    }

    // Dev mode: exe is in src-tauri/target/{debug,release}/
    let dev_path = exe_dir
        .join("..")
        .join("..")
        .join("..")
        .join("src-tauri")
        .join(SIDECAR_DIR)
        .join(SIDECAR_BIN);
    if dev_path.exists() {
        return dev_path;
    }

    // Return the bundled path even if it doesn't exist (error will surface at spawn)
    bundled
}

/// Ensure a file has executable permission (macOS/Linux).
fn ensure_executable(path: &std::path::Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(meta) = std::fs::metadata(path) {
            let mode = meta.permissions().mode();
            if mode & 0o111 == 0 {
                let mut perms = meta.permissions();
                perms.set_mode(mode | 0o755);
                let _ = std::fs::set_permissions(path, perms);
            }
        }
    }
}

/// Check whether a TCP port is available to bind on localhost.
fn is_port_available(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

/// Use `lsof` + `ps` to find the PID and process name occupying a TCP port.
fn find_port_occupier(port: u16) -> Option<(u32, String)> {
    let output = std::process::Command::new("lsof")
        .args([
            &format!("-iTCP:{}", port),
            "-sTCP:LISTEN",
            "-t",
        ])
        .output()
        .ok()?;

    let pid_str = String::from_utf8_lossy(&output.stdout)
        .lines()
        .next()?
        .trim()
        .to_string();
    let pid: u32 = pid_str.parse().ok()?;

    let name_output = std::process::Command::new("ps")
        .args(["-p", &pid_str, "-o", "comm="])
        .output()
        .ok()?;
    let name = String::from_utf8_lossy(&name_output.stdout)
        .trim()
        .to_string();

    Some((pid, if name.is_empty() { "<unknown>".to_string() } else { name }))
}

/// Send SIGTERM to a process; if it's still alive after 3 seconds, SIGKILL.
fn kill_process(pid: u32) -> bool {
    use std::process::Command;

    // SIGTERM
    let _ = Command::new("kill")
        .args(["-15", &pid.to_string()])
        .status();

    // Wait up to 3 seconds for the process to exit
    for _ in 0..30 {
        std::thread::sleep(Duration::from_millis(100));
        let status = Command::new("kill")
            .args(["-0", &pid.to_string()])
            .status();
        if let Ok(s) = status {
            if !s.success() {
                return true; // process is gone
            }
        }
    }

    // SIGKILL as last resort
    let _ = Command::new("kill")
        .args(["-9", &pid.to_string()])
        .status();
    std::thread::sleep(Duration::from_millis(500));

    // Verify it's dead
    let status = Command::new("kill")
        .args(["-0", &pid.to_string()])
        .status();
    matches!(status, Ok(s) if !s.success())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(SidecarState {
            child: Mutex::new(None),
        })
        .setup(|app| {
            // In dev mode (cargo tauri dev), don't spawn the sidecar.
            // The user runs the Python server separately with --reload:
            //   uv run uvicorn orchestrator.api.app:create_app --factory --reload --port 8093
            if cfg!(debug_assertions) {
                eprintln!("[tauri] Dev mode — skipping sidecar (run Python server manually)");
                return Ok(());
            }

            // --- Port conflict detection ---
            if !is_port_available(SERVER_PORT) {
                eprintln!("[tauri] Port {} is already in use", SERVER_PORT);

                if let Some((pid, name)) = find_port_occupier(SERVER_PORT) {
                    eprintln!("[tauri] Port occupied by {} (PID {})", name, pid);

                    let confirmed = rfd::MessageDialog::new()
                        .set_title("Port Conflict")
                        .set_description(format!(
                            "Port {} is already in use by \"{}\" (PID {}).\n\n\
                             This usually means a previous instance didn't shut down cleanly.\n\n\
                             Kill it and start fresh?",
                            SERVER_PORT, name, pid
                        ))
                        .set_level(rfd::MessageLevel::Warning)
                        .set_buttons(rfd::MessageButtons::OkCancelCustom(
                            "Kill & Restart".into(),
                            "Quit".into(),
                        ))
                        .show();

                    if confirmed == rfd::MessageDialogResult::Ok
                        || confirmed == rfd::MessageDialogResult::Custom("Kill & Restart".into())
                    {
                        eprintln!("[tauri] User chose to kill PID {}", pid);
                        if kill_process(pid) {
                            // Give the OS a moment to release the port
                            std::thread::sleep(Duration::from_millis(500));
                            if !is_port_available(SERVER_PORT) {
                                eprintln!("[tauri] Port still occupied after killing process");
                                rfd::MessageDialog::new()
                                    .set_title("Port Still in Use")
                                    .set_description(format!(
                                        "Port {} is still occupied after killing PID {}.\n\
                                         Please free the port manually and restart the app.",
                                        SERVER_PORT, pid
                                    ))
                                    .set_level(rfd::MessageLevel::Error)
                                    .set_buttons(rfd::MessageButtons::Ok)
                                    .show();
                                std::process::exit(1);
                            }
                        } else {
                            eprintln!("[tauri] Failed to kill PID {}", pid);
                            rfd::MessageDialog::new()
                                .set_title("Failed to Kill Process")
                                .set_description(format!(
                                    "Could not terminate PID {}.\n\
                                     Try running: kill -9 {}\n\n\
                                     Then restart the app.",
                                    pid, pid
                                ))
                                .set_level(rfd::MessageLevel::Error)
                                .set_buttons(rfd::MessageButtons::Ok)
                                .show();
                            std::process::exit(1);
                        }
                    } else {
                        eprintln!("[tauri] User declined to kill the existing process");
                        std::process::exit(0);
                    }
                } else {
                    // Can't identify the process
                    eprintln!("[tauri] Could not identify process on port {}", SERVER_PORT);
                    rfd::MessageDialog::new()
                        .set_title("Port Conflict")
                        .set_description(format!(
                            "Port {} is already in use but the occupying process could not be identified.\n\n\
                             Try running: lsof -iTCP:{} -sTCP:LISTEN\n\
                             Then kill the process manually and restart the app.",
                            SERVER_PORT, SERVER_PORT
                        ))
                        .set_level(rfd::MessageLevel::Error)
                        .set_buttons(rfd::MessageButtons::Ok)
                        .show();
                    std::process::exit(1);
                }
                eprintln!("[tauri] Port {} is now available", SERVER_PORT);
            }

            let app_handle = app.handle().clone();
            let sidecar_path = resolve_sidecar_path();
            eprintln!("[tauri] Sidecar path: {}", sidecar_path.display());

            // Spawn the Python sidecar as a child process
            let child = match std::process::Command::new(&sidecar_path)
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .spawn()
            {
                Ok(child) => child,
                Err(e) => {
                    eprintln!("[tauri] Failed to spawn sidecar: {}", e);
                    let handle = app_handle.clone();
                    let sidecar_path_str = sidecar_path.display().to_string();
                    std::thread::spawn(move || {
                        std::thread::sleep(Duration::from_millis(500));
                        if let Some(window) = handle.get_webview_window("main") {
                            let msg = format!(
                                "document.body.innerHTML = '<div style=\"padding:2em;font-family:system-ui\"><h1>Startup Error</h1><p>Failed to start the orchestrator server.</p><p>Path: {}</p><p>Error: {}</p></div>';",
                                sidecar_path_str, e
                            );
                            let _ = window.eval(&msg);
                        }
                    });
                    return Ok(());
                }
            };

            eprintln!("[tauri] Sidecar spawned (PID: {})", child.id());

            // Store the child handle for cleanup
            let state = app_handle.state::<SidecarState>();
            *state.child.lock().unwrap() = Some(child);

            // Log sidecar stderr in a background thread
            {
                let state_ref = app_handle.state::<SidecarState>();
                let mut guard = state_ref.child.lock().unwrap();
                if let Some(ref mut child) = *guard {
                    if let Some(stderr) = child.stderr.take() {
                        let handle_for_log = app_handle.clone();
                        std::thread::spawn(move || {
                            use std::io::BufRead;
                            let reader = std::io::BufReader::new(stderr);
                            for line in reader.lines() {
                                match line {
                                    Ok(text) => {
                                        eprintln!("[sidecar] {}", text);
                                        if text.contains("ERROR: tmux is not installed") {
                                            let _ = handle_for_log
                                                .get_webview_window("main")
                                                .map(|w| {
                                                    let _ = w.eval(
                                                        "document.body.innerHTML = '<div style=\"padding:2em;font-family:system-ui\"><h1>tmux Required</h1><p>Orchestrator needs tmux to manage sessions.</p><p>Install it with: <code>brew install tmux</code></p><p>Then restart the app.</p></div>';"
                                                    );
                                                });
                                        }
                                    }
                                    Err(_) => break,
                                }
                            }
                        });
                    }
                }
            }

            // Wait for the server to be ready, then navigate the webview
            let handle_for_nav = app_handle.clone();
            std::thread::spawn(move || {
                let health_url = format!("http://127.0.0.1:{}/api/health", SERVER_PORT);
                let ready = wait_for_server(&health_url, Duration::from_secs(30));

                if ready {
                    eprintln!("[tauri] Server is ready, navigating to dashboard");
                    if let Some(window) = handle_for_nav.get_webview_window("main") {
                        let _ = window.eval(
                            &format!("window.location.replace('http://127.0.0.1:{}');", SERVER_PORT)
                        );
                    }
                } else {
                    eprintln!("[tauri] Server failed to start within timeout");
                    if let Some(window) = handle_for_nav.get_webview_window("main") {
                        let _ = window.eval(
                            "document.body.innerHTML = '<div style=\"padding:2em;font-family:system-ui\"><h1>Startup Error</h1><p>The orchestrator server failed to start. Check the logs at:<br><code>~/Library/Application Support/Orchestrator/orchestrator.log</code></p></div>';"
                        );
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            match event {
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    // Hide the window instead of destroying it (standard macOS behavior).
                    // The app stays in the dock; Cmd+Q still fully quits.
                    api.prevent_close();
                    let _ = window.hide();
                    eprintln!("[tauri] Window hidden (close button)");
                }
                tauri::WindowEvent::Destroyed => {
                    // Kill the sidecar when the app actually quits
                    let state = window.state::<SidecarState>();
                    let mut guard = state.child.lock().unwrap();
                    if let Some(mut child) = guard.take() {
                        let _ = child.kill();
                        let _ = child.wait();
                        eprintln!("[tauri] Sidecar process killed");
                    }
                }
                _ => {}
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            match event {
                tauri::RunEvent::Reopen { has_visible_windows, .. } => {
                    // Re-show the window when the dock icon is clicked
                    if !has_visible_windows {
                        if let Some(window) = app_handle.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                            eprintln!("[tauri] Window restored from dock");
                        }
                    }
                }
                tauri::RunEvent::Exit => {
                    // Guaranteed cleanup: kill the sidecar on any app exit
                    let state = app_handle.state::<SidecarState>();
                    let mut guard = state.child.lock().unwrap();
                    if let Some(mut child) = guard.take() {
                        let _ = child.kill();
                        let _ = child.wait();
                        eprintln!("[tauri] Sidecar process killed (RunEvent::Exit)");
                    }
                }
                _ => {}
            }
        });
}
