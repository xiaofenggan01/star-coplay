using NUnit.Framework;
using StarCoPlay.V2.RefId;
using StarCoPlay.V2.RefId.Parsing;

namespace StarCoPlay.Tests.RefId.Parsing;

[TestFixture]
public class RefIdParserTests
{
    [Test]
    public void Parse_WorldTree_ReturnsParsedRefId()
    {
        var result = RefIdParser.Parse("world_entity:tree:farm:41:12#wt_1");

        Assert.That(result.Status, Is.EqualTo(RefIdStatus.Resolved));
        Assert.That(result.ParsedRefId, Is.Not.Null);
        Assert.That(result.ParsedRefId!.Raw, Is.EqualTo("world_entity:tree:farm:41:12#wt_1"));
        Assert.That(result.ParsedRefId.RefType, Is.EqualTo("world_entity"));
        Assert.That(result.ParsedRefId.Type, Is.EqualTo("tree"));
        Assert.That(result.ParsedRefId.LocatorParts, Is.EqualTo(new[] { "farm", "41", "12" }));
        Assert.That(result.ParsedRefId.IdentityGuard, Is.EqualTo("wt_1"));
    }

    [Test]
    public void Parse_InventoryItemWithMultiPartLocator_ReturnsAllLocatorParts()
    {
        var result = RefIdParser.Parse("inventory:item:chest:farmhouse:8:6:12#o_388");

        Assert.That(result.Status, Is.EqualTo(RefIdStatus.Resolved));
        Assert.That(result.ParsedRefId!.RefType, Is.EqualTo("inventory"));
        Assert.That(result.ParsedRefId.Type, Is.EqualTo("item"));
        Assert.That(result.ParsedRefId.LocatorParts, Is.EqualTo(new[] { "chest", "farmhouse", "8", "6", "12" }));
        Assert.That(result.ParsedRefId.IdentityGuard, Is.EqualTo("o_388"));
    }

    [Test]
    public void Parse_GuidSegment_ReturnsParsedRefId()
    {
        var result = RefIdParser.Parse("character:monster:mine_20:9f2e7f1a-8b4f-4f55-91a2-c5212a77d713#green_slime");

        Assert.That(result.Status, Is.EqualTo(RefIdStatus.Resolved));
        Assert.That(result.ParsedRefId!.LocatorParts[1], Is.EqualTo("9f2e7f1a-8b4f-4f55-91a2-c5212a77d713"));
    }

    [Test]
    public void Parse_UnsupportedTypeStillParses()
    {
        var result = RefIdParser.Parse("world_entity:object:farm:1:2#o_388");

        Assert.That(result.Status, Is.EqualTo(RefIdStatus.Resolved));
        Assert.That(result.ParsedRefId!.RefType, Is.EqualTo("world_entity"));
        Assert.That(result.ParsedRefId.Type, Is.EqualTo("object"));
    }

    [TestCase("world_entity:tree:farm:41:12")]
    [TestCase("world_entity:tree:farm:41:12#wt_1#extra")]
    [TestCase("world_entity:tree#wt_1")]
    [TestCase("world_entity:tree:farm:41:12#")]
    [TestCase(":tree:farm:41:12#wt_1")]
    [TestCase("world_entity::farm:41:12#wt_1")]
    [TestCase("world_entity:tree:farm::12#wt_1")]
    [TestCase("world_entity:tree:farm:41:12#(O)388")]
    [TestCase("world_entity:tree:farm house:41:12#wt_1")]
    [TestCase("world_entity:tree:farm/house:41:12#wt_1")]
    [TestCase("world_entity:tree:农场:41:12#wt_1")]
    [TestCase("world_entity:tree:farm.1:41:12#wt_1")]
    public void Parse_InvalidStructureOrUnsafeToken_ReturnsMalformedRef(string refId)
    {
        var result = RefIdParser.Parse(refId);

        Assert.That(result.Status, Is.EqualTo(RefIdStatus.MalformedRef));
        Assert.That(result.ParsedRefId, Is.Null);
        Assert.That(result.DebugReason, Is.Not.Empty);
    }
}
