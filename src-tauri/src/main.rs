// Suppress console window in release builds on Windows
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // Must run before anything else: handles Velopack install/update/uninstall hook
    // invocations and auto-applies a downloaded-but-unapplied update on boot (that is
    // the "quit instead of pressing the button" path — spec §1).
    velopack::VelopackApp::build().run();
    mkw_tracker_lib::run()
}
