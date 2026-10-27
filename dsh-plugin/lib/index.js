// src/index.ts
import { join as join5 } from "path";
import { homedir as homedir3 } from "os";
import { appendFileSync, mkdirSync as mkdirSync4 } from "fs";
import { randomUUID as randomUUID3 } from "crypto";

// src/game/paths.ts
import { existsSync, readFileSync, readdirSync } from "fs";
import { homedir } from "os";
import { join } from "path";
var GAME_NAME = "Stardew Valley";
function unique(values) {
  return [...new Set(values.filter((value) => Boolean(value)))];
}
function steamRoots(platform, home, env) {
  const roots = [
    env.STEAM_PATH,
    join(home, "Library", "Application Support", "Steam"),
    join(home, ".local", "share", "Steam"),
    join(home, ".steam", "steam"),
    "C:\\Program Files (x86)\\Steam",
    "C:\\Program Files\\Steam"
  ];
  if (platform === "win32") {
    if (env["ProgramFiles(x86)"]) roots.push(join(env["ProgramFiles(x86)"], "Steam"));
    if (env.ProgramFiles) roots.push(join(env.ProgramFiles, "Steam"));
  }
  return unique(roots);
}
function parseLibraryFolders(steamRoot) {
  const vdf = join(steamRoot, "steamapps", "libraryfolders.vdf");
  if (!existsSync(vdf)) return [];
  try {
    const text2 = readFileSync(vdf, "utf8");
    const paths = [];
    for (const match of text2.matchAll(/"path"\s+"([^"]+)"/g)) {
      paths.push(match[1].replace(/\\\\/g, "\\"));
    }
    return paths;
  } catch {
    return [];
  }
}
function gameCandidates(platform, home, env) {
  const candidates = [];
  if (env.STARCOPLAY_GAME_PATH) candidates.push(env.STARCOPLAY_GAME_PATH);
  for (const root of steamRoots(platform, home, env)) {
    candidates.push(join(root, "steamapps", "common", GAME_NAME));
    for (const library of parseLibraryFolders(root)) {
      candidates.push(join(library, "steamapps", "common", GAME_NAME));
    }
  }
  return unique(candidates);
}
function isGameDir(dir, platform) {
  if (platform === "darwin") {
    return existsSync(join(dir, "Contents", "MacOS", "Stardew Valley"));
  }
  if (platform === "win32") {
    return existsSync(join(dir, "Stardew Valley.exe")) || existsSync(join(dir, "StardewValley.exe"));
  }
  return existsSync(join(dir, "Stardew Valley.dll")) || existsSync(join(dir, "StardewValley")) || existsSync(join(dir, "Content"));
}
function modsDirFor(gameDir, platform) {
  if (platform === "darwin") return join(gameDir, "Contents", "MacOS", "Mods");
  return join(gameDir, "Mods");
}
function smapiPathFor(gameDir, platform) {
  if (platform === "darwin") return join(gameDir, "Contents", "MacOS", "StardewModdingAPI");
  if (platform === "win32") return join(gameDir, "StardewModdingAPI.exe");
  return join(gameDir, "StardewModdingAPI");
}
function readModVersion(modDir) {
  const manifest = join(modDir, "manifest.json");
  if (!existsSync(manifest)) return void 0;
  try {
    const parsed = JSON.parse(readFileSync(manifest, "utf8"));
    return typeof parsed.Version === "string" ? parsed.Version : void 0;
  } catch {
    return void 0;
  }
}
function detectGamePaths(environment = {}) {
  const platform = environment.platform ?? process.platform;
  const home = environment.home ?? homedir();
  const env = environment.env ?? process.env;
  const explicitMods = env.STARCOPLAY_MODS_PATH;
  if (explicitMods) {
    const modDir2 = join(explicitMods, "StarCoPlay");
    return {
      modsDir: explicitMods,
      smapi: false,
      modDir: modDir2,
      modVersion: readModVersion(modDir2)
    };
  }
  const gameDir = gameCandidates(platform, home, env).find((candidate) => isGameDir(candidate, platform));
  if (!gameDir) {
    return { smapi: false, error: "Stardew Valley installation was not found" };
  }
  const modsDir = modsDirFor(gameDir, platform);
  const modDir = join(modsDir, "StarCoPlay");
  const smapiPath = smapiPathFor(gameDir, platform);
  return {
    gameDir,
    modsDir,
    smapi: existsSync(smapiPath),
    smapiPath: existsSync(smapiPath) ? smapiPath : void 0,
    modDir,
    modVersion: readModVersion(modDir)
  };
}
function listBridgePlayerIds(modsDir) {
  if (!modsDir) return [];
  const starcoplayDir = join(modsDir, "StarCoPlay");
  if (!existsSync(starcoplayDir)) return [];
  try {
    return readdirSync(starcoplayDir).map((name2) => /^bridge_(\d+)$/.exec(name2)?.[1]).filter((value) => Boolean(value));
  } catch {
    return [];
  }
}

// src/route/server.ts
import { createServer } from "http";
import { connect } from "net";
import { chmodSync, existsSync as existsSync2, mkdirSync, readFileSync as readFileSync2, renameSync, rmSync, writeFileSync } from "fs";
import { dirname, join as join2 } from "path";
import { randomBytes, randomUUID } from "crypto";
import { WebSocketServer, WebSocket } from "ws";

// src/route/digest.ts
import { createHash } from "crypto";
var SNAPSHOT_VERSION = "starcoplay.capability-snapshot.v1";
function lineOf(entry) {
  return entry.source_surface + "	" + entry.capability_id + "	" + entry.capability_contract_version + "\n";
}
function canonicalEntries(entries) {
  const lines = /* @__PURE__ */ new Set();
  for (const entry of entries) {
    if (typeof entry?.source_surface !== "string" || typeof entry?.capability_id !== "string" || typeof entry?.capability_contract_version !== "string" || !entry.source_surface || !entry.capability_id || !entry.capability_contract_version) {
      continue;
    }
    lines.add(lineOf(entry));
  }
  if (lines.size === 0) throw new Error("capability snapshot is empty");
  return [...lines].sort((a, b) => a < b ? -1 : a > b ? 1 : 0).map((line) => {
    const parts = line.replace(/\n$/, "").split("	");
    return {
      source_surface: parts[0],
      capability_id: parts[1],
      capability_contract_version: parts[2]
    };
  });
}
function computeCapabilitySnapshotDigest(entries) {
  const payload = SNAPSHOT_VERSION + "\n" + canonicalEntries(entries).map(lineOf).join("");
  return "sha256:" + createHash("sha256").update(payload, "utf8").digest("hex");
}

