using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;
using StardewModdingAPI;
using StardewValley;

namespace StarCoPlay
{
    /// <summary>
    /// 调试报告器 — 收集动作日志、错误、用户反馈，生成 JSON 测试报告。
    /// 数据流：ActionExecutor/ModEntry → DebugReporter → test-reports/*.json → rsync → VPS 分析。
    /// </summary>
    public class DebugReporter
    {
        private readonly IMonitor _monitor;
        private readonly string _reportsDir;

        /*── 会话数据 ──*/
        private string _sessionId = "";
        private DateTime _sessionStart;
        private readonly List<ActionLogEntry> _actionLog = new();
        private readonly List<ErrorEntry> _errors = new();
        private readonly List<StuckEvent> _stuckEvents = new();
        private readonly List<FeedbackEntry> _userFeedback = new();

        /*── 当前动作追踪 ──*/
        private ActionLogEntry? _currentAction;

        public DebugReporter(IMonitor monitor, string modDirectory)
        {
            _monitor = monitor;
            _reportsDir = Path.Combine(modDirectory, "test-reports");
            Directory.CreateDirectory(_reportsDir);
        }

        /// <summary>当前会话的动作日志条数。</summary>
        public int ActionCount => _actionLog.Count;

        /// <summary>当前会话的错误条数。</summary>
        public int ErrorCount => _errors.Count;

        /// <summary>最近一条动作的结果摘要（供 HUD 显示）。</summary>
        public string LastActionSummary { get; private set; } = "";

        /// <summary>当前正在执行的动作名称（供 HUD 显示）。</summary>
        public string CurrentActionName { get; private set; } = "";

        /*── 最近操作环形缓冲（供 HUD 详细模式） ──*/
        private const int MaxRecentActions = 8;
        private readonly Queue<RecentActionEntry> _recentActions = new();

        /// <summary>最近完成的操作列表（HUD 详细模式显示）。</summary>
        public IReadOnlyCollection<RecentActionEntry> RecentActions => _recentActions;

        /*────────────────────────────────────────────
         *  会话生命周期
         *────────────────────────────────────────────*/

