using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.Bridge;

namespace StarCoPlay.Integrations
{
    public static class GmcmIntegration
    {
        private static IGenericModConfigMenuApi? _api;

        // 测试结果缓存（paragraph 展示用）
        private static string? _lastTokenTestResult;
        private static string? _lastConnTestResult;

        // 伪按钮状态（bool toggle 触发后自动复位）
        private static bool _triggerTokenTest;
        private static bool _triggerConnTest;

        public static void Register(
            IModHelper helper,
            IManifest manifest,
            Func<ModConfig> getConfig,
            Action saveConfig,
            Action onConfigChanged,
            Func<WsBridge?> getWsBridge)
        {
            _api ??= helper.ModRegistry.GetApi<IGenericModConfigMenuApi>("spacechase0.GenericModConfigMenu");
            if (_api == null) return;
            var api = _api;
            var registeredControlEnabled = getConfig().EnableAIControl;
            var registeredControlTarget = getConfig().TargetPlayerId?.Trim();

            // 重新注册时先清除旧菜单
            api.Unregister(manifest);

            api.Register(
                mod: manifest,
                reset: () =>
                {
                    var defaultConfig = new ModConfig();
                    var config = getConfig();
                    config.EnableAIControl = defaultConfig.EnableAIControl;
                    config.EnableDailyDemo = defaultConfig.EnableDailyDemo;
                    config.StuckThresholdTicks = defaultConfig.StuckThresholdTicks;
                    config.EnableFileLogging = defaultConfig.EnableFileLogging;
                    config.MaxLogFileSizeMB = defaultConfig.MaxLogFileSizeMB;
                    config.BridgeMode = defaultConfig.BridgeMode;
                    config.WsUrl = defaultConfig.WsUrl;
                    config.PlatformToken = defaultConfig.PlatformToken;
                    config.TargetPlayerId = defaultConfig.TargetPlayerId;
                    config.UseInputBridgeHandlers = defaultConfig.UseInputBridgeHandlers;
                    _lastTokenTestResult = null;
                    _lastConnTestResult = null;
                    helper.WriteConfig(config);
                },
                save: () =>
                {
                    var config = getConfig();
                    var currentTarget = config.TargetPlayerId?.Trim();
                    var controlChanged = config.EnableAIControl != registeredControlEnabled
                        || !string.Equals(currentTarget, registeredControlTarget, StringComparison.Ordinal);
                    if (controlChanged)
                    {
                        if (config.EnableAIControl && string.IsNullOrWhiteSpace(currentTarget)
                            && Context.IsWorldReady && Game1.player != null)
                        {
                            currentTarget = ((ulong)Game1.player.UniqueMultiplayerID).ToString();
                            config.TargetPlayerId = currentTarget;
                        }

                        var changed = AgentControlSnapshot.Normalize(
                            config.EnableAIControl,
                            currentTarget,
                            config.AgentControlRevision);
                        changed = changed.AdvanceAfter(getWsBridge()?.AcceptedAgentControlRevision ?? -1);
                        changed.ApplyTo(config);
                        registeredControlEnabled = config.EnableAIControl;
                        registeredControlTarget = config.TargetPlayerId;
                    }
                    // 处理伪按钮触发
                    if (_triggerTokenTest)
                    {
                        _triggerTokenTest = false;
                        _lastTokenTestResult = RunTokenValidation(config.PlatformToken);
                    }
                    if (_triggerConnTest)
                    {
                        _triggerConnTest = false;
                        _lastConnTestResult = RunConnectionTest(config, getWsBridge());
                    }

                    saveConfig();
                    onConfigChanged();
                }
            );

            // ── 快速开始 ──
            api.AddSectionTitle(manifest, () => "快速开始");
            api.AddParagraph(manifest, () =>
                "1) 前往 star-coplay.luosea.cn 注册账号并复制 Token\n" +
                "2) 粘贴到下方「平台 Token」\n" +
                "3) 打开「启用 AI 控制」即可唤醒搭子\n" +
                "L1 动作层（InputBridge）默认已开启，云端联调无需再改高级项。\n" +
                "保存后自动连接，无需额外程序。");

            // ── 连接设置 ──
            api.AddSectionTitle(manifest, () => "连接设置");

            api.AddTextOption(
                manifest,
                getValue: () => getConfig().PlatformToken ?? "",
                setValue: v => getConfig().PlatformToken = (v ?? "").Trim(),
                name: () => "平台 Token",
                tooltip: () => "在 star-coplay.luosea.cn 注册后获取的 Token"
            );

            // Token 验证触发器（伪按钮：勾选后保存即触发验证）
            api.AddBoolOption(
                manifest,
                getValue: () => _triggerTokenTest,
                setValue: v => _triggerTokenTest = v,
                name: () => "验证 Token",
                tooltip: () => "勾选后点击保存，将验证 Token 是否有效。验证后自动取消勾选。"
            );

            // 连接状态 + 测试结果
            api.AddParagraph(manifest, () => GetStatusText(getConfig(), getWsBridge()));

            // 连通性测试触发器
            api.AddBoolOption(
                manifest,
                getValue: () => _triggerConnTest,
                setValue: v => _triggerConnTest = v,
                name: () => "测试连接",
                tooltip: () => "勾选后点击保存，将测试与云端 Gateway 的连通性。测试后自动取消勾选。"
            );

            api.AddBoolOption(
                manifest,
                getValue: () => getConfig().EnableAIControl,
                setValue: v => getConfig().EnableAIControl = v,
                name: () => "启用 AI 控制",
                tooltip: () => "开启后 AI 搭子会自主行动。Token 未填写时此开关无效。"
            );

            // ── 游戏设置 ──
            api.AddSectionTitle(manifest, () => "游戏设置");

            // 动态生成在线玩家下拉列表
            var playerChoices = BuildPlayerChoices();
            api.AddTextOption(
                manifest,
                getValue: () => getConfig().TargetPlayerId ?? "",
                setValue: v => getConfig().TargetPlayerId = string.IsNullOrWhiteSpace(v) ? null : v.Trim(),
                name: () => "AI 控制的玩家",
                tooltip: () => "选择哪个玩家由 AI 搭子控制。\n留空 = 控制本机玩家。\n联机时会列出所有在线玩家。",
                allowedValues: playerChoices.Keys.ToArray(),
                formatAllowedValue: key => playerChoices.TryGetValue(key, out var label) ? label : key
            );

            api.AddBoolOption(
                manifest,
                getValue: () => getConfig().EnableDailyDemo,
                setValue: v => getConfig().EnableDailyDemo = v,
                name: () => "每日 Demo",
                tooltip: () => "每天自动执行简单示范行为（浇水、喂动物等）"
            );

            // ── 高级设置 ──
            api.AddSectionTitle(manifest, () => "高级设置");

            api.AddBoolOption(
                manifest,
                getValue: () => getConfig().UseInputBridgeHandlers,
                setValue: v => getConfig().UseInputBridgeHandlers = v,
                name: () => "L1 InputBridge 动作层",
                tooltip: () =>
                    "开启：模拟输入执行原子（interact、菜单、move_to 三模式等），Protocol 2.4.5 / 云端 Agent 推荐。\n" +
                    "关闭：回退旧版直接调游戏 API（无 interact 等能力）。\n" +
                    "需游戏窗口能接收按键。修改后请重启游戏生效。"
            );

            api.AddTextOption(
                manifest,
                getValue: () => getConfig().WsUrl ?? "",
                setValue: v => getConfig().WsUrl = (v ?? "").Trim(),
                name: () => "WebSocket 地址",
                tooltip: () => $"留空时使用默认值：{ModConfig.DefaultWsUrl}\n一般玩家无需修改。"
            );

            api.AddNumberOption(
                manifest,
                getValue: () => getConfig().StuckThresholdTicks,
                setValue: v => getConfig().StuckThresholdTicks = v,
                name: () => "卡住检测阈值（帧）",
                tooltip: () => "角色同一位置停留超过此帧数时判为卡住\n300 帧 ≈ 5 秒",
                min: 60,
                max: 600
            );

            api.AddBoolOption(
                manifest,
                getValue: () => getConfig().EnableFileLogging,
                setValue: v => getConfig().EnableFileLogging = v,
                name: () => "文件日志",
                tooltip: () => "将详细日志写入 Mods/StarCoPlay/logs/"
            );

            api.AddNumberOption(
                manifest,
                getValue: () => getConfig().MaxLogFileSizeMB,
                setValue: v => getConfig().MaxLogFileSizeMB = v,
                name: () => "日志大小上限（MB）",
                tooltip: () => "超过后自动轮换新文件",
                min: 1,
                max: 50
            );
        }

