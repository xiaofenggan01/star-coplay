using System;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Locations;
using StardewValley.Menus;
using StardewValley.Objects;

namespace StarCoPlay.Actions
{
    /// <summary>
    /// 回家睡觉 — 传送到 FarmHouse 床边 → 定位到床上 → 触发睡觉。
    /// 非阻塞状态机，每帧 Tick() 推进一步。
    /// </summary>
    public class GoToBedAction : IBatchAction
    {
        private readonly IMonitor _monitor;
        private readonly ActionExecutor _executor;

        private enum Phase { WarpingHome, WaitingForWarp, Sleeping, WaitingForDialog, Complete }

        private Phase _currentPhase = Phase.WarpingHome;
        private Point _bedStandSpot;
        private int _waitTicks;
        private const int MaxWarpWaitTicks = 300; // 5 秒等待传送完成
        private const int MaxDialogWaitTicks = 60; // 1 秒等待对话框出现
        private bool _isSuccess;
        private string _resultMessage = "";
        private string? _errorCode;

        public bool IsComplete => _currentPhase == Phase.Complete;
        public bool IsSuccess => _isSuccess;
        public string ResultMessage => _resultMessage;
        public string? ErrorCode => _errorCode;

        public GoToBedAction(IMonitor monitor, ActionExecutor executor)
        {
            _monitor = monitor;
            _executor = executor;
        }

        public void Start()
        {
            try
            {
                _waitTicks = 0;

                // 如果已在 FarmHouse，直接跳到睡觉
                if (Game1.currentLocation is FarmHouse)
                {
                    _monitor.Log("GoToBed: 已在 FarmHouse，直接触发睡觉", LogLevel.Debug);
                    _currentPhase = Phase.Sleeping;
                    return;
                }

                // 获取床位站立坐标（需要进入 FarmHouse 后才能精确获取，先用默认值）
                _bedStandSpot = new Point(22, 5); // FarmHouse 床附近的默认坐标

                // 直接传送到床边（而非门口），跳过室内导航
                _monitor.Log($"GoToBed: 传送到 FarmHouse 床边 ({_bedStandSpot.X},{_bedStandSpot.Y})", LogLevel.Info);
                bool warpOk = _executor.WarpTo("FarmHouse", _bedStandSpot.X, _bedStandSpot.Y);
                if (!warpOk)
                {
                    Complete(false, "无法传送到 FarmHouse", "WARP_FAILED");
                    return;
                }
                _currentPhase = Phase.WaitingForWarp;
            }
            catch (Exception ex)
            {
                _monitor.Log($"GoToBed.Start 异常: {ex.Message}\n{ex.StackTrace}", LogLevel.Error);
                Complete(false, $"启动失败: {ex.Message}", "START_ERROR");
            }
        }

        public void Tick()
        {
            try
            {
                switch (_currentPhase)
                {
                    case Phase.WaitingForWarp:
                        TickWaitingForWarp();
                        break;
                    case Phase.Sleeping:
                        TickSleeping();
                        break;
                    case Phase.WaitingForDialog:
                        TickWaitingForDialog();
                        break;
                }
            }
            catch (Exception ex)
            {
                _monitor.Log($"GoToBed.Tick 异常 (phase={_currentPhase}): {ex.Message}\n{ex.StackTrace}", LogLevel.Error);
                Complete(false, $"执行异常: {ex.Message}", "TICK_ERROR");
            }
        }

        public void Cancel()
        {
            _executor.CancelCurrent();
            Complete(false, "回家睡觉已取消", "CANCELLED");
        }

        private void TickWaitingForWarp()
        {
            _waitTicks++;

            if (Game1.currentLocation is FarmHouse)
            {
                _monitor.Log($"GoToBed: 已到达 FarmHouse（等待了 {_waitTicks} ticks）", LogLevel.Debug);
                _waitTicks = 0;
                _currentPhase = Phase.Sleeping;
                return;
            }

            if (_waitTicks >= MaxWarpWaitTicks)
            {
                _monitor.Log($"GoToBed: 等待传送超时 ({MaxWarpWaitTicks} ticks)，当前={Game1.currentLocation?.Name}", LogLevel.Warn);
                Complete(false, "等待传送到 FarmHouse 超时", "WARP_TIMEOUT");
            }
        }

