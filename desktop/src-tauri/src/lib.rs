// SwitchOps — Tauri shell.
//
// Spawns the bundled FastAPI sidecar (`switchops-backend`) on 127.0.0.1:8765,
// verifies that the backend answering that port is the sidecar it actually
// spawned, then shows the dashboard. Kills the sidecar on exit.
//
// The verification step is not incidental. A development backend left running
// from an earlier session also binds 127.0.0.1:8765. Previously the shell only
// waited for `/health` to return success, so a stale process would satisfy the
// check, the freshly spawned sidecar would fail to bind and exit unnoticed,
// and the desktop application would silently run against old code. The shell
// now passes a per-launch nonce to the sidecar through the environment and
// requires it back from `/api/system/provenance`.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::net::TcpListener;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use std::{env, path::PathBuf};

use serde::{Deserialize, Serialize};
use tauri::{Emitter, Manager, RunEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8765;
const SIDECAR_TOKEN_ENV: &str = "SWITCHOPS_SIDECAR_TOKEN";

/// Non-secret build facts the backend reports about itself.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct BackendProvenance {
    build_id: String,
    api_schema_version: u32,
    runtime_mode: String,
    started_at: String,
    #[serde(default)]
    sidecar_token: Option<String>,
    management_path_available: bool,
}

/// Outcome of trying to reach a backend we can prove is ours.
enum SidecarOutcome {
    Verified(BackendProvenance),
    /// Something already owned the port before we spawned anything.
    PortAlreadyBound,
    /// A backend answered but predates the provenance endpoint, so it cannot
    /// identify itself at all.
    BackendTooOld,
    /// A backend identified itself but did not return this launch's nonce.
    ForeignBackend(BackendProvenance),
    /// Nothing usable answered in time.
    NoResponse,
}

/// Safe subset of another backend's self-report, for display.
///
/// Deliberately excludes the nonce: echoing a token the dashboard never needs
/// would put a launch discriminator on screen for no reason.
#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct ObservedBackend {
    build_id: String,
    api_schema_version: u32,
    runtime_mode: String,
    started_at: String,
}

/// Typed payload for `switchops://backend-unverified`.
///
/// A typed reason keeps the dashboard from parsing prose to decide what to
/// render, and keeps operator wording in the frontend where it belongs.
#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct BackendUnverified {
    reason: &'static str,
    observed: Option<ObservedBackend>,
}

impl BackendUnverified {
    fn from_outcome(outcome: &SidecarOutcome) -> Option<Self> {
        match outcome {
            SidecarOutcome::Verified(_) => None,
            SidecarOutcome::PortAlreadyBound => Some(Self {
                reason: "PORT_ALREADY_BOUND",
                observed: None,
            }),
            SidecarOutcome::BackendTooOld => Some(Self {
                reason: "BACKEND_TOO_OLD",
                observed: None,
            }),
            SidecarOutcome::ForeignBackend(provenance) => Some(Self {
                reason: "FOREIGN_BACKEND",
                observed: Some(ObservedBackend {
                    build_id: provenance.build_id.clone(),
                    api_schema_version: provenance.api_schema_version,
                    runtime_mode: provenance.runtime_mode.clone(),
                    started_at: provenance.started_at.clone(),
                }),
            }),
            SidecarOutcome::NoResponse => Some(Self {
                reason: "NO_RESPONSE",
                observed: None,
            }),
        }
    }
}

/// Per-launch nonce used to tell our own sidecar apart from a stray listener.
///
/// This is a collision discriminator on loopback, not a security boundary: a
/// local process that can read our environment could already do far more. It
/// only has to be unique per launch.
fn launch_nonce() -> String {
    let mut hasher = DefaultHasher::new();
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
        .hash(&mut hasher);
    std::process::id().hash(&mut hasher);
    let stack_probe = 0u8;
    (&stack_probe as *const u8 as usize).hash(&mut hasher);
    let high = hasher.finish();
    high.hash(&mut hasher);
    format!("{:016x}{:016x}", high, hasher.finish())
}

/// True when nothing is currently listening on the backend port.
///
/// Checked before spawning so an occupied port is reported as exactly that,
/// rather than surfacing later as a confusing verification failure.
fn backend_port_is_free() -> bool {
    port_is_free(BACKEND_PORT)
}

