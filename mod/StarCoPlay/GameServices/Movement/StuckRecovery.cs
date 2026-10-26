using Microsoft.Xna.Framework;
using StarCoPlay.Logging;
using StardewModdingAPI;
using StardewValley;

namespace StarCoPlay
{
    /// <summary>
    /// Recovers an AI-controlled player without treating warp as navigation.
    /// State cleanup is the normal response; a safe-point warp is reserved for
    /// repeated, independently confirmed failures at the same tile.
    /// </summary>
    public class StuckRecovery
    {
        private const int CooldownThreshold = 120;

        private readonly IMonitor _monitor;
        private readonly ActionExecutor _executor;
        private ModLogger? _logger;
        private int _cooldownFrames;
        private bool _recoveryPending;
        private string? _evidenceLocation;
        private Vector2 _evidenceTile;
        private int _sameTileEvidenceWindows;

        public StuckRecovery(IMonitor monitor, ActionExecutor executor)
        {
            _monitor = monitor;
            _executor = executor;
        }

        public void SetLogger(ModLogger logger) => _logger = logger;

        public bool TryRecover()
        {
            if (_cooldownFrames < CooldownThreshold)
                return false;

            var player = Game1.player;
            var location = Game1.currentLocation;
            if (player == null || location == null)
                return false;

            bool gameplayBlocked = Game1.activeClickableMenu != null
                || Game1.dialogueUp
                || Game1.eventUp;
            if (gameplayBlocked)
            {
                _monitor.Log(
                    "[StuckRecovery] recovery deferred: menu/dialogue/event is active",
                    LogLevel.Trace);
                return false;
            }

            RecordStuckEvidence(location.NameOrUniqueName, player.Tile);
            bool usingTool = player.UsingTool;

            // Cancel the failed action before mutating player state. This also
            // prevents the old path executor from resuming after recovery.
            _executor.CancelCurrent();

            bool recovered;
            if (StuckRecoveryPolicy.CanUseEmergencyWarp(
                _sameTileEvidenceWindows,
                usingTool,
                gameplayBlocked))
            {
                _monitor.Log(
                    $"[StuckRecovery] emergency warp authorized after " +
                    $"{_sameTileEvidenceWindows} confirmed same-tile windows",
                    LogLevel.Warn);
                recovered = DoSafeWarp(player);
            }
            else
            {
                recovered = DoSoftRecovery(player);
            }

            _recoveryPending |= recovered;
            return recovered;
        }

        public void Tick()
        {
            if (_cooldownFrames < CooldownThreshold + 1)
                _cooldownFrames++;
        }

        /// <summary>
        /// Clears escalation evidence only after actual movement or a map
        /// transition. Merely setting canMove=true is not proof of recovery.
        /// </summary>
        public void OnRecovered()
        {
            if (!_recoveryPending)
                return;

            var player = Game1.player;
            var location = Game1.currentLocation;
            if (player == null || location == null)
                return;

            bool locationChanged = !string.Equals(
                _evidenceLocation,
                location.NameOrUniqueName,
                System.StringComparison.Ordinal);
            bool actuallyMoved = Vector2.Distance(player.Tile, _evidenceTile) >= 0.5f;
            if (!locationChanged && !actuallyMoved)
                return;

            _recoveryPending = false;
            ClearStuckEvidence();
            _monitor.Log(
                "[StuckRecovery] actual movement confirmed; escalation evidence reset",
                LogLevel.Trace);
        }

        public void Reset()
        {
            _cooldownFrames = CooldownThreshold + 1;
            _recoveryPending = false;
            ClearStuckEvidence();
        }

        private void RecordStuckEvidence(string locationName, Vector2 tile)
        {
            if (string.Equals(
                    _evidenceLocation,
                    locationName,
                    System.StringComparison.Ordinal)
                && Vector2.Distance(_evidenceTile, tile) < 0.5f)
            {
                _sameTileEvidenceWindows++;
                return;
            }

            _evidenceLocation = locationName;
            _evidenceTile = tile;
            _sameTileEvidenceWindows = 1;
        }

        private void ClearStuckEvidence()
        {
            _evidenceLocation = null;
            _evidenceTile = Vector2.Zero;
            _sameTileEvidenceWindows = 0;
        }

        private bool DoSoftRecovery(Farmer player)
        {
            _monitor.Log(
                $"[StuckRecovery] soft recovery: UsingTool={player.UsingTool} " +
                $"canMove={player.canMove} evidence={_sameTileEvidenceWindows}/" +
                $"{StuckRecoveryPolicy.EmergencyWarpEvidenceWindows}",
                LogLevel.Warn);

            player.UsingTool = false;
            player.canMove = true;
            player.forceCanMove();
            player.Halt();
            player.FarmerSprite.StopAnimation();
            player.completelyStopAnimatingOrDoingAction();
            _cooldownFrames = 0;

            _logger?.LogExecution(
                $"Stuck recovery soft reset; sameTileEvidence={_sameTileEvidenceWindows}",
                LogLevel.Warn);
            return true;
        }

        private bool DoSafeWarp(Farmer player)
        {
            var location = Game1.currentLocation;
            var safeTarget = MapData.GetSafeWarpTarget(location.Name);
            string targetMap = safeTarget != null ? location.Name : "FarmHouse";
            int targetX = safeTarget?.x ?? 9;
            int targetY = safeTarget?.y ?? 9;

            player.UsingTool = false;
            player.canMove = true;
            player.forceCanMove();
            player.Halt();
            player.FarmerSprite.StopAnimation();
            player.completelyStopAnimatingOrDoingAction();
            player.controller = null;

            Game1.warpFarmer(targetMap, targetX, targetY, 2);
            _cooldownFrames = 0;

            _monitor.Log(
                $"[StuckRecovery] emergency safe warp completed: " +
                $"{targetMap} ({targetX},{targetY}) evidence={_sameTileEvidenceWindows}",
                LogLevel.Warn);
            _logger?.LogExecution(
                $"Emergency stuck recovery warp to {targetMap}({targetX},{targetY}); " +
                $"sameTileEvidence={_sameTileEvidenceWindows}",
                LogLevel.Warn);
            return true;
        }
    }
}
