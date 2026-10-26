using StardewValley;

namespace StarCoPlay.GameServices.Menu;

public sealed class UiClickResult
{
    public bool Success { get; init; }
    public string Method { get; init; } = "";
    public string Effect { get; init; } = "no_op";
    public int ComponentId { get; init; }
    public string ComponentName { get; init; } = "";
    public string Source { get; init; } = "";
    public int X { get; init; }
    public int Y { get; init; }
    public string CursorItemBefore { get; init; } = "";
    public string CursorItemAfter { get; init; } = "";
    public string SlotItemBefore { get; init; } = "";
    public string SlotItemAfter { get; init; } = "";
    public string MenuTypeBefore { get; init; } = "";
    public string MenuTypeAfter { get; init; } = "";
    public string? Error { get; init; }

    public static UiClickResult Failed(string error) => new()
    {
        Success = false,
        Error = error
    };
}