/// Whether nothing is listening on one port of the loopback interface.
///
/// Split out from `backend_port_is_free` so it can be exercised against a port
/// the caller owns. A test that assumed 8765 was free would fail whenever a
/// SwitchOps desktop session happened to be running, which is exactly when
/// people run the tests.
fn port_is_free(port: u16) -> bool {
    TcpListener::bind((BACKEND_HOST, port)).is_ok()
}

struct SidecarState(Mutex<Option<CommandChild>>);

/// The verification verdict, retained for the lifetime of the process.
///
/// The event alone is not enough: it can fire before the webview has
/// registered a listener, and a missed event would leave the dashboard sitting
/// on "connecting" forever. The frontend therefore *asks* on mount, and the
/// event only carries a verdict that arrives later.
struct VerificationState(Mutex<Option<BackendUnverified>>);

fn ewps_export_dir_from(base: PathBuf) -> PathBuf {
    base.join("SwitchOps").join("data").join("ewps-exports")
}

#[tauri::command]
fn open_ewps_export_folder() -> Result<String, String> {
    #[cfg(windows)]
    {
        let local_app_data = env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .ok_or_else(|| "LOCALAPPDATA is unavailable.".to_string())?;
        let export_dir = ewps_export_dir_from(local_app_data);
        std::fs::create_dir_all(&export_dir)
            .map_err(|error| format!("Could not prepare the EWPS export folder: {error}"))?;
        std::process::Command::new("explorer.exe")
            .arg(&export_dir)
            .spawn()
            .map_err(|error| format!("Could not open the EWPS export folder: {error}"))?;
        return Ok(export_dir.to_string_lossy().into_owned());
    }
    #[cfg(not(windows))]
    {
        Err(
            "Opening the EWPS export folder is available in the Windows desktop application only."
                .to_string(),
        )
    }
}

/// Report whether this session could vouch for the backend on the port.
///
/// `None` means verified. Returning the verdict rather than only emitting it
/// makes the check race-free at any point after startup.
#[tauri::command]
fn backend_verification(
    state: tauri::State<'_, Arc<VerificationState>>,
) -> Option<BackendUnverified> {
    state.0.lock().unwrap().clone()
}

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

