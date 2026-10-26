using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StarCoPlay.Bridge;

namespace StarCoPlay
{
    public enum HudMode
    {
        None,
        Compact,
        Detailed
    }

    public class HudOverlay
    {
        private readonly IMonitor _monitor;
        private readonly ITranslationHelper _tr;
        private readonly StateReader _state;
        private readonly ActionExecutor _actions;
        private readonly CommandProcessor _commandProcessor;
        private readonly StuckDetector _stuckDetector;
        private readonly StuckRecovery _stuckRecovery;
        private readonly System.Func<bool> _isBridgeActive;
        private readonly System.Func<WsBridge?> _getWsBridge;
        private readonly System.Func<IndicatorContext> _getIndicatorCtx;

        private HudMode _mode = HudMode.Compact;

        private const int PanelX = 10;
        private const int PanelY = 10;
        private const int LineHeight = 24;
        private const int Padding = 8;

        public HudOverlay(
            IMonitor monitor,
            ITranslationHelper translation,
            StateReader state,
            ActionExecutor actions,
            CommandProcessor commandProcessor,
            StuckDetector stuckDetector,
            StuckRecovery stuckRecovery,
            System.Func<bool> isBridgeActive,
            System.Func<WsBridge?> getWsBridge,
            System.Func<IndicatorContext> getIndicatorCtx)
        {
            _monitor = monitor;
            _tr = translation;
            _state = state;
            _actions = actions;
            _commandProcessor = commandProcessor;
            _stuckDetector = stuckDetector;
            _stuckRecovery = stuckRecovery;
            _isBridgeActive = isBridgeActive;
            _getWsBridge = getWsBridge;
            _getIndicatorCtx = getIndicatorCtx;
        }

        /// <summary>HUD 指示器所需的上下文数据（由 ModEntry 每帧聚合）。</summary>
        public struct IndicatorContext
        {
            public bool HasToken;
            public bool EnableAIControl;
            public bool IsAIControlledInstance;
            public string? TargetPlayerName;  // AI 控制的玩家名
        }

        private string T(string key) => _tr.Get(key);

        public void OnButtonPressed(object? sender, ButtonPressedEventArgs e)
        {
            if (e.Button == SButton.F7)
            {
                _mode = _mode switch
                {
                    HudMode.None => HudMode.Compact,
                    HudMode.Compact => HudMode.Detailed,
                    HudMode.Detailed => HudMode.None,
                    _ => HudMode.Compact
                };
                _monitor.Log($"HUD mode: {_mode}", LogLevel.Debug);
            }
        }

