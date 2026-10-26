const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const root = path.join(__dirname, "..");
const src = path.join(root, "supabase", ".env");
const dst = path.join(root, "supabase", "functions", ".env");
if (fs.existsSync(src)) {
  fs.copyFileSync(src, dst);
}

const r = spawnSync(
  "npx",
  ["supabase", "functions", "serve", "--env-file", "supabase/.env"],
  { cwd: root, stdio: "inherit", shell: true },
);

process.exit(r.status === null ? 1 : r.status);