// src/route/protocol.ts
var TERMINAL_STATES = /* @__PURE__ */ new Set(["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "TIMEOUT"]);
function fenceOf(material) {
  return {
    context_ref: material.context_ref,
    principal_ref: material.principal_ref,
    runtime_ref: material.runtime_ref,
    game_session_ref: material.game_session_ref,
    game_instance_nonce: material.game_instance_nonce,
    generation: material.generation,
    binding_ref: material.binding_ref,
    binding_version: material.binding_version,
    lease_epoch: material.lease_epoch,
    capability_digest: material.capability_digest
  };
}
function buildLocalCommand(material, capability, params, commandId, timeoutMs, nowMs = Date.now()) {
  if (!capability) throw new Error("capability is required");
  if (!commandId) throw new Error("commandId is required");
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error("timeoutMs must be positive");
  return {
    type: "local_command",
    fence: fenceOf(material),
    command_id: commandId,
    payload: { capability, params: params ?? {} },
    wire_format: "v2-json",
    deadline_at: (nowMs + timeoutMs) / 1e3
  };
}
function buildLocalControl(material, playerId, revision) {
  if (!playerId) throw new Error("playerId is required");
  if (!Number.isSafeInteger(revision) || revision < 0) throw new Error("revision must be a non-negative integer");
  return {
    type: "local_control",
    fence: fenceOf(material),
    enabled: true,
    target_player_id: playerId,
    revision
  };
}
function isLocalIdentity(value) {
  if (typeof value !== "object" || value === null) return false;
  const frame = value;
  return frame.type === "local_identity" && typeof frame.player_unique_id === "string" && frame.player_unique_id.length > 0 && (frame.revision === void 0 || typeof frame.revision === "number");
}
function isTerminalState(state) {
  return TERMINAL_STATES.has(state);
}
function isLocalCommandResult(value) {
  if (typeof value !== "object" || value === null) return false;
  const frame = value;
  return frame.type === "local_command_result" && typeof frame.command_id === "string";
}

// src/route/server.ts
var RouteUnavailableError = class extends Error {
  code = "route_unavailable";
  constructor(message = "the Stardew Valley mod is not connected yet") {
    super(message);
    this.name = "RouteUnavailableError";
  }
};
var RouteTimeoutError = class extends Error {
  code = "route_timeout";
  constructor(message = "the game did not answer in time") {
    super(message);
    this.name = "RouteTimeoutError";
  }
};
function loadOrCreateMaterialSeed(materialPath) {
  try {
    if (existsSync2(materialPath)) {
      const parsed = JSON.parse(readFileSync2(materialPath, "utf8"));
      if (typeof parsed.proof === "string" && parsed.proof.length >= 32 && typeof parsed.game_instance_nonce === "string" && parsed.game_instance_nonce.length >= 16 && typeof parsed.token === "string" && parsed.token.length >= 32) {
        return {
          proof: parsed.proof,
          game_instance_nonce: parsed.game_instance_nonce,
          token: parsed.token
        };
      }
    }
  } catch {
  }
  const seed = {
    proof: randomBytes(32).toString("base64url"),
    game_instance_nonce: randomBytes(16).toString("hex"),
    token: randomBytes(32).toString("base64url")
  };
  try {
    mkdirSync(dirname(materialPath), { recursive: true, mode: 448 });
    writeFileSync(materialPath, JSON.stringify(seed, null, 2) + "\n", { mode: 384 });
    try {
      chmodSync(materialPath, 384);
    } catch {
    }
  } catch {
  }
  return seed;
}
var StarCoPlayRouteServer = class {
  options;
  material;
  httpServer;
  wss;
  connection;
  pending = /* @__PURE__ */ new Map();
  queue = Promise.resolve();
  running = false;
  lastError;
  connectedAt;
  identity;
  lastControlKey;
  rendezvousWatchdog;
  gameEventListeners = /* @__PURE__ */ new Set();
  /** Subscribe to runtime events the mod forwards over local_direct. */
  onGameEvent(listener) {
    this.gameEventListeners.add(listener);
    return () => this.gameEventListeners.delete(listener);
  }
  constructor(options) {
    this.options = options;
    const entries = options.capabilityEntries;
    const seed = loadOrCreateMaterialSeed(join2(dirname(options.rendezvousPath), "route-material.json"));
    this.material = {
      endpoint: "",
      proof: seed.proof,
      context_ref: "dsh-local",
      principal_ref: "dsh-local",
      runtime_ref: "dsh-local",
      game_session_ref: "dsh-local",
      game_instance_nonce: seed.game_instance_nonce,
      generation: 1,
      binding_ref: "dsh-local",
      binding_version: 1,
      lease_epoch: 1,
      capability_digest: computeCapabilitySnapshotDigest(entries),
      capability_entries: entries,
      require_token: true,
      token: seed.token
    };
  }
  /** Token the launched SMAPI child must carry to be accepted. */
  get sessionToken() {
    return this.material.token ?? "";
  }
  status() {
    return {
      running: this.running,
      connected: Boolean(this.connection?.ready),
      endpoint: this.material.endpoint || void 0,
      rendezvousPath: this.options.rendezvousPath,
      lastError: this.lastError,
      connectedAt: this.connectedAt,
      identity: this.identity
    };
  }
  async start() {
    if (this.running) return;
    await this.ensureRendezvousIsFree();
    const directory = dirname(this.options.rendezvousPath);
    mkdirSync(directory, { recursive: true, mode: 448 });
    try {
      chmodSync(directory, 448);
    } catch {
    }
    this.httpServer = createServer();
    this.wss = new WebSocketServer({ server: this.httpServer });
    this.wss.on("connection", (socket) => this.handleConnection(socket));
    await new Promise((resolve2, reject) => {
      const server = this.httpServer;
      const onError = (error) => reject(error);
      server.once("error", onError);
      server.listen(0, "127.0.0.1", () => {
        server.off("error", onError);
        resolve2();
      });
    });
    const address = this.httpServer.address();
    if (typeof address !== "object" || address === null) {
      throw new Error("failed to allocate a loopback port");
    }
    this.material.endpoint = "ws://127.0.0.1:" + address.port;
    this.publishRendezvous();
    this.running = true;
    this.lastError = void 0;
    this.log("route listening on " + this.material.endpoint);
    this.rendezvousWatchdog = setInterval(() => {
      if (!this.running) return;
      if (existsSync2(this.options.rendezvousPath)) return;
      try {
        this.publishRendezvous();
        this.log("rendezvous republished (the game had claimed it)");
      } catch (error) {
        this.log("rendezvous republish failed: " + (error instanceof Error ? error.message : String(error)));
      }
    }, 1e3);
    this.rendezvousWatchdog.unref?.();
  }
  async stop() {
    this.running = false;
    if (this.rendezvousWatchdog) {
      clearInterval(this.rendezvousWatchdog);
      this.rendezvousWatchdog = void 0;
    }
    this.connectedAt = void 0;
    this.rejectAll(new RouteUnavailableError("the local game route stopped"));
    try {
      this.connection?.helloDeadline.unref();
    } catch {
    }
    try {
      this.connection?.socket.close();
    } catch {
    }
    this.connection = void 0;
    try {
      this.wss?.close();
    } catch {
    }
    this.wss = void 0;
    if (this.httpServer) {
      await new Promise((resolve2) => {
        const server = this.httpServer;
        server.close(() => resolve2());
        setTimeout(() => resolve2(), 500).unref();
      });
    }
    this.httpServer = void 0;
    this.removeOwnedRendezvous();
  }
  /** Push a task-board snapshot to the mod for the in-game HUD. Best effort. */
  syncTaskBoard(snapshot) {
    const connection = this.connection;
    if (!connection?.ready || connection.socket.readyState !== WebSocket.OPEN) return false;
    const frame = {
      type: "local_task_board",
      fence: fenceOf(this.material),
      board: snapshot
    };
    try {
      connection.socket.send(JSON.stringify(frame));
      return true;
    } catch (error) {
      this.log("task board sync failed: " + (error instanceof Error ? error.message : String(error)));
      return false;
    }
  }
  command(capability, params = {}, timeoutMs = 3e4) {
    const run = this.queue.then(
      () => this.executeCommand(capability, params, timeoutMs),
      () => this.executeCommand(capability, params, timeoutMs)
    );
    this.queue = run.catch(() => void 0);
    return run;
  }
  executeCommand(capability, params, timeoutMs) {
    const connection = this.connection;
    if (!connection?.ready || connection.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new RouteUnavailableError());
    }
    const commandId = "cmd_" + randomUUID();
    const frame = buildLocalCommand(
      this.material,
      capability,
      params,
      commandId,
      timeoutMs
    );
    return new Promise((resolve2, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(commandId);
        reject(new RouteTimeoutError(capability + " timed out after " + timeoutMs + "ms"));
      }, timeoutMs);
      timer.unref();
      this.pending.set(commandId, { resolve: resolve2, reject, timer });
      try {
        connection.socket.send(JSON.stringify(frame));
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(commandId);
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    });
  }
  handleConnection(socket) {
    if (this.connection?.ready) {
      socket.close(1008, "another game route is already connected");
      return;
    }
    const helloDeadline = setTimeout(() => {
      try {
        socket.close(1008, "local_direct_hello timeout");
      } catch {
      }
    }, 1e4);
    helloDeadline.unref();
    const connection = { socket, ready: false, helloDeadline };
    this.connection = connection;
    socket.on("message", (data) => this.handleMessage(connection, data.toString()));
    socket.on("close", () => {
      clearTimeout(helloDeadline);
      if (this.connection === connection) {
        this.connection = void 0;
        this.connectedAt = void 0;
        this.rejectAll(new RouteUnavailableError("the game disconnected"));
      }
    });
    socket.on("error", (error) => {
      this.log("socket error: " + error.message);
    });
  }
  handleMessage(connection, raw) {
    let frame;
    try {
      frame = JSON.parse(raw);
    } catch {
      return;
    }
    if (typeof frame !== "object" || frame === null) return;
    const value = frame;
    if (!connection.ready) {
      if (value.type !== "local_direct_hello") return;
      if (value.proof !== this.material.proof) {
        this.log("rejected local_direct_hello: invalid proof");
        try {
          connection.socket.close(1008, "invalid proof");
        } catch {
        }
        return;
      }
      if (this.material.require_token && value.token !== this.material.token) {
        this.log("rejected local_direct_hello: invalid instance token");
        try {
          connection.socket.close(1008, "invalid token");
        } catch {
        }
        return;
      }
      clearTimeout(connection.helloDeadline);
      connection.ready = true;
      this.connectedAt = Date.now();
      this.lastControlKey = void 0;
      connection.socket.send(JSON.stringify({
        type: "local_direct_ready",
        reconnect_proof: randomBytes(32).toString("base64url")
      }));
      this.log("game connected via local_direct");
      return;
    }
    if (value.type === "local_event") {
      this.handleGameEvent(value);
      return;
    }
    if (isLocalIdentity(value)) {
      this.handleIdentity(value);
      return;
    }
    if (isLocalCommandResult(value)) {
      const pending = this.pending.get(value.command_id);
      if (!pending) return;
      const state = String(value.state ?? "");
      if (!isTerminalState(state)) return;
      clearTimeout(pending.timer);
      this.pending.delete(value.command_id);
      pending.resolve({ ...value, state });
    }
  }
  handleGameEvent(frame) {
    const fence = frame.fence;
    const nonce = fence && typeof fence.game_instance_nonce === "string" ? fence.game_instance_nonce : void 0;
    if (!nonce || nonce !== this.material.game_instance_nonce) return;
    const event = frame.event;
    if (!event || typeof event.type !== "string") return;
    const raw = event.data;
    const data = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    const ts = typeof event.ts === "number" ? event.ts : void 0;
    for (const listener of this.gameEventListeners) {
      try {
        listener({ type: event.type, data, ts });
      } catch (error) {
        this.log("game event listener failed: " + (error instanceof Error ? error.message : String(error)));
      }
    }
  }
  handleIdentity(identity) {
    this.identity = identity;
    const playerId = identity.player_unique_id;
    if (!playerId) return;
    const currentTarget = typeof identity.target_player_id === "string" ? identity.target_player_id : null;
    if (identity.is_ai_controlled && currentTarget === playerId) return;
    const revision = Number.isSafeInteger(identity.revision) ? Math.max(0, identity.revision) + 1 : 1;
    const key = playerId + ":" + revision;
    if (this.lastControlKey === key) return;
    const connection = this.connection;
    if (!connection?.ready || connection.socket.readyState !== WebSocket.OPEN) return;
    try {
      connection.socket.send(JSON.stringify(buildLocalControl(this.material, playerId, revision)));
      this.lastControlKey = key;
      this.log("sent local_control target=" + playerId + " revision=" + revision);
    } catch (error) {
      this.log("local_control send failed: " + (error instanceof Error ? error.message : String(error)));
    }
  }
  rejectAll(error) {
    for (const [commandId, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(error);
      this.pending.delete(commandId);
    }
  }
  publishRendezvous() {
    const temp = this.options.rendezvousPath + "." + process.pid + ".tmp";
    writeFileSync(temp, JSON.stringify(this.material), { mode: 384 });
    try {
      chmodSync(temp, 384);
    } catch {
    }
    renameSync(temp, this.options.rendezvousPath);
    try {
      chmodSync(this.options.rendezvousPath, 384);
    } catch {
    }
  }
  async ensureRendezvousIsFree() {
    if (!existsSync2(this.options.rendezvousPath)) return;
    try {
      const existing = JSON.parse(readFileSync2(this.options.rendezvousPath, "utf8"));
      const parsed = parseEndpoint(String(existing.endpoint ?? ""));
      if (parsed && await isPortOpen(parsed.host, parsed.port)) {
        throw new Error("another StarCoPlay route is already running");
      }
    } catch (error) {
      if (error instanceof Error && error.message.includes("already running")) throw error;
    }
    rmSync(this.options.rendezvousPath, { force: true });
  }
  removeOwnedRendezvous() {
    try {
      if (!existsSync2(this.options.rendezvousPath)) return;
      const existing = JSON.parse(readFileSync2(this.options.rendezvousPath, "utf8"));
      if (existing.proof === this.material.proof) {
        rmSync(this.options.rendezvousPath, { force: true });
      }
    } catch {
    }
  }
  log(message) {
    this.options.logger?.(message);
  }
};
function parseEndpoint(endpoint) {
  try {
    const url = new URL(endpoint);
    const port2 = Number(url.port);
    if (url.hostname && Number.isInteger(port2) && port2 > 0) {
      return { host: url.hostname, port: port2 };
    }
  } catch {
  }
  const parts = endpoint.split(":");
  const port = Number(parts[1]);
  if (parts[0] && Number.isInteger(port) && port > 0) {
    return { host: parts[0], port };
  }
  return null;
}
function isPortOpen(host, port) {
  return new Promise((resolve2) => {
    const socket = connect({ host, port });
    const done = (value) => {
      socket.destroy();
      resolve2(value);
    };
    socket.setTimeout(400);
    socket.once("connect", () => done(true));
    socket.once("timeout", () => done(false));
    socket.once("error", () => done(false));
  });
}

// src/tools/status.ts
import { defineTool } from "@deepseek-ai/dsh-tools";
var REQUIRED_MOD_VERSION_FOR_HUD = "2.5.0";
function isModVersionAtLeast(version, minimum) {
  if (!version) return false;
  const parse = (value) => value.split(".").map((part) => Number.parseInt(part, 10) || 0);
  const current = parse(version);
  const required = parse(minimum);
  const length = Math.max(current.length, required.length);
  for (let index = 0; index < length; index += 1) {
    const left = current[index] ?? 0;
    const right = required[index] ?? 0;
    if (left !== right) return left > right;
  }
  return true;
}
function formatStatus(status) {
  return JSON.stringify(status, null, 2);
}
function registerStatusTool(ctx, getStatus) {
  ctx.tools.register(defineTool({
    name: "starcoplay_status",
    description: [
      "Check whether the StarCoPlay mod, SMAPI, and the local game route are ready.",
      "Call this before trying to play together. If the mod or SMAPI is missing,",
      "tell the player what to install instead of pretending the game tools exist."
    ].join(" "),
    parameters: {},
    output: {
      schema: { type: "string" },
      render: (_args, value) => [{ type: "text", text: value }]
    },
    async execute() {
      return formatStatus(getStatus());
    }
  }));
}

// src/tools/game.ts
import { defineTool as defineTool2 } from "@deepseek-ai/dsh-tools";
var EMOTES = ["happy", "sad", "angry", "heart", "exclamation", "question", "sleep", "sick", "laugh", "note", "music", "blush"];
var DIRECTIONS = ["up", "down", "left", "right"];
var FACE_DIRECTIONS = ["north", "south", "east", "west"];
function compactValue(value, limit = 4e3) {
  const text2 = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  if (!text2) return "";
  return text2.length > limit ? text2.slice(0, limit) + "\n... (truncated)" : text2;
}
function distanceFrom(entity, position) {
  const point = entity?.position ?? {};
  if (typeof point.tile_x !== "number" || typeof position?.tile_x !== "number") return 999;
  return Math.max(Math.abs(point.tile_x - position.tile_x), Math.abs(point.tile_y - position.tile_y));
}
var ENTITY_HINTS = ["health", "growth_stage", "ready", "watered", "has_moss", "stump"];
function renderEntity(entity, position) {
  const point = entity?.position ?? {};
  const near = distanceFrom(entity, position) <= 2 ? "*" : "";
  const props = entity?.properties && typeof entity.properties === "object" ? entity.properties : {};
  const hints = ENTITY_HINTS.filter((key) => props[key] !== void 0 && props[key] !== null).map((key) => key + "=" + String(props[key])).join(",");
  return `${entity?.name ?? "?"}(${point.tile_x},${point.tile_y})${near}${hints ? "[" + hints + "]" : ""}`;
}
function renderObservation(data) {
  if (!data || typeof data !== "object") return null;
  const self = data.self ?? {};
  const world = data.world ?? {};
  const position = self.position ?? {};
  const where = position.location ?? world.location_name;
  if (where === void 0) return null;
  const lines = [];
  const coords = typeof position.tile_x === "number" ? `(${position.tile_x},${position.tile_y})` : "";
  lines.push(`\u25CE ${where}${coords}${self.facing ? " \u671D\u5411 " + self.facing : ""}`);
  const clock = [
    world.time?.display,
    world.season && world.year ? `${world.season} Y${world.year}` : world.season,
    world.day_of_month ? `\u7B2C${world.day_of_month}\u5929` : void 0,
    world.weather
  ].filter(Boolean).join(" \xB7 ");
  if (clock) lines.push("\u23F1 " + clock);
  const stats = [];
  if (typeof self.energy === "number") stats.push(`\u4F53\u529B ${Math.round(self.energy * 100)}%`);
  if (typeof self.health === "number") stats.push(`\u751F\u547D ${Math.round(self.health * 100)}%`);
  if (typeof self.money === "number") stats.push(`${self.money}g`);
  if (self.equipped?.hand) stats.push(`\u624B\u6301 ${self.equipped.hand}`);
  if (stats.length) lines.push("\xB7 " + stats.join(" \xB7 "));
  const inventory = (Array.isArray(self.inventory) ? self.inventory : []).filter((slot) => slot && !slot.is_empty);
  if (inventory.length) lines.push("\u5305 " + inventory.map((slot) => `${slot.name}\xD7${slot.count}`).join(" "));
  const entities = Array.isArray(data.entities) ? data.entities : [];
  if (entities.length) {
    const grouped = /* @__PURE__ */ new Map();
    for (const entity of entities) {
      if (!entity || typeof entity !== "object") continue;
      const type = String(entity.type ?? "other");
      if (!grouped.has(type)) grouped.set(type, []);
      grouped.get(type).push(entity);
    }
    for (const [type, list] of [...grouped.entries()].sort((a, b) => a[0] < b[0] ? -1 : 1)) {
      const sorted = [...list].sort((a, b) => distanceFrom(a, position) - distanceFrom(b, position));
      const shown = sorted.slice(0, 14);
      const more = list.length > shown.length ? ` \u2026\u5171${list.length}` : "";
      lines.push(`\u25B8 ${type}${list.length > 1 ? "\xD7" + list.length : ""}: ` + shown.map((e) => renderEntity(e, position)).join(" ") + more);
    }
  }
  const text2 = lines.join("\n");
  return text2.length > 2600 ? text2.slice(0, 2600) + "\n\u2026(truncated)" : text2;
}
function formatGameResult(result) {
  const output = result.output;
  if (result.state !== "SUCCEEDED") {
    const error = output && typeof output.error === "object" && output.error !== null ? output.error : void 0;
    const code = result.error_code || (error && typeof error.code === "string" ? error.code : result.state);
    const observation = output && typeof output.observation === "string" ? output.observation : "";
    return "\u64CD\u4F5C\u5931\u8D25\uFF08" + code + "\uFF09" + (observation ? "\n" + observation : "");
  }
  if (output && typeof output === "object") {
    const observation = typeof output.observation === "string" ? output.observation.trim() : "";
    const data = output.data;
    if (data !== void 0 && data !== null) {
      const rendered = renderObservation(data) ?? compactValue(data, 3500);
      if (rendered) return observation ? observation + "\n" + rendered : rendered;
    }
    if (observation) return observation;
    if (output.success === false) {
      return "\u64CD\u4F5C\u672A\u6210\u529F\uFF1A" + compactValue(output);
    }
  }
  return compactValue(output ?? result);
}
function registerGameTool(ctx, route, spec) {
  ctx.tools.register(defineTool2({
    name: spec.name,
    description: spec.description,
    parameters: spec.parameters,
    output: {
      schema: { type: "string" },
      render: (_args, value) => [{ type: "text", text: value }]
    },
    async execute(args) {
      const normalized = spec.normalize ? spec.normalize(args) : args;
      if (typeof normalized === "string") return normalized;
      const params = {};
      for (const [key, value] of Object.entries(normalized)) {
        if (value !== void 0 && value !== null && value !== "") params[key] = value;
      }
      try {
        const result = await route.command(spec.capability, params, spec.timeoutMs);
        return formatGameResult(result);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        return "\u6E38\u620F\u8FD8\u6CA1\u6709\u8FDE\u63A5\uFF1A" + message + "\n\u8BF7\u5148\u8BFB\u6863\uFF0C\u5E76\u786E\u8BA4 StarCoPlay Mod \u5DF2\u542F\u7528\u3001BridgeMode \u4E3A auto \u6216 local_direct\u3002";
      }
    }
  }));
}
function registerGameTools(ctx, route) {
  registerGameTool(ctx, route, {
    name: "starcoplay_observe",
    description: "\u67E5\u770B\u5F53\u524D\u6E38\u620F\u72B6\u6001\uFF1A\u89D2\u8272\u4F4D\u7F6E\u3001\u65F6\u95F4\u3001\u5929\u6C14\u3001\u4F53\u529B\u3001\u624B\u6301\u7269\u54C1\u3001\u9644\u8FD1 NPC/\u4F5C\u7269/\u52A8\u7269/\u53EF\u4EA4\u4E92\u7269\u3002\u505A\u4EFB\u4F55\u51B3\u5B9A\u524D\u5148\u8C03\u7528\u5B83\u3002\u53EA\u8BFB\u3002",
    capability: "observe",
    timeoutMs: 3e4,
    parameters: {}
  });
  registerGameTool(ctx, route, {
    name: "starcoplay_say",
    description: "\u5728\u6E38\u620F\u5185\u804A\u5929\u6846\u8BF4\u8BDD\uFF0C\u73A9\u5BB6\u624D\u80FD\u542C\u89C1\u3002\u666E\u901A\u56DE\u590D\u73A9\u5BB6\u770B\u4E0D\u5230\uFF1B\u9700\u8981\u8BA9\u73A9\u5BB6\u542C\u89C1\u65F6\u5FC5\u987B\u8C03\u7528\u5B83\u3002",
    capability: "say",
    timeoutMs: 15e3,
    parameters: {
      message: { type: "string", required: true, description: "\u8981\u8BF4\u7684\u8BDD" }
    },
    normalize(args) {
      const message = typeof args.message === "string" ? args.message.trim() : "";
      return message ? { message } : "message \u4E0D\u80FD\u4E3A\u7A7A";
    }
  });
  registerGameTool(ctx, route, {
    name: "starcoplay_emote",
    description: "\u5728\u89D2\u8272\u5934\u9876\u663E\u793A\u8868\u60C5\u6C14\u6CE1\uFF0C\u4E0D\u8BF4\u8BDD\u3002\u7528\u4E8E\u8865\u5145\u60C5\u7EEA\u53CD\u9988\u3002",
    capability: "emote",
    timeoutMs: 15e3,
    parameters: {
      type: { type: "string", required: true, enum: EMOTES, description: "\u8868\u60C5\u7C7B\u578B" }
    }
  });
  registerGameTool(ctx, route, {
    name: "starcoplay_face",
    description: "\u539F\u5730\u8F6C\u5411\uFF0C\u4E0D\u79FB\u52A8\u3002\u7528\u4E8E\u4EA4\u4E92\u6216\u4F7F\u7528\u5DE5\u5177\u524D\u8C03\u6574\u671D\u5411\u3002",
    capability: "face",
    timeoutMs: 15e3,
    parameters: {
      direction: { type: "string", required: true, enum: DIRECTIONS, description: "\u65B9\u5411" }
    }
  });
  registerGameTool(ctx, route, {
    name: "starcoplay_move_to",
    description: "\u5728\u5F53\u524D\u5730\u56FE\u5185\u8D70\u5230\u6307\u5B9A tile\u3002\u8DE8\u5730\u56FE\u8BF7\u7528 starcoplay_go_to\u3002",
    capability: "move_to",
    timeoutMs: 6e4,
    parameters: {
      x: { type: "number", required: true, description: "\u76EE\u6807 tile X" },
      y: { type: "number", required: true, description: "\u76EE\u6807 tile Y" }
    }
  });
  registerGameTool(ctx, route, {
    name: "starcoplay_go_to",
    description: "\u8D70\u5411\u4E00\u4E2A\u8BED\u4E49\u76EE\u6807\uFF1ANPC \u540D\u3001\u52A8\u7269\u540D\u3001\u5730\u56FE\u540D\u3001\u5EFA\u7B51\u540D\u3001\u5750\u6807\uFF0825,60 \u6216 Town:25,60\uFF09\u6216 player\u3002\u4F1A\u8DE8\u5730\u56FE\u5BFB\u8DEF\u3002",
    capability: "go_to",
    timeoutMs: 12e4,
    parameters: {
      target: { type: "string", required: true, description: "\u76EE\u6807\u6807\u8BC6" },
      face_direction: { type: "string", enum: FACE_DIRECTIONS, description: "\u5230\u8FBE\u540E\u671D\u5411" }
    }
  });
  registerGameTool(ctx, route, {
    name: "starcoplay_do",
    description: "\u6309\u4EA4\u4E92\u952E\uFF08\u5BF9\u8BDD\u3001\u5F00\u95E8\u3001\u5F00\u7BB1\u3001\u6536\u83B7\u3001\u629A\u6478\u52A8\u7269\u7B49\uFF09\uFF0C\u6E38\u620F\u5F15\u64CE\u6839\u636E\u4E0A\u4E0B\u6587\u51B3\u5B9A\u6548\u679C\u3002\u4E0D\u4F20 xy \u65F6\u5BF9\u9762\u524D\u7684 tile \u751F\u6548\u3002",
    capability: "do",
    timeoutMs: 3e4,
    parameters: {
      x: { type: "number", description: "\u76EE\u6807 tile X" },
      y: { type: "number", description: "\u76EE\u6807 tile Y" }
    }
  });
  registerGameTool(ctx, route, {
    name: "starcoplay_use_tool",
    description: "\u4F7F\u7528\u5F53\u524D\u624B\u6301\u5DE5\u5177\uFF08\u6D47\u6C34\u3001\u780D\u6811\u3001\u6572\u77F3\u5934\u3001\u9504\u5730\u7B49\uFF09\u3002\u4E0D\u4F20 xy \u65F6\u5BF9\u9762\u524D\u7684 tile \u751F\u6548\u3002",
    capability: "use_tool",
    timeoutMs: 3e4,
    parameters: {
      x: { type: "number", description: "\u76EE\u6807 tile X" },
      y: { type: "number", description: "\u76EE\u6807 tile Y" }
    }
  });
  registerGameTool(ctx, route, {
    name: "starcoplay_equip",
    description: "\u5207\u6362\u624B\u6301\u7269\u54C1\u3002item \u548C slot \u4E8C\u9009\u4E00\u3002",
    capability: "equip",
    timeoutMs: 3e4,
    parameters: {
      item: { type: "string", description: "\u7269\u54C1\u540D\uFF08\u6A21\u7CCA\u5339\u914D\uFF09" },
      slot: { type: "number", description: "\u80CC\u5305\u69FD\u4F4D\uFF080 \u8D77\uFF09" }
    },
    normalize(args) {
      if (typeof args.item !== "string" && typeof args.slot !== "number") {
        return "item \u548C slot \u81F3\u5C11\u8981\u63D0\u4F9B\u4E00\u4E2A";
      }
      return args;
    }
  });
  registerGameTool(ctx, route, {
    name: "starcoplay_go_bed",
    description: "\u56DE\u5BB6\u5E76\u7761\u89C9\uFF0C\u63A8\u8FDB\u5230\u4E0B\u4E00\u5929\u3002\u4F1A\u5BFB\u8DEF\u3001\u627E\u5E8A\u3001\u4EA4\u4E92\u5E76\u786E\u8BA4\u7761\u89C9\u5BF9\u8BDD\u3002",
    capability: "go_bed",
    timeoutMs: 18e4,
    parameters: {
      force_tp: { type: "boolean", description: "\u662F\u5426\u76F4\u63A5\u4F20\u9001\u56DE\u5BB6\uFF08\u9ED8\u8BA4 false\uFF09" }
    }
  });
  registerGameTool(ctx, route, {
    name: "starcoplay_water_crops",
    description: "\u7ED9\u5F53\u524D\u5730\u56FE\u4E0A\u6240\u6709\u672A\u6D47\u6C34\u7684\u4F5C\u7269\u6D47\u6C34\uFF0C\u81EA\u52A8\u88C5\u5907\u6C34\u58F6\u3001\u5BFB\u8DEF\u3001\u8865\u6C34\u3002\u8FD4\u56DE\u6D47\u6C34\u6570\u91CF\u548C\u505C\u6B62\u539F\u56E0\u3002",
    capability: "water_crops",
    timeoutMs: 18e4,
    parameters: {
      max_count: { type: "number", description: "\u6700\u591A\u6D47\u591A\u5C11\u682A\uFF0C\u9ED8\u8BA4 50" }
    }
  });
  registerGameTool(ctx, route, {
    name: "starcoplay_chop_trees",
    description: "\u780D\u6389\u5F53\u524D\u5730\u56FE\u4E0A\u6240\u6709\u6210\u719F\u6811\u6728\uFF1A\u81EA\u52A8\u626B\u63CF\u3001\u5BFB\u8DEF\u3001\u5BF9\u51C6\u3001\u6309\u65A7\u5934\u7B49\u7EA7\u7B97\u597D\u6325\u780D\u6B21\u6570\u3001\u780D\u5012\u5E76\u6361\u8D77\u6389\u843D\u3002\u8FD4\u56DE\u780D\u4E86\u591A\u5C11\u68F5\u548C\u505C\u6B62\u539F\u56E0\u3002",
    capability: "chop_trees",
    timeoutMs: 3e5,
    parameters: {
      max_count: { type: "number", description: "\u6700\u591A\u780D\u51E0\u68F5\uFF0C\u9ED8\u8BA4 10" },
      include_stumps: { type: "boolean", description: "\u662F\u5426\u8FDE\u6811\u6869\u4E00\u8D77\u780D\uFF0C\u9ED8\u8BA4 false" }
    }
  });
  registerGameTool(ctx, route, {
    name: "starcoplay_harvest_crops",
    description: "\u6536\u83B7\u5F53\u524D\u5730\u56FE\u4E0A\u6240\u6709\u6210\u719F\u4F5C\u7269\uFF0C\u53EF\u9009\u81EA\u52A8\u8865\u79CD\u3002\u8FD4\u56DE\u6536\u83B7\u6570\u91CF\u548C\u505C\u6B62\u539F\u56E0\u3002",
    capability: "harvest_crops",
    timeoutMs: 18e4,
    parameters: {
      max_count: { type: "number", description: "\u6700\u591A\u6536\u591A\u5C11\u682A\uFF0C\u9ED8\u8BA4 50" },
      replant_seed: { type: "string", description: "\u6536\u83B7\u540E\u8865\u79CD\u7684\u79CD\u5B50\u540D\uFF08\u53EF\u9009\uFF09" }
    }
  });
}

// src/tools/tasks.ts
import { defineTool as defineTool3 } from "@deepseek-ai/dsh-tools";

// src/tasks/model.ts
var COMPLETION_TYPES = [
  "inventory_at_least",
  "gold_at_least",
  "manual_confirm"
];
function emptyBoard(playerId) {
  return {
    schema_version: 1,
    player_id: playerId,
    revision: 0,
    tasks: [],
    archived: []
  };
}
function sanitizePlayerId(playerId) {
  const cleaned = String(playerId || "").replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 64);
  return cleaned || "unknown";
}
function normalizeCompletion(raw) {
  const value = raw ?? {};
  const type = String(value.type ?? "");
  if (!COMPLETION_TYPES.includes(type)) {
    throw new Error(
      "completion.type must be one of: " + COMPLETION_TYPES.join(", ")
    );
  }
  if (type === "inventory_at_least") {
    const item = String(value.item ?? "").trim();
    const qty = Number(value.qty ?? 0);
    if (!item) throw new Error("completion.item is required for inventory_at_least");
    if (!Number.isFinite(qty) || qty <= 0) {
      throw new Error("completion.qty must be a positive number");
    }
    return { type, item, qty: Math.floor(qty) };
  }
  if (type === "gold_at_least") {
    const qty = Number(value.qty ?? 0);
    if (!Number.isFinite(qty) || qty <= 0) {
      throw new Error("completion.qty must be a positive number");
    }
    return { type, qty: Math.floor(qty) };
  }
  return { type: "manual_confirm" };
}
function statusLabel(status) {
  switch (status) {
    case "active":
      return "\u8FDB\u884C\u4E2D";
    case "proposed":
      return "\u6392\u961F";
    case "paused":
      return "\u5DF2\u6682\u505C";
    case "blocked":
      return "\u5361\u4F4F";
    case "done":
      return "\u5B8C\u6210";
    case "failed":
      return "\u5931\u8D25";
    case "cancelled":
      return "\u5DF2\u53D6\u6D88";
    default:
      return status;
  }
}

