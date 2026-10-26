using System;
using System.Collections.Generic;
using System.Text.Json;

namespace StarCoPlay
{
    /// <summary>
    /// Latest task-board snapshot pushed by the DSH plugin (`local_task_board`).
    /// The mod only renders it; the plugin owns the data.
    /// </summary>
    public static class TaskBoardState
    {
        private static readonly object Gate = new();
        private static TaskBoardSnapshot? _current;

        public sealed class TaskLine
        {
            public string Id { get; init; } = "";
            public string Title { get; init; } = "";
            public string Status { get; init; } = "proposed";
            public string ProgressText { get; init; } = "";
            public string? BlockedReason { get; init; }
        }

        public sealed class TaskBoardSnapshot
        {
            public long Revision { get; init; }
            public DateTime UpdatedAt { get; init; } = DateTime.UtcNow;
            public List<TaskLine> Tasks { get; init; } = new();
            public string? LastCompletedTitle { get; init; }
            public DateTime? LastCompletedAt { get; init; }
        }

        public static TaskBoardSnapshot? Current
        {
            get { lock (Gate) return _current; }
        }

        public static void Apply(JsonElement board)
        {
            if (board.ValueKind != JsonValueKind.Object) return;

            var tasks = new List<TaskLine>();
            if (board.TryGetProperty("tasks", out var tasksProp) && tasksProp.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in tasksProp.EnumerateArray())
                {
                    if (item.ValueKind != JsonValueKind.Object) continue;
                    tasks.Add(new TaskLine
                    {
                        Id = GetString(item, "id"),
                        Title = GetString(item, "title"),
                        Status = GetString(item, "status"),
                        ProgressText = GetString(item, "progress_text"),
                        BlockedReason = item.TryGetProperty("blocked_reason", out var blocked)
                            && blocked.ValueKind == JsonValueKind.String
                            ? blocked.GetString()
                            : null,
                    });
                }
            }

            string? lastCompletedTitle = null;
            DateTime? lastCompletedAt = null;
            if (board.TryGetProperty("last_completed", out var last) && last.ValueKind == JsonValueKind.Object)
            {
                lastCompletedTitle = GetString(last, "title");
                var at = GetString(last, "at");
                if (DateTime.TryParse(at, out var parsed))
                {
                    lastCompletedAt = parsed.ToUniversalTime();
                }
            }

            var snapshot = new TaskBoardSnapshot
            {
                Revision = board.TryGetProperty("revision", out var revision) && revision.TryGetInt64(out var value)
                    ? value
                    : 0,
                UpdatedAt = DateTime.UtcNow,
                Tasks = tasks,
                LastCompletedTitle = lastCompletedTitle,
                LastCompletedAt = lastCompletedAt,
            };

            lock (Gate)
            {
                if (_current != null && snapshot.Revision < _current.Revision) return;
                _current = snapshot;
            }
        }

        private static string GetString(JsonElement element, string name)
        {
            return element.TryGetProperty(name, out var prop) && prop.ValueKind == JsonValueKind.String
                ? prop.GetString() ?? ""
                : "";
        }

        public static void Clear()
        {
            lock (Gate) _current = null;
        }
    }
}
