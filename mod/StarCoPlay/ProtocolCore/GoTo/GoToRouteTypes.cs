using System.Collections.Generic;
using StarCoPlay.Protocol.V245;

namespace StarCoPlay.ProtocolCore.GoTo;

internal enum GoToPhase
{
    None,
    Planning,
    StartingStep,
    WaitingMoveTo,
    WaitingInteract,
    WaitingMapStable,
    Done,
    Failed
}

internal enum GoToStepKind
{
    EdgeWarp,
    DoorInteract,
    FinalMoveExact,
    FinalMoveAdjacent
}

internal sealed record GoToStep(
    GoToStepKind Kind,
    string SourceMap,
    string TargetMap,
    int X,
    int Y,
    Direction Direction,
    Direction StandSide,
    Direction FaceDirection,
    MapExit? Exit);

internal sealed record GoToRoutePlan(
    IReadOnlyList<GoToStep> Steps,
    IReadOnlyList<string> RouteMaps);
