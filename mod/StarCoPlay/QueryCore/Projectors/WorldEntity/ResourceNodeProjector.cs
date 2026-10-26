using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;
using SObject = StardewValley.Object;

namespace StarCoPlay.V2.Query.Projectors.WorldEntity;

public static class ResourceNodeProjector
{
    public static WorldEntityFact Project(SObject obj, string mapNameOrUniqueName, int tileX, int tileY)
    {
        var qualifiedItemId = obj.QualifiedItemId ?? "";
        var nodeKind = ClassifyNodeKind(obj);
        var health = (int)obj.MinutesUntilReady;
        var requiredTool = GetRequiredTool(obj);
        var isSpawned = obj.IsSpawnedObject;

        var refId = RefIdBuilder.BuildResourceNode(mapNameOrUniqueName, tileX, tileY, qualifiedItemId);

        var position = new TilePosition
        {
            MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
            X = tileX,
            Y = tileY,
        };

        var nodeFact = new ResourceNodeFact
        {
            NodeKind = nodeKind,
            NodeGuard = RefIdTokenEncoder.EncodeQualifiedItemId(qualifiedItemId),
            QualifiedItemId = qualifiedItemId,
            HitsToDestroy = health,
            RequiredTool = requiredTool,
            IsSpawnedObject = isSpawned,
        };

        var summary = $"Node({nodeKind}) [{requiredTool}, hp={health}]";

        return new WorldEntityFact
        {
            Ref = refId,
            Type = "resource_node",
            FactKind = "resource_node",
            Position = position,
            Summary = summary,
            ResourceNode = nodeFact,
        };
    }

    private static string ClassifyNodeKind(SObject obj)
    {
        var itemId = obj.ItemId ?? "";
        return itemId switch
        {
            "751" or "849" => "copper",
            "290" or "850" => "iron",
            "764" or "851" => "gold",
            "765" or "852" => "iridium",
            "2" or "4" or "6" or "8" or "10" or "12" or "14" => "gem",
            "343" or "450" => "stone",
            _ => itemId,
        };
    }

    private static string GetRequiredTool(SObject obj)
    {
        // Twig (294, 295) → axe; weeds/grass (313, 674, 675, 677, 678) → scythe;
        // stone/ore/gem nodes → pickaxe; unknown breakable nodes → pickaxe
        var name = obj.Name ?? "";
        if (name == "Twig") return "axe";
        if (name == "Weeds") return "scythe";
        return "pickaxe";
    }
}