// src/tools/tasks.ts
function text(value) {
  return {
    schema: { type: "string" },
    render: (_args, output) => [{ type: "text", text: output }]
  };
}
function formatTask(task) {
  const parts = [
    `${task.id} \xB7 ${task.title}`,
    `\u72B6\u6001\uFF1A${statusLabel(task.status)}`,
    `\u5B8C\u6210\u6761\u4EF6\uFF1A${describeCompletion(task)}`
  ];
  if (task.progress_text) parts.push(`\u8FDB\u5EA6\uFF1A${task.progress_text}`);
  if (task.blocked_reason) parts.push(`\u5361\u4F4F\uFF1A${task.blocked_reason}`);
  if (task.constraints.length) parts.push(`\u7EA6\u675F\uFF1A${task.constraints.join("\uFF1B")}`);
  return parts.join("\n");
}
function describeCompletion(task) {
  const completion = task.completion;
  if (completion.type === "inventory_at_least") {
    return `\u80CC\u5305 ${completion.item} \u2265 ${completion.qty}`;
  }
  if (completion.type === "gold_at_least") return `\u91D1\u5E01 \u2265 ${completion.qty}`;
  return "\u9700\u8981\u73A9\u5BB6\u786E\u8BA4";
}
function registerTaskTools(ctx, route, board) {
  ctx.tools.register(defineTool3({
    name: "starcoplay_task_add",
    description: [
      "\u628A\u73A9\u5BB6\u5E03\u7F6E\u7684\u4E8B\u60C5\u8BB0\u8FDB\u4EFB\u52A1\u677F\u3002\u73A9\u5BB6\u8BF4\u201C\u5E2E\u6211\u505A X / \u53BB Y / \u5F04 Z \u4E2A\u201D\u65F6\u8C03\u7528\u3002",
      "\u5FC5\u987B\u7ED9\u51FA\u53EF\u9A8C\u8BC1\u7684 completion\uFF1A\u80CC\u5305\u6570\u91CF\u3001\u91D1\u5E01\uFF0C\u6216 manual_confirm\u3002",
      "\u8BB0\u4E0B\u540E\u7528 starcoplay_say \u5411\u73A9\u5BB6\u590D\u8FF0\u4E00\u53E5\uFF0C\u7136\u540E\u5F00\u59CB\u6267\u884C\u3002"
    ].join(" "),
    parameters: {
      title: { type: "string", required: true, description: "\u4E00\u53E5\u8BDD\u4EFB\u52A1\u540D\uFF0C\u4F8B\u5982\u201C\u505A 20 \u4E2A\u7BB1\u5B50\u201D" },
      goal: { type: "object", required: true, additionalProperties: true, description: '\u7ED3\u6784\u5316\u76EE\u6807\uFF0C\u4F8B\u5982 {"type":"craft","item":"Chest","qty":20}' },
      completion: {
        type: "object",
        required: true,
        additionalProperties: true,
        description: '\u5B8C\u6210\u5224\u5B9A\uFF0C\u4F8B\u5982 {"type":"inventory_at_least","item":"Chest","qty":20} \u6216 {"type":"gold_at_least","qty":5000}'
      },
      constraints: { type: "array", description: '\u7EA6\u675F\uFF0C\u4F8B\u5982 ["\u4E0D\u5356\u5DE5\u5177"]' },
      priority: { type: "number", description: "\u4F18\u5148\u7EA7\uFF0C\u6570\u5B57\u8D8A\u5927\u8D8A\u9760\u524D\uFF0C\u9ED8\u8BA4 5" },
      deadline: { type: "object", additionalProperties: true, description: '\u53EF\u9009\u622A\u6B62\u6761\u4EF6\uFF0C\u4F8B\u5982 {"season":"spring","day":28}' },
      derived: { type: "array", description: '\u6D3E\u751F\u9700\u6C42\uFF0C\u4F8B\u5982 [{"type":"acquire","item":"Wood","qty":120}]' }
    },
    output: text(""),
    async execute(args) {
      const result = await board.add({
        title: String(args.title ?? ""),
        goal: args.goal ?? {},
        completion: args.completion ?? {},
        constraints: Array.isArray(args.constraints) ? args.constraints.map(String) : [],
        priority: Number(args.priority ?? 5),
        deadline: args.deadline ?? void 0,
        derived: Array.isArray(args.derived) ? args.derived : void 0
      });
      if (!result.ok || !result.task) return "\u4EFB\u52A1\u672A\u8BB0\u5F55\uFF1A" + (result.error || "\u672A\u77E5\u9519\u8BEF");
      return "\u5DF2\u8BB0\u5F55\u4EFB\u52A1\u3002\n" + formatTask(result.task) + "\n\u4E0B\u4E00\u6B65\uFF1A\u7528 starcoplay_say \u5411\u73A9\u5BB6\u590D\u8FF0\uFF0C\u7136\u540E\u5F00\u59CB\u6267\u884C\u3002";
    }
  }));
  ctx.tools.register(defineTool3({
    name: "starcoplay_task_list",
    description: "\u5217\u51FA\u4EFB\u52A1\u677F\uFF1A\u8FDB\u884C\u4E2D\u3001\u6392\u961F\u3001\u5361\u4F4F\u7684\u4EFB\u52A1\u3002\u73A9\u5BB6\u95EE\u201C\u8FD8\u6709\u4EC0\u4E48\u6CA1\u505A\u5B8C\u201D\u65F6\u8C03\u7528\u3002",
    parameters: {},
    output: text(""),
    async execute() {
      const tasks = board.list();
      if (!tasks.length) return "\u4EFB\u52A1\u677F\u662F\u7A7A\u7684\u3002";
      return tasks.map(formatTask).join("\n\n");
    }
  }));
  ctx.tools.register(defineTool3({
    name: "starcoplay_task_update",
    description: "\u4FEE\u6539\u4EFB\u52A1\uFF1Apause\uFF08\u6682\u505C\uFF09\u3001resume\uFF08\u7EE7\u7EED\uFF09\u3001cancel\uFF08\u53D6\u6D88\uFF09\u3001activate\uFF08\u5207\u6362\u4E3A\u5F53\u524D\u4EFB\u52A1\uFF09\u3001modify\uFF08\u6539\u6807\u9898/\u4F18\u5148\u7EA7/\u5B8C\u6210\u6761\u4EF6\uFF09\u3002",
    parameters: {
      id: { type: "string", required: true, description: "\u4EFB\u52A1 id\uFF0C\u4F8B\u5982 task_1a2b3c4d" },
      action: {
        type: "string",
        required: true,
        description: "pause | resume | cancel | activate | modify"
      },
      title: { type: "string", description: "modify \u65F6\u7684\u65B0\u6807\u9898" },
      priority: { type: "number", description: "modify \u65F6\u7684\u65B0\u4F18\u5148\u7EA7" },
      completion: { type: "object", additionalProperties: true, description: "modify \u65F6\u7684\u65B0\u5B8C\u6210\u6761\u4EF6" }
    },
    output: text(""),
    async execute(args) {
      const result = board.update(
        String(args.id ?? ""),
        String(args.action ?? ""),
        {
          title: args.title,
          priority: args.priority,
          completion: args.completion
        }
      );
      if (!result.ok || !result.task) return "\u4FEE\u6539\u5931\u8D25\uFF1A" + (result.error || "\u672A\u77E5\u9519\u8BEF");
      return "\u5DF2\u66F4\u65B0\u3002\n" + formatTask(result.task);
    }
  }));
  ctx.tools.register(defineTool3({
    name: "starcoplay_task_report",
    description: [
      "\u62A5\u544A\u4EFB\u52A1\u8FDB\u5C55\uFF1A\u8FDB\u5EA6\u6587\u672C\u3001\u7ED3\u6784\u5316\u8FDB\u5EA6\u3001\u8BC1\u636E\u3001\u5361\u4F4F\u539F\u56E0\u3002",
      "\u6BCF\u6B21\u6267\u884C\u5B8C\u4E00\u6BB5\u52A8\u4F5C\u540E\u8C03\u7528\uFF0C\u8BA9\u4EFB\u52A1\u677F\u548C\u6E38\u620F\u5185 HUD \u4FDD\u6301\u540C\u6B65\u3002",
      "completed=true \u65F6\u63D2\u4EF6\u4F1A\u53BB\u6E38\u620F\u91CC\u6838\u5BF9\u5B8C\u6210\u6761\u4EF6\uFF0C\u4E0D\u901A\u8FC7\u4E0D\u4F1A\u6807\u8BB0\u5B8C\u6210\u3002"
    ].join(" "),
    parameters: {
      id: { type: "string", required: true, description: "\u4EFB\u52A1 id" },
      progress_text: { type: "string", description: "\u7ED9\u73A9\u5BB6\u770B\u7684\u4E00\u884C\u8FDB\u5EA6\uFF0C\u4F8B\u5982\u201C\u6728\u5934 48/120\u201D" },
      progress: { type: "object", additionalProperties: true, description: '\u7ED3\u6784\u5316\u8FDB\u5EA6\uFF0C\u4F8B\u5982 {"wood":48}' },
      evidence: { type: "string", description: "\u8FD9\u6B21\u505A\u4E86\u4EC0\u4E48\uFF0C\u4F8B\u5982\u201Cchop_trees wood_gained=24\u201D" },
      blocked_reason: { type: "string", description: "\u5361\u4F4F\u539F\u56E0\uFF1B\u6E05\u7A7A\u65F6\u4F20\u7A7A\u5B57\u7B26\u4E32" },
      completed: { type: "boolean", description: "\u8BA4\u4E3A\u5DF2\u5B8C\u6210\u65F6\u4F20 true\uFF0C\u63D2\u4EF6\u4F1A\u6838\u5BF9\u4E16\u754C\u72B6\u6001" }
    },
    output: text(""),
    async execute(args) {
      const id = String(args.id ?? "");
      const blocked = args.blocked_reason === void 0 ? void 0 : String(args.blocked_reason || "") || null;
      const report = board.report(id, {
        progress_text: typeof args.progress_text === "string" ? args.progress_text : void 0,
        progress: args.progress ?? void 0,
        evidence: typeof args.evidence === "string" ? args.evidence : void 0,
        blocked_reason: blocked
      });
      if (!report.ok || !report.task) return "\u62A5\u544A\u5931\u8D25\uFF1A" + (report.error || "\u672A\u77E5\u9519\u8BEF");
      if (args.completed === true) {
        const checked = await board.check(id, route);
        if (checked.ok) {
          return "\u5B8C\u6210\u6761\u4EF6\u5DF2\u6838\u5BF9\u901A\u8FC7\uFF1A" + checked.detail + "\n\u4EFB\u52A1\u5DF2\u5B8C\u6210\uFF0C\u5411\u73A9\u5BB6\u62A5\u544A\u7ED3\u679C\u3002";
        }
        return "\u8FD8\u5DEE\u4E00\u70B9\uFF1A" + checked.detail + "\n\u4EFB\u52A1\u4FDD\u6301\u8FDB\u884C\u4E2D\uFF0C\u7EE7\u7EED\u6216\u62A5\u544A\u5361\u4F4F\u539F\u56E0\u3002";
      }
      return "\u5DF2\u66F4\u65B0\u8FDB\u5EA6\u3002\n" + formatTask(report.task);
    }
  }));
  ctx.tools.register(defineTool3({
    name: "starcoplay_task_check",
    description: "\u6838\u5BF9\u67D0\u4E2A\u4EFB\u52A1\u7684\u5B8C\u6210\u6761\u4EF6\u3002\u53EA\u8BFB\uFF0C\u4E0D\u6539\u53D8\u6E38\u620F\u72B6\u6001\u3002",
    parameters: {
      id: { type: "string", required: true, description: "\u4EFB\u52A1 id" }
    },
    output: text(""),
    async execute(args) {
      const checked = await board.check(String(args.id ?? ""), route);
      return (checked.ok ? "\u5DF2\u5B8C\u6210\uFF1A" : "\u672A\u5B8C\u6210\uFF1A") + checked.detail;
    }
  }));
}

