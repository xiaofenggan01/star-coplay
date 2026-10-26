using System;
using System.Linq;
using System.Globalization;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using HarmonyLib;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;
using StarCoPlay.Bridge;

namespace StarCoPlay
{
    /// <summary>
    /// 游戏内聊天命令处理器 — 玩家在聊天框输入 /agent_xxx 触发 Agent 功能。
    /// 通过 Harmony 拦截 ChatBox.runCommand，结果输出到聊天框。
    ///
    /// 用户命令（始终可用）：
    ///   /agent_ping         测试服务器连通性
    ///   /agent_token <t>    设置/验证/清除 Token
    ///   /agent_control      列出玩家或指定 AI 控制对象
    ///   /agent_status       查看当前完整状态
    ///   /agent_cancel       取消 AI 当前动作
    ///   /agent_gn           强制回家睡觉
    ///   /agent <指令>       自然语言指令
    ///   /agent_players      列出在线玩家
    ///   /agent_bug <text>   反馈 bug
    ///   /agent_help         命令帮助
    ///
    /// 开发命令（PlatformToken=dev 时额外可用）：
    ///   agent_goto / agent_warp / agent_interact / agent_equip / agent_usetool 等
    /// </summary>
    public class ChatCommandHandler
    {
        public static ChatCommandHandler? Instance { get; private set; }

        private readonly IMonitor _monitor;
        private readonly StateReader _state;
        private readonly ActionExecutor _actions;
        private readonly DebugReporter _reporter;
        private readonly IModHelper _helper;
        private readonly Func<ModConfig> _getConfig;
        private readonly Action _saveConfig;
        private readonly Func<WsBridge?> _getWsBridge;
        private readonly Action _onConfigChanged;
        private readonly Func<bool> _triggerGoBed;
        private readonly Func<string?> _triggerReload;

        /// <summary>开发模式（PlatformToken=dev）启用额外调试命令。</summary>
        private readonly bool _devMode;

        public ChatCommandHandler(
            IMonitor monitor,
            StateReader state,
            ActionExecutor actions,
            DebugReporter reporter,
            IModHelper helper,
            Func<ModConfig> getConfig,
            Action saveConfig,
            Func<WsBridge?> getWsBridge,
            Action onConfigChanged,
            Func<bool> triggerGoBed,
            Func<string?> triggerReload)
        {
            _monitor = monitor;
            _state = state;
            _actions = actions;
            _reporter = reporter;
            _helper = helper;
            _getConfig = getConfig;
            _saveConfig = saveConfig;
            _getWsBridge = getWsBridge;
            _onConfigChanged = onConfigChanged;
            _triggerGoBed = triggerGoBed;
            _triggerReload = triggerReload;
            _devMode = "dev".Equals(getConfig().PlatformToken, StringComparison.OrdinalIgnoreCase);
            Instance = this;
        }

        public bool HandleCommand(string command)
        {
            var parts = command.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length == 0) return false;

            string name = parts[0];
            string[] args = parts.Length > 1 ? parts[1..] : Array.Empty<string>();

            if (!Context.IsWorldReady && name != "agent_help" && name != "agent_ping" && name != "agent_token" && name != "agent_reload")
            {
                ChatMsg("需要加载存档后才能使用", Color.Orange);
                return true;
            }

            // ── 用户命令（始终可用）──
            switch (name)
            {
                case "agent_ping": CmdPing(); return true;
                case "agent_token": CmdToken(args); return true;
                case "agent_control": CmdControl(args); return true;
                case "agent_status": CmdStatus(); return true;
                case "agent_cancel": CmdCancel(); return true;
                case "agent_gn": CmdGoodNight(); return true;
                case "agent_new": CmdNewInstance(); return true;
                case "agent_players": CmdPlayers(); return true;
                case "agent_bug": CmdBug(args); return true;
                case "agent_reload": CmdReload(); return true;
                case "agent_help": CmdHelp(); return true;
                case "agent":
                    if (args.Length < 1) { ChatMsg("用法: /agent <自然语言指令>", Color.Orange); return true; }
                    string directive = string.Join(" ", args);
                    EventEmitter.Instance?.EmitUserDirective(directive, "chat");
                    ChatMsg($"[指令已下达] {directive}", Color.LightGreen);
                    return true;
            }

