using Microsoft.Xna.Framework;
using StardewValley.Objects;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.WorldEntity;

public static class ContainerProjector
{
    public static WorldEntityFact Project(Chest chest, string mapNameOrUniqueName, int tileX, int tileY)
    {
        var qualifiedItemId = chest.QualifiedItemId ?? "";
        var containerKind = ClassifyContainerKind(chest);
        var capacity = chest.GetActualCapacity();
        var items = chest.GetItemsForPlayer();
        var itemsCount = 0;
        foreach (var item in items)
        {
            if (item != null) itemsCount++;
        }

        var refId = RefIdBuilder.BuildContainer(mapNameOrUniqueName, tileX, tileY, qualifiedItemId);

        var position = new TilePosition
        {
            MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
            X = tileX,
            Y = tileY,
        };

        var containerFact = new ContainerFact
        {
            QualifiedItemId = qualifiedItemId,
            ContainerKind = containerKind,
            Capacity = capacity,
            ItemsCount = itemsCount,
            PlayerChest = chest.playerChest.Value,
            Fridge = chest.fridge.Value,
            SpecialChestType = chest.SpecialChestType.ToString(),
            Color = ColorToHex(chest.playerChoiceColor.Value),
        };

        var summary = $"{containerKind} ({itemsCount}/{capacity} items)";

        return new WorldEntityFact
        {
            Ref = refId,
            Type = "container",
            FactKind = "container",
            Position = position,
            Summary = summary,
            Container = containerFact,
        };
    }

    private static string ClassifyContainerKind(Chest chest)
    {
        return chest.SpecialChestType switch
        {
            Chest.SpecialChestTypes.JunimoChest    => "junimo_chest",
            Chest.SpecialChestTypes.MiniShippingBin => "mini_shipping_bin",
            Chest.SpecialChestTypes.AutoLoader      => "auto_loader",
            Chest.SpecialChestTypes.BigChest        => "big_chest",
            _ when chest.fridge.Value               => "fridge",
            _ when chest.QualifiedItemId == "(BC)130" => "stone_chest",
            _ when chest.playerChest.Value          => "chest",
            _                                       => "container",
        };
    }

    private static string ColorToHex(Color color)
    {
        if (color == Color.Black || color.A == 0) return "";
        return $"#{color.R:X2}{color.G:X2}{color.B:X2}";
    }
}
