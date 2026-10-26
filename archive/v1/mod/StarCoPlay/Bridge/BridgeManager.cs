using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading;
using StarCoPlay.Models;
using StardewModdingAPI;

namespace StarCoPlay.Bridge
{
    /// <summary>
    /// 管理 bridge/ 目录生命周期，提供原子写、追加写工具，维护 seq 计数器。
    /// Mod 与 Python Agent 通过此文件桥双向通信。
    /// </summary>
    public class BridgeManager
    {
        private readonly IMonitor _monitor;
        private readonly string _bridgeDir;
        private readonly string _gameStatePath;
        private readonly string _eventsPath;
        private readonly string _commandsPath;
        private readonly string _agentStatusPath;
        private readonly string _toolsSchemaPath;
        private readonly string _modDir;

        private int _seq;
        private long _lastCommandOffset;

        private static readonly JsonSerializerOptions JsonOpts = new()
        {
            PropertyNamingPolicy = new SnakeCaseNamingPolicy(),
            WriteIndented = false
        };

        /// <summary>构造 BridgeManager。</summary>
        /// <param name="monitor">SMAPI 日志接口。</param>
        /// <param name="modDir">Mod 根目录路径。</param>
        public BridgeManager(IMonitor monitor, string modDir)
        {
            _monitor = monitor;
            _modDir = modDir;
            _bridgeDir = Path.Combine(modDir, "bridge");
            _gameStatePath = Path.Combine(_bridgeDir, "game_state.json");
            _eventsPath = Path.Combine(_bridgeDir, "events.jsonl");
            _commandsPath = Path.Combine(_bridgeDir, "commands.jsonl");
            _agentStatusPath = Path.Combine(_bridgeDir, "agent_status.json");
            _toolsSchemaPath = Path.Combine(_bridgeDir, "tools.json");
        }

        /// <summary>bridge/ 目录的绝对路径。</summary>
        public string BridgeDir => _bridgeDir;