        public void OnRenderedHud(object? sender, RenderedHudEventArgs e)
        {
            if (!Context.IsWorldReady) return;

            DrawConnectionIndicator(e.SpriteBatch, Game1.smallFont);

            if (_mode == HudMode.None) return;

            var batch = e.SpriteBatch;
            var font = Game1.smallFont;
            var lines = new List<(string text, Color color)>();

            bool bridgeActive = _isBridgeActive();
            var wsRef = _getWsBridge();
            string connLabel = GetWsStatusLabel(wsRef, bridgeActive);
            Color connColor = GetWsStatusColor(wsRef, bridgeActive);
            lines.Add(($"{T("hud.brand")} · {connLabel}", connColor));

            var player = _state.GetPlayerState();
            var world = _state.GetWorldState();

            if (_mode == HudMode.Compact)
            {
                lines.Add(($"{player.Location} ({player.TileX},{player.TileY})  {TimeHelper.FormatTime(world.Time)}", Color.White));

                var recentCmds = _commandProcessor.RecentV2Commands;
                if (recentCmds.Count > 0)
                {
                    var latest = recentCmds.Last();
                    string pendingInfo = _commandProcessor.HasPendingV2Command
                        ? $" | {T("hud.compact.pending")}"
                        : "";
                    lines.Add(($"{T("hud.compact.last")}: #{latest.Seq} {latest.Status}{pendingInfo}",
                        latest.Status == "OK" ? Color.LightGray : Color.Salmon));
                }
                else if (_commandProcessor.HasPendingV2Command)
                {
                    lines.Add((string.Format(CultureInfo.CurrentCulture, T("hud.ai.pending-line"), _commandProcessor.PendingV2Seq),
                        Color.Yellow));
                }
            }
            else
            {
                lines.Add(("", Color.White));
                lines.Add(($"== {T("hud.section.player")} ==", Color.Gold));

                string facing = player.FacingDirection >= 0 && player.FacingDirection <= 3
                    ? T($"hud.facing.{player.FacingDirection}")
                    : "?";
                lines.Add(($"{T("hud.pos")}: {player.Location} ({player.TileX},{player.TileY}) {T("hud.face")}: {facing}", Color.White));

                float energyPct = player.MaxEnergy > 0 ? (float)player.Energy / player.MaxEnergy * 100 : 0;
                float healthPct = player.MaxHealth > 0 ? (float)player.Health / player.MaxHealth * 100 : 0;
                Color energyColor = energyPct < 20 ? Color.Red : (energyPct < 50 ? Color.Yellow : Color.White);
                lines.Add(($"{T("hud.energy")}: {player.Energy}/{player.MaxEnergy} ({energyPct:F0}%)  {T("hud.hp")}: {player.Health}/{player.MaxHealth} ({healthPct:F0}%)", energyColor));
                var toolName = string.IsNullOrEmpty(player.CurrentToolName) ? T("hud.tool.none") : player.CurrentToolName;
                lines.Add(($"{T("hud.gold")}: {player.Gold}g  {T("hud.tool")}: {toolName}", Color.White));

                lines.Add(("", Color.White));
                lines.Add(($"== {T("hud.section.world")} ==", Color.Gold));
                lines.Add(($"{T("hud.time-line")}: {TimeHelper.FormatTime(world.Time)}  {world.Season} Y{world.Year} D{world.Day} ({world.DayOfWeek})", Color.White));
                var fest = world.IsFestivalDay ? $" [{T("hud.festival")}]" : "";
                lines.Add(($"{T("hud.weather")}: {world.Weather}{fest}", Color.White));

                lines.Add(("", Color.White));
                lines.Add(($"== {T("hud.section.commands")} ==", Color.Gold));
                for (int i = 1; i <= 5; i++)
                    lines.Add((T($"hud.cmdhelp.{i}"), new Color(200, 230, 255)));

                if (_commandProcessor.HasPendingV2Command)
                {
                    lines.Add(("", Color.White));
                    lines.Add((string.Format(CultureInfo.CurrentCulture, T("hud.ai.pending-line"), _commandProcessor.PendingV2Seq),
                        Color.Yellow));
                }

                lines.Add(("", Color.White));
                lines.Add(($"== {T("hud.section.stuck")} ==", Color.Gold));

                bool isStuck = _stuckDetector.IsStuck;
                int stuckTicks = _stuckDetector.StuckTicks;
                int stuckSec = stuckTicks / 60;
                string stuckStatus = isStuck
                    ? string.Format(CultureInfo.CurrentCulture, T("hud.stuck.stuck"), stuckSec)
                    : string.Format(CultureInfo.CurrentCulture, T("hud.stuck.ok"), stuckSec);
                lines.Add(($"{T("hud.stuck.detector")}: {stuckStatus}", isStuck ? Color.Red : Color.LightGray));

                string actionState = _actions.IsBusy
                    ? $"{T("hud.action.busy")} ({_actions.CurrentState})"
                    : T("hud.action.idle");
                lines.Add(($"{T("hud.action.executor")}: {actionState}", _actions.IsBusy ? Color.Yellow : Color.LightGray));
            }

            int panelWidth = _mode == HudMode.Compact ? 380 : 520;
            int panelHeight = Padding * 2 + lines.Count * LineHeight;

            DrawBox(batch, PanelX, PanelY, panelWidth, panelHeight, new Color(0, 0, 0, 160));

            for (int i = 0; i < lines.Count; i++)
            {
                var (text, color) = lines[i];
                if (string.IsNullOrEmpty(text)) continue;

                var pos = new Vector2(PanelX + Padding, PanelY + Padding + i * LineHeight);
                batch.DrawString(font, text, pos + Vector2.One, Color.Black * 0.5f);
                batch.DrawString(font, text, pos, color);
            }

            string hint = _mode == HudMode.Compact ? T("hud.hint.f7-detailed") : T("hud.hint.f7-hide");
            var hintSize = font.MeasureString(hint);
            var hintPos = new Vector2(PanelX + panelWidth - hintSize.X - Padding, PanelY + panelHeight - LineHeight);
            batch.DrawString(font, hint, hintPos + Vector2.One, Color.Black * 0.3f);
            batch.DrawString(font, hint, hintPos, Color.Gray);
        }

        /*──────────────────────────────────────────────────────────────
         *  右上角状态指示器 — 三段式信息 + 分级颜色
         *
         *  ✅ 正常态（绿色）：
         *    ● 连接成功｜小星：忙碌中
         *
         *  ⚠️ 异常态（黄色/红色）：
         *    ● 连接异常
         *      Token 验证失败
         *──────────────────────────────────────────────────────────────*/
        private void DrawConnectionIndicator(SpriteBatch batch, SpriteFont font)
        {
            var ws = _getWsBridge();
            if (ws == null) return; // FileBridge 模式不显示

            var ctx = _getIndicatorCtx();
            var (line1, line2, dotColor) = BuildIndicatorLines(ws, ctx);

            // 测量尺寸
            var line1Size = font.MeasureString(line1);
            float maxWidth = line1Size.X;
            float totalHeight = line1Size.Y;
            if (line2 != null)
            {
                var line2Size = font.MeasureString(line2);
                maxWidth = System.Math.Max(maxWidth, line2Size.X);
                totalHeight += line2Size.Y + 2;
            }

            int dotSize = 10;
            int gap = 6;
            int contentWidth = dotSize + gap + (int)maxWidth;
            int x = Game1.uiViewport.Width - contentWidth - 14;
            int y = 12;

            // 背景
            DrawBox(batch, x - 6, y - 4, contentWidth + 12, (int)totalHeight + 8, new Color(0, 0, 0, 140));

            // 圆点（垂直居中于第一行）
            DrawBox(batch, x, y + (int)(line1Size.Y / 2) - dotSize / 2, dotSize, dotSize, dotColor);

            // 第一行
            var textX = x + dotSize + gap;
            DrawText(batch, font, line1, new Vector2(textX, y), Color.White);

            // 第二行（异常详情，颜色稍暗）
            if (line2 != null)
            {
                var y2 = y + (int)line1Size.Y + 2;
                DrawText(batch, font, line2, new Vector2(textX, y2), new Color(255, 200, 150));
            }
        }