        /// <summary>HTTP 调用验证 Token 有效性。</summary>
        private static string RunTokenValidation(string? token)
        {
            if (string.IsNullOrWhiteSpace(token))
                return "验证失败：Token 为空";

            try
            {
                using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
                var body = JsonSerializer.Serialize(new { platform_token = token });
                var content = new StringContent(body, Encoding.UTF8, "application/json");
                var response = client.PostAsync("https://star-coplay.luosea.cn/api/validate-token", content)
                    .GetAwaiter().GetResult();
                var json = response.Content.ReadAsStringAsync().GetAwaiter().GetResult();
                using var doc = JsonDocument.Parse(json);
                var root = doc.RootElement;

                if (root.TryGetProperty("valid", out var validEl) && validEl.GetBoolean())
                {
                    var nickname = root.TryGetProperty("nickname", out var nEl) ? nEl.GetString() : "?";
                    return $"Token 验证通过 — 用户：{nickname}";
                }
                else
                {
                    var error = root.TryGetProperty("error", out var eEl) ? eEl.GetString() : "unknown";
                    return $"Token 无效：{error}";
                }
            }
            catch (Exception ex)
            {
                return $"验证请求失败：{ex.Message}";
            }
        }

        /// <summary>测试平台连通性（检查 WsBridge 状态或尝试 HTTP ping）。</summary>
        private static string RunConnectionTest(ModConfig config, WsBridge? ws)
        {
            // 如果已有 WsBridge 且已连接，直接返回状态
            if (ws != null && ws.ConnectionState == WsConnectionState.Connected)
                return $"连接正常 — Agent: {ws.AgentInstanceId ?? "?"}";

            if (ws != null && ws.ConnectionState == WsConnectionState.AuthError)
                return $"连接失败（认证错误）：{ws.FatalReason}";

            // 尝试 HTTP 请求 Gateway 健康端点
            var url = config.EffectiveWsUrl();
            try
            {
                // 将 ws:// 转为 http:// 尝试 ping（Gateway 通常在同一端口提供 HTTP）
                var httpUrl = url.Replace("wss://", "https://").Replace("ws://", "http://");
                // 去掉 /ws 路径，尝试根路径
                var baseUrl = httpUrl.TrimEnd('/');
                if (baseUrl.EndsWith("/ws")) baseUrl = baseUrl[..^3];

                using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
                var response = client.GetAsync(baseUrl + "/health").GetAwaiter().GetResult();
                if (response.IsSuccessStatusCode)
                    return $"Gateway 可达（{baseUrl}）";
                else
                    return $"Gateway 响应异常：HTTP {(int)response.StatusCode}";
            }
            catch (Exception ex)
            {
                return $"Gateway 不可达：{ex.Message}";
            }
        }

