using System;
using System.Collections.Generic;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2;
using StarCoPlay.V2.Models;

namespace StarCoPlay.Protocol;

/// <summary>
/// Maps generated protocol CommandRequest messages to the current V2 runtime command shape.
/// </summary>
public static class ProtoCommandMapper
{
    public static V2Command ToV2Command(CommandRequest request, out ProtocolMappingDiagnostics diagnostics)
    {
        diagnostics = new ProtocolMappingDiagnostics();
        ValidateEnvelope(request);

        var protocolCapability = NormalizeProtocolCapability(request);
        ValidateCapabilityAuthorization(protocolCapability, request.Source);
        var runtimeCapability = CapabilityNameMap.ToRuntime(protocolCapability);

        return new V2Command
        {
            Seq = StableSeq(request.CommandId),
            ProtocolVersion = AdapterV2.ProtocolVersion,
            Capability = runtimeCapability,
            ActionId = request.CommandId,
            Params = MapParams(protocolCapability, request, diagnostics),
        };
    }

    private static void ValidateEnvelope(CommandRequest request)
    {
        if (request == null)
            throw ProtocolMappingException.InvalidParams("CommandRequest is required");
        if (string.IsNullOrWhiteSpace(request.CommandId))
            throw ProtocolMappingException.InvalidParams("command_id is required");
        if (request.TimeoutMs <= 0)
            throw ProtocolMappingException.InvalidParams("timeout_ms must be > 0");
        if (request.Source == CommandSource.Unspecified)
            throw ProtocolMappingException.InvalidParams("source must not be COMMAND_SOURCE_UNSPECIFIED");
        if (string.IsNullOrWhiteSpace(request.Capability))
            throw ProtocolMappingException.InvalidParams("capability is required");
        if (request.ParamsCase == CommandRequest.ParamsOneofCase.None)
            throw ProtocolMappingException.InvalidParams("oneof params is required");
    }

    private static string NormalizeProtocolCapability(CommandRequest request)
    {
        var fromOneof = request.ParamsCase switch
        {
            CommandRequest.ParamsOneofCase.Say => "say",
            CommandRequest.ParamsOneofCase.Emote => "emote",
            CommandRequest.ParamsOneofCase.Face => "face",
            CommandRequest.ParamsOneofCase.MoveTo => "move_to",
            CommandRequest.ParamsOneofCase.GoTo => "go_to",
            CommandRequest.ParamsOneofCase.GoToBed => "go_to_bed",
            CommandRequest.ParamsOneofCase.Interact => "interact",
            CommandRequest.ParamsOneofCase.UseTool => "use_tool",
            CommandRequest.ParamsOneofCase.Equip => "equip",
            CommandRequest.ParamsOneofCase.OpenMenu => "open_menu",
            CommandRequest.ParamsOneofCase.MenuClick => "menu_click",
            CommandRequest.ParamsOneofCase.MenuClose => "menu_close",
            CommandRequest.ParamsOneofCase.Tp => "tp",
            _ => throw ProtocolMappingException.NotSupported($"Unsupported params branch: {request.ParamsCase}"),
        };

        if (!string.Equals(request.Capability, fromOneof, StringComparison.OrdinalIgnoreCase))
        {
            throw ProtocolMappingException.InvalidParams(
                $"capability '{request.Capability}' does not match params branch '{fromOneof}'");
        }

        return fromOneof;
    }

    private static void ValidateCapabilityAuthorization(string protocolCapability, CommandSource source)
    {
        if (!CapabilityNameMap.IsInternalProtocolCapability(protocolCapability))
            return;

        if (source is CommandSource.AgentRuntime or CommandSource.Diagnostic)
            return;

        throw ProtocolMappingException.InvalidParams(
            $"capability '{protocolCapability}' is internal-only and requires agent runtime or diagnostic source");
    }

    private static Dictionary<string, object> MapParams(
        string protocolCapability,
        CommandRequest request,
        ProtocolMappingDiagnostics diagnostics)
    {
        return protocolCapability switch
        {
            "say" => MapSay(request.Say),
            "emote" => MapEmote(request.Emote),
            "face" => MapFace(request.Face),
            "move_to" => MapMoveTo(request.MoveTo),
            "go_to" => MapGoTo(request.GoTo, diagnostics),
            "go_to_bed" => MapGoToBed(request.GoToBed, diagnostics),
            "interact" => MapInteract(request.Interact),
            "use_tool" => MapUseTool(request.UseTool),
            "equip" => MapEquip(request.Equip),
            "open_menu" => new Dictionary<string, object>(),
            "menu_click" => MapMenuClick(request.MenuClick),
            "menu_close" => new Dictionary<string, object>(),
            "tp" => MapTp(request.Tp, diagnostics),
            _ => throw ProtocolMappingException.NotSupported($"Unsupported protocol capability: {protocolCapability}"),
        };
    }

