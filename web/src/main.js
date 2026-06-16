import "../../src/theme.css";
import "./app.css";
import { serverUrl } from "../../src/lib/syncSettings.js";
import { startPresence } from "./presenceClient.js";
import App from "./App.svelte";

// The season server origin. Override for local dev via VITE_API_BASE (e.g. http://localhost:8787).
const API_BASE = import.meta.env.VITE_API_BASE || "https://api.thekartoff.com";
serverUrl.set(API_BASE);          // PlayerPanel/empty-state copy reads this as "configured"
startPresence(API_BASE);          // read-only presence socket -> shared stores

const app = new App({ target: document.getElementById("app") });
export default app;