// src/tasks/board.ts
import { randomUUID as randomUUID2 } from "crypto";

// src/tasks/verify.ts
function succeeded(result) {
  return result.state === "SUCCEEDED" || result.state === "OK";
}
function findItems(value, depth = 0) {
  if (depth > 6 || value === null || typeof value !== "object") return [];
  if (Array.isArray(value)) {
    for (const entry of value) {
      const found = findItems(entry, depth + 1);
      if (found.length) return found;
    }
    return [];
  }
  const record = value;
  if (Array.isArray(record.items)) {
    return record.items.filter((entry) => typeof entry === "object" && entry !== null);
  }
  for (const entry of Object.values(record)) {
    const found = findItems(entry, depth + 1);
    if (found.length) return found;
  }
  return [];
}
function findNumber(value, keys, depth = 0) {
  if (depth > 6 || value === null || typeof value !== "object") return null;
  const record = value;
  for (const key of keys) {
    const candidate = record[key];
    if (typeof candidate === "number" && Number.isFinite(candidate)) return candidate;
  }
  for (const entry of Object.values(record)) {
    const found = findNumber(entry, keys, depth + 1);
    if (found !== null) return found;
  }
  return null;
}
function normalize(value) {
  return String(value ?? "").toLowerCase().replace(/[\s_\-()[\]（）]/g, "");
}
function itemTokens(item) {
  return ["item_id", "qualified_item_id", "internal_name", "display_name", "name"].map((key) => normalize(item[key])).filter(Boolean);
}
function matchesItem(item, target) {
  const wanted = normalize(target);
  if (!wanted) return false;
  return itemTokens(item).some((token) => token === wanted || token.includes(wanted) || wanted.includes(token));
}
function stackOf(item) {
  const raw = item.stack ?? item.count ?? item.quantity ?? 1;
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : 1;
}
async function checkCompletion(route, completion) {
  try {
    if (completion.type === "inventory_at_least") {
      const result = await route.command(
        "query_inventory_snapshot",
        { container_kind: "player", include_empty_slots: false, include_details: true },
        15e3
      );
      if (!succeeded(result)) {
        return { ok: false, detail: "\u80CC\u5305\u67E5\u8BE2\u5931\u8D25\uFF1A" + (result.error_code || result.state) };
      }
      const items = findItems(result.output);
      const count = items.filter((item) => matchesItem(item, completion.item || "")).reduce((sum, item) => sum + stackOf(item), 0);
      const need = completion.qty || 0;
      return {
        ok: count >= need,
        detail: `${completion.item} ${count}/${need}`
      };
    }
    if (completion.type === "gold_at_least") {
      const result = await route.command(
        "query_runtime_snapshot",
        { include_player: true, include_world_time: false, include_weather: false, include_menu_state: false },
        15e3
      );
      if (!succeeded(result)) {
        return { ok: false, detail: "\u91D1\u5E01\u67E5\u8BE2\u5931\u8D25\uFF1A" + (result.error_code || result.state) };
      }
      const money = findNumber(result.output, ["money", "gold", "total_money"]);
      if (money === null) return { ok: false, detail: "\u65E0\u6CD5\u4ECE\u6E38\u620F\u72B6\u6001\u8BFB\u53D6\u91D1\u5E01" };
      const need = completion.qty || 0;
      return { ok: money >= need, detail: `\u91D1\u5E01 ${money}/${need}` };
    }
    return { ok: false, detail: "\u8BE5\u4EFB\u52A1\u9700\u8981\u73A9\u5BB6\u786E\u8BA4\u5B8C\u6210\uFF08manual_confirm\uFF09" };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { ok: false, detail: "\u65E0\u6CD5\u9A8C\u8BC1\uFF1A" + message };
  }
}

