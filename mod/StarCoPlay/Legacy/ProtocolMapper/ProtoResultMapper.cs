using System;
using System.Collections.Generic;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2;
using StarCoPlay.V2.Models;

namespace StarCoPlay.Protocol;

/// <summary>
/// Maps current V2 runtime results back to generated protocol CommandResult messages.
/// </summary>
public static class ProtoResultMapper
{
    public static CommandResult FromV2Result(
        string protocolCapability,
        V2Result v2,
        ProtocolMappingDiagnostics? diagnostics = null)
    {
        if (v2 == null)
            throw new ArgumentNullException(nameof(v2));

        var result = new CommandResult
        {
            CommandId = v2.ActionId,
            Success = v2.Success,
            Status = ToCommandStatus(v2),
            Capability = protocolCapability,
            Observation = new Observation { Text = BuildObservation(v2, diagnostics) },
        };

        if (!v2.Success)
        {
            result.Error = new Error
            {
                Code = ToErrorCode(v2.Error?.Code),
                Message = v2.Error?.Message ?? "Command failed",
                Detail = v2.Error?.Code ?? "",
            };
        }

        AttachCapabilityData(result, protocolCapability, v2);
        return result;
    }

    public static CommandResult FromMappingException(
        CommandRequest request,
        ProtocolMappingException ex)
    {
        var capability = string.IsNullOrWhiteSpace(request?.Capability)
            ? request?.ParamsCase.ToString() ?? ""
            : request.Capability;

        return new CommandResult
        {
            CommandId = request?.CommandId ?? "",
            Success = false,
            Status = CommandStatus.Rejected,
            Capability = capability,
            Observation = new Observation { Text = ex.Message },
            Error = new Error
            {
                Code = ToErrorCode(ex.ErrorCode),
                Message = ex.Message,
                Detail = ex.ErrorCode,
            },
        };
    }

    private static void AttachCapabilityData(CommandResult result, string protocolCapability, V2Result v2)
    {
        switch (protocolCapability)
        {
            case "say":
                result.Say = new SayResult { ContentLength = ReadString(v2.Data, "message").Length };
                break;
            case "emote":
                result.Emote = new EmoteResult { EmoteType = ToProtocolEmote(ReadString(v2.Data, "emote")) };
                break;
            case "face":
                result.Face = new FaceResult { FinalDirection = ToProtocolDirection(ReadInt(v2.Data, "direction_int")) };
                break;
            case "move_to":
                // Phase 1: detailed MoveToResult is intentionally left as a documented gap.
                break;
            case "go_to":
                // Phase 1: detailed GoToResult is intentionally left as a documented gap.
                break;
            case "go_to_bed":
                // Phase 1: detailed GoToBedResult is intentionally left as a documented gap.
                break;
            case "interact":
                // Phase 1: detailed InteractResult is intentionally left as a documented gap.
                break;
            case "use_tool":
                // Phase 1: detailed UseToolResult is intentionally left as a documented gap.
                break;
            case "equip":
                result.Equip = MapEquipResult(v2.Data);
                break;
            case "menu_click":
                result.Menu = new MenuResult { Action = "click" };
                break;
            case "menu_close":
                result.Menu = new MenuResult { Action = "close" };
                break;
            case "open_menu":
                result.Menu = new MenuResult { Action = "open" };
                break;
            case "tp":
                result.Tp = new TpResult
                {
                    Reason = TeleportReason.Unspecified,
                    Authorized = v2.Success,
                };
                break;
        }
    }

    private static V245.EquipResult MapEquipResult(object? data)
    {
        return new V245.EquipResult
        {
            SelectedBy = ReadString(data, "selected_by"),
            Slot = ReadInt(data, "slot"),
            ItemName = ReadString(data, "item_name"),
            ItemDisplayName = ReadString(data, "item_display_name"),
            Method = ReadString(data, "method"),
            Swapped = ReadBool(data, "swapped"),
            FromSlot = ReadInt(data, "from_slot"),
            ToSlot = ReadInt(data, "to_slot"),
            SwappedOut = ReadString(data, "swapped_out"),
        };
    }

