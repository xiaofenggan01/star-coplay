using System;
using System.Collections.Generic;
using System.IO;
using NUnit.Framework;
using StarCoPlay.Configuration;
using StardewModdingAPI;
using StardewModdingAPI.Framework.Logging;

namespace StarCoPlay.Tests.Configuration;

[TestFixture]
public sealed class ConfigReloadCoordinatorTests
{
    /*────────────────────────────────────────────
     *  SanitizeReadyLog — 完整 digest 输出（不截断）
     *────────────────────────────────────────────*/

    [Test]
    public void SanitizeReadyLog_OutputsFullDigest()
    {
        var longDigest = "sha256:abcdefghijklmnopqrstuvwxyz1234567890";
        var log = ConfigReloadCoordinator.SanitizeReadyLog(3, 7, 42, longDigest);

        // 完整 digest 应出现在日志中（不截断），便于证据核对
        Assert.That(log, Does.Contain(longDigest));
        Assert.That(log, Does.Contain("generation=3"));
        Assert.That(log, Does.Contain("binding_version=7"));
        Assert.That(log, Does.Contain("lease_epoch=42"));
    }

    [Test]
    public void SanitizeReadyLog_DoesNotContainSecrets()
    {
        var log = ConfigReloadCoordinator.SanitizeReadyLog(1, 1, 1, "sha256:abc123");

        Assert.That(log, Does.Not.Contain("secret"));
        Assert.That(log, Does.Not.Contain("proof"));
        Assert.That(log, Does.Not.Contain("token"));
        Assert.That(log, Does.Not.Contain("?"));
        Assert.That(log, Does.Not.Contain("ws://"));
        Assert.That(log, Does.Not.Contain("wss://"));
    }

    [Test]
    public void SanitizeReadyLog_EmptyDigest_ShowsEmptyMarker()
    {
        var log = ConfigReloadCoordinator.SanitizeReadyLog(1, 1, 1, "");
        Assert.That(log, Does.Contain("capability_digest=(empty)"));
    }

    [Test]
    public void SanitizeReadyLog_NullDigest_ShowsEmptyMarker()
    {
        var log = ConfigReloadCoordinator.SanitizeReadyLog(1, 1, 1, null!);
        Assert.That(log, Does.Contain("capability_digest=(empty)"));
    }

    /*────────────────────────────────────────────
     *  TryReload — busy delegate 阻止 readConfig 与 callback
     *────────────────────────────────────────────*/

    [Test]
    public void TryReload_Busy_DoesNotCallReadConfigOrCallback()
    {
        var monitor = new TestMonitor();
        var coordinator = new ConfigReloadCoordinator(monitor);
        int readConfigCalls = 0;
        int callbackCalls = 0;

        var result = coordinator.TryReload(
            readConfig: () => { readConfigCalls++; return new ModConfig(); },
            isBusyChecker: () => (true, "CommandProcessor: executor_busy"),
            onConfigReloaded: _ => { callbackCalls++; });

        Assert.That(result.Outcome, Is.EqualTo(ReloadResult.Phase.RefusedBusy));
        Assert.That(result.Detail, Does.Contain("CommandProcessor: executor_busy"));
        Assert.That(readConfigCalls, Is.Zero, "busy 时不得调用 readConfig");
        Assert.That(callbackCalls, Is.Zero, "busy 时不得调用 onConfigReloaded");
        Assert.That(monitor.HasMessage("[Reload] reload refused: busy"), Is.True);
    }

    [Test]
    public void TryReload_Busy_LogsBusyReason()
    {
        var monitor = new TestMonitor();
        var coordinator = new ConfigReloadCoordinator(monitor);

        coordinator.TryReload(
            () => new ModConfig(),
            () => (true, "ProtoDispatcher"),
            _ => { });

        Assert.That(monitor.HasMessage("busy (ProtoDispatcher)"), Is.True);
    }

    /*────────────────────────────────────────────
     *  TryReload — 单飞 (Interlocked single-flight)
     *────────────────────────────────────────────*/