            // ── 开发命令（仅 dev 模式）──
            if (_devMode)
            {
                switch (name)
                {
                    case "agent_npcs": CmdNpcs(); return true;
                    case "agent_objects": CmdObjects(args); return true;
                    case "agent_farm": CmdFarm(); return true;
                    case "agent_goto": CmdGoto(args); return true;
                    case "agent_goto_npc": CmdGotoNpc(args); return true;
                    case "agent_warp": CmdWarp(args); return true;
                    case "agent_interact": CmdInteract(args); return true;
                    case "agent_equip": CmdEquip(args); return true;
                    case "agent_usetool": CmdUseTool(args); return true;
                    case "agent_drop": CmdDrop(args); return true;
                    case "agent_ship": CmdShip(args); return true;
                    case "agent_tool": CmdToolState(args); return true;
                    case "agent_value": CmdItemValue(args); return true;
                    case "agent_emote": CmdEmote(args); return true;
                    case "agent_report": CmdReport(); return true;
                }
            }

            ChatMsg($"未知命令: /{name}，输入 /agent_help 查看", Color.Orange);
            return true;
        }

        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        //  用户命令
        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        private void CmdPing()
        {
            var config = _getConfig();
            var wsUrl = config.EffectiveWsUrl(); // wss://star-api.luosea.cn/ws 等
            // 从 WS URL 推导 HTTP 健康检查地址
            var httpBase = wsUrl
                .Replace("wss://", "https://")
                .Replace("ws://", "http://");
            if (httpBase.EndsWith("/ws")) httpBase = httpBase[..^3];
            var pingUrl = httpBase.TrimEnd('/') + "/api/ping";

            ChatMsg($"正在测试平台连通性 ({pingUrl})...", Color.Gray);
            try
            {
                using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(8) };
                var sw = System.Diagnostics.Stopwatch.StartNew();
                var resp = client.GetAsync(pingUrl).GetAwaiter().GetResult();
                sw.Stop();
                if (resp.IsSuccessStatusCode)
                    ChatMsg($"平台连通 ✓  延迟 {sw.ElapsedMilliseconds}ms", Color.LightGreen);
                else
                    ChatMsg($"平台响应异常: HTTP {(int)resp.StatusCode}", Color.Orange);
            }
            catch (Exception ex)
            {
                ChatMsg($"平台不可达: {ex.Message}", Color.Salmon);
            }
        }

        private void CmdToken(string[] args)
        {
            var config = _getConfig();

            // 无参数：显示当前状态
            if (args.Length == 0)
            {
                var t = config.PlatformToken ?? "";
                if (string.IsNullOrWhiteSpace(t))
                    ChatMsg("未设置 Token。用法: /agent_token <Token>", Color.Orange);
                else
                    ChatMsg($"当前 Token 已设置（长度 {t.Length}）", Color.LightGreen);
                return;
            }

            // clear：清除 Token
            if (args[0].Equals("clear", StringComparison.OrdinalIgnoreCase))
            {
                config.PlatformToken = "";
                _saveConfig();
                _onConfigChanged();
                ChatMsg("Token 已清除", Color.LightGreen);
                return;
            }

            // 设置 Token 并验证
            var token = string.Join(" ", args).Trim();
            config.PlatformToken = token;
            _saveConfig();

            // dev 模式跳过 API 验证
            if (token.Equals("dev", StringComparison.OrdinalIgnoreCase))
            {
                ChatMsg("Token 已保存（开发模式，跳过线上验证）", Color.LightGreen);
                _onConfigChanged();
                return;
            }

            ChatMsg("Token 已保存，正在验证...", Color.Gray);
            var nickname = ValidateTokenHttp(token);
            _onConfigChanged();
        }

        private void CmdControl(string[] args)
        {
            var config = _getConfig();

            // 无参数：列出所有在线玩家供选择
            if (args.Length == 0)
            {
                var farmers = Game1.getOnlineFarmers().ToList();
                ChatMsg("[在线玩家]", Color.LightGreen);
                for (int i = 0; i < farmers.Count; i++)
                {
                    var f = farmers[i];
                    string marker = "";
                    if (config.TargetPlayerId == ((ulong)f.UniqueMultiplayerID).ToString())
                        marker = " ← AI 控制中";
                    // TargetPlayerId 为空时不标记任何玩家为 AI（需通过 /agent_control 显式设置）
                    ChatMsg($"  {i + 1}. {f.Name}{(f.IsMainPlayer ? " [主机]" : "")} — {f.UniqueMultiplayerID}{marker}");
                }
                ChatMsg("用法: /agent_control <序号|玩家名|ID>  或  /agent_control close", Color.Gray);
                return;
            }

            var arg = args[0].Trim();

            // close / disable：关闭 AI 控制
            if (arg.Equals("close", StringComparison.OrdinalIgnoreCase)
                || arg.Equals("disable", StringComparison.OrdinalIgnoreCase))
            {
                var snapshot = AgentControlSnapshot.Normalize(false, null, config.AgentControlRevision)
                    .AdvanceAfter(_getWsBridge()?.AcceptedAgentControlRevision ?? -1);
                snapshot.ApplyTo(config);
                _saveConfig();
                _helper.Multiplayer.SendMessage(
                    new AgentControlSnapshotMessage
                    {
                        Enabled = snapshot.Enabled,
                        TargetPlayerId = snapshot.TargetPlayerId,
                        Revision = snapshot.Revision,
                    },
                    "StarCoPlay.AIControl",
                    modIDs: new[] { _helper.ModRegistry.ModID });
                ChatMsg("AI 控制已关闭", Color.Orange);
                _onConfigChanged();
                return;
            }

            // 尝试匹配：序号 / 玩家名 / 玩家 ID
            var allFarmers = Game1.getOnlineFarmers().ToList();
            Farmer? target = null;

            // 1. 序号
            if (int.TryParse(arg, out int idx) && idx >= 1 && idx <= allFarmers.Count)
            {
                target = allFarmers[idx - 1];
            }
            // 2. 玩家 ID（ulong）
            else if (ulong.TryParse(arg, out ulong pid))
            {
                target = allFarmers.FirstOrDefault(f => (ulong)f.UniqueMultiplayerID == pid);
            }
            // 3. 玩家名（模糊匹配）
            if (target == null)
            {
                var nameLower = arg.ToLower();
                target = allFarmers.FirstOrDefault(f => f.Name.Equals(arg, StringComparison.OrdinalIgnoreCase))
                      ?? allFarmers.FirstOrDefault(f => f.Name.ToLower().Contains(nameLower));
            }

            if (target == null)
            {
                ChatMsg($"找不到玩家: {arg}。输入 /agent_control 查看在线玩家列表", Color.Orange);
                return;
            }

            var selected = AgentControlSnapshot.Normalize(
                true,
                ((ulong)target.UniqueMultiplayerID).ToString(CultureInfo.InvariantCulture),
                config.AgentControlRevision)
                .AdvanceAfter(_getWsBridge()?.AcceptedAgentControlRevision ?? -1);
            selected.ApplyTo(config);
            _saveConfig();

            _helper.Multiplayer.SendMessage(
                new AgentControlSnapshotMessage
                {
                    Enabled = selected.Enabled,
                    TargetPlayerId = selected.TargetPlayerId,
                    Revision = selected.Revision,
                },
                "StarCoPlay.AIControl",
                modIDs: new[] { _helper.ModRegistry.ModID });

            ChatMsg($"AI 将控制 {target.Name}，已启用", Color.LightGreen);
            _onConfigChanged();
        }

        private void CmdStatus()
        {
            var config = _getConfig();
            var ws = _getWsBridge();
            var world = _state.GetWorldState();
            var player = _state.GetPlayerState();

            // 连接状态
            if (ws != null)
            {
                string connStatus = ws.ConnectionState switch
                {
                    WsConnectionState.Connected => $"已连接（Agent: {ws.AgentInstanceId ?? "?"}）",
                    WsConnectionState.Connecting => "连接中...",
                    WsConnectionState.Reconnecting => "重连中...",
                    WsConnectionState.AuthError => $"认证失败: {ws.FatalReason}",
                    WsConnectionState.SessionEnded => $"会话结束: {ws.FatalReason}",
                    _ => "未连接"
                };
                ChatMsg($"[连接] {connStatus}", ws.IsConnected ? Color.LightGreen : Color.Orange);
            }
            else
            {
                ChatMsg("[连接] 本地文件桥模式", Color.Gray);
            }

            // Token（显示前 4 位 + 验证用户名）
            var hasToken = !string.IsNullOrWhiteSpace(config.PlatformToken);
            if (hasToken)
            {
                var t = config.PlatformToken!;
                var prefix = t.Length >= 4 ? t[..4] + "..." : t;
                if (t.Equals("dev", StringComparison.OrdinalIgnoreCase))
                {
                    ChatMsg($"[Token] {prefix}（开发模式）", Color.LightGreen);
                }
                else
                {
                    // 静默验证获取用户名（不输出验证消息，只取结果）
                    string? nickname = null;
                    try
                    {
                        using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
                        var body = JsonSerializer.Serialize(new { platform_token = t });
                        var content = new StringContent(body, Encoding.UTF8, "application/json");
                        var resp = client.PostAsync("https://star-coplay.luosea.cn/api/validate-token", content)
                            .GetAwaiter().GetResult();
                        var json = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
                        using var doc = JsonDocument.Parse(json);
                        if (doc.RootElement.TryGetProperty("valid", out var vEl) && vEl.GetBoolean()
                            && doc.RootElement.TryGetProperty("nickname", out var nEl))
                            nickname = nEl.GetString();
                    }
                    catch { /* 静默失败 */ }

                    if (nickname != null)
                        ChatMsg($"[Token] {prefix} — {nickname} ✓", Color.LightGreen);
                    else
                        ChatMsg($"[Token] {prefix}（验证失败或不可达）", Color.Orange);
                }
            }
            else
            {
                ChatMsg("[Token] 未设置", Color.Orange);
            }

            // AI 控制
            string aiTarget = "本机玩家";
            if (!string.IsNullOrEmpty(config.TargetPlayerId))
            {
                var f = Game1.getOnlineFarmers().FirstOrDefault(
                    f => ((ulong)f.UniqueMultiplayerID).ToString() == config.TargetPlayerId);
                aiTarget = f != null ? f.Name : config.TargetPlayerId;
            }
            string aiLine = config.EnableAIControl ? $"已启用 → {aiTarget}" : "未启用";
            var gatewayRole = ws?.ConnectionRole ?? "unknown";
            ChatMsg($"[AI] {aiLine} | revision={config.AgentControlRevision} | gateway_role={gatewayRole}",
                config.EnableAIControl ? Color.LightGreen : Color.Orange);

            // Agent 状态
            if (ws != null && ws.IsConnected)
            {
                string agentLine = ws.AgentStatus switch
                {
                    "thinking" => "思考中",
                    "executing" => "忙碌中",
                    "idle" => "空闲",
                    "starting" => "启动中",
                    _ => "就绪"
                };
                ChatMsg($"[Agent] {agentLine}");
            }

            // 游戏信息
            ChatMsg($"[游戏] {world.Season} Y{world.Year} D{world.Day} {TimeHelper.FormatTime(world.Time)} {world.Weather}");
            ChatMsg($"[角色] {player.Location} ({player.TileX},{player.TileY}) 体力:{player.Energy}/{player.MaxEnergy} 金币:{player.Gold}");
        }

        private void CmdCancel()
        {
            _actions.CancelCurrent();
            ChatMsg("动作已取消", Color.LightGreen);
        }

        private void CmdGoodNight()
        {
            // 广播 go_bed 到所有实例，只有 AI 实例会执行
            _helper.Multiplayer.SendMessage(
                new { action = "go_bed" },
                "StarCoPlay.AIControl",
                modIDs: new[] { _helper.ModRegistry.ModID });
            ChatMsg("搭子正在回家睡觉...", Color.LightGreen);
        }

        private void CmdReload()
        {
            ChatMsg("正在重载配置...", Color.Gray);
            var error = _triggerReload();
            if (error == null)
                ChatMsg("配置已应用，连接尝试已启动", Color.LightGreen);
            else
                ChatMsg(error, Color.Salmon);
        }

        private void CmdNewInstance()
        {
            try
            {
                // SMAPI 提供游戏安装目录
                var gameDir = Constants.GamePath;
                string? smapiPath = null;

                // 按平台查找 SMAPI 可执行文件
                if (Constants.TargetPlatform == GamePlatform.Windows)
                {
                    var candidate = System.IO.Path.Combine(gameDir, "StardewModdingAPI.exe");
                    if (System.IO.File.Exists(candidate)) smapiPath = candidate;
                }
                else
                {
                    // macOS / Linux
                    var candidate = System.IO.Path.Combine(gameDir, "StardewModdingAPI");
                    if (System.IO.File.Exists(candidate)) smapiPath = candidate;
                }

                if (smapiPath == null)
                {
                    ChatMsg($"未找到 SMAPI 启动器（目录: {gameDir}）", Color.Orange);
                    return;
                }

                ChatMsg("正在启动第二个游戏实例...", Color.Gray);

                if (Constants.TargetPlatform == GamePlatform.Windows)
                {
                    System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                    {
                        FileName = smapiPath,
                        WorkingDirectory = gameDir,
                        UseShellExecute = true,
                    });
                }
                else
                {
                    // macOS: 通过 Terminal.app 启动（显示 SMAPI 控制台）
                    var escapedPath = smapiPath.Replace("\"", "\\\"");
                    System.Diagnostics.Process.Start("open", $"-a Terminal \"{escapedPath}\"");
                }

                ChatMsg("第二个游戏实例已启动！请在新窗口中加入联机游戏。", Color.LightGreen);
            }
            catch (Exception ex)
            {
                ChatMsg($"启动失败: {ex.Message}", Color.Salmon);
                _monitor.Log($"CmdNewInstance failed: {ex}", LogLevel.Error);
            }
        }

        private void CmdPlayers()
        {
            ChatMsg("[在线玩家]", Color.LightGreen);
            var config = _getConfig();
            foreach (var f in Game1.getOnlineFarmers())
            {
                string host = f.IsMainPlayer ? " [主机]" : "";
                string self = f.UniqueMultiplayerID == Game1.player.UniqueMultiplayerID ? " (本机)" : "";
                string aiMark = "";
                if (config.TargetPlayerId == ((ulong)f.UniqueMultiplayerID).ToString())
                    aiMark = " ← AI";
                ChatMsg($"  {f.Name}{host}{self} — {f.UniqueMultiplayerID}{aiMark}");
            }
        }

        /// <summary>HTTP 调 validate-token API，返回昵称（验证通过）或 null（失败时已输出消息）。</summary>
        private string? ValidateTokenHttp(string token)
        {
            try
            {
                using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
                var body = JsonSerializer.Serialize(new { platform_token = token });
                var content = new StringContent(body, Encoding.UTF8, "application/json");
                var resp = client.PostAsync("https://star-coplay.luosea.cn/api/validate-token", content)
                    .GetAwaiter().GetResult();
                var json = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
                using var doc = JsonDocument.Parse(json);
                var root = doc.RootElement;

                if (root.TryGetProperty("valid", out var v) && v.GetBoolean())
                {
                    var nickname = root.TryGetProperty("nickname", out var n) ? n.GetString() : "?";
                    ChatMsg($"Hello, {nickname}! Token 验证通过 ✓", Color.LightGreen);
                    return nickname;
                }
                else
                {
                    var err = root.TryGetProperty("error", out var e) ? e.GetString() : "unknown";
                    ChatMsg($"Token 无效: {err}", Color.Salmon);
                    return null;
                }
            }
            catch (Exception ex)
            {
                ChatMsg($"验证请求失败: {ex.Message}", Color.Orange);
                return null;
            }
        }

        private void CmdBug(string[] args)
        {
            if (args.Length == 0) { ChatMsg("用法: /agent_bug <问题描述>", Color.Orange); return; }
            string text = string.Join(" ", args);
            _reporter.LogError("user_bug", text);
            ChatMsg($"Bug 已记录，感谢反馈！", Color.LightGreen);
        }

        private void CmdHelp()
        {
            ChatMsg("[Star CoPlay 命令]", Color.LightGreen);
            ChatMsg("/agent <指令>          对 AI 搭子下达指令");
            ChatMsg("/agent_control         查看/设置 AI 控制的玩家");
            ChatMsg("/agent_status          查看完整状态");
            ChatMsg("/agent_cancel          取消 AI 当前动作");
            ChatMsg("/agent_gn              让搭子回家睡觉");
            ChatMsg("/agent_reload          重载配置并重建 Bridge 连接");
            ChatMsg("/agent_new             启动第二个游戏实例");
            ChatMsg("/agent_ping            测试服务器连通性");
            ChatMsg("/agent_token <Token>   设置/验证 Token（clear 清空）");
            ChatMsg("/agent_players         列出在线玩家");
            ChatMsg("/agent_bug <描述>      反馈问题");
            if (_devMode)
                ChatMsg("（开发模式：goto/warp/interact/equip 等调试命令已启用）", Color.Gray);
        }

        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        //  开发命令（仅 PlatformToken=dev 时可用）
        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        private void CmdNpcs()
        {
            var npcs = _state.GetNearbyNpcs(radius: 999);
            if (npcs.Count == 0) { ChatMsg("当前地图无 NPC"); return; }
            ChatMsg($"NPC ({npcs.Count}):");
            foreach (var npc in npcs.Take(5))
                ChatMsg($"  {npc.DisplayName} ({npc.TileX},{npc.TileY}) {npc.Hearts}H");
            if (npcs.Count > 5)
                ChatMsg($"  ...还有 {npcs.Count - 5} 个");
        }

        private void CmdObjects(string[] args)
        {
            int radius = args.Length > 0 && int.TryParse(args[0], out int r) ? r : 10;
            var objects = _state.GetNearbyObjects(radius);
            ChatMsg($"附近物品 (半径{radius}): {objects.Count}");
            foreach (var obj in objects.Take(5))
                ChatMsg($"  ({obj.TileX},{obj.TileY}) {obj.Name} [{obj.Category}]");
            if (objects.Count > 5)
                ChatMsg($"  ...还有 {objects.Count - 5} 个");
        }

        private void CmdFarm()
        {
            var s = _state.GetFarmSummary();
            if (s == null) { ChatMsg("无法获取农场信息", Color.Orange); return; }
            ChatMsg($"农场: {s.CropCount}株作物 | 未浇水:{s.UnwateredCount} | 可收获:{s.HarvestableCount}");
        }

        private void CmdGoto(string[] args)
        {
            if (args.Length < 2 || !int.TryParse(args[0], out int x) || !int.TryParse(args[1], out int y))
            { ChatMsg("用法: /agent_goto <x> <y>", Color.Orange); return; }
            if (_actions.NavigateTo(new Microsoft.Xna.Framework.Point(x, y)))
                ChatMsg($"导航中 → ({x},{y})");
            else
                ChatMsg($"导航失败: ({x},{y}) 不可达", Color.Orange);
        }

        private void CmdGotoNpc(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_goto_npc <name>", Color.Orange); return; }
            string npcName = string.Join(" ", args);
            var npc = Game1.currentLocation?.characters.FirstOrDefault(c =>
                c.Name.Equals(npcName, StringComparison.OrdinalIgnoreCase) ||
                c.displayName.Equals(npcName, StringComparison.OrdinalIgnoreCase));
            if (npc == null) { ChatMsg($"当前地图找不到 {npcName}", Color.Orange); return; }
            _actions.NavigateTo(new Microsoft.Xna.Framework.Point((int)npc.Tile.X, (int)npc.Tile.Y + 1));
            ChatMsg($"导航中 → {npc.displayName}");
        }

        private void CmdWarp(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_warp <location> [x] [y]", Color.Orange); return; }
            int x = 64, y = 15;
            if (args.Length >= 3) { int.TryParse(args[1], out x); int.TryParse(args[2], out y); }
            if (_actions.WarpTo(args[0], x, y))
                ChatMsg($"传送中 → {args[0]}");
            else
                ChatMsg($"传送失败: {args[0]}", Color.Orange);
        }

        private void CmdInteract(string[] args)
        {
            if (args.Length < 2 || !int.TryParse(args[0], out int x) || !int.TryParse(args[1], out int y))
            { ChatMsg("用法: /agent_interact <x> <y>", Color.Orange); return; }
            bool ok = _actions.InteractWithTile(x, y);
            ChatMsg(ok ? $"交互成功 ({x},{y})" : $"({x},{y}) 无可交互对象", ok ? null : Color.Orange);
        }

        private void CmdEquip(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_equip <name>", Color.Orange); return; }
            string name = string.Join(" ", args);
            if (_actions.EquipItemByName(name).Success)
                ChatMsg($"已装备: {name}");
            else
                ChatMsg($"装备失败: 找不到 {name}", Color.Orange);
        }

        private void CmdUseTool(string[] args)
        {
            if (args.Length < 2 || !int.TryParse(args[0], out int x) || !int.TryParse(args[1], out int y))
            { ChatMsg("用法: /agent_usetool <x> <y>", Color.Orange); return; }
            if (_actions.UseToolAt(x, y))
                ChatMsg($"工具使用 → ({x},{y})");
            else
                ChatMsg("工具使用失败", Color.Orange);
        }

        private void CmdDrop(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_drop <name> [count]", Color.Orange); return; }
            int count = -1;
            string name;
            if (args.Length >= 2 && int.TryParse(args[^1], out int c))
            { count = c; name = string.Join(" ", args[..^1]); }
            else
            { name = string.Join(" ", args); }
            if (_actions.DropItem(name, count))
                ChatMsg($"已丢弃: {name}");
            else
                ChatMsg($"丢弃失败: 找不到 {name}", Color.Orange);
        }

        private void CmdShip(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_ship <name> [count]", Color.Orange); return; }
            int count = -1;
            string name;
            if (args.Length >= 2 && int.TryParse(args[^1], out int c))
            { count = c; name = string.Join(" ", args[..^1]); }
            else
            { name = string.Join(" ", args); }
            if (_actions.ShipItem(name, count))
                ChatMsg($"已放入出货箱: {name}");
            else
                ChatMsg($"出货失败: 找不到 {name}", Color.Orange);
        }

        private void CmdToolState(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_tool <工具名>", Color.Orange); return; }
            string name = string.Join(" ", args);
            var ts = _state.GetToolState(name);
            if (ts == null) { ChatMsg($"找不到工具: {name}", Color.Orange); return; }
            string info = $"{ts.ToolName} [{ts.UpgradeName}] 槽位:{ts.SlotIndex}";
            if (ts.WaterLevel.HasValue) info += $" 水量:{ts.WaterLevel}/{ts.WaterCapacity}";
            ChatMsg(info);
        }

        private void CmdItemValue(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_value <物品名>", Color.Orange); return; }
            string name = string.Join(" ", args);
            var v = _state.GetItemValue(name);
            if (v == null) { ChatMsg($"背包中找不到: {name}", Color.Orange); return; }
            ChatMsg($"{v.Name} 售价:{v.SellPrice}g x{v.Stack} = {v.TotalValue}g");
        }

        private void CmdEmote(string[] args)
        {
            if (args.Length < 1)
            {
                var emotes = string.Join(", ", _actions.GetAvailableEmotes().Keys);
                ChatMsg($"用法: /agent_emote <名称>  可用: {emotes}", Color.Orange);
                return;
            }
            string emoteName = string.Join(" ", args);
            if (_actions.DoEmote(emoteName))
                ChatMsg($"表情: {emoteName}");
            else
            {
                var emotes = string.Join(", ", _actions.GetAvailableEmotes().Keys);
                ChatMsg($"未知表情: {emoteName}  可用: {emotes}", Color.Orange);
            }
        }

        private void CmdReport()
        {
            _reporter.GenerateReport();
            ChatMsg($"报告已生成 ({_reporter.ActionCount}条动作, {_reporter.ErrorCount}条错误)");
        }

        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        //  输出辅助
        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        private static void ChatMsg(string msg, Color? color = null)
        {
            Game1.chatBox?.addMessage(msg, color ?? Color.White);
        }
    }

    /*────────────────────────────────────────────
     *  Harmony Patch: 拦截 ChatBox.runCommand
     *────────────────────────────────────────────*/

    [HarmonyPatch(typeof(ChatBox), "runCommand")]
    public static class ChatBoxRunCommandPatch
    {
        public static bool Prefix(string commandText)
        {
            if (ChatCommandHandler.Instance == null) return true;
            if (!commandText.StartsWith("agent_") && commandText != "agent" && !commandText.StartsWith("agent ")) return true;
            return !ChatCommandHandler.Instance.HandleCommand(commandText);
        }
    }

    [HarmonyPatch(typeof(ChatBox), "receiveChatMessage")]
    public static class ChatBoxReceiveChatPatch
    {
        public static void Postfix(long sourceFarmer, int chatKind, LocalizedContentManager.LanguageCode language, string message)
        {
            if (chatKind != 0) return;
            if (EventEmitter.Instance == null) return;

            string playerName = "Unknown";
            if (Game1.getAllFarmers() != null)
            {
                foreach (var farmer in Game1.getAllFarmers())
                {
                    if (farmer.UniqueMultiplayerID == sourceFarmer)
                    {
                        playerName = farmer.Name;
                        break;
                    }
                }
            }

            EventEmitter.Instance.EmitChatReceived(sourceFarmer, playerName, message);
        }
    }
}
