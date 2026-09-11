//! The SourceWork desktop shell.
//!
//! A Rust/Tauri window around the existing Python backend. The page is served
//! by the backend on loopback and is the *same* web UI a browser gets; the shell
//! adds what a browser tab cannot: a window that owns the process, a tray, and
//! native notifications. It calls no Tauri APIs from JavaScript, so the page
//! needs no bindings and still works in a browser.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use std::collections::HashMap;
use std::sync::Mutex;

use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_notification::NotificationExt;

/// The backend, once it is up. `None` while starting or after a failed start.
struct Shell {
    backend: Mutex<Option<backend::Backend>>,
}

fn main() {
    tauri::Builder::default()
        // A second launch raises the window that already exists rather than
        // starting a rival backend on more ports.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| focus(app)))
        .plugin(tauri_plugin_notification::init())
        .manage(Shell { backend: Mutex::new(None) })
        .setup(|app| {
            build_tray(app.handle())?;
            // The shell page first, so the wait for the backend has a window
            // instead of an empty desktop.
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::default())
                .title("SourceWork")
                .inner_size(1180.0, 820.0)
                .min_inner_size(880.0, 600.0)
                .build()?;
            let _ = window;

            let handle = app.handle().clone();
            std::thread::spawn(move || start_backend(handle));
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // Close means hide: the tray keeps the app (and any run) alive.
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running SourceWork");
}

/// Start the backend, then move the window from the shell page to the UI.
fn start_backend(app: AppHandle) {
    match backend::Backend::start(&app) {
        Ok(started) => {
            let url = format!("{}/?shell=1", started.base);
            if let Some(window) = app.get_webview_window("main") {
                match url.parse::<tauri::Url>() {
                    Ok(parsed) => {
                        if let Err(err) = window.navigate(parsed) {
                            eprintln!("could not open the UI: {err}");
                        }
                    }
                    Err(err) => eprintln!("bad backend URL {url:?}: {err}"),
                }
            }
            let base = started.base.clone();
            if let Some(shell) = app.try_state::<Shell>() {
                if let Ok(mut guard) = shell.backend.lock() {
                    *guard = Some(started);
                }
            }
            start_notifier(app, base);
        }
        Err(detail) => {
            let log = backend::log_path().display().to_string();
            if let Some(window) = app.get_webview_window("main") {
                let detail = serde_json::to_string(&detail).unwrap_or_else(|_| "\"\"".into());
                let log = serde_json::to_string(&log).unwrap_or_else(|_| "\"\"".into());
                let _ = window.eval(format!("window.showStartError({detail}, {log})"));
            }
        }
    }
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let show_item = MenuItem::with_id(app, "show", "Show SourceWork", true, None::<&str>)?;
    let new_item = MenuItem::with_id(app, "new", "New PRD", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&show_item, &new_item, &separator, &quit_item])?;

    TrayIconBuilder::with_id("main")
        .icon(Image::from_bytes(include_bytes!("../icons/icon.png"))?)
        .tooltip("SourceWork")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => focus(app),
            "new" => {
                focus(app);
                if let Some(window) = app.get_webview_window("main") {
                    // `#/new` is the app's own route; setting the hash routes
                    // client-side, so nothing typed is lost to a page reload.
                    let _ = window.eval("location.hash = '#/new'");
                }
            }
            "quit" => quit(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                focus(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

fn focus(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn quit(app: &AppHandle) {
    if let Some(shell) = app.try_state::<Shell>() {
        if let Ok(mut guard) = shell.backend.lock() {
            if let Some(mut running) = guard.take() {
                running.shutdown();
            }
        }
    }
    app.exit(0);
}

fn window_focused(app: &AppHandle) -> bool {
    app.get_webview_window("main")
        .and_then(|window| window.is_focused().ok())
        .unwrap_or(false)
}

fn in_flight(status: &str) -> bool {
    matches!(status, "running" | "queued")
}

/// Tell the user a run finished, but only when they are not looking at it -
/// the page already shows the result, and an app that notifies about what is on
/// screen is one people mute.
fn start_notifier(app: AppHandle, base: String) {
    std::thread::spawn(move || {
        let mut last: HashMap<String, String> = HashMap::new();
        loop {
            std::thread::sleep(std::time::Duration::from_secs(4));

            // A backend that dies after startup leaves a window pointing at a
            // port nothing answers on. Say so once, then stop watching.
            let died = match app.try_state::<Shell>() {
                Some(shell) => match shell.backend.lock() {
                    Ok(mut guard) => match guard.as_mut() {
                        Some(process) => process.exited(),
                        None => return, // quit is in progress
                    },
                    Err(_) => None,
                },
                None => None,
            };
            if let Some(status) = died {
                let message = serde_json::to_string(
                    &format!("The backend stopped unexpectedly ({status}). See {}", backend::log_path().display()),
                )
                .unwrap_or_default();
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.eval(format!("window.showStartError({message}, '')"));
                }
                return;
            }

            let Ok(runs) = backend::runs(&base) else {
                continue;
            };
            for (id, title, status) in runs {
                let terminal = matches!(status.as_str(), "ok" | "failed" | "cancelled");
                let was = last.get(&id).cloned();
                if was.as_deref().map(in_flight).unwrap_or(false) && terminal && !window_focused(&app)
                {
                    let (headline, body) = match status.as_str() {
                        "ok" => ("PRD ready", title.clone()),
                        "failed" => ("Run failed", title.clone()),
                        _ => ("Run cancelled", title.clone()),
                    };
                    let _ = app
                        .notification()
                        .builder()
                        .title(headline)
                        .body(body)
                        .show();
                }
                last.insert(id, status);
            }
        }
    });
}