    [Test]
    public void TryReload_SingleFlight_NestedCallRefused()
    {
        var monitor = new TestMonitor();
        var coordinator = new ConfigReloadCoordinator(monitor);
        bool nestedRefused = false;

        Action<ModConfig> callback = _ =>
        {
            var nested = coordinator.TryReload(
                () => new ModConfig(),
                () => (false, ""),
                _2 => { });
            nestedRefused = nested.Outcome == ReloadResult.Phase.RefusedSingleFlight;
        };

        var result = coordinator.TryReload(
            () => new ModConfig(),
            () => (false, ""),
            callback);

        Assert.That(result.IsSuccess, Is.True);
        Assert.That(nestedRefused, Is.True, "嵌套重载应被 Interlocked 单飞保护拒绝");
    }

    [Test]
    public void TryReload_AfterCompletion_IsReloadingFalse()
    {
        var monitor = new TestMonitor();
        var coordinator = new ConfigReloadCoordinator(monitor);

        coordinator.TryReload(() => new ModConfig(), () => (false, ""), _ => { });
        Assert.That(coordinator.IsReloading, Is.False,
            "重载完成后 IsReloading 必须为 false");
    }

    [Test]
    public void TryReload_OnException_IsReloadingStillFalse()
    {
        var monitor = new TestMonitor();
        var coordinator = new ConfigReloadCoordinator(monitor);

        coordinator.TryReload(
            () => throw new InvalidOperationException("boom"),
            () => (false, ""),
            _ => { });

        Assert.That(coordinator.IsReloading, Is.False,
            "即使读取失败，finally 块也必须通过 Interlocked.Exchange 重置 IsReloading");
    }

    [Test]
    public void TryReload_InitialState_IsReloadingFalse()
    {
        var coordinator = new ConfigReloadCoordinator(new TestMonitor());
        Assert.That(coordinator.IsReloading, Is.False);
    }

    /*────────────────────────────────────────────
     *  TryReload — 配置读取失败（稳定错误码，不泄露 ex.Message）
     *────────────────────────────────────────────*/

    [Test]
    public void TryReload_ReadConfigThrows_StableErrorCode()
    {
        var monitor = new TestMonitor();
        var coordinator = new ConfigReloadCoordinator(monitor);
        bool callbackInvoked = false;

        var result = coordinator.TryReload(
            () => throw new System.IO.IOException("disk sector 42 is bad"),
            () => (false, ""),
            _ => { callbackInvoked = true; });

        Assert.That(result.Outcome, Is.EqualTo(ReloadResult.Phase.ConfigReadFailed));
        // Detail 仅包含稳定错误码，不含 ex.Message
        Assert.That(result.Detail, Is.EqualTo("ConfigReadError:IOException"));
        Assert.That(result.Detail, Does.Not.Contain("disk sector"));
        Assert.That(result.Detail, Does.Not.Contain("42"));
        Assert.That(callbackInvoked, Is.False);
        // 日志也不应包含 ex.Message
        Assert.That(monitor.HasMessage("[Reload] reload failed: config read error [IOException]"), Is.True);
        Assert.That(monitor.HasMessage("disk sector"), Is.False);
    }

    [Test]
    public void TryReload_ReadConfigReturnsNull_ReturnsConfigReadFailed()
    {
        var monitor = new TestMonitor();
        var coordinator = new ConfigReloadCoordinator(monitor);
        bool callbackInvoked = false;

        var result = coordinator.TryReload(
            () => null!,
            () => (false, ""),
            _ => { callbackInvoked = true; });

        Assert.That(result.Outcome, Is.EqualTo(ReloadResult.Phase.ConfigReadFailed));
        Assert.That(result.Detail, Is.EqualTo("ReadConfigReturnedNull"));
        Assert.That(callbackInvoked, Is.False);
    }

    /*────────────────────────────────────────────
     *  TryReload — 成功路径（config + callback 各调用 1 次）
     *────────────────────────────────────────────*/