// src/tasks/board.ts
var HUD_LIMIT = 8;
var ACTIVE_STATUSES = ["active", "blocked", "paused", "proposed"];
function sortForDisplay(tasks) {
  const rank = { active: 0, blocked: 1, paused: 2, proposed: 3 };
  return [...tasks].sort((a, b) => {
    const byStatus = (rank[a.status] ?? 9) - (rank[b.status] ?? 9);
    if (byStatus !== 0) return byStatus;
    if (a.priority !== b.priority) return b.priority - a.priority;
    return a.created_at.localeCompare(b.created_at);
  });
}
var TaskBoardService = class {
  constructor(options) {
    this.options = options;
  }
  options;
  board = null;
  lastCompleted = null;
  notifyTimer = null;
  clock() {
    return this.options.now ? this.options.now() : /* @__PURE__ */ new Date();
  }
  ensure() {
    const playerId = this.options.getPlayerId();
    if (!playerId) return null;
    if (!this.board || this.board.player_id !== playerId) {
      this.board = this.options.store.load(playerId);
    }
    return this.board;
  }
  status() {
    const board = this.ensure();
    return {
      connected: board !== null,
      player_id: board?.player_id ?? null,
      tasks: board?.tasks.length ?? 0
    };
  }
  async add(input) {
    const board = this.ensure();
    if (!board) return { ok: false, error: "\u6E38\u620F\u5C1A\u672A\u8FDE\u63A5\uFF1A\u5148\u8BFB\u6863\u518D\u5E03\u7F6E\u4EFB\u52A1\u3002" };
    const title = String(input.title || "").trim();
    if (!title) return { ok: false, error: "title \u4E0D\u80FD\u4E3A\u7A7A" };
    if (!input.goal || typeof input.goal !== "object") {
      return { ok: false, error: "goal \u5FC5\u987B\u662F\u5BF9\u8C61" };
    }
    let completion;
    try {
      completion = normalizeCompletion(input.completion);
    } catch (error) {
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
    const now = this.clock().toISOString();
    const hasActive = board.tasks.some((task2) => task2.status === "active");
    const task = {
      id: "task_" + randomUUID2().slice(0, 8),
      title,
      source: "player",
      goal: input.goal,
      derived: Array.isArray(input.derived) ? input.derived : void 0,
      completion,
      constraints: Array.isArray(input.constraints) ? input.constraints.map(String) : [],
      priority: Number.isFinite(input.priority) ? Number(input.priority) : 5,
      deadline: input.deadline,
      status: hasActive ? "proposed" : "active",
      progress: {},
      progress_text: "",
      blocked_reason: null,
      evidence: [],
      created_at: now,
      updated_at: now
    };
    board.tasks.push(task);
    this.persist(board);
    return { ok: true, task };
  }
  list() {
    const board = this.ensure();
    if (!board) return [];
    return sortForDisplay(board.tasks.filter((task) => !["done", "failed", "cancelled"].includes(task.status)));
  }
  update(id, action, patch = {}) {
    const board = this.ensure();
    if (!board) return { ok: false, error: "\u6E38\u620F\u5C1A\u672A\u8FDE\u63A5" };
    const task = board.tasks.find((entry) => entry.id === id);
    if (!task) return { ok: false, error: "\u627E\u4E0D\u5230\u4EFB\u52A1 " + id };
    const now = this.clock().toISOString();
    switch (action) {
      case "pause":
        if (task.status === "active") task.status = "paused";
        break;
      case "resume":
        if (task.status === "paused" || task.status === "blocked") {
          this.pauseOthers(board, task.id);
          task.status = "active";
          task.blocked_reason = null;
        }
        break;
      case "cancel":
        task.status = "cancelled";
        break;
      case "activate":
        this.pauseOthers(board, task.id);
        task.status = "active";
        task.blocked_reason = null;
        break;
      case "modify":
        if (typeof patch.title === "string" && patch.title.trim()) task.title = patch.title.trim();
        if (Number.isFinite(Number(patch.priority))) task.priority = Number(patch.priority);
        if (patch.deadline && typeof patch.deadline === "object") {
          task.deadline = patch.deadline;
        }
        if (patch.completion) {
          try {
            task.completion = normalizeCompletion(patch.completion);
          } catch (error) {
            return { ok: false, error: error instanceof Error ? error.message : String(error) };
          }
        }
        break;
      default:
        return { ok: false, error: "\u672A\u77E5\u64CD\u4F5C\uFF1A" + action };
    }
    task.updated_at = now;
    if (task.status === "cancelled") this.activateNext(board);
    this.persist(board);
    return { ok: true, task };
  }
  report(id, input) {
    const board = this.ensure();
    if (!board) return { ok: false, error: "\u6E38\u620F\u5C1A\u672A\u8FDE\u63A5" };
    const task = board.tasks.find((entry) => entry.id === id);
    if (!task) return { ok: false, error: "\u627E\u4E0D\u5230\u4EFB\u52A1 " + id };
    if (typeof input.progress_text === "string") task.progress_text = input.progress_text;
    if (input.progress && typeof input.progress === "object") {
      task.progress = { ...task.progress, ...input.progress };
    }
    if (typeof input.evidence === "string" && input.evidence.trim()) {
      task.evidence.push(input.evidence.trim());
      if (task.evidence.length > 20) task.evidence.shift();
    }
    if (input.blocked_reason !== void 0) {
      task.blocked_reason = input.blocked_reason;
      if (input.blocked_reason) {
        task.status = "blocked";
      } else if (task.status === "blocked") {
        task.status = "active";
      }
    }
    task.updated_at = this.clock().toISOString();
    this.persist(board);
    return { ok: true, task };
  }
  async check(id, route) {
    const board = this.ensure();
    if (!board) return { ok: false, detail: "\u6E38\u620F\u5C1A\u672A\u8FDE\u63A5" };
    const task = board.tasks.find((entry) => entry.id === id);
    if (!task) return { ok: false, detail: "\u627E\u4E0D\u5230\u4EFB\u52A1 " + id };
    const result = await checkCompletion(route, task.completion);
    const now = this.clock().toISOString();
    task.updated_at = now;
    if (result.ok) {
      task.status = "done";
      task.progress_text = task.progress_text || result.detail;
      this.lastCompleted = { title: task.title, at: now };
      board.archived.push(task);
      board.tasks = board.tasks.filter((entry) => entry.id !== task.id);
      this.activateNext(board);
    } else if (task.status !== "blocked") {
      task.blocked_reason = result.detail;
    }
    this.persist(board);
    return { ok: result.ok, detail: result.detail, task };
  }
  snapshot() {
    const board = this.ensure();
    if (!board) return null;
    const lines = sortForDisplay(
      board.tasks.filter((task) => ACTIVE_STATUSES.includes(task.status))
    ).slice(0, HUD_LIMIT).map((task) => ({
      id: task.id,
      title: task.title,
      status: task.status,
      progress_text: task.progress_text || statusLabel(task.status),
      blocked_reason: task.blocked_reason ?? null
    }));
    return {
      type: "task_board",
      revision: board.revision,
      updated_at: this.clock().toISOString(),
      tasks: lines,
      last_completed: this.lastCompleted
    };
  }
  pauseOthers(board, keepId) {
    for (const entry of board.tasks) {
      if (entry.id !== keepId && entry.status === "active") entry.status = "paused";
    }
  }
  activateNext(board) {
    if (board.tasks.some((entry) => entry.status === "active")) return;
    const next = sortForDisplay(board.tasks.filter((entry) => entry.status === "proposed"))[0];
    if (next) {
      next.status = "active";
      next.updated_at = this.clock().toISOString();
    }
  }
  persist(board) {
    board.revision += 1;
    this.options.store.save(board);
    this.scheduleNotify();
  }
  scheduleNotify() {
    if (!this.options.onChange || this.notifyTimer) return;
    this.notifyTimer = setTimeout(() => {
      this.notifyTimer = null;
      const snapshot = this.snapshot();
      if (snapshot) this.options.onChange?.(snapshot);
    }, 50);
    this.notifyTimer.unref?.();
  }
};

// src/tasks/store.ts
import { existsSync as existsSync3, mkdirSync as mkdirSync2, readFileSync as readFileSync3, renameSync as renameSync2, writeFileSync as writeFileSync2 } from "fs";
import { homedir as homedir2 } from "os";
import { join as join3 } from "path";
var DEFAULT_DIR = join3(homedir2(), ".starcoplay", "tasks");
var TaskStore = class {
  constructor(dir = DEFAULT_DIR) {
    this.dir = dir;
  }
  dir;
  pathFor(playerId) {
    return join3(this.dir, sanitizePlayerId(playerId) + ".json");
  }
  load(playerId) {
    const path = this.pathFor(playerId);
    if (!existsSync3(path)) return emptyBoard(playerId);
    try {
      const parsed = JSON.parse(readFileSync3(path, "utf8"));
      if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.tasks)) {
        throw new Error("invalid task board shape");
      }
      return {
        schema_version: 1,
        player_id: playerId,
        revision: Number.isFinite(parsed.revision) ? Number(parsed.revision) : 0,
        tasks: parsed.tasks,
        archived: Array.isArray(parsed.archived) ? parsed.archived : []
      };
    } catch {
      try {
        renameSync2(path, path + ".corrupt-" + Date.now());
      } catch {
      }
      return emptyBoard(playerId);
    }
  }
  save(board) {
    mkdirSync2(this.dir, { recursive: true });
    const path = this.pathFor(board.player_id);
    const tmp = path + ".tmp";
    writeFileSync2(tmp, JSON.stringify(board, null, 2), "utf8");
    renameSync2(tmp, path);
  }
};

