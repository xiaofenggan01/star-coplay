using StardewValley;
using StarCoPlay.Protocol.V245;

namespace StarCoPlay.ProtocolCore.GoTo;

internal static class GoToErrorMapper
{
    public static string BuildStepFailMessage(
        int stepIndex,
        GoToStep step,
        string childCapability,
        string childError)
    {
        var player = Game1.player;
        string currentMap  = Game1.currentLocation?.NameOrUniqueName ?? "unknown";
        string currentTile = player != null
            ? $"({(int)player.Tile.X},{(int)player.Tile.Y})"
            : "(unknown)";

        return $"step[{stepIndex}] {step.Kind} {step.SourceMap}->{step.TargetMap} " +
               $"via {childCapability} failed at {currentMap}{currentTile}: {childError}";
    }
}