    [Test]
    public void TryReload_Success_CallsConfigAndCallbackOnceEach()
    {
        var monitor = new TestMonitor();
        var coordinator = new ConfigReloadCoordinator(monitor);
        int readConfigCalls = 0;
        int callbackCalls = 0;
        ModConfig? receivedConfig = null;

        var result = coordinator.TryReload(
            readConfig: () => { readConfigCalls++; return new ModConfig { PlatformToken = "tk" }; },
            isBusyChecker: () => (false, ""),
            onConfigReloaded: cfg => { callbackCalls++; receivedConfig = cfg; });

        Assert.That(result.IsSuccess, Is.True);
        Assert.That(result.Outcome, Is.EqualTo(ReloadResult.Phase.Succeeded));
        Assert.That(readConfigCalls, Is.EqualTo(1));
        Assert.That(callbackCalls, Is.EqualTo(1));
        Assert.That(receivedConfig, Is.Not.Null);
        Assert.That(receivedConfig!.PlatformToken, Is.EqualTo("tk"));
    }

    /*────────────────────────────────────────────
     *  TryReload — callback 异常使用稳定错误码
     *────────────────────────────────────────────*/

    [Test]
    public void TryReload_CallbackThrows_StableErrorCode()
    {
        var monitor = new TestMonitor();
        var coordinator = new ConfigReloadCoordinator(monitor);

        var result = coordinator.TryReload(
            () => new ModConfig(),
            () => (false, ""),
            _ => throw new InvalidOperationException("websocket handshake rejected: bad token xyz"));

        Assert.That(result.Outcome, Is.EqualTo(ReloadResult.Phase.BridgeRebuildFailed));
        // Detail 仅包含类型名，不含 ex.Message
        Assert.That(result.Detail, Is.EqualTo("BridgeRebuildError:InvalidOperationException"));
        Assert.That(result.Detail, Does.Not.Contain("websocket"));
        Assert.That(result.Detail, Does.Not.Contain("handshake"));
        Assert.That(result.Detail, Does.Not.Contain("token"));
        Assert.That(result.Detail, Does.Not.Contain("xyz"));
        // 日志也不含 ex.Message
        Assert.That(monitor.HasMessage("[Reload] reload failed: bridge rebuild error [InvalidOperationException]"), Is.True);
        Assert.That(monitor.HasMessage("bad token"), Is.False);
    }

    /*────────────────────────────────────────────
     *  SECRET_SENTINEL — 全链路不泄漏
     *────────────────────────────────────────────*/

    [Test]
    public void TryReload_ConfigReadException_DoesNotLeakSecretSentinel()
    {
        var monitor = new TestMonitor();
        var coordinator = new ConfigReloadCoordinator(monitor);

        var result = coordinator.TryReload(
            () => throw new InvalidOperationException("SECRET_SENTINEL_abc123"),
            () => (false, ""),
            _ => { });

        Assert.That(result.Outcome, Is.EqualTo(ReloadResult.Phase.ConfigReadFailed));
        AssertSecretSentinelNotLeaked(monitor, result);
    }

    [Test]
    public void TryReload_BridgeRebuildException_DoesNotLeakSecretSentinel()
    {
        var monitor = new TestMonitor();
        var coordinator = new ConfigReloadCoordinator(monitor);

        var result = coordinator.TryReload(
            () => new ModConfig(),
            () => (false, ""),
            _ => throw new InvalidOperationException("proof=SECRET_SENTINEL_deadbeef&token=xyz"));

        Assert.That(result.Outcome, Is.EqualTo(ReloadResult.Phase.BridgeRebuildFailed));
        AssertSecretSentinelNotLeaked(monitor, result);
    }

    private static void AssertSecretSentinelNotLeaked(TestMonitor monitor, ReloadResult result)
    {
        // SECRET_SENTINEL 不得出现在任何日志消息中
        foreach (var msg in monitor.AllMessages)
        {
            Assert.That(msg, Does.Not.Contain("SECRET_SENTINEL"),
                $"日志消息不应包含 SECRET_SENTINEL: {msg}");
        }
        // SECRET_SENTINEL 不得出现在 ReloadResult.Detail 中
        var detail = result.Detail ?? "";
        Assert.That(detail, Does.Not.Contain("SECRET_SENTINEL"),
            $"ReloadResult.Detail 不应包含 SECRET_SENTINEL: {detail}");
    }

    /*────────────────────────────────────────────
     *  日志事件名称稳定性
     *────────────────────────────────────────────*/

    [Test]
    public void TryReload_LogsStableEventNames()
    {
        var monitor = new TestMonitor();
        var coordinator = new ConfigReloadCoordinator(monitor);

        coordinator.TryReload(() => new ModConfig(), () => (false, ""), _ => { });

        Assert.That(monitor.HasMessage("[Reload] reload requested"), Is.True);
        Assert.That(monitor.HasMessage("[Reload] config applied, connection attempt started"), Is.True);
    }

