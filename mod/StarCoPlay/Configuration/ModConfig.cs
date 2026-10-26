using System.Collections.Generic;

namespace StarCoPlay
{
    public class ModConfig
    {
        public const string DefaultWsUrl = "ws://101.133.239.80/ws";

        public bool EnableAIControl { get; set; } = false;

        public bool EnableDailyDemo { get; set; } = true;

        public int StuckThresholdTicks { get; set; } = 300;

        public bool EnableFileLogging { get; set; } = true;

        public int MaxLogFileSizeMB { get; set; } = 10;

        public string? TargetPlayerId { get; set; } = null;

        /// <summary>共享 AI 控制快照的单调版本；每次 select/close 递增。</summary>
        public long AgentControlRevision { get; set; } = 0;

        public string BridgeMode { get; set; } = "ws";

        public string WsUrl { get; set; } = "";

        public string PlatformToken { get; set; } = "";

        /// <summary>
        /// 启用 L1 V2 Handler（基于 InputBridge 按键模拟）。
        /// true（默认）= 当前模拟输入执行原子：interact / 菜单 / move_to 三模式等，Protocol 2.4.5 与云端 Agent 必需。
        /// false = 旧 Handler（直接 SDV API，仅作回滚；无 interact 等协议能力）。
        /// </summary>
        public bool UseInputBridgeHandlers { get; set; } = true;

        /// <summary>
        /// WalkThrough 出口的按键方向覆盖（最高优先级，覆盖内置白名单和启发式推断）。
        /// 键格式："FromMap→ToMap" 或 "FromMap->ToMap"；值："north"/"south"/"east"/"west"（或 up/down/left/right）。
        /// 用例：MapData 内置白名单未覆盖、且启发式推不准的特殊地图对（玩家自定义地图、Mod 新增区域）。
        /// 例：{"FarmHouse→Farm": "south", "Custom_Map→Town": "east"}
        /// </summary>
        public Dictionary<string, string> WarpDirectionOverrides { get; set; } = new();

        /// <summary>游戏内常驻任务条（由 DSH 插件推送），默认开启。</summary>
        public bool TaskHudEnabled { get; set; } = true;

        public int TaskHudX { get; set; } = 16;

        public int TaskHudY { get; set; } = 16;

        /// <summary>最多显示几条任务（1-3）。</summary>
        public int TaskHudMaxLines { get; set; } = 2;

        /// <summary>任务条背景不透明度（0.3-1.0）。</summary>
        public float TaskHudOpacity { get; set; } = 0.78f;

        public string EffectiveWsUrl()
        {
            var u = WsUrl?.Trim();
            return string.IsNullOrEmpty(u) ? DefaultWsUrl : u!;
        }
    }
}
