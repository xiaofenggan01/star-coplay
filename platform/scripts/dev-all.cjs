const path = require("path");
const { spawn, spawnSync } = require("child_process");

const root = path.join(__dirname, "..");

const r = spawnSync("npx", ["supabase", "start"], { cwd: root, stdio: "inherit", shell: true });
if (r.status !== 0) process.exit(r.status === null ? 1 : r.status);

const fn = spawn("node", [path.join(__dirname, "serve-functions.cjs")], {
  cwd: root,
  stdio: "inherit",
  shell: true,
});
const web = spawn("npm", ["run", "dev", "--prefix", "web"], {
  cwd: root,
  stdio: "inherit",
  shell: true,
});

function shutdown() {
  try {
    fn.kill();
  } catch (_) {}
  try {
    web.kill();
  } catch (_) {}
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

fn.on("exit", (code, signal) => {
  if (signal === "SIGINT" || signal === "SIGTERM") return;
  if (code !== 0 && code !== null) shutdown();
});
web.on("exit", (code, signal) => {
  if (signal === "SIGINT" || signal === "SIGTERM") return;
  if (code !== 0 && code !== null) shutdown();
});
