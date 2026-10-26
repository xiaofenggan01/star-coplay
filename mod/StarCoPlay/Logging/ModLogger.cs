using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Text;
using StardewModdingAPI;

namespace StarCoPlay.Logging
{
    /// <summary>
    /// 双轨日志系统：SMAPI Monitor（用户可见）+ 独立文件日志（开发者排查）。
    /// 批量写入，主线程 Flush，每类文件保留最近 3 份轮转备份。
    ///
    /// 日志文件（logs/）：
    ///   bridge.log        — Bridge 协议通信（commands/events/results），不写 Monitor
    ///   execution.log     — 命令执行详情（V2 capabilities）
    ///   state_changes.log — 游戏状态变更（地图切换、物品变化、对话）
    ///   errors.log        — 所有 Error/Warn 级别日志聚合
    ///
    /// 线程假设：Enqueue 可在任意线程（ConcurrentQueue 保证安全），
    ///            Flush 仅在 SMAPI 主线程（OnUpdateTicked）调用。
    /// </summary>
    public class ModLogger
    {
        private readonly IMonitor _monitor;
        private readonly bool _enableFileLogging;
        private readonly long _maxLogFileBytes;

        // 每类文件最多保留的轮转备份数（当前文件不计）
        private const int MaxRotatedFiles = 3;

        // 日志写入队列（ConcurrentQueue 保证 Enqueue 线程安全）
        private readonly ConcurrentQueue<LogEntry> _logQueue = new();

        // 日志文件路径（初始化后不可变）
        private readonly string _bridgeLogPath;
        private readonly string _executionLogPath;
        private readonly string _stateChangesLogPath;
        private readonly string _errorsLogPath;

        public ModLogger(IMonitor monitor, string modDir, bool enableFileLogging, int maxLogFileSizeMB)
        {
            _monitor = monitor;
            _maxLogFileBytes = (long)maxLogFileSizeMB * 1024 * 1024;

            var logsDir = Path.Combine(modDir, "logs");
            _bridgeLogPath = Path.Combine(logsDir, "bridge.log");
            _executionLogPath = Path.Combine(logsDir, "execution.log");
            _stateChangesLogPath = Path.Combine(logsDir, "state_changes.log");
            _errorsLogPath = Path.Combine(logsDir, "errors.log");

            // 日志目录创建失败时降级为仅 Monitor 模式，不崩溃 Mod
            if (enableFileLogging)
            {
                try
                {
                    Directory.CreateDirectory(logsDir);
                    _enableFileLogging = true;
                }
                catch (Exception ex)
                {
                    _enableFileLogging = false;
                    monitor.Log($"[ModLogger] 无法创建日志目录 {logsDir}，降级到仅 Monitor 模式: {ex.Message}", LogLevel.Warn);
                }
            }
            else
            {
                _enableFileLogging = false;
            }
        }

        /// <summary>
        /// 记录 Bridge 协议通信（bridge.log）。
        /// 不写 SMAPI Monitor（bridge 流量高，避免干扰控制台）。
        /// Warn 及以上级别额外写 Monitor，确保重要 bridge 错误不被静默。
        /// </summary>
        public void LogBridge(string message, LogLevel level = LogLevel.Debug)
        {
            if (level >= LogLevel.Warn)
                _monitor.Log($"[Bridge] {message}", level);

            if (_enableFileLogging)
                _logQueue.Enqueue(new LogEntry("bridge", message, level));
        }

        /// <summary>记录命令执行详情（execution.log + SMAPI Monitor）。</summary>
        public void LogExecution(string message, LogLevel level = LogLevel.Debug)
        {
            _monitor.Log($"[Exec] {message}", level);
            if (_enableFileLogging)
                _logQueue.Enqueue(new LogEntry("execution", message, level));
        }

        /// <summary>记录游戏状态变更（state_changes.log + SMAPI Monitor）。</summary>
        public void LogStateChange(string message, LogLevel level = LogLevel.Debug)
        {
            _monitor.Log($"[State] {message}", level);
            if (_enableFileLogging)
                _logQueue.Enqueue(new LogEntry("state_changes", message, level));
        }

