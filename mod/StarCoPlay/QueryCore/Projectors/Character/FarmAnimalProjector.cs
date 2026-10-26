using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.Character;

public static class FarmAnimalProjector
{
    public static CharacterFact Project(FarmAnimal animal)
    {
        var animalId = animal.myID.Value;
        var animalType = animal.type.Value;
        var mapName = animal.currentLocation?.NameOrUniqueName ?? "unknown";
        var tileX = (int)animal.Tile.X;
        var tileY = (int)animal.Tile.Y;
        var produceReady = !string.IsNullOrEmpty(animal.currentProduce.Value);

        var animalFact = new FarmAnimalFact
        {
            AnimalId        = animalId.ToString(),
            AnimalType      = animalType,
            HomeBuildingType = animal.home?.buildingType.Value ?? "",
            HomeBuildingId  = animal.home?.id.Value.ToString("N") ?? "",
            ProduceReady    = produceReady,
            Friendship      = animal.friendshipTowardFarmer.Value,
            WasPetted       = animal.wasPet.Value,
            WasAutoPetted   = animal.wasAutoPet.Value,
            Happiness       = animal.happiness.Value,
        };

        return new CharacterFact
        {
            Ref = RefIdBuilder.BuildFarmAnimal(animalId),
            Type = "farm_animal",
            FactKind = "farm_animal",
            Name = animal.Name,
            DisplayName = animal.displayName ?? animal.Name,
            Position = new TilePosition
            {
                MapId = RefIdTokenEncoder.EncodeMapToken(mapName),
                X = tileX,
                Y = tileY,
            },
            Summary = $"{animal.displayName ?? animal.Name} ({animalType}), friendship:{animal.friendshipTowardFarmer.Value}",
            FarmAnimal = animalFact,
        };
    }
}