// src/tools/setup.ts
import { defineTool as defineTool4 } from "@deepseek-ai/dsh-tools";

// src/game/install.ts
import AdmZip from "adm-zip";
import { execFile } from "child_process";
import { existsSync as existsSync4, mkdirSync as mkdirSync3, mkdtempSync, readFileSync as readFileSync4, rmSync as rmSync2, writeFileSync as writeFileSync3 } from "fs";
import { tmpdir } from "os";
import { dirname as dirname2, join as join4, resolve, sep } from "path";
import { promisify } from "util";
var execFileAsync = promisify(execFile);
var DEFAULT_MOD_ZIP_URL = "https://github.com/xiaofenggan01/star-coplay/releases/download/mod-latest/StarCoPlay.zip";
function normalizeEntryName(name2) {
  return name2.replace(/\\/g, "/");
}
function findManifest(entries) {
  const candidates = entries.filter((entry) => !entry.isDirectory).map((entry) => ({ entry, name: normalizeEntryName(entry.entryName) })).filter(({ name: name2 }) => name2.endsWith("manifest.json")).sort((a, b) => a.name.length - b.name.length);
  for (const { entry, name: name2 } of candidates) {
    let parsed;
    try {
      parsed = JSON.parse(entry.getData().toString("utf8"));
    } catch {
      continue;
    }
    const uniqueId = typeof parsed.UniqueID === "string" ? parsed.UniqueID : "";
    const entryDll = typeof parsed.EntryDll === "string" ? parsed.EntryDll : "";
    if (!uniqueId.includes("StarCoPlay") && !entryDll.includes("StarCoPlay")) continue;
    return {
      prefix: name2.slice(0, -"manifest.json".length),
      version: typeof parsed.Version === "string" ? parsed.Version : "unknown",
      uniqueId
    };
  }
  return null;
}
function isSkipRelative(relative) {
  if (relative === "config.json") return true;
  if (relative.startsWith("bridge_")) return true;
  if (relative.startsWith("logs/")) return true;
  if (relative.startsWith("test-reports/")) return true;
  return false;
}
function safeRelative(relative) {
  const normalized = relative.replace(/\\/g, "/");
  if (!normalized) return false;
  if (normalized.startsWith("/")) return false;
  if (/^[A-Za-z]:/.test(normalized)) return false;
  return !normalized.split("/").includes("..");
}
function mergeConfig(modRoot, modsDir, warnings) {
  const configPath = join4(modRoot, "config.json");
  let config = {};
  if (existsSync4(configPath)) {
    try {
      const parsed = JSON.parse(readFileSync4(configPath, "utf8"));
      if (parsed && typeof parsed === "object") config = parsed;
    } catch {
      warnings.push("\u5DF2\u6709 config.json \u65E0\u6CD5\u89E3\u6790\uFF0C\u5C06\u4F7F\u7528\u9ED8\u8BA4\u914D\u7F6E");
    }
  }
  config.BridgeMode = "auto";
  config.EnableAIControl = true;
  const existingTarget = config.TargetPlayerId;
  if (typeof existingTarget !== "string" || !existingTarget.trim()) {
    const ids = listBridgePlayerIds(modsDir);
    if (ids.length === 1) {
      config.TargetPlayerId = ids[0];
    } else if (ids.length > 1) {
      config.TargetPlayerId = ids[0];
      warnings.push("\u68C0\u6D4B\u5230\u591A\u4E2A\u73A9\u5BB6 ID\uFF0C\u5DF2\u9009\u62E9 " + ids[0] + "\uFF1B\u5982\u4E0D\u5BF9\u8BF7\u5728\u6E38\u620F\u5185\u6267\u884C /agent_control \u91CD\u65B0\u8BBE\u7F6E");
    } else {
      warnings.push("\u8FD8\u6CA1\u6709\u627E\u5230\u73A9\u5BB6 ID\uFF1A\u9996\u6B21\u8BFB\u6863\u540E\u8BF7\u5728\u6E38\u620F\u5185\u6267\u884C /agent_control\uFF0C\u6216\u518D\u6B21\u8FD0\u884C\u5B89\u88C5");
    }
  }
  writeFileSync3(configPath, JSON.stringify(config, null, 2) + "\n", { mode: 384 });
}
function describeError(error) {
  if (error instanceof Error) {
    const cause = error.cause;
    const causeText = cause instanceof Error ? " (" + cause.message + ")" : "";
    return error.message + causeText;
  }
  return String(error);
}
async function downloadWithCurl(url) {
  const dir = mkdtempSync(join4(tmpdir(), "starcoplay-mod-"));
  const file = join4(dir, "StarCoPlay.zip");
  try {
    await execFileAsync("curl", [
      "--noproxy",
      "*",
      "-L",
      "--fail",
      "--max-time",
      "180",
      "-o",
      file,
      url
    ]);
    return readFileSync4(file);
  } finally {
    rmSync2(dir, { recursive: true, force: true });
  }
}
async function downloadZip(url, fetchImpl) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12e3);
  timer.unref?.();
  try {
    const response = await fetchImpl(url, { redirect: "follow", signal: controller.signal });
    if (!response.ok) {
      throw new Error("HTTP " + response.status + " " + response.statusText);
    }
    return Buffer.from(await response.arrayBuffer());
  } catch (error) {
    try {
      return await downloadWithCurl(url);
    } catch (curlError) {
      throw new Error(
        "fetch \u5931\u8D25\uFF1A" + describeError(error) + "\uFF1Bcurl \u56DE\u9000\u5931\u8D25\uFF1A" + describeError(curlError)
      );
    }
  } finally {
    clearTimeout(timer);
  }
}
async function installMod(options = {}) {
  const warnings = [];
  const paths = options.gamePaths ?? detectGamePaths();
  if (!paths.modsDir) {
    return {
      ok: false,
      message: "\u6CA1\u6709\u627E\u5230\u661F\u9732\u8C37\u7684 Mods \u76EE\u5F55\u3002\u8BF7\u5148\u5B89\u88C5\u6E38\u620F\u548C SMAPI\uFF0C\u6216\u8BBE\u7F6E STARCOPLAY_GAME_PATH\u3002",
      warnings
    };
  }
  if (!paths.smapi) {
    warnings.push("\u6CA1\u6709\u68C0\u6D4B\u5230 SMAPI\uFF1AMod \u4E0D\u4F1A\u88AB\u52A0\u8F7D\uFF0C\u8BF7\u5148\u5B89\u88C5 SMAPI 4.x\u3002");
  }
  const envZip = process.env.STARCOPLAY_MOD_ZIP;
  let buffer;
  try {
    if (options.zipBuffer) {
      buffer = options.zipBuffer;
    } else if (options.zipPath) {
      buffer = readFileSync4(options.zipPath);
    } else if (envZip) {
      buffer = readFileSync4(envZip);
    } else {
      buffer = await downloadZip(
        options.url || DEFAULT_MOD_ZIP_URL,
        options.fetchImpl || fetch
      );
    }
  } catch (error) {
    return {
      ok: false,
      message: "\u65E0\u6CD5\u83B7\u53D6 Mod \u5B89\u88C5\u5305\uFF1A" + (error instanceof Error ? error.message : String(error)),
      warnings
    };
  }
  let zip;
  try {
    zip = new AdmZip(buffer);
  } catch (error) {
    return {
      ok: false,
      message: "Mod \u5B89\u88C5\u5305\u4E0D\u662F\u6709\u6548\u7684 zip\uFF1A" + (error instanceof Error ? error.message : String(error)),
      warnings
    };
  }
  const entries = zip.getEntries();
  const manifest = findManifest(entries);
  if (!manifest) {
    return {
      ok: false,
      message: "\u5B89\u88C5\u5305\u91CC\u6CA1\u6709\u627E\u5230 StarCoPlay \u7684 manifest.json\u3002",
      warnings
    };
  }
  const modRoot = join4(paths.modsDir, "StarCoPlay");
  mkdirSync3(modRoot, { recursive: true });
  let skipped = 0;
  for (const entry of entries) {
    if (entry.isDirectory) continue;
    const entryName = normalizeEntryName(entry.entryName);
    if (!entryName.startsWith(manifest.prefix)) continue;
    const relative = entryName.slice(manifest.prefix.length);
    if (isSkipRelative(relative)) continue;
    if (!safeRelative(relative)) {
      warnings.push("\u8DF3\u8FC7\u4E0D\u5B89\u5168\u7684 zip \u8DEF\u5F84\uFF1A" + entryName);
      skipped += 1;
      continue;
    }
    const target = resolve(modRoot, relative);
    if (target !== modRoot && !target.startsWith(modRoot + sep)) {
      warnings.push("\u8DF3\u8FC7\u8D8A\u754C\u7684 zip \u8DEF\u5F84\uFF1A" + entryName);
      skipped += 1;
      continue;
    }
    mkdirSync3(dirname2(target), { recursive: true });
    writeFileSync3(target, entry.getData());
  }
  if (skipped > 0) {
    warnings.push("\u6709 " + skipped + " \u4E2A\u6587\u4EF6\u56E0\u8DEF\u5F84\u4E0D\u5B89\u5168\u88AB\u8DF3\u8FC7\u3002");
  }
  mergeConfig(modRoot, paths.modsDir, warnings);
  return {
    ok: true,
    message: "\u5DF2\u5B89\u88C5 StarCoPlay " + manifest.version + " \u5230 " + modRoot + "\u3002\u8BF7\u91CD\u542F\u6E38\u620F\u6216\u91CD\u65B0\u8BFB\u6863\u3002",
    modDir: modRoot,
    installedVersion: manifest.version,
    warnings
  };
}

