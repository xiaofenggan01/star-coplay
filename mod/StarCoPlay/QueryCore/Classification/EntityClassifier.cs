using StardewValley;
using StardewValley.TerrainFeatures;
using StardewValley.Objects;

namespace StarCoPlay.V2.Query.Classification;

public enum WorldEntityType
{
    Tree,
    FruitTree,
    Crop,
    Machine,
    Container,
    ResourceNode,
    LooseItem,
    ResourceClump,
    Bed,
    Furniture,
    Door,
    Warp,
    Unknown,
}

public static class EntityClassifier
{
    /// <summary>Classify an object from location.Objects.</summary>
    public static WorldEntityType ClassifyObject(StardewValley.Object obj)
    {
        if (obj is Chest)
            return WorldEntityType.Container;

        if (obj.bigCraftable.Value)
            return WorldEntityType.Machine;

        if (IsBreakableResource(obj))
            return WorldEntityType.ResourceNode;

        if (obj.IsSpawnedObject)
            return WorldEntityType.LooseItem;

        return WorldEntityType.LooseItem;
    }

    /// <summary>Classify a terrain feature from location.terrainFeatures.</summary>
    public static WorldEntityType ClassifyTerrainFeature(TerrainFeature tf)
    {
        return tf switch
        {
            StardewValley.TerrainFeatures.Tree => WorldEntityType.Tree,
            FruitTree => WorldEntityType.FruitTree,
            HoeDirt dirt when dirt.crop != null => WorldEntityType.Crop,
            _ => WorldEntityType.Unknown,
        };
    }

    /// <summary>Classify a furniture item from location.furniture.</summary>
    public static WorldEntityType ClassifyFurniture(Furniture f)
    {
        return f switch
        {
            BedFurniture => WorldEntityType.Bed,
            _ => WorldEntityType.Furniture,
        };
    }

    private static bool IsBreakableResource(StardewValley.Object obj)
    {
        if (obj.Name == "Stone" || obj.Name == "Twig" || obj.Name == "Weeds")
            return true;

        if (obj.Name.Contains("Ore") || obj.Name.Contains("Node") || obj.Name.Contains("Mystic"))
            return true;

        return false;
    }
}