        /// <summary>
        /// 记录错误和警告（errors.log）。
        /// 无论是否启用文件日志，均通过 SMAPI Monitor 输出，确保错误不被静默丢弃。
        /// 调用方无需额外 _monitor.Log。
        /// </summary>
        public void LogError(string message, Exception? ex = null, LogLevel level = LogLevel.Error)
        {
            var fullMessage = ex != null ? $"{message}\n{ex}" : message;
            _monitor.Log($"[ModLogger Error] {fullMessage}", level);  // 无条件写 Monitor
            if (_enableFileLogging)
                _logQueue.Enqueue(new LogEntry("errors", fullMessage, level));
        }

        /// <summary>记录普通日志（仅 SMAPI Monitor，不写文件）。</summary>
        public void Log(string message, LogLevel level = LogLevel.Debug)
        {
            _monitor.Log(message, level);
        }

        /// <summary>
        /// 批量刷新日志队列到文件（在 ModEntry.OnUpdateTicked 中调用，每帧一次）。
        /// 按 category 聚合，每类文件只调用一次 AppendAllText，减少 syscall 数量。
        /// </summary>
        public void Flush()
        {
            if (!_enableFileLogging || _logQueue.IsEmpty)
                return;

            // 按 logPath 聚合本次 Flush 的所有待写条目
            var batches = new Dictionary<string, StringBuilder>();

            while (_logQueue.TryDequeue(out var entry))
            {
                var logPath = entry.Category switch
                {
                    "bridge"        => _bridgeLogPath,
                    "execution"     => _executionLogPath,
                    "state_changes" => _stateChangesLogPath,
                    "errors"        => _errorsLogPath,
                    _               => null
                };

                if (logPath == null) continue;

                if (!batches.TryGetValue(logPath, out var sb))
                {
                    sb = new StringBuilder();
                    batches[logPath] = sb;
                }

                var timestamp = DateTimeOffset.Now.ToString("yyyy-MM-dd HH:mm:ss.fffzzz");
                var levelStr = LevelToString(entry.Level);
                sb.Append($"[{timestamp}] [{levelStr}] {entry.Message}\n");
            }

            // 每类文件只写一次
            foreach (var (logPath, sb) in batches)
            {
                try
                {
                    RotateIfNeeded(logPath);
                    File.AppendAllText(logPath, sb.ToString());
                }
                catch (Exception ex)
                {
                    // 写入失败时输出完整异常（包含堆栈）到 Monitor，不再尝试写文件
                    _monitor.Log($"[ModLogger] 日志写入失败 ({logPath}): {ex}", LogLevel.Error);
                }
            }
        }

        /// <summary>
        /// 日志文件轮转：当前文件超过大小限制时重命名为 .1，最多保留 MaxRotatedFiles 份。
        /// 注意：轮转操作非原子，极端情况（进程崩溃于轮转中途）可能导致文件链断裂。
        /// </summary>
        private void RotateIfNeeded(string logPath)
        {
            if (!File.Exists(logPath)) return;

            try
            {
                if (new FileInfo(logPath).Length < _maxLogFileBytes) return;
            }
            catch
            {
                return; // 无法读取文件信息，跳过轮转
            }

            // 移动旧备份（从最旧到最新倒序），保留 MaxRotatedFiles 份
            for (int i = MaxRotatedFiles - 1; i >= 1; i--)
            {
                var src = $"{logPath}.{i}";
                var dst = $"{logPath}.{i + 1}";
                if (!File.Exists(src)) continue;
                try
                {
                    if (File.Exists(dst)) File.Delete(dst);
                    File.Move(src, dst);
                }
                catch
                {
                    // 单步轮转失败不影响整体，继续尝试
                }
            }

            // 当前文件 -> .1
            try
            {
                var dst1 = $"{logPath}.1";
                if (File.Exists(dst1)) File.Delete(dst1);
                File.Move(logPath, dst1);
            }
            catch
            {
                // 轮转失败时继续追加到原文件（可接受，只是文件变大）
            }
        }

        private static string LevelToString(LogLevel level) => level switch
        {
            LogLevel.Trace => "TRACE",
            LogLevel.Debug => "DEBUG",
            LogLevel.Info  => "INFO ",
            LogLevel.Warn  => "WARN ",
            LogLevel.Error => "ERROR",
            LogLevel.Alert => "ALERT",
            _              => level.ToString().ToUpper().PadRight(5)
        };

        private record LogEntry(string Category, string Message, LogLevel Level);
    }
}
