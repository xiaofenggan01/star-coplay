using System;
using NUnit.Framework;
using StarCoPlay.V2.RefId.Building;

namespace StarCoPlay.Tests.RefId.Building;

[TestFixture]
public class RefIdBuilderTests
{
    [Test]
    public void BuildTree_ReturnsProtocolRefIdWithParsedFields()
    {
        var refId = RefIdBuilder.BuildTree("Farm", 41, 12, "1");

        Assert.That(refId.Value, Is.EqualTo("world_entity:tree:farm:41:12#wt_1"));
        Assert.That(refId.RefType, Is.EqualTo("world_entity"));
        Assert.That(refId.Type, Is.EqualTo("tree"));
        Assert.That(refId.LocatorSegments, Is.EqualTo(new[] { "farm", "41", "12" }));
        Assert.That(refId.IdentityGuard, Is.EqualTo("wt_1"));
    }

    [Test]
    public void BuildMachine_UsesQualifiedItemGuard()
    {
        var refId = RefIdBuilder.BuildMachine("Farm", 20, 11, "(BC)12");

        Assert.That(refId.Value, Is.EqualTo("world_entity:machine:farm:20:11#bc_12"));
    }

    [Test]
    public void BuildContainer_UsesQualifiedItemGuard()
    {
        var refId = RefIdBuilder.BuildContainer("FarmHouse", 8, 6, "(BC)130");

        Assert.That(refId.Value, Is.EqualTo("world_entity:container:farmhouse:8:6#bc_130"));
    }

    [Test]
    public void BuildBed_UsesFurnitureGuard()
    {
        var refId = RefIdBuilder.BuildBed("FarmHouse", 4, 7, "(F)Bed");

        Assert.That(refId.Value, Is.EqualTo("world_entity:bed:farmhouse:4:7#f_bed"));
    }

    [Test]
    public void BuildNpc_UsesNameAsLocatorAndGuard()
    {
        var refId = RefIdBuilder.BuildNpc("Mr Qi");

        Assert.That(refId.Value, Is.EqualTo("character:npc:mr_qi#mr_qi"));
        Assert.That(refId.LocatorSegments, Is.EqualTo(new[] { "mr_qi" }));
    }

    [Test]
    public void BuildPlayerInventoryItem_UsesPlayerSlotLocator()
    {
        var refId = RefIdBuilder.BuildPlayerInventoryItem(3, "(O)388");

        Assert.That(refId.Value, Is.EqualTo("inventory:item:player:3#o_388"));
        Assert.That(refId.LocatorSegments, Is.EqualTo(new[] { "player", "3" }));
    }

    [TestCase(-1)]
    [TestCase(-10)]
    public void BuildPlayerInventoryItem_RejectsNegativeSlot(int slot)
    {
        Assert.Throws<ArgumentOutOfRangeException>(() =>
            RefIdBuilder.BuildPlayerInventoryItem(slot, "(O)388"));
    }

    [Test]
    public void BuildWorldEntity_RejectsUnsafeIdentityGuard()
    {
        Assert.Throws<ArgumentException>(() =>
            RefIdBuilder.BuildWorldEntity("tree", "Farm", 1, 2, "(O)388"));
    }
}
