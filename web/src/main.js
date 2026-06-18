import "../../src/theme.css";
import "./app.css";
import { startPresence } from "./presenceClient.js";
import App from "./App.svelte";
import { API_BASE } from "./lib/api.js";

startPresence(API_BASE);          // read-only presence socket -> shared stores

const app = new App({ target: document.getElementById("app") });
export default app;
