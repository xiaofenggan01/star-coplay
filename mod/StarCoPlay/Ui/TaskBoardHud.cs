using System;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;

namespace StarCoPlay
{
    /// <summary>
    /// Persistent top-left task strip fed by the DSH plugin.
    /// Read-only: it never captures input and hides itself while a menu or event is open.
    /// </summary>
    public class TaskBoardHud
    {
        private readonly ITranslationHelper _tr;
        private readonly Func<ModConfig> _config;

        private const int PanelWidth = 300;
        private const int Padding = 8;
        private const int DotSize = 8;
        private const int TextGap = 6;
        private const int RightGap = 10;

        private static readonly Color ActiveColor = new(127, 196, 106);
        private static readonly Color QueuedColor = new(154, 147, 138);
        private static readonly Color BlockedColor = new(232, 192, 74);
        private static readonly Color DoneColor = new(185, 230, 165);
        private static readonly Color Ink = new(246, 232, 200);
        private static readonly Color Muted = new(246, 232, 200, 150);
        private static readonly Color PanelColor = new(20, 16, 12);

        public TaskBoardHud(ITranslationHelper translation, Func<ModConfig> config)
        {
            _tr = translation;
            _config = config;
        }

        public void OnRenderedHud(object? sender, RenderedHudEventArgs e)
        {
            if (!Context.IsWorldReady) return;
            if (Game1.activeClickableMenu != null) return;
            if (Game1.CurrentEvent != null) return;

            var config = _config();
            if (config == null || !config.TaskHudEnabled) return;

            var snapshot = TaskBoardState.Current;
            if (snapshot == null) return;

            var showCompleted = snapshot.LastCompletedAt.HasValue
                && (DateTime.UtcNow - snapshot.LastCompletedAt.Value).TotalSeconds <= 3
                && !string.IsNullOrWhiteSpace(snapshot.LastCompletedTitle);

            if (snapshot.Tasks.Count == 0 && !showCompleted) return;

            var font = Game1.smallFont;
            int lineHeight = font.LineSpacing + 4;
            int maxLines = Math.Clamp(config.TaskHudMaxLines, 1, 3);

            var rows = new System.Collections.Generic.List<(string text, string? progress, string? reason, Color color)>();
            int shown = Math.Min(snapshot.Tasks.Count, maxLines);
            for (int i = 0; i < shown; i++)
            {
                var task = snapshot.Tasks[i];
                rows.Add((task.Title, task.ProgressText, task.BlockedReason, StatusColor(task.Status)));
            }

            bool showMore = snapshot.Tasks.Count > shown && maxLines >= 2;
            if (showMore)
            {
                rows.Add((string.Format(_tr.Get("taskhud.more"), snapshot.Tasks.Count - shown), null, null, Muted));
            }
            if (showCompleted)
            {
                rows.Add((string.Format(_tr.Get("taskhud.done"), snapshot.LastCompletedTitle), null, null, DoneColor));
            }

            if (rows.Count == 0) return;

            int panelHeight = Padding * 2 + rows.Count * lineHeight;
            int x = Math.Max(0, config.TaskHudX);
            int y = Math.Max(0, config.TaskHudY);
            if (x + PanelWidth > Game1.uiViewport.Width) x = Math.Max(0, Game1.uiViewport.Width - PanelWidth);
            if (y + panelHeight > Game1.uiViewport.Height) y = Math.Max(0, Game1.uiViewport.Height - panelHeight);

            var batch = e.SpriteBatch;
            float opacity = Math.Clamp(config.TaskHudOpacity, 0.3f, 1f);

            batch.Draw(Game1.fadeToBlackRect, new Rectangle(x, y, PanelWidth, panelHeight),
                PanelColor * opacity);
            batch.Draw(Game1.fadeToBlackRect, new Rectangle(x, y, PanelWidth, 1),
                Color.White * (0.18f * opacity));
            batch.Draw(Game1.fadeToBlackRect, new Rectangle(x, y + panelHeight - 1, PanelWidth, 1),
                Color.White * (0.10f * opacity));

            for (int i = 0; i < rows.Count; i++)
            {
                var (text, progress, reason, color) = rows[i];
                int rowY = y + Padding + i * lineHeight;

                bool isHeader = progress != null || i == 0;
                if (isHeader)
                {
                    batch.Draw(Game1.fadeToBlackRect,
                        new Rectangle(x + Padding, rowY + 6, DotSize, DotSize), color);

                    string progressText = progress ?? "";
                    float progressWidth = string.IsNullOrEmpty(progressText) ? 0f : font.MeasureString(progressText).X;
                    float titleWidth = PanelWidth - Padding - (DotSize + TextGap) - RightGap
                        - (progressWidth > 0 ? progressWidth + TextGap : 0);
                    string title = Fit(font, text, Math.Max(40f, titleWidth));
                    var titlePos = new Vector2(x + Padding + DotSize + TextGap, rowY);
                    batch.DrawString(font, title, titlePos + Vector2.One, Color.Black * 0.5f);
                    batch.DrawString(font, title, titlePos, Ink);

                    if (!string.IsNullOrEmpty(progressText))
                    {
                        var progressPos = new Vector2(x + PanelWidth - RightGap - progressWidth, rowY);
                        batch.DrawString(font, progressText, progressPos + Vector2.One, Color.Black * 0.5f);
                        batch.DrawString(font, progressText, progressPos, Muted);
                    }

                    if (!string.IsNullOrEmpty(reason))
                    {
                        string reasonText = Fit(font, reason, PanelWidth - Padding * 2 - DotSize - TextGap);
                        var reasonPos = new Vector2(x + Padding + DotSize + TextGap, rowY + font.LineSpacing - 2);
                        batch.DrawString(font, reasonText, reasonPos + Vector2.One, Color.Black * 0.5f);
                        batch.DrawString(font, reasonText, reasonPos, BlockedColor);
                    }
                }
                else
                {
                    string line = Fit(font, text, PanelWidth - Padding * 2 - DotSize - TextGap);
                    var pos = new Vector2(x + Padding + DotSize + TextGap, rowY);
                    batch.DrawString(font, line, pos + Vector2.One, Color.Black * 0.5f);
                    batch.DrawString(font, line, pos, color);
                }
            }
        }

        private static Color StatusColor(string status)
        {
            return status switch
            {
                "active" => ActiveColor,
                "blocked" => BlockedColor,
                "done" => DoneColor,
                _ => QueuedColor,
            };
        }

        private static string Fit(SpriteFont font, string text, float maxWidth)
        {
            if (string.IsNullOrEmpty(text)) return "";
            if (font.MeasureString(text).X <= maxWidth) return text;
            const string ellipsis = "…";
            for (int length = text.Length - 1; length > 0; length--)
            {
                string candidate = text.Substring(0, length).TrimEnd() + ellipsis;
                if (font.MeasureString(candidate).X <= maxWidth) return candidate;
            }
            return ellipsis;
        }
    }
}