        private static void DrawText(SpriteBatch batch, SpriteFont font, string text, Vector2 pos, Color color)
        {
            batch.DrawString(font, text, pos + Vector2.One, Color.Black * 0.5f);
            batch.DrawString(font, text, pos, color);
        }

        /// <summary>
        /// 构建指示器内容。返回（主行, 副行（null=无异常）, 圆点颜色）。
        /// 检查链路：Token → WS连接 → AI启用 → 角色权限 → Agent就绪 → LLM状态
        /// </summary>
        private (string line1, string? line2, Color dotColor) BuildIndicatorLines(WsBridge ws, IndicatorContext ctx)
        {
            var red = new Color(244, 67, 54);
            var yellow = new Color(255, 193, 7);
            var green = new Color(76, 175, 80);
            var gray = new Color(158, 158, 158);

            // ── 阶段 1：Token ──
            if (!ctx.HasToken)
                return ("未配置", "请在 Mod 设置中填入 Token", gray);

            // ── 阶段 2：WS 连接 ──
            switch (ws.ConnectionState)
            {
                case WsConnectionState.Connecting:
                    return ("连接中...", null, yellow);
                case WsConnectionState.Reconnecting:
                    return ("重连中...", null, yellow);
                case WsConnectionState.AuthError:
                    return ("连接异常", "Token 验证失败", red);
                case WsConnectionState.SessionEnded:
                    var reason = ws.FatalReason ?? "服务端断开";
                    return ("连接异常", reason, red);
                case WsConnectionState.Disconnected:
                    return ("未连接", null, gray);
            }

            // ── 阶段 3：AI 启用 ──
            if (!ctx.EnableAIControl)
                return ("已连接", "AI 控制未启用", yellow);

            if (!ctx.IsAIControlledInstance)
                return ("已连接", "当前角色非 AI 控制目标", yellow);

            // ── 阶段 4：Agent 状态（思考/执行/空闲都是正常运转 → 绿灯）──
            var playerLabel = ctx.TargetPlayerName ?? "搭子";
            var agentStatus = ws.AgentStatus;
            return agentStatus switch
            {
                "thinking" => ($"连接成功｜{playerLabel}：思考中", null, green),
                "executing" => ($"连接成功｜{playerLabel}：忙碌中", null, green),
                "idle" => ($"连接成功｜{playerLabel}：空闲", null, green),
                // starting → Agent 还在初始化，尚未可用
                "starting" => ($"连接成功｜{playerLabel}：启动中", null, yellow),
                _ => ($"连接成功｜{playerLabel}：就绪", null, green),
            };
        }

        private string GetWsStatusLabel(WsBridge? ws, bool bridgeActive)
        {
            if (ws == null) return bridgeActive ? T("hud.status.file-bridge") : T("hud.status.idle");
            return ws.ConnectionState switch
            {
                WsConnectionState.Connected => T("hud.ws.connected"),
                WsConnectionState.Connecting => T("hud.ws.connecting"),
                WsConnectionState.Reconnecting => T("hud.ws.reconnecting"),
                WsConnectionState.AuthError => T("hud.ws.auth-error"),
                WsConnectionState.SessionEnded => T("hud.ws.session-ended"),
                _ => T("hud.ws.disconnected")
            };
        }

        private static Color GetWsStatusColor(WsBridge? ws, bool bridgeActive)
        {
            if (ws == null) return bridgeActive ? Color.LightGreen : Color.Gray;
            return ws.ConnectionState switch
            {
                WsConnectionState.Connected => Color.LightGreen,
                WsConnectionState.Connecting or WsConnectionState.Reconnecting => Color.Yellow,
                WsConnectionState.AuthError or WsConnectionState.SessionEnded => Color.Salmon,
                _ => Color.Gray
            };
        }

        private static void DrawBox(SpriteBatch batch, int x, int y, int width, int height, Color color)
        {
            batch.Draw(Game1.staminaRect, new Rectangle(x, y, width, height), color);
        }
    }
}
