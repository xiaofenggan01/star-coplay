using System.Collections.Generic;
using StardewModdingAPI;
using StarCoPlay.Protocol.V245;

namespace StarCoPlay.ProtocolCore.GoTo;

internal static class GoToRoutePlanner
{
    public static GoToRoutePlan? Plan(
        string fromMap,
        string targetMap,
        int targetX,
        int targetY,
        ArrivalMode arrivalMode,
        Direction standSide,
        Direction faceDirection,
        IMonitor? monitor = null)
    {
        var routeMaps = new List<string>();
        var steps    = new List<GoToStep>();

        bool sameMap = fromMap == targetMap;

        if (sameMap)
        {
            routeMaps.Add(fromMap);
        }
        else
        {
            var route = MapData.FindRoute(fromMap, targetMap);
            if (route == null || route.Count == 0)
            {
                monitor?.Log($"[GoToPlanner] No route from '{fromMap}' to '{targetMap}'", LogLevel.Warn);
                return null;
            }

            routeMaps.Add(fromMap);
            string currentMap = fromMap;

            foreach (var exit in route)
            {
                routeMaps.Add(exit.TargetMap);

                if (exit.Kind == WarpKind.WalkThrough)
                {
                    if (exit.WarpTiles.Count == 0)
                    {
                        monitor?.Log($"[GoToPlanner] WalkThrough exit from '{currentMap}' to '{exit.TargetMap}' has no WarpTiles", LogLevel.Warn);
                        return null;
                    }
                    if (exit.PressDirection == null)
                    {
                        monitor?.Log($"[GoToPlanner] WalkThrough exit from '{currentMap}' to '{exit.TargetMap}' has no PressDirection", LogLevel.Warn);
                        return null;
                    }

                    var warpTile = exit.WarpTiles[0];
                    var pressDir = CompassToProtoDirection(exit.PressDirection.Value);

                    steps.Add(new GoToStep(
                        Kind:          GoToStepKind.EdgeWarp,
                        SourceMap:     currentMap,
                        TargetMap:     exit.TargetMap,
                        X:             warpTile.X,
                        Y:             warpTile.Y,
                        Direction:     pressDir,
                        StandSide:     Direction.Unspecified,
                        FaceDirection: Direction.Unspecified,
                        Exit:          exit));
                }
                else // InteractDoor
                {
                    if (exit.DoorTiles.Count == 0)
                    {
                        monitor?.Log($"[GoToPlanner] InteractDoor exit from '{currentMap}' to '{exit.TargetMap}' has no DoorTiles", LogLevel.Warn);
                        return null;
                    }

                    var doorTile = exit.DoorTiles[0];
                    steps.Add(new GoToStep(
                        Kind:          GoToStepKind.DoorInteract,
                        SourceMap:     currentMap,
                        TargetMap:     exit.TargetMap,
                        X:             doorTile.X,
                        Y:             doorTile.Y,
                        Direction:     Direction.Unspecified,
                        StandSide:     Direction.Unspecified,
                        FaceDirection: Direction.Unspecified,
                        Exit:          exit));
                }

                currentMap = exit.TargetMap;
            }
        }

        // Final positioning step
        if (arrivalMode == ArrivalMode.ArrivalAdjacent)
        {
            steps.Add(new GoToStep(
                Kind:          GoToStepKind.FinalMoveAdjacent,
                SourceMap:     targetMap,
                TargetMap:     targetMap,
                X:             targetX,
                Y:             targetY,
                Direction:     Direction.Unspecified,
                StandSide:     standSide,
                FaceDirection: faceDirection,
                Exit:          null));
        }
        else
        {
            steps.Add(new GoToStep(
                Kind:          GoToStepKind.FinalMoveExact,
                SourceMap:     targetMap,
                TargetMap:     targetMap,
                X:             targetX,
                Y:             targetY,
                Direction:     Direction.Unspecified,
                StandSide:     Direction.Unspecified,
                FaceDirection: faceDirection,
                Exit:          null));
        }

        return new GoToRoutePlan(steps, routeMaps);
    }

    private static Direction CompassToProtoDirection(CompassDirection compass) => compass switch
    {
        CompassDirection.North => Direction.Up,
        CompassDirection.South => Direction.Down,
        CompassDirection.East  => Direction.Right,
        CompassDirection.West  => Direction.Left,
        _                      => Direction.Unspecified
    };
}
