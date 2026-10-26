using System;
using System.Collections.Generic;
using System.Globalization;
using StarCoPlay.V2.RefId.Encoding;
using ProtocolRefId = StarCoPlay.Protocol.V245.RefId;

namespace StarCoPlay.V2.RefId.Building;

public static class RefIdBuilder
{
    public static ProtocolRefId BuildWorldEntity(string type, string mapNameOrUniqueName, int x, int y, string identityGuard)
    {
        if (!RefIdTokenEncoder.IsSafeToken(identityGuard))
            throw new ArgumentException("Identity guard must already be a safe token.", nameof(identityGuard));

        return Build(
            "world_entity",
            RefIdTokenEncoder.EncodeProtocolKey(type),
            new[]
            {
                RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
                x.ToString(CultureInfo.InvariantCulture),
                y.ToString(CultureInfo.InvariantCulture),
            },
            identityGuard);
    }

    public static ProtocolRefId BuildTree(string mapNameOrUniqueName, int x, int y, string treeType)
    {
        return BuildWorldEntity("tree", mapNameOrUniqueName, x, y, RefIdTokenEncoder.EncodeWildTreeType(treeType));
    }

    public static ProtocolRefId BuildFruitTree(string mapNameOrUniqueName, int x, int y, string treeIdOrQualifiedItemId)
    {
        return BuildWorldEntity("fruit_tree", mapNameOrUniqueName, x, y, EncodeObjectGuard(treeIdOrQualifiedItemId));
    }

    public static ProtocolRefId BuildCrop(string mapNameOrUniqueName, int x, int y, string cropGuardRaw)
    {
        return BuildWorldEntity("crop", mapNameOrUniqueName, x, y, EncodeObjectGuard(cropGuardRaw));
    }

    public static ProtocolRefId BuildResourceNode(string mapNameOrUniqueName, int x, int y, string nodeKindOrQualifiedItemId)
    {
        var guard = LooksLikeQualifiedItemId(nodeKindOrQualifiedItemId)
            ? RefIdTokenEncoder.EncodeQualifiedItemId(nodeKindOrQualifiedItemId)
            : RefIdTokenEncoder.EncodeResourceNodeKind(nodeKindOrQualifiedItemId);

        return BuildWorldEntity("resource_node", mapNameOrUniqueName, x, y, guard);
    }

    public static ProtocolRefId BuildLooseItem(string mapNameOrUniqueName, int x, int y, string qualifiedItemId)
    {
        return BuildWorldEntity("loose_item", mapNameOrUniqueName, x, y, RefIdTokenEncoder.EncodeQualifiedItemId(qualifiedItemId));
    }

    public static ProtocolRefId BuildMachine(string mapNameOrUniqueName, int x, int y, string qualifiedItemId)
    {
        return BuildWorldEntity("machine", mapNameOrUniqueName, x, y, RefIdTokenEncoder.EncodeQualifiedItemId(qualifiedItemId));
    }

    public static ProtocolRefId BuildContainer(string mapNameOrUniqueName, int x, int y, string qualifiedItemIdOrContainerKey)
    {
        var guard = LooksLikeQualifiedItemId(qualifiedItemIdOrContainerKey)
            ? RefIdTokenEncoder.EncodeQualifiedItemId(qualifiedItemIdOrContainerKey)
            : RefIdTokenEncoder.EncodeProtocolKey(qualifiedItemIdOrContainerKey);

        return BuildWorldEntity("container", mapNameOrUniqueName, x, y, guard);
    }

    public static ProtocolRefId BuildBed(string mapNameOrUniqueName, int x, int y, string furnitureQualifiedItemId)
    {
        return BuildWorldEntity("bed", mapNameOrUniqueName, x, y, RefIdTokenEncoder.EncodeQualifiedItemId(furnitureQualifiedItemId));
    }

    public static ProtocolRefId BuildFurniture(string mapNameOrUniqueName, int x, int y, string qualifiedItemId)
    {
        return BuildWorldEntity("furniture", mapNameOrUniqueName, x, y, RefIdTokenEncoder.EncodeQualifiedItemId(qualifiedItemId));
    }

    public static ProtocolRefId BuildResourceClump(string mapNameOrUniqueName, int x, int y, string clumpKind)
    {
        return BuildWorldEntity("resource_clump", mapNameOrUniqueName, x, y, RefIdTokenEncoder.EncodeProtocolKey(clumpKind));
    }

    public static ProtocolRefId BuildDoor(string mapNameOrUniqueName, int x, int y, string targetMap)
    {
        return BuildWorldEntity("door", mapNameOrUniqueName, x, y, RefIdTokenEncoder.EncodeMapToken(targetMap));
    }

    public static ProtocolRefId BuildWarp(string mapNameOrUniqueName, int x, int y, string targetMap)
    {
        return BuildWorldEntity("warp", mapNameOrUniqueName, x, y, RefIdTokenEncoder.EncodeMapToken(targetMap));
    }