        private void TickSleeping()
        {
            if (!(Game1.currentLocation is FarmHouse farmHouse))
            {
                Complete(false, "不在 FarmHouse 中", "NOT_IN_FARMHOUSE");
                return;
            }

            // 查找玩家的床（优先双人床）
            var bed = farmHouse.furniture
                .OfType<BedFurniture>()
                .FirstOrDefault(b => b.bedType == BedFurniture.BedType.Double)
                ?? farmHouse.furniture
                    .OfType<BedFurniture>()
                    .FirstOrDefault();

            if (bed != null)
            {
                // 将玩家直接定位到床上（确保 checkForAction 能触发）
                float bedCenterX = bed.TileLocation.X * 64f + 64f;
                float bedCenterY = bed.TileLocation.Y * 64f + 64f;
                Game1.player.Position = new Vector2(bedCenterX, bedCenterY);
                Game1.player.faceDirection(0); // 面朝上

                _monitor.Log($"GoToBed: 找到床 {bed.Name} type={bed.bedType} @ ({bed.TileLocation.X},{bed.TileLocation.Y})，" +
                    $"玩家已定位到 ({bedCenterX / 64f:F1},{bedCenterY / 64f:F1})，触发睡觉", LogLevel.Info);

                // 触发床交互 → 弹出"要睡觉吗？"对话框
                bed.checkForAction(Game1.player);
                _monitor.Log("GoToBed: 已触发 checkForAction，等待睡觉对话框...", LogLevel.Debug);
                _waitTicks = 0;
                _currentPhase = Phase.WaitingForDialog;
            }
            else
            {
                // 没找到 BedFurniture，列出家具辅助诊断
                var names = farmHouse.furniture.Select(f => $"{f.Name}@({f.TileLocation.X},{f.TileLocation.Y})").ToList();
                _monitor.Log($"GoToBed: 未找到 BedFurniture！家具: [{string.Join(", ", names)}]", LogLevel.Warn);

                // 回退：用 getBedSpot + 直接交互
                var spot = farmHouse.getBedSpot();
                _monitor.Log($"GoToBed: 回退到 getBedSpot ({spot.X},{spot.Y})", LogLevel.Warn);
                Game1.player.setTileLocation(new Vector2(spot.X, spot.Y));
                _executor.InteractWithTile(spot.X, spot.Y);
                Complete(true, "已尝试在 getBedSpot 交互（未找到 BedFurniture）", null);
            }
        }

        private void TickWaitingForDialog()
        {
            _waitTicks++;

            // 方法 1：尝试通过 answerDialogueAction 直接确认睡觉
            if (Game1.currentLocation is FarmHouse farmHouseDialog)
            {
                try
                {
                    bool answered = farmHouseDialog.answerDialogueAction("Sleep_Yes", null);
                    if (answered)
                    {
                        _monitor.Log($"GoToBed: answerDialogueAction('Sleep_Yes') 成功（tick {_waitTicks}）", LogLevel.Info);
                        // 关闭残留对话框
                        if (Game1.activeClickableMenu is DialogueBox)
                        {
                            Game1.activeClickableMenu = null;
                        }
                        Complete(true, "已确认睡觉对话框", null);
                        return;
                    }
                }
                catch (Exception ex)
                {
                    _monitor.Log($"GoToBed: answerDialogueAction 异常: {ex.Message}", LogLevel.Trace);
                }
            }

            // 方法 2：检测 DialogueBox 并尝试选择 Yes
            if (Game1.activeClickableMenu is DialogueBox dialogBox)
            {
                try
                {
                    // DialogueBox 的 responses 列表中第一个通常是 Yes
                    var responses = dialogBox.responses;
                    if (responses != null && responses.Length > 0)
                    {
                        _monitor.Log($"GoToBed: 检测到 DialogueBox，responses={responses.Length}，选择第一个（Yes）", LogLevel.Info);
                        // 模拟选择第一个选项（通常是"是"）
                        Game1.activeClickableMenu = null;
                        if (Game1.currentLocation is FarmHouse fh)
                        {
                            fh.answerDialogueAction(responses[0].responseKey, null);
                        }
                        Complete(true, "已通过 DialogueBox 确认睡觉", null);
                        return;
                    }
                }
                catch (Exception ex)
                {
                    _monitor.Log($"GoToBed: DialogueBox 处理异常: {ex.Message}", LogLevel.Trace);
                }
            }

            // 方法 3：直接设置玩家在床上状态（兜底）
            if (_waitTicks >= 5)
            {
                _monitor.Log("GoToBed: 对话框未检测到，尝试直接设置 isInBed", LogLevel.Warn);
                try
                {
                    Game1.player.isInBed.Value = true;
                    if (Game1.currentLocation is FarmHouse fh2)
                    {
                        fh2.answerDialogueAction("Sleep_Yes", null);
                    }
                    Complete(true, "已通过 isInBed 兜底触发睡觉", null);
                    return;
                }
                catch (Exception ex)
                {
                    _monitor.Log($"GoToBed: isInBed 兜底异常: {ex.Message}", LogLevel.Warn);
                }
            }

            if (_waitTicks >= MaxDialogWaitTicks)
            {
                _monitor.Log("GoToBed: 等待睡觉对话框超时", LogLevel.Warn);
                Complete(false, "等待睡觉对话框超时", "DIALOG_TIMEOUT");
            }
        }

        private void Complete(bool success, string message, string? errorCode)
        {
            _isSuccess = success;
            _resultMessage = message;
            _errorCode = errorCode;
            _currentPhase = Phase.Complete;
            _monitor.Log($"GoToBed 完成: success={success} message={message}", success ? LogLevel.Info : LogLevel.Warn);
        }
    }
}
