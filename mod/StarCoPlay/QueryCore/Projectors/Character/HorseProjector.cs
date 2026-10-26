using StardewValley.Characters;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.Character;

public static class HorseProjector
{
    public static CharacterFact Project(Horse horse)
    {
        var mapName = horse.currentLocation?.NameOrUniqueName ?? "unknown";
        var tileX = (int)horse.Tile.X;
        var tileY = (int)horse.Tile.Y;
        var guid = horse.HorseId;

        var horseFact = new HorseFact
        {
            HorseGuid   = guid.ToString("N"),
            OwnerId     = horse.ownerId.Value.ToString(),
            HasRider    = horse.rider != null,
            DisplayName = horse.Name ?? "",
        };

        return new CharacterFact
        {
            Ref = RefIdBuilder.BuildHorse(guid),
            Type = "horse",
            FactKind = "horse",
            Name = horse.Name,
            DisplayName = horse.displayName ?? horse.Name,
            Position = new TilePosition
            {
                MapId = RefIdTokenEncoder.EncodeMapToken(mapName),
                X = tileX,
                Y = tileY,
            },
            Summary = $"Horse '{horse.Name}' at {mapName} ({tileX},{tileY})",
            Horse = horseFact,
        };
    }
}
