using StardewValley.TerrainFeatures;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.WorldEntity;

public static class TreeProjector
{
    public static WorldEntityFact Project(Tree tree, string mapNameOrUniqueName, int tileX, int tileY)
    {
        var treeType = tree.treeType.Value;
        var growthStage = tree.growthStage.Value;
        var isStump = tree.stump.Value;
        var isTapped = tree.tapped.Value;
        var hasMoss = tree.hasMoss.Value;
        var health = tree.health.Value;
        var fertilized = tree.fertilized.Value;
        var hasSeed = tree.hasSeed.Value;
        var displayName = GetDisplayName(treeType);

        var refId = RefIdBuilder.BuildTree(mapNameOrUniqueName, tileX, tileY, treeType);

        var position = new TilePosition
        {
            MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
            X = tileX,
            Y = tileY,
        };

        var treeFact = new TreeFact
        {
            TreeType      = treeType,
            TreeTypeGuard = RefIdTokenEncoder.EncodeWildTreeType(treeType),
            GrowthStage   = growthStage,
            IsStump       = isStump,
            IsTapped      = isTapped,
            HasMoss       = hasMoss,
            Health        = health,
            Fertilized    = fertilized,
            HasSeed       = hasSeed,
            DisplayName   = displayName,
        };

        var summary = BuildSummary(treeType, growthStage, isStump, isTapped, hasMoss);

        var fact = new WorldEntityFact
        {
            Ref = refId,
            Type = "tree",
            FactKind = "tree",
            Position = position,
            Summary = summary,
            Tree = treeFact,
        };

        return fact;
    }

    private static string GetDisplayName(string treeType) => treeType switch
    {
        "1" => "Oak Tree",
        "2" => "Maple Tree",
        "3" => "Pine Tree",
        "6" => "Palm Tree",
        "7" => "Mushroom Tree",
        "8" => "Mahogany Tree",
        _ => "Tree",
    };

    private static string BuildSummary(string treeType, int growthStage, bool isStump, bool isTapped, bool hasMoss)
    {
        var typeName = GetDisplayName(treeType);
        var parts = new System.Collections.Generic.List<string> { $"stage {growthStage}" };
        if (isStump) parts.Add("stump");
        if (isTapped) parts.Add("tapped");
        if (hasMoss) parts.Add("mossy");

        return $"{typeName} ({string.Join(", ", parts)})";
    }
}