/// Poll the backend until it identifies itself with our nonce.
///
/// Returning provenance rather than a bare bool is what makes a stale backend
/// detectable: `/health` alone cannot distinguish our sidecar from a leftover
/// development process listening on the same loopback port.
async fn wait_for_verified_backend(expected_token: &str) -> SidecarOutcome {
    let url = format!("http://{BACKEND_HOST}:{BACKEND_PORT}/api/system/provenance");
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();

    let mut last_seen: Option<BackendProvenance> = None;

    for _ in 0..40 {
        if let Ok(resp) = client.get(&url).send().await {
            if resp.status().is_success() {
                if let Ok(body) = resp.text().await {
                    match serde_json::from_str::<BackendProvenance>(&body) {
                        Ok(provenance) => {
                            if provenance.sidecar_token.as_deref() == Some(expected_token) {
                                return SidecarOutcome::Verified(provenance);
                            }
                            last_seen = Some(provenance);
                        }
                        Err(error) => {
                            eprintln!("[switchops] unreadable provenance response: {error}");
                        }
                    }
                }
            } else if resp.status().as_u16() == 404 {
                // A backend predating the provenance endpoint. By definition
                // not the sidecar we just built.
                eprintln!(
                    "[switchops] backend on {BACKEND_HOST}:{BACKEND_PORT} has no provenance endpoint"
                );
                return SidecarOutcome::BackendTooOld;
            }
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }

    match last_seen {
        Some(provenance) => SidecarOutcome::ForeignBackend(provenance),
        None => SidecarOutcome::NoResponse,
    }
}

/// Developer-facing one-line summary for the terminal.
///
/// Operator wording lives in the frontend, which receives the typed reason.
/// Keeping prose out of the event stops the dashboard having to parse a
/// sentence to decide what to render.
fn startup_failure_log(outcome: &SidecarOutcome) -> Option<String> {
    match outcome {
        SidecarOutcome::Verified(_) => None,
        SidecarOutcome::PortAlreadyBound => Some(format!(
            "PORT_ALREADY_BOUND: another process already owns {BACKEND_HOST}:{BACKEND_PORT}; \
             no sidecar was started"
        )),
        SidecarOutcome::BackendTooOld => Some(format!(
            "BACKEND_TOO_OLD: the backend on {BACKEND_HOST}:{BACKEND_PORT} has no provenance \
             endpoint and cannot identify itself"
        )),
        SidecarOutcome::ForeignBackend(p) => Some(format!(
            "FOREIGN_BACKEND: {BACKEND_HOST}:{BACKEND_PORT} answered with build {} ({}, API \
             schema v{}, started {}) which is not this launch's sidecar",
            p.build_id, p.runtime_mode, p.api_schema_version, p.started_at
        )),
        SidecarOutcome::NoResponse => Some(format!(
            "NO_RESPONSE: no backend answered on {BACKEND_HOST}:{BACKEND_PORT} before the deadline"
        )),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let sidecar_state: Arc<SidecarState> = Arc::new(SidecarState(Mutex::new(None)));
    let verification_state: Arc<VerificationState> =
        Arc::new(VerificationState(Mutex::new(None)));
    let verification_state_setup = verification_state.clone();
    let sidecar_state_setup = sidecar_state.clone();
    let sidecar_state_exit = sidecar_state.clone();

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            open_ewps_export_folder,
            backend_verification
        ])
        .manage(verification_state.clone())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .setup(move |app| {
            let token = launch_nonce();

            // Check before spawning: if the port is already taken, the sidecar
            // cannot bind and would exit silently, leaving the other process
            // to answer every request the dashboard makes.
            let port_was_free = backend_port_is_free();
            if !port_was_free {
                eprintln!(
                    "[switchops] {BACKEND_HOST}:{BACKEND_PORT} is already in use; not starting a sidecar"
                );
            } else {
                let shell = app.shell();
                let sidecar = shell
                    .sidecar("switchops-backend")
                    .expect("failed to resolve switchops-backend sidecar")
                    .args(["--host", BACKEND_HOST, "--port", &BACKEND_PORT.to_string()])
                    .env("SWITCH_MOCK_MODE", "false")
                    .env(SIDECAR_TOKEN_ENV, &token);
                let (mut events, child) =
                    sidecar.spawn().expect("failed to spawn switchops-backend");
                *sidecar_state_setup.0.lock().unwrap() = Some(child);

                tauri::async_runtime::spawn(async move {
                    while let Some(event) = events.recv().await {
                        // Surface sidecar stderr. A backend that cannot bind
                        // used to fail invisibly here.
                        if let tauri_plugin_shell::process::CommandEvent::Stderr(line) = event {
                            let text = String::from_utf8_lossy(&line);
                            let text = text.trim();
                            if !text.is_empty() {
                                eprintln!("[switchops-backend] {text}");
                            }
                        }
                    }
                });
            }

            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let outcome = if port_was_free {
                    wait_for_verified_backend(&token).await
                } else {
                    SidecarOutcome::PortAlreadyBound
                };

                match &outcome {
                    SidecarOutcome::Verified(provenance) => {
                        eprintln!(
                            "[switchops] backend verified: build {} ({}, API schema v{}, management-path {})",
                            provenance.build_id,
                            provenance.runtime_mode,
                            provenance.api_schema_version,
                            if provenance.management_path_available {
                                "available"
                            } else {
                                "MISSING"
                            }
                        );
                    }
                    _ => {
                        if let Some(summary) = startup_failure_log(&outcome) {
                            eprintln!("[switchops] startup failed: {summary}");
                        }
                    }
                }

                let payload = BackendUnverified::from_outcome(&outcome);
                // Recorded before the window is shown, so a frontend that asks
                // immediately on mount always sees the verdict.
                *verification_state_setup.0.lock().unwrap() = payload.clone();

                if let Some(window) = app_handle.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                    if let Some(payload) = payload {
                        // Tell the dashboard rather than letting it query a
                        // backend this shell cannot vouch for. The dashboard
                        // treats this as a local runtime-integrity fault, not
                        // as a device or network diagnosis.
                        let _ = window.emit("switchops://backend-unverified", payload);
                    }
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

#[cfg(test)]
mod tests {
    use super::ewps_export_dir_from;
    use std::path::PathBuf;

    #[test]
    fn export_folder_is_fixed_below_local_app_data() {
        let path = ewps_export_dir_from(PathBuf::from(r"C:\Users\researcher\AppData\Local"));
        assert_eq!(
            path,
            PathBuf::from(r"C:\Users\researcher\AppData\Local\SwitchOps\data\ewps-exports")
        );
    }

    #[test]
    fn export_folder_command_accepts_no_user_path() {
        let signature: fn() -> Result<String, String> = super::open_ewps_export_folder;
        let _ = signature;
    }

    #[test]
    fn launch_nonce_differs_between_launches() {
        assert_ne!(super::launch_nonce(), super::launch_nonce());
        assert_eq!(super::launch_nonce().len(), 32);
    }

    #[test]
    fn an_occupied_port_is_detected_before_spawning() {
        use std::net::TcpListener;

        // Bind an ephemeral port so the result does not depend on whether a
        // SwitchOps session is currently using the real backend port.
        let squatter = TcpListener::bind((super::BACKEND_HOST, 0))
            .expect("test needs a loopback port");
        let port = squatter.local_addr().expect("bound address").port();

        assert!(
            !super::port_is_free(port),
            "a process already on the port must be detected"
        );
        drop(squatter);
        assert!(super::port_is_free(port), "the port is free once released");
    }

    #[test]
    fn the_production_check_targets_the_backend_port() {
        // The zero-argument form is what setup() calls; keep it wired to 8765.
        let _ = super::backend_port_is_free();
        assert_eq!(super::BACKEND_PORT, 8765);
    }

    fn sample_provenance() -> super::BackendProvenance {
        super::BackendProvenance {
            build_id: "abc123def456".to_string(),
            api_schema_version: 2,
            runtime_mode: "frozen-sidecar".to_string(),
            started_at: "2026-08-26T00:00:00Z".to_string(),
            sidecar_token: Some("nonce".to_string()),
            management_path_available: true,
        }
    }

    #[test]
    fn every_unverified_outcome_has_a_distinct_typed_reason() {
        // The original defect was a silent fallback onto a stale backend.
        // Each failure mode must be individually recognisable by the frontend.
        let outcomes = [
            super::SidecarOutcome::PortAlreadyBound,
            super::SidecarOutcome::BackendTooOld,
            super::SidecarOutcome::ForeignBackend(sample_provenance()),
            super::SidecarOutcome::NoResponse,
        ];
        let mut reasons = Vec::new();
        for outcome in &outcomes {
            let payload = super::BackendUnverified::from_outcome(outcome)
                .expect("an unverified backend must be reported");
            reasons.push(payload.reason);
            let summary =
                super::startup_failure_log(outcome).expect("failure must be logged");
            assert!(summary.contains("8765"), "log should name the port");
        }
        reasons.sort_unstable();
        assert_eq!(
            reasons,
            vec![
                "BACKEND_TOO_OLD",
                "FOREIGN_BACKEND",
                "NO_RESPONSE",
                "PORT_ALREADY_BOUND",
            ]
        );
    }

    #[test]
    fn a_verified_backend_reports_no_failure() {
        let verified = super::SidecarOutcome::Verified(sample_provenance());
        assert!(super::startup_failure_log(&verified).is_none());
        assert!(super::BackendUnverified::from_outcome(&verified).is_none());
    }

    #[test]
    fn a_foreign_backend_payload_never_carries_the_nonce() {
        let outcome = super::SidecarOutcome::ForeignBackend(sample_provenance());
        let payload =
            super::BackendUnverified::from_outcome(&outcome).expect("must be reported");
        let json = serde_json::to_string(&payload).expect("payload must serialise");
        assert!(!json.contains("nonce"), "the launch nonce must not be emitted");
        assert!(!json.contains("sidecarToken"));
        assert!(json.contains("abc123def456"), "build id is safe and useful");
        assert!(json.contains("FOREIGN_BACKEND"));
    }

    #[test]
    fn reasons_without_an_identified_backend_carry_no_observed_block() {
        for outcome in [
            super::SidecarOutcome::PortAlreadyBound,
            super::SidecarOutcome::BackendTooOld,
            super::SidecarOutcome::NoResponse,
        ] {
            let payload =
                super::BackendUnverified::from_outcome(&outcome).expect("must be reported");
            assert!(payload.observed.is_none());
        }
    }

    #[test]
    fn provenance_json_from_the_backend_deserializes() {
        // The minimized provenance contract the backend actually serves.
        let body = r#"{
            "buildId": "9eab64d9d4d1",
            "apiSchemaVersion": 2,
            "runtimeMode": "development",
            "startedAt": "2026-08-26T16:13:41.716422Z",
            "sidecarToken": "test-nonce-abc123",
            "managementPathAvailable": true
        }"#;
        let parsed: super::BackendProvenance =
            serde_json::from_str(body).expect("backend contract must parse");
        assert_eq!(parsed.sidecar_token.as_deref(), Some("test-nonce-abc123"));
        assert!(parsed.management_path_available);
        assert_eq!(parsed.api_schema_version, 2);
    }
}
