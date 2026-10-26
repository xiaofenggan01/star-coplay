using System.Collections.Generic;
using Newtonsoft.Json;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2;

/// <summary>
/// Factory for building standardized V2Result objects.
/// </summary>
public static class ResultBuilder
{
    public static V2Result Success(int seq, string actionId, object? data = null)
    {
        return new V2Result
        {
            Seq = seq,
            ActionId = actionId,
            Success = true,
            Status = "ok",
            Data = data,
        };
    }

    public static V2Result Partial(int seq, string actionId, object? data = null)
    {
        return new V2Result
        {
            Seq = seq,
            ActionId = actionId,
            Success = true,
            Status = "partial",
            Data = data,
        };
    }

    public static V2Result Failure(int seq, string actionId, string errorCode, string message)
    {
        return new V2Result
        {
            Seq = seq,
            ActionId = actionId,
            Success = false,
            Status = "failed",
            Error = new V2Error { Code = errorCode, Message = message },
        };
    }

    /// <summary>
    /// Append an observation. Multiple calls are joined with "; ".
    /// </summary>
    public static V2Result WithObservation(this V2Result result, string observation)
    {
        if (string.IsNullOrEmpty(result.Observation))
            result.Observation = observation;
        else
            result.Observation += "; " + observation;
        return result;
    }

    public static V2Result WithSideEffect(this V2Result result, string type, string detail)
    {
        result.SideEffects ??= new List<V2SideEffect>();
        result.SideEffects.Add(new V2SideEffect { Type = type, Detail = detail });
        return result;
    }

    private static readonly JsonSerializerSettings _settings = new()
    {
        Formatting = Formatting.None,
        NullValueHandling = NullValueHandling.Ignore,
    };

    /// <summary>Serialize result to a single-line JSON string.</summary>
    public static string ToJson(V2Result result)
    {
        return JsonConvert.SerializeObject(result, _settings);
    }

    // ── Common error codes ────────────────────────────────────────────────

    public static class Errors
    {
        // ── 通用 ──
        public const string Unknown = "UNKNOWN";
        public const string UnknownCapability = "UNKNOWN_CAPABILITY";
        public const string InvalidParams = "INVALID_PARAMS";
        public const string Timeout = "TIMEOUT";
        public const string Cancelled = "CANCELLED";
        public const string NotImplemented = "NOT_IMPLEMENTED";
        public const string NotSupported = "NOT_SUPPORTED";

        // ── 目标相关 ──
        public const string TargetNotFound = "TARGET_NOT_FOUND";
        public const string TargetUnreachable = "TARGET_UNREACHABLE";
        public const string TargetNotInteractable = "TARGET_NOT_INTERACTABLE";
        public const string NotInteractable = "NOT_INTERACTABLE";
        public const string OutOfRange = "OUT_OF_RANGE";

        // ── 资源相关 ──
        public const string OutOfEnergy = "OUT_OF_ENERGY";
        public const string ItemNotFound = "ITEM_NOT_FOUND";
        public const string ItemInsufficient = "ITEM_INSUFFICIENT";
        public const string InventoryFull = "INVENTORY_FULL";
        public const string ContainerFull = "CONTAINER_FULL";
        public const string ContainerNotFound = "CONTAINER_NOT_FOUND";
        public const string InvalidContainerId = "INVALID_CONTAINER_ID";
        public const string InsufficientFunds = "INSUFFICIENT_FUNDS";

        // ── 工具相关 ──
        public const string WrongTool = "WRONG_TOOL";
        public const string ToolNotFound = "TOOL_NOT_FOUND";
        public const string ToolEmpty = "TOOL_EMPTY";

        // ── 导航相关 ──
        public const string PathBlocked = "PATH_BLOCKED";
        public const string DestinationInvalid = "DESTINATION_INVALID";
        public const string MapNotFound = "MAP_NOT_FOUND";
        public const string NavigationTimeout = "NAVIGATION_TIMEOUT";
        public const string WarpTimeout = "WARP_TIMEOUT";
        public const string LocationNotFound = "LOCATION_NOT_FOUND";

        // ── 商店相关 ──
        public const string ItemNotInShop = "ITEM_NOT_IN_SHOP";
        public const string ShopNotFound = "SHOP_NOT_FOUND";
        public const string ShopClosed = "SHOP_CLOSED";

        // ── 执行中断 ──
        public const string Interrupted = "INTERRUPTED";
        public const string GameStateChanged = "GAME_STATE_CHANGED";
        public const string TimeLimitReached = "TIME_LIMIT_REACHED";
        public const string CompoundBusy = "COMPOUND_BUSY";

        // ── 系统/通信 ──
        public const string IpcError = "IPC_ERROR";
        public const string ProtocolMismatch = "PROTOCOL_MISMATCH";

        // ── 兼容保留（非设计规格，已在代码中使用） ──
        public const string InvalidIntent = "INVALID_INTENT";
        public const string ExecutionFailed = "EXECUTION_FAILED";
        public const string AnimationTimeout = "ANIMATION_TIMEOUT";
    }
}
