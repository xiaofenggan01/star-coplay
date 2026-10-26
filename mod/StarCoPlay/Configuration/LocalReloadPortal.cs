using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Serialization;
using StardewModdingAPI;

namespace StarCoPlay.Configuration
{
    /// <summary>
    /// 文件型本地控制面 — 供 Runtime Manager/本地 Agent 在不依赖游戏输入、
    /// 窗口焦点、现有 Bridge 连接的情况下触发与 HandleReload 完全相同的逻辑。
    ///
    /// 协议：
    ///   请求：~/.starcoplay/control/reload.request.json（脚本 temp+rename 原子发布）
    ///   结果：~/.starcoplay/control/reload.result.json（Mod 原子写入）
    ///   请求由 Mod 用 File.Move 原子 claim 后在 SMAPI 主线程消费，防止重复。
    ///
    /// 安全：
    ///   目录 0700 / 文件 0600（Unix 尽力；不支持的平台安全降级）。
    ///   请求/结果均不包含 token/secret/config 内容。
    /// </summary>
    public sealed class LocalReloadPortal
    {
        [DllImport("libc", SetLastError = true)]
        private static extern int chmod(string path, uint mode);

        private readonly IMonitor _monitor;
        private readonly string _controlDir;
        private readonly string _requestPath;
        private readonly string _resultPath;

        /// <summary>控制面根目录。</summary>
        public static string DefaultControlDir => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".starcoplay", "control");

        /// <summary>请求文件路径。</summary>
        public static string DefaultRequestPath => Path.Combine(
            DefaultControlDir, "reload.request.json");

        /// <summary>结果文件路径。</summary>
        public static string DefaultResultPath => Path.Combine(
            DefaultControlDir, "reload.result.json");

        public LocalReloadPortal(IMonitor monitor)
            : this(monitor, DefaultControlDir, DefaultRequestPath, DefaultResultPath) { }

        /// <summary>可注入路径的构造函数（测试用）。</summary>
        internal LocalReloadPortal(IMonitor monitor, string controlDir,
            string requestPath, string resultPath)
        {
            _monitor = monitor;
            _controlDir = controlDir;
            _requestPath = requestPath;
            _resultPath = resultPath;
            EnsureDirectory();
        }

        /*────────────────────────────────────────────
         *  目录初始化
         *────────────────────────────────────────────*/

        private void EnsureDirectory()
        {
            try
            {
                Directory.CreateDirectory(_controlDir);
                TrySetOwnerOnlyDir(_controlDir);
            }
            catch (Exception ex)
            {
                _monitor.Log($"[LocalReloadPortal] control dir init failed [{ex.GetType().Name}]", LogLevel.Warn);
            }
        }

        /*────────────────────────────────────────────
         *  请求消费（原子 claim → 反序列化）
         *────────────────────────────────────────────*/

        /// <summary>
        /// 原子 claim 并反序列化请求文件。
        /// 返回 null 表示当前没有待处理的请求。
        /// </summary>
        public ReloadRequest? TryClaimRequest()
        {
            try
            {
                if (!File.Exists(_requestPath))
                    return null;

                var claimPath = _requestPath + ".claimed-" + Guid.NewGuid().ToString("N");
                File.Move(_requestPath, claimPath); // 同目录原子 rename，单 owner claim
                string json;
                try { json = File.ReadAllText(claimPath); }
                finally { File.Delete(claimPath); }

                var request = JsonSerializer.Deserialize<ReloadRequest>(json);
                if (request == null || string.IsNullOrWhiteSpace(request.request_id))
                {
                    _monitor.Log("[LocalReloadPortal] claimed request is null or missing request_id, discarding",
                        LogLevel.Warn);
                    return null;
                }

                _monitor.Log($"[LocalReloadPortal] claimed request {request.request_id}", LogLevel.Info);
                return request;
            }
            catch (FileNotFoundException)
            {
                // 文件在 Exists 与 Move 之间被删除（极竞态），等价于无请求
                return null;
            }
            catch (Exception ex)
            {
                _monitor.Log($"[LocalReloadPortal] claim failed [{ex.GetType().Name}]", LogLevel.Warn);
                return null;
            }
        }

        /*────────────────────────────────────────────
         *  结果写入（temp + rename 原子发布）
         *────────────────────────────────────────────*/

