using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Dispatch;
using StarCoPlay.ProtocolCore.Results;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.GameServices.Menu;

namespace StarCoPlay.ProtocolCore.Handlers;

// ─────────────────────────────────────────────
//  1. OpenMenuProtoHandler
// ─────────────────────────────────────────────

public sealed class OpenMenuProtoHandler : IProtoCommandHandler
{
    private readonly InputCombo _combo;
    private readonly InputBridge _inputBridge;
    private readonly IMonitor _monitor;
    private string _menuTypeBefore = "None";
    private string _targetMenu = "inventory";

    public string Capability => "open_menu";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.OpenMenu;
    public ProtoCommandKind Kind => ProtoCommandKind.ActiveShort;
    public bool IsActive { get; private set; }

    public OpenMenuProtoHandler(InputCombo combo, InputBridge inputBridge, IMonitor monitor)
    {
        _combo = combo; _inputBridge = inputBridge; _monitor = monitor;
    }

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        if (Game1.player == null)
            return ProtoValidationResult.Invalid("player is null");
        if (Game1.player.UsingTool)
            return ProtoValidationResult.Invalid("player is using a tool");

        var p = ctx.Request.OpenMenu;
        var menu = string.IsNullOrEmpty(p.Menu) ? "inventory" : p.Menu;
        if (menu != "inventory" && menu != "quest_log")
            return ProtoValidationResult.Invalid(
                "open_menu only supports inventory or quest_log; use interact to open contextual menus");

        return ProtoValidationResult.Ok();
    }

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        var p = ctx.Request.OpenMenu;
        _targetMenu = string.IsNullOrEmpty(p.Menu) ? "inventory" : p.Menu;
        _menuTypeBefore = Game1.activeClickableMenu?.GetType().Name ?? "None";

        // Already have a menu open — check if it's the target
        if (Game1.activeClickableMenu != null)
        {
            if (IsTargetMenuOpen())
            {
                ctx.TypedResult = BuildResult($"already_open:{_targetMenu}");
                IsActive = false;
                return HandlerStartResult.Immediate();
            }
            else
            {
                return HandlerStartResult.Rejected("another menu is open");
            }
        }

        // No menu open — open the target
        if (_targetMenu == "quest_log")
            _combo.OpenQuestLog();
        else
            _combo.OpenMenu();

        IsActive = true;
        return HandlerStartResult.Async();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx)
    {
        if (_inputBridge.IsBusy) return HandlerTickResult.Continue;
        if (!IsTargetMenuOpen()) return HandlerTickResult.Continue;

        // For inventory, ensure we're on tab 0
        if (_targetMenu == "inventory" && Game1.activeClickableMenu is GameMenu gm && gm.currentTab != 0)
        {
            gm.changeTab(0);
        }

        ctx.TypedResult = BuildResult($"opened:{_targetMenu}");
        IsActive = false;
        return HandlerTickResult.Completed;
    }

    public void Cancel(ProtoCommandContext ctx, string reason)
    {
        _inputBridge.Clear();
        IsActive = false;
    }

    private bool IsTargetMenuOpen()
    {
        return _targetMenu switch
        {
            "inventory" => Game1.activeClickableMenu is GameMenu,
            "quest_log" => Game1.activeClickableMenu is QuestLog,
            _ => false
        };
    }

    private MenuResult BuildResult(string completionReason)
    {
        var menuTypeAfter = Game1.activeClickableMenu?.GetType().Name ?? "None";
        return new MenuResult
        {
            Action = "open",
            Transition = new MenuTransition
            {
                MenuTypeBefore = _menuTypeBefore,
                MenuTypeAfter = menuTypeAfter,
                UiOpenBefore = _menuTypeBefore != "None",
                UiOpenAfter = menuTypeAfter != "None"
            },
        };
    }
}

// ─────────────────────────────────────────────
//  2. QueryMenuProtoHandler
// ─────────────────────────────────────────────

public sealed class QueryMenuProtoHandler : IProtoCommandHandler
{
    private readonly MenuNavigator _menuNavigator;

    public string Capability => "query_menu";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.QueryMenu;
    public ProtoCommandKind Kind => ProtoCommandKind.PassiveRead;
    public bool IsActive => false;

