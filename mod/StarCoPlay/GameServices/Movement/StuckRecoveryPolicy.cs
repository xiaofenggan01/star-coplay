namespace StarCoPlay
{
    internal static class StuckRecoveryPolicy
    {
        internal const int EmergencyWarpEvidenceWindows = 4;

        internal static bool CanUseEmergencyWarp(
            int sameTileEvidenceWindows,
            bool usingTool,
            bool gameplayBlocked)
        {
            return sameTileEvidenceWindows >= EmergencyWarpEvidenceWindows
                && !usingTool
                && !gameplayBlocked;
        }
    }
}
