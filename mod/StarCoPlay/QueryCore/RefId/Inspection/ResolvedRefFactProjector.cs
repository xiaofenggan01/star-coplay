using System;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Characters;
using StardewValley.Monsters;
using StardewValley.Objects;
using StardewValley.TerrainFeatures;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.Query.Projectors;
using StarCoPlay.V2.Query.Projectors.Character;
using StarCoPlay.V2.Query.Projectors.WorldEntity;
using StarCoPlay.V2.RefId.Resolution;
using SObject = StardewValley.Object;


namespace StarCoPlay.V2.RefId.Inspection;

public sealed class ResolvedRefFactProjector : IRefFactProjector
{
    private readonly IMonitor? _monitor;

    public ResolvedRefFactProjector(IMonitor? monitor = null) => _monitor = monitor;

    public bool TryProject(RefIdResolutionResult resolution, out InspectedRef inspectedRef)
    {
        inspectedRef = new InspectedRef();

        if (resolution.Source?.Value == null) return false;
        if (resolution.Status is not (RefIdStatus.Resolved or RefIdStatus.ResolvedDegraded)) return false;

        var parsed = resolution.ParsedRefId!;
        var source = resolution.Source;

        return parsed.RefType switch
        {
            "world_entity" => TryProjectWorldEntity(parsed, source, out inspectedRef),
            "character" => TryProjectCharacter(parsed, source, out inspectedRef),
            "inventory" => TryProjectInventory(parsed, source, out inspectedRef),
            "ui_element" => TryProjectUiElement(parsed, source, out inspectedRef),
            _ => false,
        };
    }

    private static bool TryProjectWorldEntity(ParsedRefId parsed, ResolvedRefSource source, out InspectedRef inspectedRef)
    {
        inspectedRef = new InspectedRef();
        var mapId = source.Location!.NameOrUniqueName;
        var tileX = (int)source.Tile!.Value.X;
        var tileY = (int)source.Tile!.Value.Y;

        WorldEntityFact? fact = parsed.Type switch
        {
            "tree" when source.Value is Tree tree =>
                TreeProjector.Project(tree, mapId, tileX, tileY),

            "fruit_tree" when source.Value is FruitTree ft =>
                FruitTreeProjector.Project(ft, mapId, tileX, tileY),

            "crop" when source.Value is HoeDirt dirt =>
                CropProjector.Project(dirt, mapId, tileX, tileY),

            "machine" when source.Value is SObject obj =>
                MachineProjector.Project(obj, mapId, tileX, tileY),

            "container" when source.Value is Chest chest =>
                ContainerProjector.Project(chest, mapId, tileX, tileY),

            "container" when source.Value is SObject =>
                null, // SObject classified as Container but not Chest — StaleTypeMismatch at projector level

            "resource_node" when source.Value is SObject obj =>
                ResourceNodeProjector.Project(obj, mapId, tileX, tileY),

            "loose_item" when source.Value is SObject obj =>
                LooseItemProjector.Project(obj, mapId, tileX, tileY),

            "bed" when source.Value is BedFurniture bed =>
                BedProjector.Project(bed, mapId, tileX, tileY),

            "furniture" when source.Value is Furniture furniture =>
                FurnitureProjector.Project(furniture, mapId, tileX, tileY),

            "resource_clump" when source.Value is ResourceClump clump =>
                ResourceClumpProjector.Project(clump, mapId, tileX, tileY),

            "door" when source.Value is DoorResolvedData door =>
                DoorProjector.Project(door.DoorTile, door.TargetMap, mapId, source.Location),

            "warp" when source.Value is Warp warp =>
                WarpProjector.Project(warp, mapId),

            _ => null,
        };

        if (fact == null) return false;

        inspectedRef = new InspectedRef { WorldEntity = fact };
        return true;
    }

    private static bool TryProjectCharacter(ParsedRefId parsed, ResolvedRefSource source, out InspectedRef inspectedRef)
    {
        inspectedRef = new InspectedRef();

        CharacterFact? fact = parsed.Type switch
        {
            "npc" when source.Value is NPC npc =>
                NpcProjector.Project(npc),

            "farm_animal" when source.Value is FarmAnimal animal =>
                FarmAnimalProjector.Project(animal),

            "pet" when source.Value is Pet pet =>
                PetProjector.Project(pet),

            "horse" when source.Value is Horse horse =>
                HorseProjector.Project(horse),

            "monster" when source.Value is Monster monster =>
                MonsterProjector.Project(monster),

            _ => null,
        };

        if (fact == null) return false;

        inspectedRef = new InspectedRef { Character = fact };
        return true;
    }

    private static bool TryProjectInventory(ParsedRefId parsed, ResolvedRefSource source, out InspectedRef inspectedRef)
    {
        inspectedRef = new InspectedRef();

        if (source.Value is not Item item) return false;

        var containerKind = source.ContainerKind ?? "player";
        var containerLocator = source.ContainerLocator ?? "local";
        var slot = source.InventorySlot ?? 0;

        var fact = InventoryProjector.ProjectItem(item, containerKind, containerLocator, slot);
        inspectedRef = new InspectedRef { InventoryItem = fact };
        return true;
    }

    private static bool TryProjectUiElement(ParsedRefId parsed, ResolvedRefSource source, out InspectedRef inspectedRef)
    {
        inspectedRef = new InspectedRef();

        if (source.Value is not UiElementFact fact) return false;

        inspectedRef = new InspectedRef { UiElement = fact };
        return true;
    }
}