    public static ProtocolRefId BuildNpc(string npcName)
    {
        var npcKey = RefIdTokenEncoder.EncodeProtocolKey(npcName);
        return Build("character", "npc", new[] { npcKey }, npcKey);
    }

    public static ProtocolRefId BuildMonster(string mapNameOrUniqueName, Guid guid)
    {
        var guidToken = guid.ToString("N");
        return Build("character", "monster", new[] { RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName), guidToken }, guidToken);
    }

    public static ProtocolRefId BuildFarmAnimal(long animalId)
    {
        var idToken = animalId.ToString(CultureInfo.InvariantCulture);
        return Build("character", "farm_animal", new[] { idToken }, idToken);
    }

    public static ProtocolRefId BuildPet(Guid guid)
    {
        var guidToken = guid.ToString("N");
        return Build("character", "pet", new[] { guidToken }, guidToken);
    }

    public static ProtocolRefId BuildHorse(Guid guid)
    {
        var guidToken = guid.ToString("N");
        return Build("character", "horse", new[] { guidToken }, guidToken);
    }

    public static ProtocolRefId BuildPlayerInventoryItem(int slot, string qualifiedItemId)
    {
        return BuildInventoryItem("player", slot, qualifiedItemId);
    }

    public static ProtocolRefId BuildInventoryItem(string owner, int slot, string qualifiedItemId)
    {
        if (slot < 0)
            throw new ArgumentOutOfRangeException(nameof(slot), "Inventory slot cannot be negative.");

        return Build(
            "inventory",
            "item",
            new[] { owner, slot.ToString(CultureInfo.InvariantCulture) },
            RefIdTokenEncoder.EncodeQualifiedItemId(qualifiedItemId));
    }

    /// <summary>
    /// Build a self-contained inventory item ref for container items (chest/fridge).
    /// Chest locator: [chest, map_token, x, y, slot] (5 parts)
    /// Fridge locator: [fridge, map_token, slot] (3 parts)
    /// </summary>
    public static ProtocolRefId BuildContainerInventoryItem(
        string containerKind, string mapToken, int? tileX, int? tileY, int slot, string qualifiedItemId)
    {
        if (slot < 0)
            throw new ArgumentOutOfRangeException(nameof(slot), "Inventory slot cannot be negative.");

        var encodedMap = RefIdTokenEncoder.EncodeMapToken(mapToken);
        var slotStr = slot.ToString(CultureInfo.InvariantCulture);

        string[] locator = containerKind switch
        {
            "chest" => new[]
            {
                "chest", encodedMap,
                (tileX ?? 0).ToString(CultureInfo.InvariantCulture),
                (tileY ?? 0).ToString(CultureInfo.InvariantCulture),
                slotStr
            },
            "fridge" => new[] { "fridge", encodedMap, slotStr },
            _ => new[] { containerKind, encodedMap, slotStr },
        };

        return Build("inventory", "item", locator,
            RefIdTokenEncoder.EncodeQualifiedItemId(qualifiedItemId));
    }

    public static ProtocolRefId Build(string refType, string type, IReadOnlyList<string> locatorSegments, string identityGuard)
    {
        if (!RefIdTokenEncoder.IsSafeToken(refType))
            throw new ArgumentException("Ref type must be a safe token.", nameof(refType));

        if (!RefIdTokenEncoder.IsSafeToken(type))
            throw new ArgumentException("Type must be a safe token.", nameof(type));

        if (locatorSegments.Count == 0)
            throw new ArgumentException("At least one locator segment is required.", nameof(locatorSegments));

        foreach (var segment in locatorSegments)
        {
            if (!RefIdTokenEncoder.IsSafeToken(segment))
                throw new ArgumentException("Every locator segment must be a safe token.", nameof(locatorSegments));
        }

        if (!RefIdTokenEncoder.IsSafeToken(identityGuard))
            throw new ArgumentException("Identity guard must be a safe token.", nameof(identityGuard));

        var value = $"{refType}:{type}:{string.Join(":", locatorSegments)}#{identityGuard}";
        var refId = new ProtocolRefId
        {
            Value = value,
            RefType = refType,
            Type = type,
            IdentityGuard = identityGuard,
        };
        refId.LocatorSegments.Add(locatorSegments);
        return refId;
    }

    private static string EncodeObjectGuard(string raw)
    {
        if (LooksLikeQualifiedItemId(raw))
            return RefIdTokenEncoder.EncodeQualifiedItemId(raw);

        return $"o_{RefIdTokenEncoder.EncodeProtocolKey(raw)}";
    }

    private static bool LooksLikeQualifiedItemId(string raw)
    {
        var trimmed = raw?.Trim() ?? "";
        return trimmed.StartsWith("(", StringComparison.Ordinal) && trimmed.Contains(')', StringComparison.Ordinal);
    }
}