    public QueryMenuProtoHandler(MenuNavigator menuNavigator, IMonitor monitor)
    {
        _menuNavigator = menuNavigator;
    }

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        return ProtoValidationResult.Ok();
    }

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        var gsSnapshot = _menuNavigator.QueryMenu();

        var protoSnapshot = new Protocol.V245.MenuSnapshot
        {
            MenuType = gsSnapshot.MenuType,
            ActiveTab = gsSnapshot.ActiveTab ?? "",
        };

        foreach (var c in gsSnapshot.Components)
        {
            protoSnapshot.Components.Add(new Protocol.V245.MenuComponent
            {
                ComponentId = c.Id,
                Source = c.Source == MenuNavigator.SourceTab
                    ? MenuComponentSource.Tab
                    : MenuComponentSource.Page,
                Label = c.Label ?? c.Name ?? "",
                Visible = c.Visible,
                Enabled = true,
                ItemName = c.ItemName ?? "",
                ItemStack = c.ItemStack ?? 0,
                Price = c.Price ?? 0,
            });
        }

        ctx.TypedResult = protoSnapshot;
        return HandlerStartResult.Immediate();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx)
    {
        return HandlerTickResult.Continue;
    }

    public void Cancel(ProtoCommandContext ctx, string reason)
    {
    }
}

// ─────────────────────────────────────────────
//  3. MenuClickProtoHandler
// ─────────────────────────────────────────────

public sealed class MenuClickProtoHandler : IProtoCommandHandler
{
    private readonly MenuNavigator _menuNavigator;
    private readonly InputBridge _inputBridge;
    private readonly IMonitor _monitor;
    private string _menuTypeBefore = "None";
    private int _componentId;
    private MenuComponentSource _source;

    public string Capability => "menu_click";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.MenuClick;
    public ProtoCommandKind Kind => ProtoCommandKind.ActiveShort;
    public bool IsActive { get; private set; }

    public MenuClickProtoHandler(MenuNavigator menuNavigator, InputBridge inputBridge, IMonitor monitor)
    {
        _menuNavigator = menuNavigator; _inputBridge = inputBridge; _monitor = monitor;
    }

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        if (Game1.activeClickableMenu == null)
            return ProtoValidationResult.Invalid("no active menu");

        var p = ctx.Request.MenuClick;
        if (p.ComponentId < -1)
            return ProtoValidationResult.Invalid("component_id must be -1 or non-negative");

        // component_id=-1 是"默认区域点击"（推进 DialogueBox 文本），仅支持 isQuestion=false 的对话框
        if (p.ComponentId == -1)
        {
            if (Game1.activeClickableMenu is not DialogueBox db)
                return ProtoValidationResult.Invalid("default menu_click (component_id=-1) only supports DialogueBox");
            if (db.isQuestion)
                return ProtoValidationResult.Invalid("default menu_click (component_id=-1) only supports non-question DialogueBox; use component_id of the response for question dialogues");
        }

        return ProtoValidationResult.Ok();
    }

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        var p = ctx.Request.MenuClick;
        _menuTypeBefore = Game1.activeClickableMenu?.GetType().Name ?? "None";
        _componentId = p.ComponentId;
        _source = p.Source;

        // component_id=-1：默认区域点击，推进 DialogueBox 文本
        if (p.ComponentId == -1)
        {
            if (!_menuNavigator.ClickDefault())
            {
                var err = _menuNavigator.LastClickResult?.Error ?? "default click failed";
                return HandlerStartResult.Rejected(err);
            }
        }
        else
        {
            var sourceStr = p.Source == MenuComponentSource.Tab ? "tab" : "page";
            if (!_menuNavigator.Click(p.ComponentId, sourceStr))
            {
                var err = _menuNavigator.LastClickResult?.Error ?? "component not found or not reachable";
                return HandlerStartResult.Rejected(err);
            }
        }

        var clickResult = _menuNavigator.LastClickResult;
        var menuTypeAfter = clickResult?.MenuTypeAfter
            ?? Game1.activeClickableMenu?.GetType().Name ?? "None";

        _monitor.Log($"[MenuClick] id={_componentId} effect={clickResult?.Effect} " +
            $"method={clickResult?.Method}", LogLevel.Debug);

        ctx.TypedResult = new MenuResult
        {
            Action = "click",
            ComponentId = _componentId,
            Source = _source,
            ClickCount = 1,
            Transition = new MenuTransition
            {
                MenuTypeBefore = _menuTypeBefore,
                MenuTypeAfter = menuTypeAfter,
                UiOpenBefore = _menuTypeBefore != "None",
                UiOpenAfter = menuTypeAfter != "None"
            },
            Timing = ProtoTiming.Capture(ctx)
        };
        IsActive = false;
        return HandlerStartResult.Immediate();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx)
    {
        return HandlerTickResult.Continue;
    }

    public void Cancel(ProtoCommandContext ctx, string reason)
    {
        IsActive = false;
    }
}

