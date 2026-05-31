import { invoke } from "@tauri-apps/api/core";

// Outbound command to the Python sidecar (Rust shell serializes to newline-delimited
// JSON on the sidecar's stdin). Fire-and-forget; failures are swallowed like before.
export function send(msg) {
  invoke("send_to_tracker", { message: JSON.stringify(msg) }).catch(() => {});
}
