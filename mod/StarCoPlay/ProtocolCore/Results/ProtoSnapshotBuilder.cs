using StardewValley;
using StardewValley.Menus;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;

namespace StarCoPlay.ProtocolCore.Results;

public static class ProtoSnapshotBuilder
{
    public static LocationSnapshot CurrentLocationSnapshot()
    {
        var player = Game1.player;
        if (player == null)
            return new LocationSnapshot();

        return new LocationSnapshot
        {
            MapToken = Game1.currentLocation?.NameOrUniqueName ?? "",
            X = (int)player.Tile.X,
            Y = (int)player.Tile.Y,
            Facing = ToProtoDirection(player.FacingDirection)
        };
    }

    public static Direction CurrentFacingDirection()
    {
        var player = Game1.player;
        if (player == null) return Direction.Unspecified;
        return ToProtoDirection(player.FacingDirection);
    }

    public static float CurrentStamina()
    {
        return Game1.player?.stamina ?? 0f;
    }

    public static int CurrentMoney()
    {
        return Game1.player?.Money ?? 0;
    }

    public static bool CurrentMenuOpen()
    {
        return Game1.activeClickableMenu != null;
    }

    public static string CurrentMenuType()
    {
        var menu = Game1.activeClickableMenu;
        if (menu == null) return "";
        return menu.GetType().Name;
    }

    public static ResourceDelta StaminaDelta(ProtoCommandContext ctx)
    {
        float current = Game1.player?.stamina ?? 0f;
        return new ResourceDelta
        {
            Before = (int)ctx.StartStamina,
            After = (int)current,
            Delta = (int)(current - ctx.StartStamina)
        };
    }

    public static Direction ToProtoDirection(int facingDirection)
    {
        return facingDirection switch
        {
            0 => Direction.Up,
            1 => Direction.Right,
            2 => Direction.Down,
            3 => Direction.Left,
            _ => Direction.Unspecified
        };
    }

    public static int FromProtoDirection(Direction dir)
    {
        return dir switch
        {
            Direction.Up => 0,
            Direction.Right => 1,
            Direction.Down => 2,
            Direction.Left => 3,
            _ => -1
        };
    }

    public static void CaptureStartState(ProtoCommandContext ctx)
    {
        var player = Game1.player;
        if (player == null) return;

        ctx.StartStamina = player.stamina;
        ctx.StartMoney = player.Money;
        ctx.StartLocation = Game1.currentLocation?.NameOrUniqueName ?? "";
        ctx.StartTileX = (int)player.Tile.X;
        ctx.StartTileY = (int)player.Tile.Y;
        ctx.StartFacing = player.FacingDirection;
        ctx.StartMenuOpen = Game1.activeClickableMenu != null;
    }
}
