using System;
using System.Linq;
using StardewValley;
using StardewValley.Characters;
using StardewValley.Monsters;
using StarCoPlay.V2.Query.Projectors.Character;
using StarCoPlay.V2.RefId.Encoding;
using StarCoPlay.V2.RefId.Protocol;

namespace StarCoPlay.V2.RefId.Resolution;

public sealed class CharacterResolver : IRefIdResolver
{
    public RefIdResolutionResult Resolve(ParsedRefId parsedRefId)
    {
        var factKind = RefIdProtocolMapper.InferFactKind(parsedRefId);

        return parsedRefId.Type switch
        {
            "npc" => ResolveNpc(parsedRefId, factKind),
            "farm_animal" => ResolveFarmAnimal(parsedRefId, factKind),
            "pet" => ResolvePet(parsedRefId, factKind),
            "horse" => ResolveHorse(parsedRefId, factKind),
            "monster" => ResolveMonster(parsedRefId, factKind),
            _ => new RefIdResolutionResult(RefIdStatus.Unsupported, parsedRefId, factKind,
                $"Character type '{parsedRefId.Type}' not supported.", null),
        };
    }

    private static RefIdResolutionResult ResolveNpc(ParsedRefId parsed, string factKind)
    {
        if (parsed.LocatorParts.Count != 1)
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsed, factKind,
                null, "NPC locator must have exactly 1 part: npc_key.");

        var npcKey = parsed.LocatorParts[0];
        NPC? found = null;
        var count = 0;

        foreach (var npc in Utility.getAllCharacters())
        {
            if (RefIdTokenEncoder.EncodeProtocolKey(npc.Name) == npcKey)
            {
                found = npc;
                count++;
            }
        }

        if (count == 0)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsed, factKind, null, "NPC not found in world.");

        if (count > 1)
            return new RefIdResolutionResult(RefIdStatus.Ambiguous, parsed, factKind, null, "Multiple NPCs match key.");

        // Guard for NPC is the encoded name (same as locator key)
        if (npcKey != parsed.IdentityGuard)
            return new RefIdResolutionResult(RefIdStatus.StaleGuardMismatch, parsed, factKind, null,
                $"Guard mismatch: expected '{npcKey}', got '{parsed.IdentityGuard}'.");

        var source = new ResolvedRefSource(found!, found!.currentLocation);
        return new RefIdResolutionResult(RefIdStatus.Resolved, parsed, factKind, null, null, source);
    }

    private static RefIdResolutionResult ResolveFarmAnimal(ParsedRefId parsed, string factKind)
    {
        if (parsed.LocatorParts.Count != 1)
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsed, factKind,
                null, "Farm animal locator must have exactly 1 part: animal_id.");

        if (!long.TryParse(parsed.LocatorParts[0], out var animalId))
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsed, factKind,
                null, "Animal ID must be a valid long.");

        var farm = Game1.getFarm();
        if (farm == null)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsed, factKind, null, "Farm not loaded.");

        var animal = farm.getAllFarmAnimals().FirstOrDefault(a => a.myID.Value == animalId);
        if (animal == null)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsed, factKind, null, "Farm animal not found.");

        var expectedGuard = animalId.ToString();
        if (expectedGuard != parsed.IdentityGuard)
            return new RefIdResolutionResult(RefIdStatus.StaleGuardMismatch, parsed, factKind, null,
                $"Guard mismatch: expected '{expectedGuard}', got '{parsed.IdentityGuard}'.");

        var source = new ResolvedRefSource(animal, animal.currentLocation);
        return new RefIdResolutionResult(RefIdStatus.Resolved, parsed, factKind, null, null, source);
    }

    private static RefIdResolutionResult ResolvePet(ParsedRefId parsed, string factKind)
    {
        if (parsed.LocatorParts.Count != 1)
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsed, factKind,
                null, "Pet locator must have exactly 1 part: guid_hex.");

        if (!Guid.TryParse(parsed.LocatorParts[0], out var guid))
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsed, factKind,
                null, "Locator must be a valid GUID.");

        foreach (var location in Game1.locations)
        {
            foreach (var character in location.characters)
            {
                if (character is Pet pet && pet.petId.Value == guid)
                {
                    var expectedGuard = guid.ToString("N");
                    if (expectedGuard != parsed.IdentityGuard)
                        return new RefIdResolutionResult(RefIdStatus.StaleGuardMismatch, parsed, factKind, null,
                            $"Guard mismatch: expected '{expectedGuard}', got '{parsed.IdentityGuard}'.");

                    var source = new ResolvedRefSource(pet, location);
                    return new RefIdResolutionResult(RefIdStatus.Resolved, parsed, factKind, null, null, source);
                }
            }
        }

        return new RefIdResolutionResult(RefIdStatus.Missing, parsed, factKind, null, "Pet with GUID not found.");
    }

    private static RefIdResolutionResult ResolveHorse(ParsedRefId parsed, string factKind)
    {
        if (parsed.LocatorParts.Count != 1)
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsed, factKind,
                null, "Horse locator must have exactly 1 part: guid_hex.");

        if (!Guid.TryParse(parsed.LocatorParts[0], out var guid))
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsed, factKind,
                null, "Locator must be a valid GUID.");

        foreach (var location in Game1.locations)
        {
            foreach (var character in location.characters)
            {
                if (character is Horse horse && horse.HorseId == guid)
                {
                    var expectedGuard = guid.ToString("N");
                    if (expectedGuard != parsed.IdentityGuard)
                        return new RefIdResolutionResult(RefIdStatus.StaleGuardMismatch, parsed, factKind, null,
                            $"Guard mismatch: expected '{expectedGuard}', got '{parsed.IdentityGuard}'.");

                    var source = new ResolvedRefSource(horse, location);
                    return new RefIdResolutionResult(RefIdStatus.Resolved, parsed, factKind, null, null, source);
                }
            }
        }

        return new RefIdResolutionResult(RefIdStatus.Missing, parsed, factKind, null, "Horse with GUID not found.");
    }

    private static RefIdResolutionResult ResolveMonster(ParsedRefId parsed, string factKind)
    {
        // Format: character:monster:<map_token>:<synthetic_guid_n>#<synthetic_guid_n>
        if (parsed.LocatorParts.Count != 2)
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsed, factKind,
                null, "Monster locator must have 2 parts: map_token, guid_hex.");

        var mapToken = parsed.LocatorParts[0];
        var guidHex = parsed.LocatorParts[1];

        var location = MapLookup.Find(mapToken);
        if (location == null)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsed, factKind, null, "Map not found.");

        foreach (var character in location.characters)
        {
            if (character is Monster monster)
            {
                var syntheticGuid = MonsterProjector.DeriveGuid(
                    location.NameOrUniqueName,
                    monster.Name,
                    (int)monster.Tile.X,
                    (int)monster.Tile.Y);

                if (syntheticGuid.ToString("N") == guidHex)
                {
                    // Guard is the same guid_hex
                    if (guidHex != parsed.IdentityGuard)
                        return new RefIdResolutionResult(RefIdStatus.StaleGuardMismatch, parsed, factKind, null,
                            $"Guard mismatch: expected '{guidHex}', got '{parsed.IdentityGuard}'.");

                    var source = new ResolvedRefSource(monster, location);
                    return new RefIdResolutionResult(RefIdStatus.Resolved, parsed, factKind, null, null, source);
                }
            }
        }

        return new RefIdResolutionResult(RefIdStatus.Missing, parsed, factKind, null, "Monster with GUID not found.");
    }
}
