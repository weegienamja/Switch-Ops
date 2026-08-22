// SwitchOps — Tauri shell.
//
// Spawns the bundled FastAPI sidecar (`switchops-backend`) on 127.0.0.1:8765,
// waits for `/health`, then shows the dashboard. Kills the sidecar on exit.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8765;

struct SidecarState(Mutex<Option<CommandChild>>);

#[cfg(windows)]
fn stop_sidecar(child: CommandChild) {
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let pid = child.pid().to_string();
    let tree_stopped = std::process::Command::new("taskkill")
        .args(["/PID", &pid, "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .status()
        .map(|status| status.success())
        .unwrap_or(false);

    if !tree_stopped {
        let _ = child.kill();
    }
}

#[cfg(not(windows))]
fn stop_sidecar(child: CommandChild) {
    let _ = child.kill();
}

async fn wait_for_health() -> bool {
    let url = format!("http://{BACKEND_HOST}:{BACKEND_PORT}/health");
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();
    for _ in 0..40 {
        if let Ok(resp) = client.get(&url).send().await {
            if resp.status().is_success() {
                return true;
            }
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    false
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let sidecar_state: Arc<SidecarState> = Arc::new(SidecarState(Mutex::new(None)));
    let sidecar_state_setup = sidecar_state.clone();
    let sidecar_state_exit = sidecar_state.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .setup(move |app| {
            let desktop_mode = std::env::var("SWITCHOPS_DESKTOP_MODE")
                .unwrap_or_else(|_| "real".to_string());
            let mock_mode = desktop_mode.eq_ignore_ascii_case("mock");
            let shell = app.shell();
            let sidecar = shell
                .sidecar("switchops-backend")
                .expect("failed to resolve switchops-backend sidecar")
                .args([
                    "--host",
                    BACKEND_HOST,
                    "--port",
                    &BACKEND_PORT.to_string(),
                ])
                .env(
                    "SWITCH_MOCK_MODE",
                    if mock_mode { "true" } else { "false" },
                );
            let (mut events, child) = sidecar
                .spawn()
                .expect("failed to spawn switchops-backend");
            *sidecar_state_setup.0.lock().unwrap() = Some(child);

            tauri::async_runtime::spawn(async move {
                while events.recv().await.is_some() {
                    // Drain stdout/stderr so a chatty sidecar cannot block.
                }
            });

            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let _healthy = wait_for_health().await;
                if let Some(window) = app_handle.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running SwitchOps")
        .run(move |_app, event| {
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                if let Some(child) = sidecar_state_exit.0.lock().unwrap().take() {
                    // PyInstaller one-file executables use a parent/child process
                    // pair on Windows, so terminate the complete sidecar tree.
                    stop_sidecar(child);
                }
            }
        });
}