        /// <summary>开始新的测试会话（存档加载时调用）。</summary>
        public void StartSession()
        {
            _sessionStart = DateTime.Now;
            _sessionId = _sessionStart.ToString("yyyy-MM-ddTHH-mm-ss");
            _actionLog.Clear();
            _errors.Clear();
            _stuckEvents.Clear();
            _userFeedback.Clear();
            _currentAction = null;
            LastActionSummary = "";
            CurrentActionName = "";

            _monitor.Log($"DebugReporter: 会话 {_sessionId} 开始", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  动作日志（由 ActionExecutor 回调）
         *────────────────────────────────────────────*/

        /// <summary>记录动作开始。</summary>
        public void LogActionStart(string actionName, Dictionary<string, object>? parameters = null)
        {
            var stateBefore = CaptureStateSnapshot();

            _currentAction = new ActionLogEntry
            {
                Timestamp = DateTime.Now.ToString("HH:mm:ss"),
                Action = actionName,
                Parameters = parameters ?? new Dictionary<string, object>(),
                StateBefore = stateBefore
            };

            CurrentActionName = actionName;

            string paramStr = parameters != null && parameters.Count > 0
                ? " params={" + string.Join(", ", parameters.Select(kv => $"{kv.Key}={kv.Value}")) + "}"
                : "";
            _monitor.Log($"[Action.Start] {actionName}{paramStr} | loc={stateBefore.Location} tile=({stateBefore.Tile?[0]},{stateBefore.Tile?[1]}) energy={stateBefore.Energy}", LogLevel.Debug);
        }

        /// <summary>记录动作完成。</summary>
        public void LogActionEnd(string result, long durationMs = 0)
        {
            if (_currentAction == null) return;

            _currentAction.Result = result;
            _currentAction.DurationMs = durationMs;
            _currentAction.StateAfter = CaptureStateSnapshot();

            _actionLog.Add(_currentAction);

            LastActionSummary = $"{_currentAction.Action} → {result}";
            CurrentActionName = "";

            // 记入最近操作环形缓冲
            string paramStr = _currentAction.Parameters.Count > 0
                ? string.Join(",", _currentAction.Parameters.Select(kv => $"{kv.Value}"))
                : "";
            _recentActions.Enqueue(new RecentActionEntry
            {
                Time = _currentAction.Timestamp,
                Action = _currentAction.Action,
                Params = paramStr,
                Result = result,
                DurationMs = durationMs
            });
            if (_recentActions.Count > MaxRecentActions)
                _recentActions.Dequeue();

            _currentAction = null;

            // 失败/错误结果用 Warn 级别，成功用 Debug
            var logLevel = result.StartsWith("success") ? LogLevel.Debug : LogLevel.Warn;
            _monitor.Log($"[Action.End] {LastActionSummary} ({durationMs}ms)", logLevel);
        }

        /*────────────────────────────────────────────
         *  错误追踪
         *────────────────────────────────────────────*/

        /// <summary>记录错误（自动输出到 SMAPI 日志 Warn 级别）。</summary>
        public void LogError(string source, string message, string? stackTrace = null)
        {
            _errors.Add(new ErrorEntry
            {
                Timestamp = DateTime.Now.ToString("HH:mm:ss"),
                Source = source,
                Message = message,
                StackTrace = stackTrace
            });

            _monitor.Log($"[Error] [{source}] {message}", LogLevel.Warn);
            if (!string.IsNullOrEmpty(stackTrace))
                _monitor.Log($"[Error.Stack] {stackTrace}", LogLevel.Debug);
        }

        /*────────────────────────────────────────────
         *  卡住事件
         *────────────────────────────────────────────*/

        /// <summary>记录卡住检测事件。</summary>
        public void LogStuckEvent(int tileX, int tileY, int stuckTicks)
        {
            _stuckEvents.Add(new StuckEvent
            {
                Timestamp = DateTime.Now.ToString("HH:mm:ss"),
                TileX = tileX,
                TileY = tileY,
                StuckTicks = stuckTicks
            });

            _monitor.Log($"[Stuck] 位置=({tileX},{tileY}) 持续={stuckTicks}ticks ({stuckTicks / 60}s) 当前动作={CurrentActionName}", LogLevel.Warn);
        }

        /*────────────────────────────────────────────
         *  用户反馈
         *────────────────────────────────────────────*/

        /// <summary>记录用户通过 agent_feedback 命令输入的文本。</summary>
        public void AddFeedback(string text)
        {
            _userFeedback.Add(new FeedbackEntry
            {
                Timestamp = DateTime.Now.ToString("HH:mm:ss"),
                Text = text
            });

            _monitor.Log($"DebugReporter: 反馈已记录 — {text}", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  报告生成
         *────────────────────────────────────────────*/

        /// <summary>生成 JSON 测试报告并写入 test-reports/ 目录。</summary>
        public string GenerateReport()
        {
            var report = new TestReport
            {
                SessionId = _sessionId,
                Metadata = new SessionMetadata
                {
                    GameVersion = Game1.version?.ToString() ?? "",
                    SmapiVersion = Constants.ApiVersion.ToString(),
                    ModVersion = "0.3.0",
                    StartedAt = _sessionStart.ToString("yyyy-MM-ddTHH:mm:ss"),
                    EndedAt = DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss")
                },
                ActionLog = new List<ActionLogEntry>(_actionLog),
                Errors = new List<ErrorEntry>(_errors),
                StuckEvents = new List<StuckEvent>(_stuckEvents),
                UserFeedback = new List<FeedbackEntry>(_userFeedback)
            };

            var options = new JsonSerializerOptions
            {
                WriteIndented = true,
                PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
                DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
            };

            string json = JsonSerializer.Serialize(report, options);
            string fileName = $"report-{_sessionId}.json";
            string filePath = Path.Combine(_reportsDir, fileName);

            File.WriteAllText(filePath, json);
            _monitor.Log($"DebugReporter: 报告已生成 — {filePath}", LogLevel.Info);

            return filePath;
        }

        /*────────────────────────────────────────────
         *  状态快照（内部辅助）
         *────────────────────────────────────────────*/
        private static StateSnapshot CaptureStateSnapshot()
        {
            if (!Context.IsWorldReady) return new StateSnapshot();

            var player = Game1.player;
            return new StateSnapshot
            {
                Tile = new[] { (int)player.Tile.X, (int)player.Tile.Y },
                Energy = (int)player.stamina,
                Gold = player.Money,
                Location = player.currentLocation?.Name ?? "Unknown",
                Time = Game1.timeOfDay
            };
        }
    }

    /*────────────────────────────────────────────
     *  报告数据模型
     *────────────────────────────────────────────*/

    public class TestReport
    {
        public string SessionId { get; set; } = "";
        public SessionMetadata Metadata { get; set; } = new();
        public List<ActionLogEntry> ActionLog { get; set; } = new();
        public List<ErrorEntry> Errors { get; set; } = new();
        public List<StuckEvent> StuckEvents { get; set; } = new();
        public List<FeedbackEntry> UserFeedback { get; set; } = new();
    }

    public class SessionMetadata
    {
        public string GameVersion { get; set; } = "";
        public string SmapiVersion { get; set; } = "";
        public string ModVersion { get; set; } = "";
        public string StartedAt { get; set; } = "";
        public string EndedAt { get; set; } = "";
    }

    public class ActionLogEntry
    {
        public string Timestamp { get; set; } = "";
        public string Action { get; set; } = "";
        public Dictionary<string, object> Parameters { get; set; } = new();
        public string Result { get; set; } = "";
        public long DurationMs { get; set; }
        public StateSnapshot? StateBefore { get; set; }
        public StateSnapshot? StateAfter { get; set; }
    }

    public class ErrorEntry
    {
        public string Timestamp { get; set; } = "";
        public string Source { get; set; } = "";
        public string Message { get; set; } = "";
        public string? StackTrace { get; set; }
    }

    public class StuckEvent
    {
        public string Timestamp { get; set; } = "";
        public int TileX { get; set; }
        public int TileY { get; set; }
        public int StuckTicks { get; set; }
    }

    public class FeedbackEntry
    {
        public string Timestamp { get; set; } = "";
        public string Text { get; set; } = "";
    }

    public class StateSnapshot
    {
        public int[]? Tile { get; set; }
        public int Energy { get; set; }
        public int Gold { get; set; }
        public string Location { get; set; } = "";
        public int Time { get; set; }
    }

    /// <summary>最近操作记录（供 HUD 详细模式显示）。</summary>
    public class RecentActionEntry
    {
        public string Time { get; set; } = "";
        public string Action { get; set; } = "";
        public string Params { get; set; } = "";
        public string Result { get; set; } = "";
        public long DurationMs { get; set; }

        public override string ToString()
        {
            string dur = DurationMs > 0 ? $" ({DurationMs}ms)" : "";
            string param = string.IsNullOrEmpty(Params) ? "" : $"({Params})";
            return $"{Time} {Action}{param} → {Result}{dur}";
        }
    }
}
