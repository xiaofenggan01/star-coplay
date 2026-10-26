using System;
using System.Collections.Generic;
using System.Text.Json;
using Google.Protobuf;
using NUnit.Framework;
using StarCoPlay.Bridge;
using StarCoPlay.Protocol.V245;

namespace StarCoPlay.Tests.Protocol;

[TestFixture]
public sealed class LocalDirectBridgeContractTests
{
    private static LocalDirectFence Fence(string nonce = "nonce") => new(
        "ctx", "principal", "runtime", "game", nonce, 1, "binding", 1, 1, "digest");

    private static Dictionary<string, object> FenceJson(LocalDirectFence f) => new()
    {
        ["context_ref"] = f.ContextRef, ["principal_ref"] = f.PrincipalRef,
        ["runtime_ref"] = f.RuntimeRef, ["game_session_ref"] = f.GameSessionRef,
        ["game_instance_nonce"] = f.GameInstanceNonce, ["generation"] = f.Generation,
        ["binding_ref"] = f.BindingRef, ["binding_version"] = f.BindingVersion,
        ["lease_epoch"] = f.LeaseEpoch, ["capability_digest"] = f.CapabilityDigest,
    };

    private static string V2Frame(LocalDirectFence fence, string commandId = "cmd", object? payload = null,
        double? deadline = null) => JsonSerializer.Serialize(new Dictionary<string, object?>
    {
        ["type"] = "local_command", ["fence"] = FenceJson(fence),
        ["command_id"] = commandId, ["deadline_at"] = deadline ?? DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 60,
        ["wire_format"] = "v2-json", ["payload"] = payload ?? new Dictionary<string, object>
        {
            ["capability"] = "say", ["params"] = new Dictionary<string, object> { ["message"] = "hi" },
        },
    });

