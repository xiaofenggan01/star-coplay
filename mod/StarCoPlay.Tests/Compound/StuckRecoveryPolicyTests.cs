using NUnit.Framework;

namespace StarCoPlay.Tests.Compound
{
    [TestFixture]
    public class StuckRecoveryPolicyTests
    {
        [TestCase(0)]
        [TestCase(1)]
        [TestCase(2)]
        [TestCase(3)]
        public void EmergencyWarpRequiresFourConfirmedSameTileWindows(int windows)
        {
            Assert.That(
                StuckRecoveryPolicy.CanUseEmergencyWarp(windows, false, false),
                Is.False);
        }

        [Test]
        public void EmergencyWarpIsAllowedAfterFourConfirmedWindows()
        {
            Assert.That(
                StuckRecoveryPolicy.CanUseEmergencyWarp(4, false, false),
                Is.True);
        }

        [TestCase(true, false)]
        [TestCase(false, true)]
        public void ToolAnimationAndGameplayUiNeverPermitEmergencyWarp(
            bool usingTool,
            bool gameplayBlocked)
        {
            Assert.That(
                StuckRecoveryPolicy.CanUseEmergencyWarp(20, usingTool, gameplayBlocked),
                Is.False);
        }
    }
}
