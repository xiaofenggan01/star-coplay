using System;
using System.Threading;
using StardewModdingAPI;

namespace StarCoPlay.Configuration
{
    /// <summary>
    /// 配置重载结果。
    /// </summary>
    public readonly struct ReloadResult
    {
        public enum Phase
        {
            /// <summary>配置已应用，连接尝试已启动（尚未确认 ready）。</summary>
            Succeeded,
            /// <summary>拒绝：上一次重载仍在进行中（单飞保护）。</summary>
            RefusedSingleFlight,
            /// <summary>拒绝：CommandProcessor 或 ProtoDispatcher 正忙。</summary>
            RefusedBusy,
            /// <summary>失败：Helper.ReadConfig 读取/解析失败。</summary>
            ConfigReadFailed,
            /// <summary>失败：Bridge 重建过程中异常。</summary>
            BridgeRebuildFailed,
        }

        public Phase Outcome { get; }
        public string? Detail { get; }
        public bool ConnectionAttemptStarted { get; }

        public bool IsSuccess => Outcome == Phase.Succeeded;

        public ReloadResult(Phase outcome, string? detail = null, bool connectionAttemptStarted = false)
        {
            Outcome = outcome;
            Detail = detail;
            ConnectionAttemptStarted = outcome == Phase.Succeeded && connectionAttemptStarted;
        }

        public override string ToString() => Detail != null
            ? $"ReloadResult({Outcome}, {Detail})"
            : $"ReloadResult({Outcome})";
    }

    /// <summary>
    /// 配置热重载协调器 — 可测试的纯函数 + 编排逻辑。
    ///
    /// 纯函数（可直接单元测试）：
    ///   <see cref="SanitizeReadyLog"/>
    ///
    /// 编排方法：
    ///   <see cref="TryReload"/> — Interlocked 单飞 + delegate busy 检查 → ReadConfig → 回调重建 Bridge。
    /// </summary>
    public class ConfigReloadCoordinator
    {
        private readonly IMonitor _monitor;
        /// <summary>0 = idle, 1 = reloading — Interlocked single-flight gate.</summary>
        private int _isReloading;

        public bool IsReloading => Volatile.Read(ref _isReloading) == 1;

        public ConfigReloadCoordinator(IMonitor monitor)
        {
            _monitor = monitor;
        }

        /*────────────────────────────────────────────
         *  纯函数 — 可直接单元测试
         *────────────────────────────────────────────*/

        /// <summary>
        /// 生成脱敏的 LocalDirect ready Info 日志行。
        /// 输出完整 generation / binding_version / lease_epoch / capability_digest
        /// （均为非 secret 字段，便于证据核对），不含 secret / proof / reconnect_proof / URL query。
        /// </summary>
        public static string SanitizeReadyLog(
            int generation, int bindingVersion, int leaseEpoch, string capabilityDigest)
        {
            var digest = string.IsNullOrEmpty(capabilityDigest) ? "(empty)" : capabilityDigest;
            return $"LocalDirect ready: generation={generation} binding_version={bindingVersion} lease_epoch={leaseEpoch} capability_digest={digest}";
        }

        /*────────────────────────────────────────────
         *  编排
         *────────────────────────────────────────────*/

        /// <summary>
        /// 尝试重载配置并重建 Bridge。
        ///
        /// 步骤：
        ///   1. Interlocked.CompareExchange 单飞检查
        ///   2. 通过 <paramref name="isBusyChecker"/> 检查 busy 状态
        ///   3. 调用 <paramref name="readConfig"/> 读取新配置
        ///   4. 调用 <paramref name="onConfigReloaded"/> 执行 Bridge 重建
        ///   5. 全部成功 → Succeeded（仅表示 config applied + connection attempt started）
        /// </summary>
        /// <param name="readConfig">读取配置的函数（通常为 () => helper.ReadConfig&lt;ModConfig&gt;()）</param>
        /// <param name="isBusyChecker">
        /// Busy 检查 delegate。生产侧传入 lambda 读取 CommandProcessor.IsBusyForProtocol
        /// 与 ProtoCommandDispatcher.IsBusy；测试侧传入可控桩。
        /// 返回 (busy, reason)。
        /// </param>
        /// <param name="onConfigReloaded">Bridge 重建回调。仅在 readConfig 成功后调用。</param>
        public ReloadResult TryReload(
            Func<ModConfig> readConfig,
            Func<(bool busy, string reason)> isBusyChecker,
            Action<ModConfig> onConfigReloaded,
            Func<bool>? connectionAttemptStarted = null)
        {
            _monitor.Log("[Reload] reload requested", LogLevel.Info);

            // 1. Interlocked 单飞
            if (Interlocked.CompareExchange(ref _isReloading, 1, 0) != 0)
            {
                _monitor.Log("[Reload] reload refused: already in progress", LogLevel.Warn);
                return new ReloadResult(ReloadResult.Phase.RefusedSingleFlight);
            }

            try
            {
                // 2. Busy 检查（通过可测 delegate）
                var (busy, reason) = isBusyChecker();
                if (busy)
                {
                    _monitor.Log($"[Reload] reload refused: busy ({reason})", LogLevel.Warn);
                    return new ReloadResult(ReloadResult.Phase.RefusedBusy, reason);
                }

                // 3. 读取配置（异常和 null 均不泄露 ex.Message / 文件内容）
                ModConfig newConfig;
                try
                {
                    newConfig = readConfig();
                }
                catch (Exception ex)
                {
                    _monitor.Log($"[Reload] reload failed: config read error [{ex.GetType().Name}]", LogLevel.Error);
                    return new ReloadResult(ReloadResult.Phase.ConfigReadFailed, $"ConfigReadError:{ex.GetType().Name}");
                }

                if (newConfig == null)
                {
                    _monitor.Log("[Reload] reload failed: ReadConfig returned null", LogLevel.Error);
                    return new ReloadResult(ReloadResult.Phase.ConfigReadFailed, "ReadConfigReturnedNull");
                }

                // 4. 回调重建 Bridge（异常不泄露 ex.Message）
                try
                {
                    onConfigReloaded(newConfig);
                }
                catch (Exception ex)
                {
                    _monitor.Log($"[Reload] reload failed: bridge rebuild error [{ex.GetType().Name}]", LogLevel.Error);
                    return new ReloadResult(ReloadResult.Phase.BridgeRebuildFailed, $"BridgeRebuildError:{ex.GetType().Name}");
                }

                var attempted = connectionAttemptStarted?.Invoke() ?? true;
                _monitor.Log(attempted
                    ? "[Reload] config applied, connection attempt started"
                    : "[Reload] config applied, bridge deferred until save loaded", LogLevel.Info);
                return new ReloadResult(
                    ReloadResult.Phase.Succeeded,
                    connectionAttemptStarted: attempted);
            }
            finally
            {
                Interlocked.Exchange(ref _isReloading, 0);
            }
        }
    }
}
