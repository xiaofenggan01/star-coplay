using NUnit.Framework;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId;
using StarCoPlay.V2.RefId.Parsing;
using StarCoPlay.V2.RefId.Protocol;

namespace StarCoPlay.Tests.RefId.Protocol;

[TestFixture]
public class RefIdProtocolMapperTests
{
    [Test]
    public void ToProtocolRefId_CopiesParsedFields()
    {
        var parsed = RefIdParser.Parse("world_entity:tree:farm:41:12#wt_1").ParsedRefId!;

        var protocolRef = RefIdProtocolMapper.ToProtocolRefId(parsed);

        Assert.That(protocolRef.Value, Is.EqualTo(parsed.Raw));
        Assert.That(protocolRef.RefType, Is.EqualTo("world_entity"));
        Assert.That(protocolRef.Type, Is.EqualTo("tree"));
        Assert.That(protocolRef.LocatorSegments, Is.EqualTo(new[] { "farm", "41", "12" }));
        Assert.That(protocolRef.IdentityGuard, Is.EqualTo("wt_1"));
    }

    [TestCase(RefIdStatus.Resolved, RefResolveStatus.RefResolved)]
    [TestCase(RefIdStatus.ResolvedDegraded, RefResolveStatus.RefResolvedDegraded)]
    [TestCase(RefIdStatus.Missing, RefResolveStatus.RefMissing)]
    [TestCase(RefIdStatus.Ambiguous, RefResolveStatus.RefAmbiguous)]
    [TestCase(RefIdStatus.Unsupported, RefResolveStatus.RefUnsupported)]
    [TestCase(RefIdStatus.StaleTypeMismatch, RefResolveStatus.RefStaleTypeMismatch)]
    [TestCase(RefIdStatus.StaleGuardMismatch, RefResolveStatus.RefStaleGuardMismatch)]
    [TestCase(RefIdStatus.StaleContextMismatch, RefResolveStatus.RefStaleContextMismatch)]
    [TestCase(RefIdStatus.MalformedRef, RefResolveStatus.RefMalformedRef)]
    public void ToProtocolStatus_MapsEveryInternalStatus(RefIdStatus status, RefResolveStatus expected)
    {
        Assert.That(RefIdProtocolMapper.ToProtocolStatus(status), Is.EqualTo(expected));
    }

    [Test]
    public void CreateResolution_UsesParsedRefMetadata()
    {
        var parsed = RefIdParser.Parse("inventory:item:player:3#o_388").ParsedRefId!;

        var resolution = RefIdProtocolMapper.CreateResolution(
            parsed,
            RefIdStatus.Unsupported,
            factKind: "inventory_item",
            summary: "Unsupported inventory ref.",
            debugReason: "Only player inventory is enabled in this test.");

        Assert.That(resolution.Status, Is.EqualTo(RefResolveStatus.RefUnsupported));
        Assert.That(resolution.Ref.Value, Is.EqualTo("inventory:item:player:3#o_388"));
        Assert.That(resolution.RefType, Is.EqualTo("inventory"));
        Assert.That(resolution.Type, Is.EqualTo("item"));
        Assert.That(resolution.FactKind, Is.EqualTo("inventory_item"));
        Assert.That(resolution.Summary, Is.EqualTo("Unsupported inventory ref."));
        Assert.That(resolution.DebugReason, Is.EqualTo("Only player inventory is enabled in this test."));
    }

    [Test]
    public void CreateMalformedResolution_PreservesRawRef()
    {
        var resolution = RefIdProtocolMapper.CreateMalformedResolution(
            "world_entity:tree:farm:41:12",
            "RefId must contain exactly one '#'.");

        Assert.That(resolution.Status, Is.EqualTo(RefResolveStatus.RefMalformedRef));
        Assert.That(resolution.Ref.Value, Is.EqualTo("world_entity:tree:farm:41:12"));
        Assert.That(resolution.DebugReason, Is.EqualTo("RefId must contain exactly one '#'."));
    }

    [TestCase("ui_element:tab:inventory:0#tab_0", "ui_element")]
    [TestCase("ui_element:dialogue_option:dm:2#opt_hello", "ui_element")]
    [TestCase("ui_element:shop_item:shop:5#si_seeds", "ui_element")]
    [TestCase("ui_element:button:menu:1#btn_ok", "ui_element")]
    public void InferFactKind_UiElement_AlwaysReturnsUiElement(string refStr, string expectedFactKind)
    {
        var parsed = RefIdParser.Parse(refStr).ParsedRefId!;

        var factKind = RefIdProtocolMapper.InferFactKind(parsed);

        Assert.That(factKind, Is.EqualTo(expectedFactKind));
    }
}