        /// <summary>
        /// 以原子方式写入结果文件。
        /// 输出仅包含稳定字段，不泄露异常 message。
        /// </summary>
        public void WriteResult(string requestId, ReloadResult reloadResult)
        {
            try
            {
                // 重新确保目录存在（热重载期间目录可能被外部删除）
                Directory.CreateDirectory(_controlDir);

                var response = new ReloadResponse
                {
                    request_id = requestId,
                    outcome = MapOutcome(reloadResult.Outcome),
                    detail = reloadResult.Detail,
                    completed_at = DateTime.UtcNow.ToString("o"),
                };

                var tmpPath = _resultPath + ".tmp-" + Guid.NewGuid().ToString("N");
                var json = JsonSerializer.Serialize(response);
                File.WriteAllText(tmpPath, json);
                TrySetOwnerOnlyFile(tmpPath);
                File.Move(tmpPath, _resultPath, overwrite: true);

                _monitor.Log(
                    $"[LocalReloadPortal] result written: {response.request_id} -> {response.outcome}",
                    LogLevel.Info);
            }
            catch (Exception ex)
            {
                _monitor.Log(
                    $"[LocalReloadPortal] write result failed [{ex.GetType().Name}]",
                    LogLevel.Error);
            }
        }

        /*────────────────────────────────────────────
         *  结果映射
         *────────────────────────────────────────────*/

        /// <summary>
        /// ReloadResult.Phase → 稳定蛇形 outcome 字符串。
        /// 不引入新的失败类别，直接映射现有 Phase 枚举。
        /// </summary>
        internal static string MapOutcome(ReloadResult.Phase phase) => phase switch
        {
            ReloadResult.Phase.Succeeded => "succeeded",
            ReloadResult.Phase.RefusedSingleFlight => "refused_single_flight",
            ReloadResult.Phase.RefusedBusy => "refused_busy",
            ReloadResult.Phase.ConfigReadFailed => "config_read_failed",
            ReloadResult.Phase.BridgeRebuildFailed => "bridge_rebuild_failed",
            _ => "unknown",
        };

        /*────────────────────────────────────────────
         *  权限工具
         *────────────────────────────────────────────*/

        /// <summary>尽力设目录 0700；不支持的平台静默降级。</summary>
        internal static void TrySetOwnerOnlyDir(string path)
        {
            try
            {
                if (OperatingSystem.IsWindows()) return; // Windows: 继承父目录 ACL
                _ = chmod(path, 0x1C0); // 0700
            }
            catch
            {
                // 安全降级：目录创建时已有默认 umask 权限，不因 chmod 失败阻塞功能
            }
        }

        /// <summary>尽力设文件 0600。</summary>
        internal static void TrySetOwnerOnlyFile(string path)
        {
            try
            {
                if (OperatingSystem.IsWindows()) return;
                _ = chmod(path, 0x180); // 0600
            }
            catch
            {
                // 安全降级
            }
        }
    }

    /*────────────────────────────────────────────
     *  请求/响应 DTO
     *────────────────────────────────────────────*/

    /// <summary>
    /// 本地重载请求 — 由脚本写入，Mod 消费。
    /// </summary>
    public sealed class ReloadRequest
    {
        /// <summary>唯一请求标识（如 "reload-a1b2c3d4"）。</summary>
        [JsonPropertyName("request_id")]
        public string request_id { get; set; } = "";

        /// <summary>请求时间（ISO 8601 UTC）。</summary>
        [JsonPropertyName("requested_at")]
        public string requested_at { get; set; } = "";
    }

    /// <summary>
    /// 本地重载结果 — 由 Mod 写入，脚本消费。
    /// 不包含 token/secret/config/异常 message 内容。
    /// </summary>
    public sealed class ReloadResponse
    {
        /// <summary>对应请求的 request_id。</summary>
        [JsonPropertyName("request_id")]
        public string request_id { get; set; } = "";

        /// <summary>
        /// 稳定蛇形结果码：
        /// succeeded / refused_single_flight / refused_busy /
        /// config_read_failed / bridge_rebuild_failed / unknown。
        /// </summary>
        [JsonPropertyName("outcome")]
        public string outcome { get; set; } = "";

        /// <summary>稳定 detail（如 "ConfigReadError:IOException"），不会泄露异常 message。</summary>
        [JsonPropertyName("detail")]
        public string? detail { get; set; }

        /// <summary>完成时间（ISO 8601 UTC）。</summary>
        [JsonPropertyName("completed_at")]
        public string completed_at { get; set; } = "";
    }

}
