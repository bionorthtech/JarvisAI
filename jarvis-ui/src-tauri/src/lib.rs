use std::net::TcpStream;
use std::process::{Command, Stdio};
use std::time::Duration;

use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

fn backend_already_running() -> bool {
    TcpStream::connect_timeout(
        &"127.0.0.1:8000".parse().unwrap(),
        Duration::from_millis(300),
    )
    .is_ok()
}

fn spawn_backend() {
    if backend_already_running() {
        println!("[JARVIS] Backend already running on :8000, skipping spawn");
        return;
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/home/$USER".into());
    let python = format!("{home}/jarvis/venv/bin/python3");
    let script = format!("{home}/jarvis/main.py");
    let cwd    = format!("{home}/jarvis");
    match Command::new(&python)
        .arg(&script)
        .current_dir(&cwd)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(_)  => println!("[JARVIS] Python backend started on :8000"),
        Err(e) => eprintln!("[JARVIS] WARNING: could not start backend: {e}"),
    }
}

fn toggle_greeting(app: &AppHandle) {
    let _ = app.emit("jarvis://greet", ());
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.set_focus();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(|app| {
            spawn_backend();

            // ── System tray ──────────────────────────────────────────────
            let tray = TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("JARVIS")
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        toggle_greeting(app);
                    }
                })
                .build(app)?;

            app.manage(tray);

            // ── Global hotkey: Super+J ────────────────────────────────────
            let handle = app.handle().clone();
            let shortcut = Shortcut::new(Some(Modifiers::SUPER), Code::KeyJ);
            app.global_shortcut().on_shortcut(shortcut, move |_app, _shortcut, _event| {
                toggle_greeting(&handle);
            })?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error running Tauri app");
}