        /// <summary>组合连接状态 + 测试结果文本。</summary>
        private static string GetStatusText(ModConfig config, WsBridge? ws)
        {
            var status = GetConnectionStatusText(config, ws);
            var parts = new List<string> { status };

            if (_lastTokenTestResult != null)
                parts.Add($"Token 测试：{_lastTokenTestResult}");
            if (_lastConnTestResult != null)
                parts.Add($"连接测试：{_lastConnTestResult}");

            return string.Join("\n", parts);
        }

        private static Dictionary<string, string> BuildPlayerChoices()
        {
            var choices = new Dictionary<string, string>
            {
                [""] = "（本机玩家）"
            };

            if (!Context.IsWorldReady) return choices;

            try
            {
                foreach (var farmer in Game1.getOnlineFarmers())
                {
                    var id = ((ulong)farmer.UniqueMultiplayerID).ToString();
                    var name = farmer.Name;
                    var isHost = farmer.IsMainPlayer ? " [主机]" : "";
                    choices[id] = $"{name}{isHost} ({id})";
                }
            }
            catch { /* 安全降级 */ }

            return choices;
        }

        private static string GetConnectionStatusText(ModConfig config, WsBridge? ws)
        {
            if (string.IsNullOrWhiteSpace(config.PlatformToken))
                return "连接状态：● 未填 Token — 填入后自动连接";

            if (ws == null)
                return "连接状态：● 待进入存档后建立连接";

            return ws.ConnectionState switch
            {
                WsConnectionState.Connected =>
                    $"连接状态：● 已连接（Agent: {ws.AgentInstanceId ?? "?"}）",
                WsConnectionState.Connecting =>
                    "连接状态：● 正在连接...",
                WsConnectionState.Reconnecting =>
                    "连接状态：● 重连中...",
                WsConnectionState.AuthError =>
                    $"连接状态：● 认证失败 — {ws.FatalReason ?? "Token 无效或已过期"}",
                WsConnectionState.SessionEnded =>
                    $"连接状态：● 会话结束 — {ws.FatalReason ?? "服务端主动断开"}",
                _ =>
                    "连接状态：● 未连接"
            };
        }
    }
}
