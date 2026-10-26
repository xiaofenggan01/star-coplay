using StardewModdingAPI;
using StardewValley;
using StardewValley.Tools;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Dispatch;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.GameServices.Inventory;

using ProtoEquipResult = StarCoPlay.Protocol.V245.EquipResult;

namespace StarCoPlay.ProtocolCore.Handlers;

public sealed class EquipProtoHandler : IProtoCommandHandler
{
    private readonly InputBridge _inputBridge;
    private readonly IMonitor _monitor;

    private int _targetIndex;
    private string _selectedBy = "";
    private ItemCategory _requestedCategory;
    private bool _swapped;
    private int _fromSlot;
    private int _toSlot;
    private string? _swappedOutName;

    public string Capability => "equip";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.Equip;
    public ProtoCommandKind Kind => ProtoCommandKind.ActiveShort;
    public bool IsActive { get; private set; }

    public EquipProtoHandler(InputCombo combo, InputBridge inputBridge, IMonitor monitor)
    {
        _inputBridge = inputBridge;
        _monitor = monitor;
    }

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        var p = ctx.Request.Equip;

        if (!p.HasSlot && !p.HasItemName && p.ItemCategory == ItemCategory.Unspecified)
            return ProtoValidationResult.Invalid("at least one selector required: slot, item_name, or item_category");

        if (Game1.player == null)
            return ProtoValidationResult.Invalid("player is null");

        if (p.HasSlot && (p.Slot < 1 || p.Slot > Game1.player.Items.Count))
            return ProtoValidationResult.Invalid($"slot must be 1-{Game1.player.Items.Count}");

        if (p.HasItemName && string.IsNullOrEmpty(p.ItemName))
            return ProtoValidationResult.Invalid("item_name must be non-empty when provided");

        if (Game1.activeClickableMenu != null)
            return ProtoValidationResult.Invalid("menu is open, cannot equip");

        return ProtoValidationResult.Ok();
    }

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        var p = ctx.Request.Equip;
        _swapped = false;
        _fromSlot = -1;
        _toSlot = -1;
        _swappedOutName = null;

        InventorySelectionResult? selection;

        if (p.HasSlot)
        {
            _selectedBy = "slot";
            _targetIndex = p.Slot - 1;
            _requestedCategory = ItemCategory.Unspecified;
            var item = PlayerInventorySelector.GetItemAtSlot(_targetIndex);
            if (item == null)
                return HandlerStartResult.Rejected("slot is empty");
            selection = new InventorySelectionResult { Slot = _targetIndex, Item = item };
        }
        else if (p.HasItemName)
        {
            _selectedBy = "item_name";
            _requestedCategory = ItemCategory.Unspecified;
            selection = PlayerInventorySelector.FindByName(p.ItemName);
            if (selection == null)
                return HandlerStartResult.Rejected("item not found in inventory");
            _targetIndex = selection.Slot;
        }
        else
        {
            _selectedBy = "item_category";
            _requestedCategory = p.ItemCategory;
            selection = PlayerInventorySelector.FindByCategory(p.ItemCategory);
            if (selection == null)
                return HandlerStartResult.Rejected("no matching item in inventory");
            _targetIndex = selection.Slot;
        }

        // 已在目标槽位（仅工具栏直达）
        if (selection != null && selection.IsToolbarSlot && Game1.player.CurrentToolIndex == _targetIndex)
        {
            ctx.TypedResult = BuildResult(selection, "none");
            IsActive = false;
            return HandlerStartResult.Immediate();
        }

        // 执行装备
        if (selection != null && !selection.IsToolbarSlot)
        {
            var swap = ToolbarSwapper.SwapToToolbar(_targetIndex);
            _swapped = true;
            _fromSlot = swap.FromSlot;
            _toSlot = swap.TargetSlot;
            _swappedOutName = swap.SwappedOutName;
            _targetIndex = swap.TargetSlot;
        }
        else
        {
            Game1.player.CurrentToolIndex = _targetIndex;
        }

        var equipped = PlayerInventorySelector.GetItemAtSlot(_targetIndex);
        var finalSelection = equipped != null
            ? new InventorySelectionResult { Slot = _targetIndex, Item = equipped }
            : selection;

        ctx.TypedResult = BuildResult(finalSelection, _swapped ? "toolbar_swap" : "direct_api");
        IsActive = false;
        _monitor.Log($"[EquipProto] Completed: selectedBy={_selectedBy} slot={_targetIndex + 1} swapped={_swapped}", LogLevel.Debug);
        return HandlerStartResult.Immediate();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx)
    {
        return HandlerTickResult.Completed;
    }

    public void Cancel(ProtoCommandContext ctx, string reason)
    {
        _inputBridge.Clear();
        IsActive = false;
        _monitor.Log($"[EquipProto] Cancelled: {reason}", LogLevel.Debug);
    }

    private ProtoEquipResult BuildResult(InventorySelectionResult? selection, string method)
    {
        return new ProtoEquipResult
        {
            SelectedBy = _selectedBy,
            Slot = _targetIndex + 1,
            ItemName = selection?.ItemName ?? "",
            ItemDisplayName = selection?.DisplayName ?? "",
            ItemCategory = _requestedCategory != ItemCategory.Unspecified
                ? _requestedCategory
                : DetectCategory(selection?.Item),
            Method = method,
            Swapped = _swapped,
            FromSlot = _swapped ? _fromSlot + 1 : 0,
            ToSlot = _swapped ? _toSlot + 1 : 0,
            SwappedOut = _swappedOutName ?? ""
        };
    }

    private static ItemCategory DetectCategory(Item? item) => item switch
    {
        StardewValley.Tools.Axe => ItemCategory.Axe,
        StardewValley.Tools.Pickaxe => ItemCategory.Pickaxe,
        StardewValley.Tools.Hoe => ItemCategory.Hoe,
        StardewValley.Tools.WateringCan => ItemCategory.WateringCan,
        StardewValley.Tools.FishingRod => ItemCategory.FishingRod,
        MeleeWeapon w when w.isScythe() => ItemCategory.Scythe,
        MeleeWeapon => ItemCategory.Weapon,
        _ => ItemCategory.Unspecified
    };
}
