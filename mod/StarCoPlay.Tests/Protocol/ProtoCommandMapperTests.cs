using System.Collections.Generic;
using NUnit.Framework;
using StarCoPlay.Protocol;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2;
using StarCoPlay.V2.Models;

namespace StarCoPlay.Tests.Protocol;

[TestFixture]
public class ProtoCommandMapperTests
{
    [Test]
    public void SayMapsContentToRuntimeMessage()
    {
        var request = BaseRequest("say");
        request.Say = new SayParams { Content = "hello" };

        var cmd = ProtoCommandMapper.ToV2Command(request, out var diagnostics);

        Assert.That(cmd.Capability, Is.EqualTo(AdapterV2.CapabilitySay));
        Assert.That(cmd.ActionId, Is.EqualTo("cmd-say"));
        Assert.That(cmd.Params["message"], Is.EqualTo("hello"));
        Assert.That(diagnostics.HasNotes, Is.False);
    }

    [Test]
    public void GoToBedMapsProtocolNameToRuntimeGoBed()
    {
        var request = BaseRequest("go_to_bed");
        request.GoToBed = new GoToBedParams { TimeoutFrames = 120 };

        var cmd = ProtoCommandMapper.ToV2Command(request, out var diagnostics);

        Assert.That(cmd.Capability, Is.EqualTo(AdapterV2.CapabilityGoBed));
        Assert.That(diagnostics.Phase1Gaps, Has.One.Contains("timeout_frames"));
    }

    [Test]
    public void InteractMapsTargetTileToFlatXY()
    {
        var request = BaseRequest("interact");
        request.Interact = new InteractParams
        {
            TargetTile = new StarCoPlay.Protocol.V245.TilePoint { X = 10, Y = 20 },
        };

        var cmd = ProtoCommandMapper.ToV2Command(request, out _);

        Assert.That(cmd.Capability, Is.EqualTo("interact"));
        Assert.That(cmd.Params["x"], Is.EqualTo(10));
        Assert.That(cmd.Params["y"], Is.EqualTo(20));
    }

    [Test]
    public void GoToMapsCoordinateTargetAndRecordsAdjacentGap()
    {
        var request = BaseRequest("go_to");
        request.GoTo = new GoToParams
        {
            MapId = "Town",
            X = 42,
            Y = 17,
            ArrivalMode = ArrivalMode.ArrivalAdjacent,
            StandSide = Direction.Down,
        };

        var cmd = ProtoCommandMapper.ToV2Command(request, out var diagnostics);

        Assert.That(cmd.Capability, Is.EqualTo(AdapterV2.CapabilityGoTo));
        Assert.That(cmd.Params["target"], Is.EqualTo("Town:42,17"));
        Assert.That(diagnostics.Phase1Gaps, Has.Some.Contains("ARRIVAL_ADJACENT"));
        Assert.That(diagnostics.Phase1Gaps, Has.Some.Contains("stand_side"));
    }

    [Test]
    public void EquipItemCategoryIsExplicitlyRejectedInPhaseOne()
    {
        var request = BaseRequest("equip");
        request.Equip = new EquipParams { ItemCategory = ItemCategory.Axe };

        var ex = Assert.Throws<ProtocolMappingException>(() =>
            ProtoCommandMapper.ToV2Command(request, out _));

        Assert.That(ex!.ErrorCode, Is.EqualTo("NOT_SUPPORTED"));
    }

    [Test]
    public void EquipItemNameMapsToBothRuntimeAliases()
    {
        var request = BaseRequest("equip");
        request.Equip = new EquipParams { ItemName = "Axe" };

        var cmd = ProtoCommandMapper.ToV2Command(request, out _);

        Assert.That(cmd.Params["item"], Is.EqualTo("Axe"));
        Assert.That(cmd.Params["item_name"], Is.EqualTo("Axe"));
    }

    [Test]
    public void CapabilityMustMatchOneofBranch()
    {
        var request = BaseRequest("say");
        request.Capability = "emote";
        request.Say = new SayParams { Content = "hello" };

        var ex = Assert.Throws<ProtocolMappingException>(() =>
            ProtoCommandMapper.ToV2Command(request, out _));

        Assert.That(ex!.ErrorCode, Is.EqualTo("INVALID_PARAMS"));
    }

    [Test]
    public void CapabilityIsRequiredAtModBoundary()
    {
        var request = BaseRequest("say");
        request.Capability = "";
        request.Say = new SayParams { Content = "hello" };

        var ex = Assert.Throws<ProtocolMappingException>(() =>
            ProtoCommandMapper.ToV2Command(request, out _));

        Assert.That(ex!.ErrorCode, Is.EqualTo("INVALID_PARAMS"));
    }

    [Test]
    public void ParamsOneofIsRequiredAtModBoundary()
    {
        var request = BaseRequest("say");

        var ex = Assert.Throws<ProtocolMappingException>(() =>
            ProtoCommandMapper.ToV2Command(request, out _));

        Assert.That(ex!.ErrorCode, Is.EqualTo("INVALID_PARAMS"));
    }

    [Test]
    public void CapabilityMatchingIsCaseInsensitiveButNormalizesToProtocolName()
    {
        var request = BaseRequest("say");
        request.Capability = "Say";
        request.Say = new SayParams { Content = "hello" };

        var cmd = ProtoCommandMapper.ToV2Command(request, out _);

        Assert.That(cmd.Capability, Is.EqualTo(AdapterV2.CapabilitySay));
        Assert.That(cmd.Params["message"], Is.EqualTo("hello"));
    }

    [Test]
    public void InternalTpRejectsLlmSource()
    {
        var request = BaseRequest("tp");
        request.Source = CommandSource.Llm;
        request.Tp = new TpParams
        {
            Target = new TilePosition { MapId = "Farm", X = 64, Y = 15 },
            Reason = TeleportReason.TeleportTest,
        };

        var ex = Assert.Throws<ProtocolMappingException>(() =>
            ProtoCommandMapper.ToV2Command(request, out _));

        Assert.That(ex!.ErrorCode, Is.EqualTo("INVALID_PARAMS"));
    }

    [Test]
    public void UnknownProtocolCapabilityMapsToCapabilityMissing()
    {
        var ex = Assert.Throws<ProtocolMappingException>(() =>
            CapabilityNameMap.ToRuntime("observe"));

        Assert.That(ex!.ErrorCode, Is.EqualTo("UNKNOWN_CAPABILITY"));
    }

    private static CommandRequest BaseRequest(string capability) => new()
    {
        CommandId = $"cmd-{capability}",
        TimeoutMs = 5000,
        Source = CommandSource.Diagnostic,
        Capability = capability,
    };

}