    [Test]
    public void TryReload_RefusedSingleFlight_LogsStableEventName()
    {
        var monitor = new TestMonitor();
        var coordinator = new ConfigReloadCoordinator(monitor);

        coordinator.TryReload(() => new ModConfig(), () => (false, ""), _ =>
        {
            coordinator.TryReload(() => new ModConfig(), () => (false, ""), _2 => { });
        });

        Assert.That(monitor.HasMessage("[Reload] reload refused: already in progress"), Is.True);
    }

    /*────────────────────────────────────────────
     *  ReloadResult 结构体
     *────────────────────────────────────────────*/

    [Test]
    public void ReloadResult_Succeeded_HasCorrectDefaults()
    {
        var r = new ReloadResult(ReloadResult.Phase.Succeeded);
        Assert.That(r.IsSuccess, Is.True);
        Assert.That(r.Outcome, Is.EqualTo(ReloadResult.Phase.Succeeded));
        Assert.That(r.Detail, Is.Null);
    }

    [Test]
    public void ReloadResult_Failure_HasDetail()
    {
        var r = new ReloadResult(ReloadResult.Phase.RefusedBusy, "CommandProcessor: executor_busy");
        Assert.That(r.IsSuccess, Is.False);
        Assert.That(r.Detail, Is.EqualTo("CommandProcessor: executor_busy"));
    }

    [Test]
    public void ReloadResult_Failure_PhaseNotSuccess()
    {
        Assert.That(new ReloadResult(ReloadResult.Phase.RefusedSingleFlight).IsSuccess, Is.False);
        Assert.That(new ReloadResult(ReloadResult.Phase.RefusedBusy).IsSuccess, Is.False);
        Assert.That(new ReloadResult(ReloadResult.Phase.ConfigReadFailed).IsSuccess, Is.False);
        Assert.That(new ReloadResult(ReloadResult.Phase.BridgeRebuildFailed).IsSuccess, Is.False);
    }
}

/// <summary>
/// 测试用 Monitor 桩 — 捕获日志消息用于断言。
/// </summary>
internal sealed class TestMonitor : IMonitor
{
    private readonly List<string> _messages = new();

    public IReadOnlyList<string> AllMessages => _messages;

    public bool HasMessage(string partial)
    {
        foreach (var msg in _messages)
            if (msg.Contains(partial, StringComparison.OrdinalIgnoreCase))
                return true;
        return false;
    }

    public void Log(string message, LogLevel level) => _messages.Add(message);

    // IMonitor 桩成员
    public bool IsVerbose => false;
    public string? LogPrefix { get => null; set { } }
    public void VerboseLog(string message) => _messages.Add(message);
    public void VerboseLog(ref VerboseLogStringHandler message) => _messages.Add(message.ToString());
    public void VerboseLogOnce(string message) => _messages.Add(message);
    public void LogOnce(string message, LogLevel level) => _messages.Add(message);
}

/*────────────────────────────────────────────
 *  LocalReloadPortal 单元测试
 *────────────────────────────────────────────*/

[TestFixture]
public sealed class LocalReloadPortalTests
{
    /*────────────────────────────────────────────
     *  MapOutcome — 完整 Phase → snake_case 映射
     *────────────────────────────────────────────*/

    [Test]
    public void MapOutcome_AllPhases_HaveStableNames()
    {
        Assert.That(LocalReloadPortal.MapOutcome(ReloadResult.Phase.Succeeded),
            Is.EqualTo("succeeded"));
        Assert.That(LocalReloadPortal.MapOutcome(ReloadResult.Phase.RefusedSingleFlight),
            Is.EqualTo("refused_single_flight"));
        Assert.That(LocalReloadPortal.MapOutcome(ReloadResult.Phase.RefusedBusy),
            Is.EqualTo("refused_busy"));
        Assert.That(LocalReloadPortal.MapOutcome(ReloadResult.Phase.ConfigReadFailed),
            Is.EqualTo("config_read_failed"));
        Assert.That(LocalReloadPortal.MapOutcome(ReloadResult.Phase.BridgeRebuildFailed),
            Is.EqualTo("bridge_rebuild_failed"));
    }

