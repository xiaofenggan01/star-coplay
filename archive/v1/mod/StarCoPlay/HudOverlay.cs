using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;

namespace StarCoPlay
{
    /// <summary>HUD 显示模式。</summary>
    public enum HudMode
    {
        None,     // 隐藏
        Compact,  // 简约：状态/位置/体力/动作/结果
        Detailed  // 详细：简约 + 命令列表 + 最近操作日志
    }

    /// <summary>
    /// HUD 状态面板 — F7 循环切换三种模式（无 → 简约 → 详细 → 无）。
    /// </summary>
    public class HudOverlay
    {
        private readonly IMonitor _monitor;
        private readonly StateReader _state;
        private readonly ActionExecutor _actions;
        private readonly DebugReporter _reporter;

        private HudMode _mode = HudMode.Compact;

        /*── 绘制参数 ──*/
        private const int PanelX = 10;
        private const int PanelY = 10;
        private const int LineHeight = 26;
        private const int Padding = 8;

        /*── 命令参考（详细模式显示） ──*/
        private static readonly string[] CommandRef = new[]
        {
            "/agent_status    状态概览",
            "/agent_goto X Y  寻路移动",
            "/agent_warp LOC  传送",
            "/agent_equip N   装备",
            "/agent_usetool X Y 使用工具",
            "/agent_drop N    丢弃物品",
            "/agent_ship N    出货",
            "/agent_tool N    工具状态",
            "/agent_value N   物品价值",
            "/agent_npcs      NPC",
            "/agent_objects   附近物品",
            "/agent_emote N   表情",
            "/agent_say TEXT  头顶气泡",
            "/agent_cancel    取消动作",
        };

        public HudOverlay(IMonitor monitor, StateReader state, ActionExecutor actions, DebugReporter reporter)
        {
            _monitor = monitor;
            _state = state;
            _actions = actions;
            _reporter = reporter;
        }

        /// <summary>F7 循环切换模式。</summary>
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
                string label = _mode switch { HudMode.None => "隐藏", HudMode.Compact => "简约", _ => "详细" };
                _monitor.Log($"HUD: {label}", LogLevel.Debug);
            }
        }

        /// <summary>绘制 HUD。</summary>
        public void OnRenderedHud(object? sender, RenderedHudEventArgs e)
        {
            if (_mode == HudMode.None || !Context.IsWorldReady) return;

            var batch = e.SpriteBatch;
            var font = Game1.smallFont;

            // 收集基础信息
            var player = _state.GetPlayerState();
            string agentStatus = _actions.IsBusy ? "忙碌" : "空闲";
            string currentAction = _reporter.CurrentActionName;
            string lastResult = _reporter.LastActionSummary;

            if (_actions.IsBusy && _actions.CurrentState == ActionState.Navigating)
            {
                var target = _actions.NavigationTarget;
                currentAction = $"导航中 → ({target.X},{target.Y})";
            }

            var lines = new List<string>();

            // === 简约信息（两种模式共用） ===
            lines.Add($"[Agent] {agentStatus}");
            lines.Add($"位置: {player.Location} ({player.TileX},{player.TileY})");
            lines.Add($"体力: {player.Energy}/{player.MaxEnergy}  金币: {player.Gold}");
            lines.Add($"动作: {(string.IsNullOrEmpty(currentAction) ? "无" : currentAction)}");
            lines.Add($"上次: {(string.IsNullOrEmpty(lastResult) ? "无" : lastResult)}");

            int compactLineCount = lines.Count;
            int panelWidth = 320;

            if (_mode == HudMode.Detailed)
            {
                panelWidth = 380;

                // === 命令列表 ===
                lines.Add("");
                lines.Add("═ 命令列表 (聊天框输入) ═");
                foreach (var cmd in CommandRef)
                    lines.Add(cmd);

                // === 最近操作日志 ===
                var recent = _reporter.RecentActions;
                if (recent.Count > 0)
                {
                    lines.Add("");
                    lines.Add("═ 最近操作 ═");
                    foreach (var entry in recent)
                        lines.Add(entry.ToString());
                }
            }

            int panelHeight = Padding * 2 + lines.Count * LineHeight;

            // 绘制半透明背景
            DrawBox(batch, PanelX, PanelY, panelWidth, panelHeight, new Color(0, 0, 0, 180));

            // 绘制文本
            for (int i = 0; i < lines.Count; i++)
            {
                var pos = new Vector2(PanelX + Padding, PanelY + Padding + i * LineHeight);

                Color color;
                if (i == 0)
                    color = Color.LightGreen;  // 标题
                else if (_mode == HudMode.Detailed && i > compactLineCount)
                {
                    // 详细模式的分隔标题用高亮，其余用浅灰
                    if (lines[i].StartsWith("═"))
                        color = Color.Gold;
                    else if (i > lines.Count - _reporter.RecentActions.Count - 1 && _reporter.RecentActions.Count > 0)
                        color = Color.LightGray;
                    else
                        color = new Color(200, 220, 255); // 命令列表淡蓝
                }
                else
                    color = Color.White;

                // 阴影 + 文字
                batch.DrawString(font, lines[i], pos + Vector2.One, Color.Black * 0.5f);
                batch.DrawString(font, lines[i], pos, color);
            }

            // 右下角模式提示
            string hint = _mode == HudMode.Compact ? "F7→详细" : "F7→隐藏";
            var hintPos = new Vector2(PanelX + panelWidth - font.MeasureString(hint).X - Padding, PanelY + panelHeight - LineHeight);
            batch.DrawString(font, hint, hintPos + Vector2.One, Color.Black * 0.3f);
            batch.DrawString(font, hint, hintPos, Color.Gray);
        }

        /// <summary>绘制半透明矩形。</summary>
        private static void DrawBox(SpriteBatch batch, int x, int y, int width, int height, Color color)
        {
            batch.Draw(Game1.staminaRect, new Rectangle(x, y, width, height), color);
        }
    }
}