// src/tools/setup.ts
var INSTALL_LABEL = "\u5B89\u88C5\u6700\u65B0\u7248";
var CANCEL_LABEL = "\u6682\u4E0D";
function describeSelection(answer) {
  const selected = answer?.answers?.[0]?.selected;
  return Array.isArray(selected) ? selected.filter((value) => typeof value === "string") : [];
}
function formatInstallResult(result) {
  const lines = [result.message];
  if (result.warnings.length > 0) {
    lines.push("", "\u6CE8\u610F\uFF1A", ...result.warnings.map((warning) => "- " + warning));
  }
  return lines.join("\n");
}
function registerInstallModTool(ctx, getStatus, install = installMod) {
  ctx.tools.register(defineTool4({
    name: "starcoplay_install_mod",
    description: [
      "Install or update the StarCoPlay mod into the Stardew Valley Mods folder.",
      "The player sees a native confirmation dialog before anything is downloaded or written.",
      "Call this when starcoplay_status reports that the mod is missing or outdated;",
      "never claim the mod was installed until the tool result says so."
    ].join(" "),
    parameters: {},
    output: {
      schema: { type: "string" },
      render: (_args, value) => [{ type: "text", text: value }]
    },
    async execute(_args, exec) {
      const status = getStatus();
      if (!status?.game?.gameDir) {
        return "\u6CA1\u6709\u627E\u5230\u661F\u9732\u8C37\u5B89\u88C5\u76EE\u5F55\u3002\u8BF7\u5148\u5B89\u88C5\u6E38\u620F\uFF0C\u6216\u5728\u73AF\u5883\u53D8\u91CF STARCOPLAY_GAME_PATH \u4E2D\u6307\u5B9A\u6E38\u620F\u8DEF\u5F84\u3002";
      }
      const current = status.game.modVersion ? "\u5F53\u524D\u7248\u672C " + status.game.modVersion + "\u3002" : "\u5F53\u524D\u6CA1\u6709\u68C0\u6D4B\u5230 StarCoPlay Mod\u3002";
      const question = {
        id: "install_mod",
        header: "StarCoPlay",
        question: current + "\u662F\u5426\u81EA\u52A8\u5B89\u88C5\u6700\u65B0\u7248 StarCoPlay Mod\uFF1F",
        options: [
          { label: INSTALL_LABEL, description: "\u4ECE GitHub Release \u4E0B\u8F7D\u5E76\u5B89\u88C5\u5230\u6E38\u620F\u7684 Mods \u76EE\u5F55" },
          { label: CANCEL_LABEL, description: "\u5148\u4E0D\u5B89\u88C5\uFF0C\u4E4B\u540E\u53EF\u4EE5\u518D\u8BA9\u6211\u88C5" }
        ]
      };
      let answer;
      try {
        const request = {
          questions: [question],
          signal: exec?.signal,
          wait: { callId: exec?.callId }
        };
        if (exec?.agent !== void 0) request.agent = exec.agent;
        answer = await ctx.userQuestions.ask(request);
      } catch (error) {
        return "\u65E0\u6CD5\u5F39\u51FA\u5B89\u88C5\u786E\u8BA4\u6846\uFF1A" + (error instanceof Error ? error.message : String(error)) + "\n\u8BF7\u624B\u52A8\u4ECE GitHub Release \u4E0B\u8F7D StarCoPlay.zip\uFF0C\u89E3\u538B\u5230 Mods/StarCoPlay\u3002";
      }
      const selected = describeSelection(answer);
      if (!selected.includes(INSTALL_LABEL)) {
        return "\u5DF2\u53D6\u6D88\u5B89\u88C5\u3002\u9700\u8981\u7684\u65F6\u5019\u518D\u53EB\u6211\u3002";
      }
      try {
        const result = await install();
        return formatInstallResult(result);
      } catch (error) {
        return "\u5B89\u88C5\u5931\u8D25\uFF1A" + (error instanceof Error ? error.message : String(error));
      }
    }
  }));
}

// src/tools/launch.ts
import { defineTool as defineTool5 } from "@deepseek-ai/dsh-tools";
function registerLaunchGameTool(ctx, launcher) {
  ctx.tools.register(defineTool5({
    name: "starcoplay_launch_game",
    description: [
      "Launch a SECOND Stardew Valley instance through SMAPI on this machine.",
      "Use it after the player has opened the co-op save in their own game; it does not launch or host the human instance.",
      "The new window starts at the title screen. Automatic co-op join is not enabled yet, so the player still",
      "joins the LAN/friend game once in that window. After joining, the mod connects to this plugin automatically."
    ].join(" "),
    parameters: {},
    output: {
      schema: { type: "string" },
      render: (_args, value) => [{ type: "text", text: value }]
    },
    async execute() {
      const result = await launcher.launch();
      return result.message;
    }
  }));
}

// src/game/launcher.ts
import { spawn } from "child_process";
import { dirname as dirname3 } from "path";
var MIN_MOD_VERSION = "2.6.0";
function versionParts(value) {
  const match = value.match(/\d+(?:\.\d+)*/)?.[0] ?? "";
  return match.split(".").map((part) => Number.parseInt(part, 10) || 0);
}
function versionAtLeast(current, minimum) {
  const a = versionParts(current);
  const b = versionParts(minimum);
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    const left = a[index] ?? 0;
    const right = b[index] ?? 0;
    if (left > right) return true;
    if (left < right) return false;
  }
  return true;
}
var GameLauncher = class {
  child;
  spawnImpl;
  detectPaths;
  graceMs;
  token;
  constructor(deps = {}) {
    this.spawnImpl = deps.spawnImpl ?? spawn;
    this.detectPaths = deps.detectPaths ?? (() => detectGamePaths());
    this.graceMs = Math.max(0, deps.graceMs ?? 1200);
    this.token = deps.token;
  }
  status() {
    const running = Boolean(this.child && this.child.exitCode === null && !this.child.killed);
    return running ? { running: true, pid: this.child?.pid } : { running: false };
  }
  async launch(paths) {
    const current = paths ?? this.detectPaths();
    if (!current.gameDir) {
      return { ok: false, message: "\u6CA1\u6709\u627E\u5230\u661F\u9732\u8C37\u5B89\u88C5\u76EE\u5F55\u3002\u8BF7\u5148\u5B89\u88C5\u6E38\u620F\uFF0C\u6216\u8BBE\u7F6E STARCOPLAY_GAME_PATH\u3002" };
    }
    if (!current.smapiPath) {
      return { ok: false, message: "\u6CA1\u6709\u68C0\u6D4B\u5230 SMAPI\u3002\u7B2C\u4E8C\u4E2A\u5B9E\u4F8B\u9700\u8981\u901A\u8FC7 SMAPI \u542F\u52A8\uFF0C\u8BF7\u5148\u5B89\u88C5 SMAPI 4.x\u3002" };
    }
    if (!current.modVersion) {
      return { ok: false, message: "\u6CA1\u6709\u68C0\u6D4B\u5230 StarCoPlay Mod\u3002\u8BF7\u5148\u5B89\u88C5 Mod " + MIN_MOD_VERSION + " \u6216\u66F4\u65B0\u3002" };
    }
    if (!versionAtLeast(current.modVersion, MIN_MOD_VERSION)) {
      return {
        ok: false,
        message: "\u5F53\u524D Mod \u7248\u672C " + current.modVersion + "\uFF0CV2 \u9700\u8981 " + MIN_MOD_VERSION + " \u624D\u652F\u6301\u5B9E\u4F8B token \u548C\u81EA\u52A8\u76EE\u6807\uFF1B\u8BF7\u5148\u8FD0\u884C Mod \u5B89\u88C5/\u66F4\u65B0\u3002"
      };
    }
    const running = this.status();
    if (running.running) {
      return { ok: true, message: "\u7B2C\u4E8C\u4E2A\u661F\u9732\u8C37\u5B9E\u4F8B\u5DF2\u7ECF\u5728\u8FD0\u884C\uFF08PID=" + running.pid + "\uFF09\u3002", pid: running.pid };
    }
    let child;
    try {
      child = this.spawnImpl(current.smapiPath, [], {
        cwd: dirname3(current.smapiPath),
        detached: true,
        stdio: "ignore",
        windowsHide: false,
        env: this.token ? { ...process.env, STARCOPLAY_LOCAL_DIRECT_TOKEN: this.token } : process.env
      });
    } catch (error) {
      return { ok: false, message: "\u542F\u52A8 SMAPI \u5931\u8D25\uFF1A" + (error instanceof Error ? error.message : String(error)) };
    }
    this.child = child;
    return await new Promise((resolve2) => {
      let settled = false;
      const finish = (result) => {
        if (settled) return;
        settled = true;
        resolve2(result);
      };
      const timer = setTimeout(() => {
        try {
          child.unref();
        } catch {
        }
        finish({
          ok: true,
          message: "\u5DF2\u542F\u52A8\u7B2C\u4E8C\u4E2A\u661F\u9732\u8C37\u5B9E\u4F8B\uFF08SMAPI\uFF09\u3002\u8BF7\u5728\u65B0\u7A97\u53E3\u91CC\u52A0\u5165\u8054\u673A\u5B58\u6863\uFF1B\u52A0\u5165\u540E\u8DEF\u7EBF\u4F1A\u81EA\u52A8\u8FDE\u4E0A\u3002",
          pid: child.pid
        });
      }, this.graceMs);
      timer.unref?.();
      child.once("error", (error) => {
        clearTimeout(timer);
        if (this.child === child) this.child = void 0;
        finish({ ok: false, message: "\u542F\u52A8 SMAPI \u5931\u8D25\uFF1A" + error.message });
      });
      child.once("spawn", () => {
        try {
          child.unref();
        } catch {
        }
      });
      child.once("exit", (code) => {
        clearTimeout(timer);
        if (this.child === child) this.child = void 0;
        if (!settled && code !== null && code !== 0) {
          finish({ ok: false, message: "SMAPI \u542F\u52A8\u540E\u7ACB\u5373\u9000\u51FA\uFF08code=" + code + "\uFF09\u3002" });
        }
      });
    });
  }
  stop() {
    const child = this.child;
    if (!child || child.exitCode !== null) {
      this.child = void 0;
      return;
    }
    try {
      child.kill();
    } catch {
    }
    this.child = void 0;
  }
};

