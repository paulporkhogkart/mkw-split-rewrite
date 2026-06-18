// The season server origin. Override for local dev via VITE_API_BASE (e.g. http://localhost:8787).
export const API_BASE =
  (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.VITE_API_BASE) ||
  "https://api.thekartoff.com";
