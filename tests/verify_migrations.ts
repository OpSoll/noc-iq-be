// Migration verification helper.
// Run with: npx ts-node tests/verify_migrations.ts
// Validates that the Alembic migration chain is linear and the head is reachable.

import { execSync } from "child_process";

type MigrationRow = { rev: string; parent: string | null; message: string };

function runAlembic(cmd: string): string {
  return execSync(`alembic ${cmd}`, { encoding: "utf8" }).trim();
}

function parseHistory(raw: string): MigrationRow[] {
  return raw
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const [rev, rest] = line.split(" -> ");
      const parent = rest?.split(",")[0]?.trim() ?? null;
      const message = rest?.split(",").slice(1).join(",").trim() ?? "";
      return { rev: rev.trim(), parent, message };
    });
}

function assertLinearChain(rows: MigrationRow[]): void {
  const revSet = new Set(rows.map((r) => r.rev));
  const parentCounts: Record<string, number> = {};
  for (const row of rows) {
    if (row.parent && revSet.has(row.parent)) {
      parentCounts[row.parent] = (parentCounts[row.parent] ?? 0) + 1;
    }
  }
  const branched = Object.entries(parentCounts).filter(([, c]) => c > 1);
  if (branched.length > 0) {
    throw new Error(`Branched migration chain detected at: ${branched.map(([r]) => r).join(", ")}`);
  }
}

function getCurrentHead(): string {
  return runAlembic("heads").split(" ")[0];
}

function getCurrentRevision(): string {
  return runAlembic("current").split(" ")[0];
}

function main() {
  console.log("Verifying migration chain...");
  const raw = runAlembic("history --verbose");
  const rows = parseHistory(raw);
  assertLinearChain(rows);
  console.log(`  Chain is linear (${rows.length} revisions)`);

  const head = getCurrentHead();
  const current = getCurrentRevision();
  if (current !== head) {
    throw new Error(`DB is at ${current}, expected head ${head}. Run: alembic upgrade head`);
  }
  console.log(`  DB is at head: ${head}`);
  console.log("Migration verification passed.");
}

main();
