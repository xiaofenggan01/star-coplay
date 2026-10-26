using StardewValley;

namespace StarCoPlay.GameServices.Inventory;

public sealed class InventorySelectionResult
{
    public int Slot { get; init; }
    public Item Item { get; init; } = null!;
    public bool IsToolbarSlot => Slot < 12;
    public string ItemName => Item.Name;
    public string DisplayName => Item.DisplayName;
    public string QualifiedItemId => Item.QualifiedItemId;
}