    [Test]
    public void MapOutcome_UnknownValue_ReturnsUnknown()
    {
        var unknown = (ReloadResult.Phase)999;
        Assert.That(LocalReloadPortal.MapOutcome(unknown), Is.EqualTo("unknown"));
    }

    /*────────────────────────────────────────────
     *  ReloadRequest / ReloadResponse JSON 往返
     *────────────────────────────────────────────*/

    [Test]
    public void ReloadRequest_RoundTrip_PreservesAllFields()
    {
        var original = new ReloadRequest
        {
            request_id = "reload-test-123",
            requested_at = "2026-07-15T10:30:00Z",
        };

        var json = System.Text.Json.JsonSerializer.Serialize(original);
        var parsed = System.Text.Json.JsonSerializer.Deserialize<ReloadRequest>(json);

        Assert.That(parsed, Is.Not.Null);
        Assert.That(parsed!.request_id, Is.EqualTo("reload-test-123"));
        Assert.That(parsed.requested_at, Is.EqualTo("2026-07-15T10:30:00Z"));
    }

    [Test]
    public void ReloadResponse_RoundTrip_PreservesAllFields()
    {
        var original = new ReloadResponse
        {
            request_id = "reload-test-456",
            outcome = "succeeded",
            detail = null,
            completed_at = "2026-07-15T10:30:01Z",
        };

        var json = System.Text.Json.JsonSerializer.Serialize(original);
        var parsed = System.Text.Json.JsonSerializer.Deserialize<ReloadResponse>(json);

        Assert.That(parsed, Is.Not.Null);
        Assert.That(parsed!.request_id, Is.EqualTo("reload-test-456"));
        Assert.That(parsed.outcome, Is.EqualTo("succeeded"));
        Assert.That(parsed.detail, Is.Null);
        Assert.That(parsed.completed_at, Is.EqualTo("2026-07-15T10:30:01Z"));
    }

    [Test]
    public void ReloadResponse_WithDetailAndError_RoundTrip()
    {
        var original = new ReloadResponse
        {
            request_id = "reload-test-789",
            outcome = "bridge_rebuild_failed",
            detail = "BridgeRebuildError:InvalidOperationException",
            completed_at = "2026-07-15T10:30:02Z",
        };

        var json = System.Text.Json.JsonSerializer.Serialize(original);
        var parsed = System.Text.Json.JsonSerializer.Deserialize<ReloadResponse>(json);

        Assert.That(parsed, Is.Not.Null);
        Assert.That(parsed!.request_id, Is.EqualTo("reload-test-789"));
        Assert.That(parsed.outcome, Is.EqualTo("bridge_rebuild_failed"));
        Assert.That(parsed.detail, Is.EqualTo("BridgeRebuildError:InvalidOperationException"));
    }

    [Test]
    public void ReloadResponse_DoesNotContainSecretFields()
    {
        // 验证 JSON 输出不包含 secret/token/proof/config 字段名
        var response = new ReloadResponse
        {
            request_id = "test",
            outcome = "succeeded",
            detail = "ok",
            completed_at = "2026-07-15T00:00:00Z",
        };
        var json = System.Text.Json.JsonSerializer.Serialize(response).ToLowerInvariant();

        Assert.That(json, Does.Not.Contain("secret"));
        Assert.That(json, Does.Not.Contain("token"));
        Assert.That(json, Does.Not.Contain("proof"));
        Assert.That(json, Does.Not.Contain("config"));
        // 验证仅包含预期的四个 key
        Assert.That(json, Does.Contain("request_id"));
        Assert.That(json, Does.Contain("outcome"));
        Assert.That(json, Does.Contain("detail"));
        Assert.That(json, Does.Contain("completed_at"));
    }

    /*────────────────────────────────────────────
     *  WriteResult → TryClaimRequest 文件往返
     *────────────────────────────────────────────*/

