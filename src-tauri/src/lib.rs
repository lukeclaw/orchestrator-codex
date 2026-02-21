use std::sync::Mutex;
use std::time::Duration;
use tauri::Manager;

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
/// In a macOS .app bundle: Contents/MacOS/orchestrator-server (next to the main binary)
/// In dev: src-tauri/binaries/orchestrator-server-{triple}
fn resolve_sidecar_path() -> std::path::PathBuf {
    let exe_path = std::env::current_exe().expect("failed to get current exe path");
    let exe_dir = exe_path.parent().expect("exe has no parent directory");

    // Try the bundled name first (no triple suffix)
    let bundled = exe_dir.join("orchestrator-server");
    if bundled.exists() {
        return bundled;
    }

    // Try with target triple suffix (dev mode or some bundler versions)
    let triple = format!("{}-{}", std::env::consts::ARCH, if cfg!(target_os = "macos") {
        "apple-darwin"
    } else if cfg!(target_os = "linux") {
        "unknown-linux-gnu"
    } else {
        "unknown"
    });
    let with_triple = exe_dir.join(format!("orchestrator-server-{}", triple));
    if with_triple.exists() {
        return with_triple;
    }

    // Fallback: check in src-tauri/binaries/ (cargo tauri dev mode)
    let dev_path = exe_dir
        .join("..")
        .join("..")
        .join("..")
        .join("src-tauri")
        .join("binaries")
        .join(format!("orchestrator-server-{}", triple));
    if dev_path.exists() {
        return dev_path;
    }

    // Return the bundled path even if it doesn't exist (error will surface at spawn)
    bundled
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
                                                        "document.body.innerHTML = '<div style=\"padding:2em;font-family:system-ui\"><h1>tmux Required</h1><p>Claude Orchestrator needs tmux to manage sessions.</p><p>Install it with: <code>brew install tmux</code></p><p>Then restart the app.</p></div>';"
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
                let health_url = "http://127.0.0.1:8093/api/health";
                let ready = wait_for_server(health_url, Duration::from_secs(30));

                if ready {
                    eprintln!("[tauri] Server is ready, navigating to dashboard");
                    if let Some(window) = handle_for_nav.get_webview_window("main") {
                        let _ = window.eval(
                            "window.location.replace('http://127.0.0.1:8093');"
                        );
                    }
                } else {
                    eprintln!("[tauri] Server failed to start within timeout");
                    if let Some(window) = handle_for_nav.get_webview_window("main") {
                        let _ = window.eval(
                            "document.body.innerHTML = '<div style=\"padding:2em;font-family:system-ui\"><h1>Startup Error</h1><p>The orchestrator server failed to start. Check the logs at:<br><code>~/Library/Application Support/ClaudeOrchestrator/orchestrator.log</code></p></div>';"
                        );
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // Kill the sidecar when the app window is closed
            if let tauri::WindowEvent::Destroyed = event {
                let state = window.state::<SidecarState>();
                let mut guard = state.child.lock().unwrap();
                if let Some(mut child) = guard.take() {
                    let _ = child.kill();
                    let _ = child.wait();
                    eprintln!("[tauri] Sidecar process killed");
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
