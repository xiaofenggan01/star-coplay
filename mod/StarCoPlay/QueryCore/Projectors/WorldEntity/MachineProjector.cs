using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;
using SObject = StardewValley.Object;

namespace StarCoPlay.V2.Query.Projectors.WorldEntity;

public static class MachineProjector
{
    public static WorldEntityFact Project(SObject obj, string mapNameOrUniqueName, int tileX, int tileY)
    {
        var qualifiedItemId = obj.QualifiedItemId ?? "";
        var internalName = obj.Name ?? "";
        var displayName = obj.DisplayName ?? "";
        var readyForHarvest = obj.readyForHarvest.Value;
        var minutesUntilReady = obj.MinutesUntilReady;
        var machineDataId = qualifiedItemId;

        var refId = RefIdBuilder.BuildMachine(mapNameOrUniqueName, tileX, tileY, qualifiedItemId);

        var position = new TilePosition
        {
            MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
            X = tileX,
            Y = tileY,
        };

        // Derive machine state: READY > PROCESSING > IDLE.
        // heldObject is product info, not a prerequisite for PROCESSING state.
        var machineState = readyForHarvest
            ? MachineState.MachineReady
            : minutesUntilReady > 0
                ? MachineState.MachineProcessing
                : MachineState.MachineIdle;

        var machineFact = new MachineFact
        {
            QualifiedItemId   = qualifiedItemId,
            MachineDataId     = machineDataId,
            InternalName      = internalName,
            DisplayName       = displayName,
            ReadyForHarvest   = readyForHarvest,
            MinutesUntilReady = minutesUntilReady,
            MachineState      = machineState,
        };

        if (obj.heldObject.Value is { } held)
        {
            machineFact.HeldObject = ProjectHeldObject(held);
        }

        if (obj.lastInputItem.Value is SObject lastInput)
        {
            machineFact.LastInputItemId = lastInput.QualifiedItemId ?? "";
        }

        var parts = new System.Collections.Generic.List<string>();
        if (readyForHarvest) parts.Add("ready");
        else if (minutesUntilReady > 0) parts.Add($"{minutesUntilReady}min left");
        else parts.Add("idle");

        var summary = $"{displayName} ({string.Join(", ", parts)})";

        return new WorldEntityFact
        {
            Ref = refId,
            Type = "machine",
            FactKind = "machine",
            Position = position,
            Summary = summary,
            Machine = machineFact,
        };
    }

    private static LooseItemFact ProjectHeldObject(SObject held)
    {
        return new LooseItemFact
        {
            QualifiedItemId = held.QualifiedItemId ?? "",
            ItemId = held.ItemId ?? "",
            InternalName = held.Name ?? "",
            DisplayName = held.DisplayName ?? "",
            Stack = held.Stack,
            Quality = held.Quality,
        };
    }
}