        /// <summary>
        /// 初始化 bridge/ 目录：创建目录、清空事件和命令文件、写入初始状态。
        /// </summary>
        public void Initialize()
        {
            try
            {
                Directory.CreateDirectory(_bridgeDir);

                // 创建空的 events.jsonl 和 commands.jsonl
                File.WriteAllText(_eventsPath, "");
                File.WriteAllText(_commandsPath, "");

                // 创建初始 agent_status.json
                var initStatus = new { status = "disconnected", current_goal = "", last_update = 0 };
                WriteFileAtomic(_agentStatusPath, JsonSerializer.Serialize(initStatus, JsonOpts));

                // 写初始 game_state.json
                var initState = new GameStateSnapshot { Status = "loading" };
                var json = JsonSerializer.Serialize(initState, JsonOpts);
                WriteStateAtomic(json);

                // 复制 tools.json 到 bridge 目录（供 Agent 层发现可用工具）
                var sourceToolsJson = Path.Combine(_modDir, "Bridge", "tools.json");
                if (File.Exists(sourceToolsJson))
                {
                    File.Copy(sourceToolsJson, _toolsSchemaPath, overwrite: true);
                    _monitor.Log("已复制 tools.json 到 bridge 目录", LogLevel.Debug);
                }
                else
                {
                    _monitor.Log($"未找到源 tools.json: {sourceToolsJson}", LogLevel.Warn);
                }

                // 重置内部状态
                _seq = 0;
                _lastCommandOffset = 0;

                _monitor.Log($"Bridge 已初始化: {_bridgeDir}", LogLevel.Info);
            }
            catch (Exception ex)
            {
                _monitor.Log($"Bridge 初始化失败: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>
        /// 关闭 bridge：将 game_state.json 状态设为 shutdown。
        /// </summary>
        public void Shutdown()
        {
            try
            {
                var shutdownState = new GameStateSnapshot { Status = "shutdown" };
                var json = JsonSerializer.Serialize(shutdownState, JsonOpts);
                WriteStateAtomic(json);
                _monitor.Log("Bridge 已关闭 (status: shutdown)", LogLevel.Info);
            }
            catch (Exception ex)
            {
                _monitor.Log($"Bridge 关闭写入失败: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>
        /// 原子写入 game_state.json：先写 .tmp 再 rename，防止 Agent 读到半写文件。
        /// </summary>
        /// <param name="json">完整的 JSON 字符串。</param>
        public void WriteStateAtomic(string json)
        {
            WriteFileAtomic(_gameStatePath, json);
        }

        /// <summary>原子写入文件（先写 .tmp 再 Move）。</summary>
        private void WriteFileAtomic(string path, string content)
        {
            try
            {
                var tmpPath = path + ".tmp";
                File.WriteAllText(tmpPath, content);
                File.Move(tmpPath, path, overwrite: true);
            }
            catch (Exception ex)
            {
                _monitor.Log($"原子写 {Path.GetFileName(path)} 失败: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>
        /// 追加一行 JSON 到 events.jsonl。
        /// </summary>
        /// <param name="jsonLine">单行 JSON（不含换行符）。</param>
        public void AppendEvent(string jsonLine)
        {
            try
            {
                File.AppendAllText(_eventsPath, jsonLine + "\n");
            }
            catch (Exception ex)
            {
                _monitor.Log($"追加事件失败: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>
        /// 线程安全递增并返回下一个序列号。
        /// </summary>
        public int NextSeq()
        {
            return Interlocked.Increment(ref _seq);
        }

        /// <summary>
        /// 读取 commands.jsonl 中上次读取位置之后的新完整行。
        /// 仅处理以换行符结尾的完整行，忽略空行。
        /// </summary>
        /// <returns>新命令列表（可能为空）。</returns>
        public List<AgentCommand> ReadNewCommands()
        {
            var commands = new List<AgentCommand>();

            try
            {
                if (!File.Exists(_commandsPath))
                    return commands;

                using var fs = new FileStream(_commandsPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);

                // 文件被截断（Agent 重写了文件）时重置 offset
                if (fs.Length < _lastCommandOffset)
                    _lastCommandOffset = 0;

                if (fs.Length == _lastCommandOffset)
                    return commands;

                fs.Seek(_lastCommandOffset, SeekOrigin.Begin);

                using var reader = new StreamReader(fs);
                var content = reader.ReadToEnd();

                // 只处理以 \n 结尾的完整行
                int lastNewline = content.LastIndexOf('\n');
                if (lastNewline < 0)
                    return commands; // 没有完整行

                // 截取到最后一个换行符（含）的部分
                var completePart = content.Substring(0, lastNewline + 1);
                _lastCommandOffset += System.Text.Encoding.UTF8.GetByteCount(completePart);

                var lines = completePart.Split('\n', StringSplitOptions.RemoveEmptyEntries);
                foreach (var line in lines)
                {
                    var trimmed = line.Trim();
                    if (string.IsNullOrEmpty(trimmed))
                        continue;

                    try
                    {
                        var cmd = JsonSerializer.Deserialize<AgentCommand>(trimmed, JsonOpts);
                        if (cmd != null)
                            commands.Add(cmd);
                    }
                    catch (JsonException jex)
                    {
                        _monitor.Log($"解析命令行失败: {jex.Message} | 内容: {trimmed}", LogLevel.Warn);
                    }
                }
            }
            catch (Exception ex)
            {
                _monitor.Log($"读取命令文件失败: {ex.Message}", LogLevel.Error);
            }

            return commands;
        }

        /// <summary>
        /// 用默认 JsonSerializerOptions 序列化对象（snake_case）。
        /// </summary>
        public static string Serialize<T>(T obj)
        {
            return JsonSerializer.Serialize(obj, JsonOpts);
        }
    }

    /// <summary>
    /// .NET 6 兼容的 snake_case JSON 命名策略。
    /// .NET 8 有内置 JsonNamingPolicy.SnakeCaseLower，但 .NET 6 需要自定义。
    /// </summary>
    internal sealed class SnakeCaseNamingPolicy : JsonNamingPolicy
    {
        public override string ConvertName(string name)
        {
            if (string.IsNullOrEmpty(name)) return name;

            var sb = new StringBuilder();
            for (int i = 0; i < name.Length; i++)
            {
                char ch = name[i];
                if (char.IsUpper(ch))
                {
                    if (i > 0) sb.Append('_');
                    sb.Append(char.ToLowerInvariant(ch));
                }
                else
                {
                    sb.Append(ch);
                }
            }
            return sb.ToString();
        }
    }
}
