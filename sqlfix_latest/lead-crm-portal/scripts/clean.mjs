import { rm } from "node:fs/promises";
import path from "node:path";

const root = process.cwd();
for (const name of [".next", "tsconfig.tsbuildinfo"]) {
  await rm(path.join(root, name), { recursive: true, force: true });
}
console.log("LeadX frontend build cache cleaned.");