// src/prompt.ts
var SYSTEM_PROMPT = [
  "You are the player's companion in Stardew Valley.",
  "You live in the same farm world and act through the starcoplay_* tools.",
  "The player talks to you in the StarCoPlay chat; your plain replies are not",
  "visible in game, so use starcoplay_say when you want the player to hear you.",
  "",
  "At the start of a session call starcoplay_status. If it reports that the",
  "StarCoPlay mod is missing or outdated, call starcoplay_install_mod: it shows",
  "the player a native confirmation dialog before downloading anything. If SMAPI",
  "is missing, tell the player to install SMAPI first. Do not invent game state.",
  "If the route is not connected, ask the player to load a save and make sure",
  "the StarCoPlay mod is enabled.",
  "",
  "TASKS: the player can hand you work that lasts beyond one reply.",
  "- When the player asks for something durable (build/craft N, collect N,",
  "  clear an area, earn gold, gift someone), call starcoplay_task_add.",
  "  Give a short title, a structured goal, and a completion you can verify:",
  "  inventory_at_least, gold_at_least, or manual_confirm when the world cannot",
  "  be checked automatically. Record derived needs (e.g. 20 chests need wood).",
  "- Confirm in one sentence with starcoplay_say, then start working.",
  "- After each meaningful step call starcoplay_task_report with progress_text",
  '  (e.g. "\u6728\u5934 48/120"), evidence (what you did), and blocked_reason when',
  "  stuck (no stamina, night, missing tool). The in-game HUD mirrors this.",
  "- When you think the task is done, call starcoplay_task_report with",
  '  completed=true. The plugin checks the world; if it reports "\u8FD8\u5DEE\u4E00\u70B9",',
  "  the task is NOT done \u2014 continue or explain why.",
  "- If starcoplay_status reports taskHud.supported=false, the in-game task",
  "  strip is unavailable; ask the player to update the StarCoPlay mod.",
  '- "\u4EFB\u52A1" / "\u8FDB\u5EA6" -> starcoplay_task_list. Pause/cancel/resume ->',
  "  starcoplay_task_update. Only one task is active at a time.",
  "",
  "If the player wants a second AI window for co-op, call",
  "starcoplay_launch_game only after their own co-op save is already running.",
  "It launches a second SMAPI instance. The player still joins the host game in",
  "that window; automatic co-op join is not enabled yet.",
  "",
  "Setup tools: starcoplay_status, starcoplay_install_mod,",
  "starcoplay_launch_game.",
  "",
  "Task tools: starcoplay_task_add, starcoplay_task_list,",
  "starcoplay_task_update, starcoplay_task_report, starcoplay_task_check.",
  "",
  "Available game tools: starcoplay_observe, starcoplay_say, starcoplay_emote,",
  "starcoplay_face, starcoplay_move_to, starcoplay_go_to, starcoplay_do,",
  "starcoplay_use_tool, starcoplay_equip, starcoplay_go_bed,",
  "starcoplay_water_crops, starcoplay_harvest_crops, starcoplay_chop_trees.",
  "",
  "Call starcoplay_observe before deciding. Never invent coordinates, item",
  "names, or map ids. Prefer one concrete action at a time, and report briefly",
  "what you did after the tool result. Do not take destructive actions (selling,",
  "shipping, chopping the player's crops, using tools on them) without an",
  "explicit request."
].join("\n");

// src/version.ts
var VERSION = "0.4.0";

// src/index.ts
var DIAG_DIR = join5(homedir3(), ".starcoplay");
var DIAG_PATH = join5(DIAG_DIR, "dsh-plugin.log");
async function buildUserMessage(text2) {
  const content = [{ type: "text", text: text2 }];
  const source = { kind: "starcoplay-dsh" };
  try {
    const specifier = "@deepseek-ai/dsh-llm";
    const llm = await import(specifier);
    if (llm && typeof llm.createUserMessage === "function") {
      return llm.createUserMessage({ content, source });
    }
    diag("[message] dsh-llm has no createUserMessage export; using local shape");
  } catch (error) {
    diag("[message] dsh-llm import failed: " + (error instanceof Error ? error.message : String(error)));
  }
  return { id: randomUUID3(), role: "user", content, source };
}
var V0_CAPABILITIES = [
  ["observe_current_view", "observe"],
  ["say", "say"],
  ["emote", "emote"],
  ["face", "face"],
  ["move_to_target", "move_to"],
  ["navigate_to_target", "go_to"],
  ["act_on_target", "do"],
  ["use_tool_at_target", "use_tool"],
  ["equip_matching_item", "equip"],
  ["sleep_for_night", "go_bed"],
  ["water_crops", "water_crops"],
  ["harvest_crops", "harvest_crops"],
  ["chop_trees", "chop_trees"]
].map(([capability_id]) => ({
  source_surface: "v2-compat",
  capability_id,
  capability_contract_version: "2.4.5"
}));
function logInfo(ctx, message) {
  diag("[info] " + message);
  try {
    const logger = typeof ctx.logger === "function" ? ctx.logger("starcoplay-dsh") : ctx.logger;
    if (logger?.info) {
      logger.info(message);
      return;
    }
  } catch {
  }
  console.error("[starcoplay-dsh] " + message);
}
function diag(message) {
  const line = "[" + (/* @__PURE__ */ new Date()).toISOString() + "] " + message + "\n";
  try {
    mkdirSync4(DIAG_DIR, { recursive: true });
    appendFileSync(DIAG_PATH, line);
  } catch {
  }
}
var name = "starcoplay-dsh";
var inject = ["tools", "systemPrompt", "userQuestions"];
async function apply(ctx, config = {}) {
  const rendezvousPath = config.rendezvousPath || join5(homedir3(), ".starcoplay", "local-direct", "rendezvous.json");
  const route = new StarCoPlayRouteServer({
    rendezvousPath,
    capabilityEntries: V0_CAPABILITIES,
    logger: (message) => logInfo(ctx, message)
  });
  let routeError;
  try {
    await route.start();
  } catch (error) {
    routeError = error instanceof Error ? error.message : String(error);
    logInfo(ctx, "route start failed: " + routeError);
  }
  const launcher = new GameLauncher({ token: route.sessionToken });
  const getStatus = () => {
    const game = detectGamePaths();
    return {
      pluginVersion: VERSION,
      route: { ...route.status(), lastError: routeError || route.status().lastError },
      game,
      launcher: launcher.status(),
      bridgePlayerIds: listBridgePlayerIds(game.modsDir),
      taskHud: {
        supported: isModVersionAtLeast(game.modVersion, REQUIRED_MOD_VERSION_FOR_HUD),
        requiredModVersion: REQUIRED_MOD_VERSION_FOR_HUD
      }
    };
  };
  registerStatusTool(ctx, getStatus);
  registerInstallModTool(ctx, getStatus);
  registerLaunchGameTool(ctx, launcher);
  registerGameTools(ctx, route);
  const taskBoard = new TaskBoardService({
    store: new TaskStore(),
    getPlayerId: () => getStatus().route.identity?.player_unique_id ?? null,
    onChange: (snapshot) => {
      try {
        route.syncTaskBoard(snapshot);
      } catch {
      }
    }
  });
  registerTaskTools(ctx, route, taskBoard);
  const taskSyncTimer = setInterval(() => {
    try {
      const snapshot = taskBoard.snapshot();
      if (snapshot) route.syncTaskBoard(snapshot);
    } catch {
    }
  }, 5e3);
  taskSyncTimer.unref?.();
  let agentsApi;
  try {
    if (typeof ctx.inject === "function") {
      ctx.inject(["agents"], (scoped) => {
        agentsApi = scoped.agents;
        diag("[agents] service bound");
      });
    }
  } catch (error) {
    diag("[agents] scoped injection failed: " + (error instanceof Error ? error.message : String(error)));
  }
  const pickLiveAgent = () => {
    try {
      if (!agentsApi) return { reason: "agents service not bound yet" };
      const agents = agentsApi;
      const roots = typeof agents.roots === "function" ? agents.roots() : [];
      const list = Array.isArray(roots) ? roots : [];
      const all = typeof agents.list === "function" ? agents.list() : [];
      diag("[lookup] roots=" + list.length + " list=" + (Array.isArray(all) ? all.length : "n/a"));
      const candidate = list[list.length - 1] ?? (Array.isArray(all) ? all[all.length - 1] : void 0);
      if (!candidate) return { reason: "no live agent in registry" };
      if (typeof candidate.followup === "function") return { agent: candidate, reason: "roots/blog.agent" };
      if (candidate.agent && typeof candidate.agent.followup === "function") {
        return { agent: candidate.agent, reason: "roots/blog.agent.agent" };
      }
      return { reason: "agent has no followup()" };
    } catch (error) {
      return { reason: "lookup threw: " + (error instanceof Error ? error.message : String(error)) };
    }
  };
  route.onGameEvent((event) => {
    diag("[event] type=" + event.type + " keys=" + Object.keys(event.data).join(","));
    if (event.type !== "chat_received") return;
    const text2 = typeof event.data.message === "string" ? event.data.message.trim() : "";
    if (!text2) return;
    const name2 = typeof event.data.player_name === "string" ? event.data.player_name : "\u73A9\u5BB6";
    logInfo(ctx, "in-game chat from " + name2 + ": " + text2);
    const { agent, reason } = pickLiveAgent();
    diag("[deliver] agent=" + Boolean(agent) + " reason=" + reason);
    if (!agent) {
      logInfo(ctx, "in-game chat dropped: " + reason);
      return;
    }
    void (async () => {
      try {
        const message = await buildUserMessage(
          "[\u661F\u9732\u8C37\u6E38\u620F\u5185\u804A\u5929] " + name2 + "\uFF1A" + text2 + "\n\uFF08\u73A9\u5BB6\u662F\u5728\u6E38\u620F\u91CC\u8BF4\u7684\u3002\u7528 starcoplay_say \u5728\u6E38\u620F\u804A\u5929\u91CC\u7B80\u77ED\u56DE\u4E00\u53E5\uFF0C\u4E0D\u8981\u53EA\u5728 DSH \u91CC\u56DE\u590D\u3002\uFF09"
        );
        agent.followup(message);
        diag("[deliver] followup() accepted id=" + String(message?.id));
        try {
          const sessions = ctx.sessions;
          if (sessions && typeof sessions.flush === "function" && agent.session) {
            await sessions.flush(agent.session);
          }
        } catch (error) {
          diag("[deliver] session flush skipped: " + (error instanceof Error ? error.message : String(error)));
        }
      } catch (error) {
        diag("[deliver] followup() threw: " + (error instanceof Error ? error.stack ?? error.message : String(error)));
        logInfo(ctx, "in-game chat delivery failed: " + String(error));
      }
    })();
  });
  try {
    if (typeof ctx.systemPrompt?.section === "function") {
      ctx.systemPrompt.section({
        name: "starcoplay",
        order: 100,
        text: SYSTEM_PROMPT
      });
    }
  } catch (error) {
    logInfo(ctx, "system prompt section failed: " + String(error));
  }
  if (typeof ctx.effect === "function") {
    ctx.effect(() => () => {
      void route.stop();
    }, "starcoplay-dsh");
  }
  logInfo(ctx, "loaded v" + VERSION);
}
export {
  apply,
  diag,
  inject,
  name
};
