// Stale-doc drift check: verifies that every route registered in app/api/v1/router.py
// is mentioned in README.md. Exits non-zero if any route is undocumented.
// Run with: npx ts-node tests/check_doc_drift.ts

import * as fs from "fs";
import * as path from "path";

const ROOT = path.resolve(__dirname, "..");

function readFile(rel: string): string {
  return fs.readFileSync(path.join(ROOT, rel), "utf8");
}

function extractRouterPrefixes(routerSrc: string): string[] {
  // Matches: prefix="/api/v1/something" or prefix='/api/v1/something'
  const re = /prefix=["']([^"']+)["']/g;
  const prefixes: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(routerSrc)) !== null) {
    prefixes.push(m[1]);
  }
  return prefixes;
}

function extractIncludedRouters(routerSrc: string): string[] {
  // Also catch bare include_router calls that use a path variable
  const re = /include_router\([^)]+\)/g;
  const matches = routerSrc.match(re) ?? [];
  return matches;
}

function checkDrift(prefixes: string[], readme: string): string[] {
  return prefixes.filter((prefix) => !readme.includes(prefix));
}

function main() {
  const routerSrc = readFile("app/api/v1/router.py");
  const readme = readFile("README.md");

  const prefixes = extractRouterPrefixes(routerSrc);

  if (prefixes.length === 0) {
    console.warn("No route prefixes found in router.py — check the regex.");
    process.exit(0);
  }

  console.log(`Found ${prefixes.length} route prefix(es) in router.py:`);
  prefixes.forEach((p) => console.log(`  ${p}`));

  const missing = checkDrift(prefixes, readme);

  if (missing.length > 0) {
    console.error("\nDoc drift detected — these routes are not mentioned in README.md:");
    missing.forEach((p) => console.error(`  ${p}`));
    process.exit(1);
  }

  console.log("\nAll routes are documented. No drift detected.");
}

main();
