import { readdirSync, readFileSync } from "node:fs";
import { extname, join, relative, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const scriptsRoot = join(root, "scripts");
const failures = [];

function visit(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      visit(path);
      continue;
    }
    if (extname(path).toLowerCase() !== ".ps1") continue;
    const bytes = readFileSync(path);
    const hasBom = bytes.length >= 3
      && bytes[0] === 0xef
      && bytes[1] === 0xbb
      && bytes[2] === 0xbf;
    const content = bytes.subarray(hasBom ? 3 : 0);
    const hasNonAscii = content.some((byte) => byte >= 0x80);
    try {
      new TextDecoder("utf-8", { fatal: true }).decode(content);
    } catch {
      failures.push(`${relative(root, path)} is not valid UTF-8`);
      continue;
    }
    if (hasNonAscii && !hasBom) {
      failures.push(`${relative(root, path)} contains non-ASCII text without UTF-8 BOM`);
    }
  }
}

visit(scriptsRoot);
if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("PowerShell script encodings are Windows PowerShell 5.1 compatible.");