    private static Dictionary<string, object> MapSay(SayParams p)
    {
        if (p == null || string.IsNullOrWhiteSpace(p.Content))
            throw ProtocolMappingException.InvalidParams("say.content is required");

        return new Dictionary<string, object> { ["message"] = p.Content };
    }

    private static Dictionary<string, object> MapEmote(EmoteParams p)
    {
        if (p == null || p.EmoteType == EmoteType.Unspecified)
            throw ProtocolMappingException.InvalidParams("emote.emote_type is required");

        return new Dictionary<string, object> { ["type"] = ToRuntimeEmote(p.EmoteType) };
    }

    private static Dictionary<string, object> MapFace(FaceParams p)
    {
        if (p == null || p.Direction == Direction.Unspecified)
            throw ProtocolMappingException.InvalidParams("face.direction is required");

        return new Dictionary<string, object> { ["direction"] = ToRuntimeDirection(p.Direction, compass: false) };
    }

    private static Dictionary<string, object> MapMoveTo(MoveToParams p)
    {
        if (p == null || p.Mode == MoveToMode.Unspecified)
            throw ProtocolMappingException.InvalidParams("move_to.mode is required");

        var result = new Dictionary<string, object>
        {
            ["x"] = p.X,
            ["y"] = p.Y,
        };

        if (p.TimeoutFrames > 0)
            result["timeout_frames"] = p.TimeoutFrames;

        switch (p.Mode)
        {
            case MoveToMode.MoveToExact:
                if (p.FaceDirection != Direction.Unspecified)
                    result["face_direction"] = ToRuntimeDirection(p.FaceDirection, compass: false);
                break;
            case MoveToMode.MoveToEdgeWarp:
                if (p.Direction == Direction.Unspecified)
                    throw ProtocolMappingException.InvalidParams("move_to.direction is required for MOVE_TO_EDGE_WARP");
                result["direction"] = ToRuntimeDirection(p.Direction, compass: true);
                break;
            case MoveToMode.MoveToAdjacent:
                if (p.AdjacentSide == Direction.Unspecified)
                    throw ProtocolMappingException.InvalidParams("move_to.adjacent_side is required for MOVE_TO_ADJACENT");
                result["adjacent_side"] = ToRuntimeDirection(p.AdjacentSide, compass: true);
                break;
            default:
                throw ProtocolMappingException.InvalidParams($"Unsupported move_to.mode: {p.Mode}");
        }

        return result;
    }

    private static Dictionary<string, object> MapGoTo(GoToParams p, ProtocolMappingDiagnostics diagnostics)
    {
        if (p == null)
            throw ProtocolMappingException.InvalidParams("go_to params are required");
        if (string.IsNullOrWhiteSpace(p.MapId))
            throw ProtocolMappingException.InvalidParams("go_to.map_id is required");
        if (p.ArrivalMode == ArrivalMode.Unspecified)
            throw ProtocolMappingException.InvalidParams("go_to.arrival_mode is required");

        var target = $"{p.MapId}:{p.X},{p.Y}";
        var result = new Dictionary<string, object> { ["target"] = target };

        if (p.FaceDirection != Direction.Unspecified)
            result["face_direction"] = ToRuntimeDirection(p.FaceDirection, compass: true);

        if (p.TimeoutFrames > 0)
            diagnostics.Gap("go_to.timeout_frames is not consumed by current runtime");

        if (p.ArrivalMode == ArrivalMode.ArrivalAdjacent)
        {
            diagnostics.Gap("go_to.ARRIVAL_ADJACENT is approximated by current go_to target resolution");
            if (p.StandSide != Direction.Unspecified)
                diagnostics.Gap("go_to.stand_side is not consumed by current runtime");
        }

        return result;
    }

    private static Dictionary<string, object> MapGoToBed(GoToBedParams p, ProtocolMappingDiagnostics diagnostics)
    {
        if (p != null && p.TimeoutFrames > 0)
            diagnostics.Gap("go_to_bed.timeout_frames is not consumed by current go_bed runtime");

        return new Dictionary<string, object>();
    }

