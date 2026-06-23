import type { DatabaseSync } from 'node:sqlite';

/** Upsert a service's deployed version + boot time. The table is created defensively because
 *  the bot is a separate process that never runs applySchema. */
export function reportService(db: DatabaseSync, service: string, version: string, bootedAt: number): void {
  db.exec('CREATE TABLE IF NOT EXISTS service_status (service TEXT PRIMARY KEY, version TEXT, booted_at INTEGER)');
  db.prepare(`INSERT INTO service_status(service,version,booted_at) VALUES(?,?,?)
              ON CONFLICT(service) DO UPDATE SET version=excluded.version, booted_at=excluded.booted_at`)
    .run(service, version, bootedAt);
}

/** The last-reported version + boot time for a service, or null if absent / table missing. */
export function readService(db: DatabaseSync, service: string): { version: string; booted_at: number } | null {
  try {
    return (db.prepare('SELECT version, booted_at FROM service_status WHERE service=?').get(service) as
      { version: string; booted_at: number } | undefined) ?? null;
  } catch { return null; }
}