    private static string ProtoFrame(LocalDirectFence fence, double? deadline = null)
    {
        var request = new CommandRequest
        {
            CommandId = "proto", TimeoutMs = 5000, Source = CommandSource.Diagnostic,
            Capability = "say", Say = new SayParams { Content = "hello" },
        };
        return JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["type"] = "local_command", ["fence"] = FenceJson(fence), ["command_id"] = "proto",
            ["deadline_at"] = deadline ?? DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 60,
            ["wire_format"] = "protocol-2.4.5",
            ["payload"] = JsonSerializer.Deserialize<JsonElement>(JsonFormatter.Default.Format(request)),
        });
    }

    [TestCase(false, false, true, true, TestName = "NonLocalBridge_NewRendezvousTriggersUpgrade")]
    [TestCase(true, true, true, false, TestName = "HealthyLocalBridge_DoesNotYieldToNewRendezvous")]
    [TestCase(true, false, true, true, TestName = "StoppedLocalBridge_NewRendezvousTriggersRebuild")]
    [TestCase(true, false, false, true, TestName = "StoppedLocalBridge_WithoutRendezvousFallsBack")]
    public void LocalLifecycle_RebuildsForNewRendezvousOrStoppedLocalBridge(
        bool currentBridgeIsLocal,
        bool localBridgeIsLive,
        bool rendezvousAvailable,
        bool expected)
    {
        Assert.That(
            LocalDirectBridge.ShouldRebuildForLocalRendezvous(
                currentBridgeIsLocal,
                localBridgeIsLive,
                rendezvousAvailable),
            Is.EqualTo(expected));
    }

    [TestCase("ws", true, false, TestName = "WsMode_IgnoresLocalRendezvous")]
    [TestCase("hosted", true, false, TestName = "HostedMode_IgnoresLocalRendezvous")]
    [TestCase("auto", true, true, TestName = "AutoMode_UsesLocalRendezvous")]
    [TestCase("local_direct", true, true, TestName = "LocalDirectMode_UsesLocalRendezvous")]
    [TestCase("local-direct", true, true, TestName = "LocalDirectAlias_UsesLocalRendezvous")]
    [TestCase("local_direct", false, false, TestName = "LocalDirectMode_WithoutRendezvousCannotSelectLocal")]
    [TestCase(null, true, false, TestName = "MissingMode_DefaultsToWs")]
    public void RouteSelection_LocalDirectRequiresExplicitModeAndRendezvous(
        string? bridgeMode,
        bool rendezvousAvailable,
        bool expected)
    {
        Assert.That(
            LocalDirectBridge.ShouldSelectForMode(bridgeMode, rendezvousAvailable),
            Is.EqualTo(expected));
    }

    [Test]
    public void RealBridge_ProtoAndV2RejectBeforeDispatcherAndDuplicateTerminalUsesCache()
    {
        var fence = Fence();
        var bridge = new LocalDirectBridge(fence);
        bridge.AcceptFrameForContractTest(V2Frame(Fence("stale"), "stale"));
        bridge.AcceptFrameForContractTest(V2Frame(fence, "expired", deadline: 1));
        Assert.That(bridge.ReadNewCommands(), Is.Empty);

        var frame = V2Frame(fence);
        bridge.AcceptFrameForContractTest(frame);
        Assert.That(bridge.ReadNewCommands(), Has.Count.EqualTo(1));
        bridge.AcceptFrameForContractTest(frame);
        Assert.That(bridge.ReadNewCommands(), Is.Empty); // duplicate nonterminal
        bridge.AcceptFrameForContractTest(V2Frame(fence, payload: new { capability = "say", @params = new { message = "changed" } }));
        Assert.That(bridge.ReadNewCommands(), Is.Empty); // collision

        bridge.WriteV2Result("{\"action_id\":\"cmd\",\"success\":true}");
        while (bridge.TryTakeOutboundForContractTest(out _)) { }
        bridge.AcceptFrameForContractTest(frame);
        Assert.That(bridge.ReadNewCommands(), Is.Empty);
        Assert.That(bridge.TryTakeOutboundForContractTest(out var cached), Is.True);
        Assert.That(cached, Does.Contain("SUCCEEDED"));

        bridge.AcceptFrameForContractTest(ProtoFrame(Fence("stale")));
        Assert.That(bridge.ReadNewProtocolEnvelopes(), Is.Empty);
        bridge.AcceptFrameForContractTest(ProtoFrame(fence));
        Assert.That(bridge.ReadNewProtocolEnvelopes(), Has.Count.EqualTo(1));
    }

    [Test]
    public void RealBridge_CancelCannotBypassFenceOrReplayIntoDispatcher()
    {
        var fence = Fence();
        var bridge = new LocalDirectBridge(fence);
        bridge.AcceptFrameForContractTest(V2Frame(fence));
        Assert.That(bridge.ReadNewCommands(), Has.Count.EqualTo(1));
        var deadline = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 60;
        string Cancel(LocalDirectFence presented) => JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["type"] = "local_cancel", ["control_id"] = "local_cancel:cmd",
            ["command_id"] = "cmd", ["deadline_at"] = deadline, ["fence"] = FenceJson(presented),
        });
        bridge.AcceptFrameForContractTest(Cancel(Fence("stale")));
        Assert.That(bridge.ReadNewCommands(), Is.Empty);
        bridge.AcceptFrameForContractTest(Cancel(fence));
        Assert.That(bridge.ReadNewCommands(), Has.Count.EqualTo(1));
        bridge.AcceptFrameForContractTest(Cancel(fence));
        Assert.That(bridge.ReadNewCommands(), Is.Empty);
    }

    [Test]
    public void RealBridge_V2TerminalOutputIsStructuredObjectNotJsonText()
    {
        var fence = Fence();
        var bridge = new LocalDirectBridge(fence);
        bridge.AcceptFrameForContractTest(V2Frame(fence));
        Assert.That(bridge.ReadNewCommands(), Has.Count.EqualTo(1));
        while (bridge.TryTakeOutboundForContractTest(out _)) { }
        bridge.WriteV2Result("{\"action_id\":\"cmd\",\"success\":true,\"count\":2}");
        Assert.That(bridge.TryTakeOutboundForContractTest(out var json), Is.True);
        using var document = JsonDocument.Parse(json!);
        var output = document.RootElement.GetProperty("output");
        Assert.That(output.ValueKind, Is.EqualTo(JsonValueKind.Object));
        Assert.That(output.GetProperty("count").GetInt32(), Is.EqualTo(2));
    }

    [TestCase("CANCELLED", "CANCELLED")]
    [TestCase("TIMEOUT", "TIMED_OUT")]
    [TestCase("NAVIGATION_TIMEOUT", "TIMED_OUT")]
    [TestCase("ITEM_NOT_FOUND", "FAILED")]
    public void RealBridge_V2FailurePreservesErrorAndMapsTerminalState(
        string errorCode, string expectedState)
    {
        var fence = Fence();
        var bridge = new LocalDirectBridge(fence);
        bridge.AcceptFrameForContractTest(V2Frame(fence));
        Assert.That(bridge.ReadNewCommands(), Has.Count.EqualTo(1));
        while (bridge.TryTakeOutboundForContractTest(out _)) { }

        bridge.WriteV2Result(JsonSerializer.Serialize(new
        {
            action_id = "cmd",
            success = false,
            status = "failed",
            error = new { code = errorCode, message = "contract test" },
        }));

        Assert.That(bridge.TryTakeOutboundForContractTest(out var json), Is.True);
        using var document = JsonDocument.Parse(json!);
        var result = document.RootElement;
        Assert.That(result.GetProperty("state").GetString(), Is.EqualTo(expectedState));
        Assert.That(result.GetProperty("error_code").GetString(), Is.EqualTo(errorCode));
        Assert.That(result.GetProperty("output").GetProperty("error")
            .GetProperty("code").GetString(), Is.EqualTo(errorCode));
    }

    [Test]
    public void RealBridge_V2FailureWithoutErrorKeepsLegacyFallback()
    {
        var bridge = new LocalDirectBridge(Fence());
        bridge.WriteV2Result("{\"action_id\":\"cmd\",\"success\":false}");

        Assert.That(bridge.TryTakeOutboundForContractTest(out var json), Is.True);
        using var document = JsonDocument.Parse(json!);
        var result = document.RootElement;
        Assert.That(result.GetProperty("state").GetString(), Is.EqualTo("FAILED"));
        Assert.That(result.GetProperty("error_code").GetString(), Is.EqualTo("execution_failed"));
    }
}
