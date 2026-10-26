using StardewValley;
using StardewValley.Characters;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.Character;

public static class PetProjector
{
    public static CharacterFact Project(Pet pet)
    {
        var mapName = pet.currentLocation?.NameOrUniqueName ?? "unknown";
        var tileX = (int)pet.Tile.X;
        var tileY = (int)pet.Tile.Y;
        var guid = pet.petId.Value;

        var lastPetted = false;
        if (pet.lastPetDay.TryGetValue(Game1.player.UniqueMultiplayerID, out int day))
            lastPetted = day >= Game1.Date.TotalDays;

        var petFact = new PetFact
        {
            PetGuid = guid.ToString("N"),
            PetType = pet.petType.Value ?? "",
            Friendship = pet.friendshipTowardFarmer.Value,
            PettedToday = lastPetted,
        };

        return new CharacterFact
        {
            Ref = RefIdBuilder.BuildPet(guid),
            Type = "pet",
            FactKind = "pet",
            Name = pet.Name,
            DisplayName = pet.displayName ?? pet.Name,
            Position = new TilePosition
            {
                MapId = RefIdTokenEncoder.EncodeMapToken(mapName),
                X = tileX,
                Y = tileY,
            },
            Summary = $"{pet.displayName ?? pet.Name} ({pet.petType.Value}), friendship:{pet.friendshipTowardFarmer.Value}",
            Pet = petFact,
        };
    }
}
