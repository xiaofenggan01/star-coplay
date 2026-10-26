using System;
using StardewValley;

namespace StarCoPlay.GameServices.Inventory;

public sealed class SwapResult
{
    public int TargetSlot { get; init; }
    public int FromSlot { get; init; }
    public string? SwappedOutName { get; init; }
}

public static class ToolbarSwapper
{
    public static SwapResult SwapToToolbar(int backpackSlot)
    {
        var player = Game1.player;
        if (backpackSlot < 12 || backpackSlot >= player.Items.Count)
            throw new ArgumentOutOfRangeException(nameof(backpackSlot),
                $"backpackSlot must be >= 12 and < Items.Count, got {backpackSlot}");

        int targetSlot = -1;
        int toolbarEnd = Math.Min(player.Items.Count, 12);
        for (int i = 0; i < toolbarEnd; i++)
        {
            if (player.Items[i] == null)
            {
                targetSlot = i;
                break;
            }
        }
        if (targetSlot < 0)
            targetSlot = player.CurrentToolIndex;

        var swappedOut = player.Items[targetSlot];
        string? swappedOutName = swappedOut?.DisplayName;

        player.Items[targetSlot] = player.Items[backpackSlot];
        player.Items[backpackSlot] = swappedOut;
        player.CurrentToolIndex = targetSlot;

        return new SwapResult
        {
            TargetSlot = targetSlot,
            FromSlot = backpackSlot,
            SwappedOutName = swappedOutName
        };
    }
}