    private static Dictionary<string, object> MapInteract(InteractParams p)
    {
        if (p?.TargetTile == null)
            throw ProtocolMappingException.InvalidParams("interact.target_tile is required");

        return new Dictionary<string, object>
        {
            ["x"] = p.TargetTile.X,
            ["y"] = p.TargetTile.Y,
        };
    }

    private static Dictionary<string, object> MapUseTool(UseToolParams p)
    {
        if (p?.TargetTile == null)
            throw ProtocolMappingException.InvalidParams("use_tool.target_tile is required");
        if (p.ChargeLevel < 0 || p.ChargeLevel > 4)
            throw ProtocolMappingException.InvalidParams("use_tool.charge_level must be between 0 and 4");

        return new Dictionary<string, object>
        {
            ["x"] = p.TargetTile.X,
            ["y"] = p.TargetTile.Y,
            ["charge_level"] = p.ChargeLevel,
        };
    }

    private static Dictionary<string, object> MapEquip(EquipParams p)
    {
        if (p == null)
            throw ProtocolMappingException.InvalidParams("equip params are required");

        if (p.HasSlot)
            return new Dictionary<string, object> { ["slot"] = p.Slot };

        if (p.HasItemName && !string.IsNullOrWhiteSpace(p.ItemName))
            return new Dictionary<string, object>
            {
                ["item"] = p.ItemName,
                ["item_name"] = p.ItemName,
            };

        if (p.ItemCategory != ItemCategory.Unspecified)
            throw ProtocolMappingException.NotSupported("equip.item_category is not supported in phase one");

        throw ProtocolMappingException.InvalidParams("equip requires slot or item_name");
    }

    private static Dictionary<string, object> MapMenuClick(MenuClickParams p)
    {
        if (p == null || p.ComponentId == 0)
            throw ProtocolMappingException.InvalidParams("menu_click.component_id is required");
        if (p.Source == MenuComponentSource.Unspecified)
            throw ProtocolMappingException.InvalidParams("menu_click.source is required");

        return new Dictionary<string, object>
        {
            ["component_id"] = p.ComponentId,
            ["source"] = p.Source == MenuComponentSource.Tab ? "tab" : "page",
        };
    }

    private static Dictionary<string, object> MapTp(TpParams p, ProtocolMappingDiagnostics diagnostics)
    {
        if (p?.Target == null)
            throw ProtocolMappingException.InvalidParams("tp.target is required");
        if (string.IsNullOrWhiteSpace(p.Target.MapId))
            throw ProtocolMappingException.InvalidParams("tp.target.map_id is required");
        if (p.Reason == TeleportReason.Unspecified)
            throw ProtocolMappingException.InvalidParams("tp.reason is required");

        if (p.FaceDirection != Direction.Unspecified)
            diagnostics.Gap("tp.face_direction is recorded by protocol but not consumed by current runtime");

        return new Dictionary<string, object>
        {
            ["location"] = p.Target.MapId,
            ["x"] = p.Target.X,
            ["y"] = p.Target.Y,
            ["reason"] = p.Reason.ToString(),
        };
    }

    private static int StableSeq(string commandId)
    {
        unchecked
        {
            var hash = 23;
            foreach (var ch in commandId)
                hash = (hash * 31) + ch;
            return hash == int.MinValue ? 0 : Math.Abs(hash);
        }
    }

    private static string ToRuntimeDirection(Direction direction, bool compass)
    {
        return direction switch
        {
            Direction.Up => compass ? "north" : "up",
            Direction.Down => compass ? "south" : "down",
            Direction.Left => compass ? "west" : "left",
            Direction.Right => compass ? "east" : "right",
            _ => throw ProtocolMappingException.InvalidParams("direction must not be UNSPECIFIED"),
        };
    }

    private static string ToRuntimeEmote(EmoteType emoteType)
    {
        return emoteType switch
        {
            EmoteType.EmoteHappy => "happy",
            EmoteType.EmoteSad => "sad",
            EmoteType.EmoteAngry => "angry",
            EmoteType.EmoteHeart => "heart",
            EmoteType.EmoteExclamation => "exclamation",
            EmoteType.EmoteQuestion => "question",
            EmoteType.EmoteLaugh => "laugh",
            _ => throw ProtocolMappingException.InvalidParams($"Unsupported emote_type: {emoteType}"),
        };
    }
}
