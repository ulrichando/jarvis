"use client";

import { Database as DatabaseIcon } from "lucide-react";
import { type DbInfo, Section, formatBytes } from "./shared";

// ── Database info ──────────────────────────────────────────────────────

export function DatabaseSection({ dbInfo }: { dbInfo: DbInfo | null }) {
  return (
    <Section
      title="Database"
      icon={<DatabaseIcon className="size-3.5" />}
      hint="SQLite files in this workspace's data/ directory."
    >
      {!dbInfo || !dbInfo.exists ? (
        <p className="text-[12px] text-muted-foreground">
          No database files in <code className="font-mono">data/</code> yet.
        </p>
      ) : (
        <>
          <div className="space-y-1">
            {dbInfo.files.map((f) => (
              <div
                key={f.name}
                className="flex items-center justify-between rounded-md border border-border/40 px-3 py-1.5 font-mono text-[12px]"
              >
                <span>{f.name}</span>
                <span className="text-muted-foreground">
                  {formatBytes(f.bytes)}
                </span>
              </div>
            ))}
          </div>
          {dbInfo.tables.length > 0 && (
            <div className="mt-3">
              <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                Tables ({dbInfo.tables.length})
              </div>
              <div className="space-y-0.5">
                {dbInfo.tables.map((t) => (
                  <div
                    key={t.name}
                    className="flex items-center justify-between rounded px-2 py-1 font-mono text-[11.5px]"
                  >
                    <span>{t.name}</span>
                    <span className="text-muted-foreground">
                      {t.rows.toLocaleString()} row{t.rows === 1 ? "" : "s"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {dbInfo.schemaError && (
            <p className="mt-2 text-[11px] text-amber-500/85">
              Schema query unavailable: {dbInfo.schemaError}
            </p>
          )}
        </>
      )}
    </Section>
  );
}
