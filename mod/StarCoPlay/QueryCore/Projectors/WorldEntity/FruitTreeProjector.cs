using StardewValley.TerrainFeatures;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.WorldEntity;

public static class FruitTreeProjector
{
    public static WorldEntityFact Project(FruitTree tree, string mapNameOrUniqueName, int tileX, int tileY)
    {
        var treeId = tree.treeId.Value ?? "";
        var qualifiedItemId = string.IsNullOrEmpty(treeId) ? "" : $"(O){treeId}";
        var growthStage = tree.growthStage.Value;
        var daysUntilMature = tree.daysUntilMature.Value;
        var fruitCount = tree.fruit.Count;
        var isStump = tree.stump.Value;
        var struckByLightning = tree.struckByLightningCountdown.Value > 0;

        var refId = RefIdBuilder.BuildFruitTree(mapNameOrUniqueName, tileX, tileY, qualifiedItemId);

        var position = new TilePosition
        {
            MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
            X = tileX,
            Y = tileY,
        };

        var fruitTreeFact = new FruitTreeFact
        {
            TreeId              = treeId,
            QualifiedItemId     = qualifiedItemId,
            ItemIdGuard         = RefIdTokenEncoder.EncodeQualifiedItemId(qualifiedItemId),
            GrowthStage         = growthStage,
            DaysUntilMature     = daysUntilMature,
            FruitCount          = fruitCount,
            IsStump             = isStump,
            StruckByLightning   = struckByLightning,
            IsInSeason          = tree.IsInSeasonHere(),
            FruitQuality        = tree.GetQuality(),
            GreenHouseTileTree  = tree.greenHouseTileTree.Value,
            DisplayName         = tree.GetDisplayName() ?? "",
        };

        var summary = BuildSummary(treeId, growthStage, fruitCount, isStump, struckByLightning);

        return new WorldEntityFact
        {
            Ref = refId,
            Type = "fruit_tree",
            FactKind = "fruit_tree",
            Position = position,
            Summary = summary,
            FruitTree = fruitTreeFact,
        };
    }

    private static string BuildSummary(string treeId, int growthStage, int fruitCount, bool isStump, bool struckByLightning)
    {
        var name = string.IsNullOrEmpty(treeId) ? "Fruit tree" : $"FruitTree({treeId})";

        var parts = new System.Collections.Generic.List<string> { $"stage {growthStage}" };
        if (fruitCount > 0) parts.Add($"{fruitCount} fruit");
        if (isStump) parts.Add("stump");
        if (struckByLightning) parts.Add("lightning-struck");

        return $"{name} ({string.Join(", ", parts)})";
    }
}