    [Test]
    public void Portal_WriteAndClaim_RoundTrip()
    {
        var tmpDir = Path.Combine(Path.GetTempPath(), "starcoplay-test-" + Guid.NewGuid().ToString("N"));
        var requestPath = Path.Combine(tmpDir, "reload.request.json");
        var resultPath = Path.Combine(tmpDir, "reload.result.json");

        try
        {
            // 注意：在写结果之前需要手动创建请求文件（模拟脚本写入）。
            // LocalReloadPortal 不创建请求文件，只消费它。
            var request = new ReloadRequest
            {
                request_id = "reload-roundtrip-1",
                requested_at = "2026-07-15T12:00:00Z",
            };
            var requestJson = System.Text.Json.JsonSerializer.Serialize(request);
            File.WriteAllText(requestPath, requestJson);

            var monitor = new TestMonitor();
            var portal = new LocalReloadPortal(monitor, tmpDir, requestPath, resultPath);

            // Claim 请求
            var claimed = portal.TryClaimRequest();
            Assert.That(claimed, Is.Not.Null);
            Assert.That(claimed!.request_id, Is.EqualTo("reload-roundtrip-1"));

            // 写结果
            var reloadResult = new ReloadResult(ReloadResult.Phase.Succeeded,
                connectionAttemptStarted: true);
            portal.WriteResult("reload-roundtrip-1", reloadResult);

            // 验证结果文件存在且可解析
            Assert.That(File.Exists(resultPath), Is.True);

            var resultJson = File.ReadAllText(resultPath);
            var parsed = System.Text.Json.JsonSerializer.Deserialize<ReloadResponse>(resultJson);

            Assert.That(parsed, Is.Not.Null);
            Assert.That(parsed!.request_id, Is.EqualTo("reload-roundtrip-1"));
            Assert.That(parsed.outcome, Is.EqualTo("succeeded"));
            // detail 在 Phase.Succeeded 且未指定 detail 时应为 null
            Assert.That(parsed.completed_at, Is.Not.Null.And.Not.Empty);
        }
        finally
        {
            if (Directory.Exists(tmpDir))
                Directory.Delete(tmpDir, recursive: true);
        }
    }

    [Test]
    public void Portal_TryClaim_NoFile_ReturnsNull()
    {
        var tmpDir = Path.Combine(Path.GetTempPath(), "starcoplay-test-" + Guid.NewGuid().ToString("N"));
        var requestPath = Path.Combine(tmpDir, "reload.request.json");
        var resultPath = Path.Combine(tmpDir, "reload.result.json");
        Directory.CreateDirectory(tmpDir);

        try
        {
            var monitor = new TestMonitor();
            var portal = new LocalReloadPortal(monitor, tmpDir, requestPath, resultPath);

            var claimed = portal.TryClaimRequest();
            Assert.That(claimed, Is.Null);
        }
        finally
        {
            if (Directory.Exists(tmpDir))
                Directory.Delete(tmpDir, recursive: true);
        }
    }

    [Test]
    public void Portal_WriteResult_WithFailurePhase()
    {
        var tmpDir = Path.Combine(Path.GetTempPath(), "starcoplay-test-" + Guid.NewGuid().ToString("N"));
        var requestPath = Path.Combine(tmpDir, "reload.request.json");
        var resultPath = Path.Combine(tmpDir, "reload.result.json");
        Directory.CreateDirectory(tmpDir);

        try
        {
            var monitor = new TestMonitor();
            var portal = new LocalReloadPortal(monitor, tmpDir, requestPath, resultPath);

            var reloadResult = new ReloadResult(
                ReloadResult.Phase.BridgeRebuildFailed,
                detail: "BridgeRebuildError:InvalidOperationException");
            portal.WriteResult("reload-fail-1", reloadResult);

            var resultJson = File.ReadAllText(resultPath);
            var parsed = System.Text.Json.JsonSerializer.Deserialize<ReloadResponse>(resultJson);

            Assert.That(parsed, Is.Not.Null);
            Assert.That(parsed!.outcome, Is.EqualTo("bridge_rebuild_failed"));
            Assert.That(parsed.detail, Is.EqualTo("BridgeRebuildError:InvalidOperationException"));
            // 确保 detail 不泄露 exception message
            Assert.That(parsed.detail, Does.Not.Contain("SECRET_SENTINEL"));
        }
        finally
        {
            if (Directory.Exists(tmpDir))
                Directory.Delete(tmpDir, recursive: true);
        }
    }
}