// ─────────────────────────────────────────────
//  4. MenuCloseProtoHandler
// ─────────────────────────────────────────────

public sealed class MenuCloseProtoHandler : IProtoCommandHandler
{
    private readonly InputCombo _combo;
    private readonly InputBridge _inputBridge;
    private readonly IMonitor _monitor;
    private string _menuTypeBefore = "None";

    public string Capability => "menu_close";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.MenuClose;
    public ProtoCommandKind Kind => ProtoCommandKind.ActiveShort;
    public bool IsActive { get; private set; }

    public MenuCloseProtoHandler(InputCombo combo, InputBridge inputBridge, IMonitor monitor)
    {
        _combo = combo; _inputBridge = inputBridge; _monitor = monitor;
    }

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        if (Game1.player == null)
            return ProtoValidationResult.Invalid("player is null");
        return ProtoValidationResult.Ok();
    }

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        _menuTypeBefore = Game1.activeClickableMenu?.GetType().Name ?? "None";

        if (Game1.activeClickableMenu == null)
        {
            ctx.TypedResult = new MenuResult
            {
                Action = "close",
                AlreadyClosed = true,
                Transition = new MenuTransition
                {
                    MenuTypeBefore = "None",
                    MenuTypeAfter = "None",
                    UiOpenBefore = false,
                    UiOpenAfter = false
                },
                Timing = ProtoTiming.Capture(ctx)
            };
            IsActive = false;
            return HandlerStartResult.Immediate();
        }

        var menu = Game1.activeClickableMenu;

        if (!menu.readyToClose())
        {
            return HandlerStartResult.Rejected("menu is not ready to close");
        }

        // DialogueBox owns additional close semantics (dialogue queues/flags and
        // Farmer.CanMove restoration) in closeDialogue().  Generic
        // exitThisMenu() only clears activeClickableMenu and leaves the player
        // movement-fenced after a remote interaction.
        if (menu is DialogueBox dialogue)
            dialogue.closeDialogue();
        else
            // Preserve cleanupBeforeExit(), exitFunction callbacks, and item
            // return logic for all other menu types.
            menu.exitThisMenu();

        // exitThisMenu() is synchronous for most menus but some may trigger
        // animations or open a follow-up menu via exitFunction. Go async to confirm.
        if (Game1.activeClickableMenu == null)
        {
            ctx.TypedResult = BuildCloseResult(ctx);
            IsActive = false;
            return HandlerStartResult.Immediate();
        }

        IsActive = true;
        return HandlerStartResult.Async();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx)
    {
        if (Game1.activeClickableMenu != null)
            return HandlerTickResult.Continue;

        ctx.TypedResult = BuildCloseResult(ctx);
        IsActive = false;
        return HandlerTickResult.Completed;
    }

    public void Cancel(ProtoCommandContext ctx, string reason)
    {
        _inputBridge.Clear();
        IsActive = false;
    }

    private MenuResult BuildCloseResult(ProtoCommandContext ctx)
    {
        return new MenuResult
        {
            Action = "close",
            AlreadyClosed = false,
            Transition = new MenuTransition
            {
                MenuTypeBefore = _menuTypeBefore,
                MenuTypeAfter = "None",
                UiOpenBefore = true,
                UiOpenAfter = false
            },
            Timing = ProtoTiming.Capture(ctx)
        };
    }
}