    private static string BuildObservation(V2Result v2, ProtocolMappingDiagnostics? diagnostics)
    {
        var text = v2.Observation ?? (v2.Success ? "Command completed" : v2.Error?.Message ?? "Command failed");
        if (diagnostics?.HasNotes == true)
        {
            if (diagnostics.Warnings.Count > 0)
                text += $" warnings=[{string.Join("; ", diagnostics.Warnings)}]";
            if (diagnostics.Phase1Gaps.Count > 0)
                text += $" phase1_gaps=[{string.Join("; ", diagnostics.Phase1Gaps)}]";
        }
        return text;
    }

    private static CommandStatus ToCommandStatus(V2Result v2)
    {
        if (v2.Success)
            return CommandStatus.Completed;

        return v2.Error?.Code switch
        {
            ResultBuilder.Errors.Timeout or ResultBuilder.Errors.AnimationTimeout or ResultBuilder.Errors.NavigationTimeout => CommandStatus.Timeout,
            ResultBuilder.Errors.Cancelled => CommandStatus.Cancelled,
            ResultBuilder.Errors.InvalidParams or ResultBuilder.Errors.UnknownCapability or ResultBuilder.Errors.NotSupported => CommandStatus.Rejected,
            _ => CommandStatus.Failed,
        };
    }

    private static ErrorCode ToErrorCode(string? code)
    {
        return code switch
        {
            ResultBuilder.Errors.InvalidParams => ErrorCode.InvalidParams,
            ResultBuilder.Errors.UnknownCapability => ErrorCode.CapabilityMissing,
            ResultBuilder.Errors.Timeout or ResultBuilder.Errors.AnimationTimeout or ResultBuilder.Errors.NavigationTimeout => ErrorCode.Timeout,
            ResultBuilder.Errors.Cancelled => ErrorCode.Cancelled,
            ResultBuilder.Errors.ExecutionFailed => ErrorCode.ExecutionFailed,
            _ => ErrorCode.ExecutionFailed,
        };
    }

    private static string ReadString(object? data, string key)
    {
        if (data is IDictionary<string, object> dict && dict.TryGetValue(key, out var value))
            return value?.ToString() ?? "";

        var prop = data?.GetType().GetProperty(ToPascalCase(key));
        return prop?.GetValue(data)?.ToString() ?? "";
    }

    private static int ReadInt(object? data, string key)
    {
        if (data is IDictionary<string, object> dict && dict.TryGetValue(key, out var value))
            return ConvertToInt(value);

        var prop = data?.GetType().GetProperty(ToPascalCase(key));
        return ConvertToInt(prop?.GetValue(data));
    }

    private static bool ReadBool(object? data, string key)
    {
        if (data is IDictionary<string, object> dict && dict.TryGetValue(key, out var value))
            return ConvertToBool(value);

        var prop = data?.GetType().GetProperty(ToPascalCase(key));
        return ConvertToBool(prop?.GetValue(data));
    }

    private static int ConvertToInt(object? value)
    {
        return value switch
        {
            int i => i,
            long l when l >= int.MinValue && l <= int.MaxValue => (int)l,
            double d when d >= int.MinValue && d <= int.MaxValue => (int)d,
            string s when int.TryParse(s, out var parsed) => parsed,
            _ => 0,
        };
    }

    private static bool ConvertToBool(object? value)
    {
        return value switch
        {
            bool b => b,
            string s when bool.TryParse(s, out var parsed) => parsed,
            _ => false,
        };
    }

    private static string ToPascalCase(string key)
    {
        var parts = key.Split('_', StringSplitOptions.RemoveEmptyEntries);
        for (var i = 0; i < parts.Length; i++)
        {
            if (parts[i].Length == 0) continue;
            parts[i] = char.ToUpperInvariant(parts[i][0]) + parts[i][1..];
        }
        return string.Join("", parts);
    }

    private static EmoteType ToProtocolEmote(string emote)
    {
        return emote.ToLowerInvariant() switch
        {
            "happy" => EmoteType.EmoteHappy,
            "sad" => EmoteType.EmoteSad,
            "angry" => EmoteType.EmoteAngry,
            "heart" => EmoteType.EmoteHeart,
            "exclamation" => EmoteType.EmoteExclamation,
            "question" => EmoteType.EmoteQuestion,
            "laugh" => EmoteType.EmoteLaugh,
            _ => EmoteType.Unspecified,
        };
    }

    private static Direction ToProtocolDirection(int direction)
    {
        return direction switch
        {
            0 => Direction.Up,
            1 => Direction.Right,
            2 => Direction.Down,
            3 => Direction.Left,
            _ => Direction.Unspecified,
        };
    }
}
