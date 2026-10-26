using StardewValley.TerrainFeatures;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.WorldEntity;

public static class ResourceClumpProjector
{
    public static WorldEntityFact Project(ResourceClump clump, string mapNameOrUniqueName, int tileX, int tileY)
    {
        var clumpIndex = clump.parentSheetIndex.Value;
        var clumpKind = ClassifyClumpKind(clumpIndex);
        var health = (int)clump.health.Value;
        var width = clump.width.Value;
        var height = clump.height.Value;
        var requiredTool = GetRequiredTool(clumpIndex);

        var refId = RefIdBuilder.BuildResourceClump(mapNameOrUniqueName, tileX, tileY, clumpKind);

        var position = new TilePosition
        {
            MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
            X = tileX,
            Y = tileY,
        };

        var clumpFact = new ResourceClumpFact
        {
            ClumpKind          = clumpKind,
            ClumpGuard         = RefIdTokenEncoder.EncodeProtocolKey(clumpKind),
            Health             = health,
            Width              = width,
            Height             = height,
            RequiredTool       = requiredTool,
            RequiredToolLevel  = GetRequiredToolLevel(clumpIndex),
        };

        var summary = $"{clumpKind} ({width}x{height}, hp={health}, {requiredTool})";

        return new WorldEntityFact
        {
            Ref = refId,
            Type = "resource_clump",
            FactKind = "resource_clump",
            Position = position,
            Summary = summary,
            ResourceClump = clumpFact,
        };
    }

    internal static string ClassifyClumpKind(int index)
    {
        // Names must match proto query_facts.proto ResourceClumpFact.clump_kind comment exactly.
        return index switch
        {
            148 => "quarry_boulder",
            600 => "large_stump",
            602 => "hollow_log",
            622 => "meteorite",
            672 => "large_rock",
            752 => "mine_rock_0",
            754 => "mine_rock_1",
            756 => "mine_rock_2",
            758 => "mine_rock_3",
            _ => $"clump_{index}",
        };
    }

    private static string GetRequiredTool(int index)
    {
        return index switch
        {
            600 or 602 => "axe",
            _ => "pickaxe",
        };
    }

    // 0=基础, 1=铜, 2=钢, 3=金, 4=铱
    private static int GetRequiredToolLevel(int index)
    {
        return index switch
        {
            600 => 1,              // large_stump: copper axe
            602 => 2,              // hollow_log: steel axe
            622 or 148 => 3,       // meteor / quarry_boulder: gold pickaxe
            672 => 2,              // large_rock: steel pickaxe
            752 or 754 or 756 or 758 => 0, // mine rocks: basic pickaxe
            _ => 0,
        };
    }
}
