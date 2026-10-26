using StardewValley;
using StardewValley.GameData.Crops;
using StardewValley.TerrainFeatures;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.WorldEntity;

public static class CropProjector
{
    public static WorldEntityFact Project(HoeDirt dirt, string mapNameOrUniqueName, int tileX, int tileY)
    {
        var crop = dirt.crop;
        var harvestItemId = crop.indexOfHarvest.Value ?? "";
        var cropId = crop.netSeedIndex.Value ?? "";
        var growthPhase = crop.currentPhase.Value;
        var dead = crop.dead.Value;
        var watered = dirt.state.Value == 1;
        var fertilizer = dirt.fertilizer.Value ?? "";
        var readyForHarvest = !dead && (crop.fullyGrown.Value || growthPhase >= crop.phaseDays.Count - 1);

        var refId = RefIdBuilder.BuildCrop(mapNameOrUniqueName, tileX, tileY, harvestItemId);

        var position = new TilePosition
        {
            MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
            X = tileX,
            Y = tileY,
        };

        var harvestMethod = crop.GetHarvestMethod();
        var qualifiedHarvestId = string.IsNullOrEmpty(harvestItemId) ? "" : ItemRegistry.QualifyItemId("(O)" + harvestItemId) ?? ("(O)" + harvestItemId);
        var displayName = ItemRegistry.GetData(qualifiedHarvestId)?.DisplayName ?? "";
        var cropFact = new CropFact
        {
            CropId              = cropId,
            HarvestItemId       = harvestItemId,
            CropGuard           = refId.IdentityGuard,
            GrowthPhase         = growthPhase,
            ReadyForHarvest     = readyForHarvest,
            Watered             = watered,
            Fertilizer          = fertilizer,
            Dead                = dead,
            HarvestMethod       = harvestMethod == HarvestMethod.Scythe ? "scythe" : "grab",
            PhasesTotal         = crop.phaseDays.Count > 0 ? crop.phaseDays.Count - 1 : 0,
            DayOfCurrentPhase   = crop.dayOfCurrentPhase.Value,
            RegrowsAfterHarvest = crop.RegrowsAfterHarvest(),
            DisplayName         = displayName,
        };

        var summary = BuildSummary(cropId, harvestItemId, growthPhase, watered, readyForHarvest, dead);

        var fact = new WorldEntityFact
        {
            Ref = refId,
            Type = "crop",
            FactKind = "crop",
            Position = position,
            Summary = summary,
            Crop = cropFact,
        };

        return fact;
    }

    private static string BuildSummary(string cropId, string harvestItemId, int growthPhase, bool watered, bool readyForHarvest, bool dead)
    {
        var name = string.IsNullOrEmpty(harvestItemId) ? (string.IsNullOrEmpty(cropId) ? "Unknown crop" : $"Crop({cropId})") : $"Crop({harvestItemId})";

        var parts = new System.Collections.Generic.List<string> { $"phase {growthPhase}" };
        if (watered) parts.Add("watered");
        if (readyForHarvest) parts.Add("ready");
        if (dead) parts.Add("dead");

        return $"{name} ({string.Join(", ", parts)})";
    }
}
