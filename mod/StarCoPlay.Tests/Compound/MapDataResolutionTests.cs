using System.Collections.Generic;
using NUnit.Framework;

namespace StarCoPlay.Tests.Compound;

[TestFixture]
public class MapDataResolutionTests
{
    [Test]
    public void ResolveMapNameFromEntries_AcceptsCaseInsensitiveInternalName()
    {
        var maps = new List<MapEntry>
        {
            new() { Name = "Greenhouse", DisplayName = "温室" },
        };

        var resolved = MapData.ResolveMapNameFromEntries(maps, "greenHOUSE");

        Assert.That(resolved, Is.EqualTo("Greenhouse"));
    }

    [Test]
    public void ResolveMapNameFromEntries_AcceptsUniqueDisplayName()
    {
        var maps = new List<MapEntry>
        {
            new() { Name = "Greenhouse", DisplayName = "温室" },
        };

        Assert.That(
            MapData.ResolveMapNameFromEntries(maps, "温室"),
            Is.EqualTo("Greenhouse"));
    }

    [Test]
    public void ResolveMapNameFromEntries_RejectsAmbiguousDisplayName()
    {
        var maps = new List<MapEntry>
        {
            new() { Name = "Cabin_A", DisplayName = "小屋" },
            new() { Name = "Cabin_B", DisplayName = "小屋" },
        };

        Assert.That(MapData.ResolveMapNameFromEntries(maps, "小屋"), Is.Null);
    }
}
