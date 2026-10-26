using System;
using System.Collections.Generic;
using System.Linq;
using StardewModdingAPI;
using StardewValley;

namespace StarCoPlay.Bridge
{
    /// <summary>
    /// 事件推送层 — 将 SMAPI 事件转换为 JSON 行，
    /// 通过 BridgeManager.AppendEvent 写入 events.jsonl。
    /// </summary>
    public class EventEmitter
    {
        private readonly IMonitor _monitor;
        private readonly BridgeManager _bridge;

        /// <summary>全局单例引用（供 Harmony Patch 访问）。</summary>
        public static EventEmitter? Instance { get; private set; }

        /// <summary>当前在附近范围内的 NPC 名称（去重用）。</summary>
        private HashSet<string> _nearbyNpcCache = new();

        /// <summary>高价值物品阈值（售价 >= 此值触发标记）。</summary>
        private const int HighValueThreshold = 300;

        public EventEmitter(IMonitor monitor, BridgeManager bridge)
        {
            _monitor = monitor;
            _bridge = bridge;
            Instance = this;
        }

        /*────────────────────────────────────────────
         *  公开事件方法
         *────────────────────────────────────────────*/

        /// <summary>新一天开始。</summary>
        public void EmitDayStarted(int day, string season, int year, string weather, int unwatered, int harvestable)
        {
            try
            {
                var data = new Dictionary<string, object>
                {
                    ["day"] = day,
                    ["season"] = season,
                    ["year"] = year,
                    ["weather"] = weather,
                    ["unwatered_crops"] = unwatered,
                    ["harvestable_crops"] = harvestable
                };
                AppendEvent("day_started", data);
                _monitor.Log($"EventEmitter: day_started day={day} season={season}", LogLevel.Trace);
            }
            catch (Exception ex)
            {
                _monitor.Log($"EventEmitter.EmitDayStarted error: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>一天即将结束。</summary>
        public void EmitDayEnding(int day, string season)
        {
            try
            {
                var data = new Dictionary<string, object>
                {
                    ["day"] = day,
                    ["season"] = season
                };
                AppendEvent("day_ending", data);
                _monitor.Log($"EventEmitter: day_ending day={day}", LogLevel.Trace);
            }
            catch (Exception ex)
            {
                _monitor.Log($"EventEmitter.EmitDayEnding error: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>游戏内时间变化（每 10 分钟一跳）。</summary>
        public void EmitTimeChanged(int time, string timeDisplay)
        {
            try
            {
                var data = new Dictionary<string, object>
                {
                    ["time"] = time,
                    ["time_display"] = timeDisplay
                };
                AppendEvent("time_changed", data);
                _monitor.Log($"EventEmitter: time_changed time={time}", LogLevel.Trace);
            }
            catch (Exception ex)
            {
                _monitor.Log($"EventEmitter.EmitTimeChanged error: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>背包内容变化。</summary>
        public void EmitInventoryChanged(IEnumerable<Item> added, IEnumerable<Item> removed, string source = "unknown")
        {
            try
            {
                var addedList = added?.Select(ItemToDict).ToList() ?? new List<Dictionary<string, object>>();
                var removedList = removed?.Select(ItemToDict).ToList() ?? new List<Dictionary<string, object>>();

                if (addedList.Count == 0 && removedList.Count == 0)
                    return;

                // 检测高价值物品（added 中售价最高的）
                string? highValueItem = null;
                int maxPrice = 0;
                if (added != null)
                {
                    foreach (var item in added)
                    {
                        if (item == null) continue;
                        int price = item.sellToStorePrice();
                        if (price >= HighValueThreshold && price > maxPrice)
                        {
                            maxPrice = price;
                            highValueItem = item.DisplayName;
                        }
                    }
                }

                if (highValueItem != null)
                {
                    _monitor.Log($"EventEmitter: high value item detected — {highValueItem} ({maxPrice}g)", LogLevel.Debug);
                }

                var data = new Dictionary<string, object>
                {
                    ["added"] = addedList,
                    ["removed"] = removedList,
                    ["source"] = source
                };
                if (highValueItem != null)
                    data["high_value_item"] = highValueItem;

                AppendEvent("inventory_changed", data);
                _monitor.Log($"EventEmitter: inventory_changed added={addedList.Count} removed={removedList.Count}", LogLevel.Trace);
            }
            catch (Exception ex)
            {
                _monitor.Log($"EventEmitter.EmitInventoryChanged error: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>NPC 进入附近范围（去重：同一 NPC 只推送一次，直到离开后重新进入）。</summary>
        public void EmitNpcNearby(string name, string location, int tileX, int tileY, float distance, int hearts, bool talkedToday, bool giftedToday)
        {
            try
            {
                if (_nearbyNpcCache.Contains(name))
                    return;

                _nearbyNpcCache.Add(name);

                var data = new Dictionary<string, object>
                {
                    ["name"] = name,
                    ["location"] = location,
                    ["tile"] = new Dictionary<string, object> { ["x"] = tileX, ["y"] = tileY },
                    ["distance"] = Math.Round(distance, 1),
                    ["hearts"] = hearts,
                    ["talked_today"] = talkedToday,
                    ["gifted_today"] = giftedToday
                };
                AppendEvent("npc_nearby", data);
                _monitor.Log($"EventEmitter: npc_nearby name={name} distance={distance:F1}", LogLevel.Trace);
            }
            catch (Exception ex)
            {
                _monitor.Log($"EventEmitter.EmitNpcNearby error: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>
        /// 更新 NPC 缓存：移除不再在附近的 NPC，使其下次进入时能重新触发事件。
        /// 在每个 tick 的 NPC 扫描后调用。
        /// </summary>
        public void UpdateNpcCache(List<string> currentNpcNames)
        {
            try
            {
                var toRemove = _nearbyNpcCache.Where(n => !currentNpcNames.Contains(n)).ToList();
                foreach (var name in toRemove)
                    _nearbyNpcCache.Remove(name);
            }
            catch (Exception ex)
            {
                _monitor.Log($"EventEmitter.UpdateNpcCache error: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>玩家切换地图。</summary>
        public void EmitPlayerWarped(string from, string to, int tileX, int tileY)
        {
            try
            {
                // 换地图后清空 NPC 缓存，允许新地图 NPC 重新触发
                _nearbyNpcCache.Clear();

                var data = new Dictionary<string, object>
                {
                    ["from_location"] = from,
                    ["to_location"] = to,
                    ["tile"] = new Dictionary<string, object> { ["x"] = tileX, ["y"] = tileY }
                };
                AppendEvent("player_warped", data);
                _monitor.Log($"EventEmitter: player_warped {from} -> {to}", LogLevel.Trace);
            }
            catch (Exception ex)
            {
                _monitor.Log($"EventEmitter.EmitPlayerWarped error: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>Agent 动作被 Mod 侧中止（如寻路卡住检测）。写入 events.jsonl 供 Python Agent 感知。</summary>
        public void EmitAgentActionCancelled(string reason, int tileX, int tileY, string actionState, int stuckTicks)
        {
            try
            {
                var data = new Dictionary<string, object>
                {
                    ["reason"] = reason,
                    ["tile_x"] = tileX,
                    ["tile_y"] = tileY,
                    ["action_state"] = actionState,
                    ["stuck_ticks"] = stuckTicks
                };
                AppendEvent("agent_action_cancelled", data);
                _monitor.Log($"EventEmitter: agent_action_cancelled reason={reason} ({tileX},{tileY})", LogLevel.Info);
            }
            catch (Exception ex)
            {
                _monitor.Log($"EventEmitter.EmitAgentActionCancelled error: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>Agent 命令执行结果。</summary>
        public void EmitCommandResult(string cmdId, bool success, string message, string? errorCode = null, object? result = null)
        {
            try
            {
                var data = new Dictionary<string, object>
                {
                    ["cmd_id"] = cmdId,
                    ["success"] = success,
                    ["message"] = message
                };
                if (errorCode != null)
                    data["error_code"] = errorCode;
                if (result != null)
                {
                    // 先将 result 独立序列化为 JSON，再解析为 JsonElement 嵌入 dict。
                    // 直接放 object 会导致 System.Text.Json 序列化嵌套自定义类型时
                    // 丢失属性或抛异常（Dictionary<string, object> 的 value 按 object 序列化）。
                    var resultJson = BridgeManager.Serialize(result);
                    var resultElement = System.Text.Json.JsonDocument.Parse(resultJson).RootElement.Clone();
                    data["result"] = resultElement;
                }

                AppendEvent("cmd_result", data);
                _monitor.Log($"EventEmitter: cmd_result cmd={cmdId} success={success}", LogLevel.Trace);
            }
            catch (Exception ex)
            {
                _monitor.Log($"EventEmitter.EmitCommandResult error: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>玩家体力低警告。</summary>
        public void EmitEnergyLow(float current, float max)
        {
            try
            {
                var data = new Dictionary<string, object>
                {
                    ["current"] = current,
                    ["max"] = max,
                    ["percent"] = max > 0 ? Math.Round(current / max * 100, 1) : 0
                };
                AppendEvent("energy_low", data);
                _monitor.Log($"EventEmitter: energy_low {current}/{max}", LogLevel.Trace);
            }
            catch (Exception ex)
            {
                _monitor.Log($"EventEmitter.EmitEnergyLow error: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>收到多人聊天消息（其他玩家发送的）。</summary>
        public void EmitChatReceived(long sourceFarmerId, string playerName, string message)
        {
            try
            {
                // 过滤自己发送的消息
                if (Game1.player != null && sourceFarmerId == Game1.player.UniqueMultiplayerID)
                    return;

                var data = new Dictionary<string, object>
                {
                    ["player_name"] = playerName,
                    ["player_id"] = sourceFarmerId,
                    ["message"] = message
                };
                AppendEvent("chat_received", data);
                _monitor.Log($"EventEmitter: chat_received from={playerName} msg={message}", LogLevel.Debug);
            }
            catch (Exception ex)
            {
                _monitor.Log($"EventEmitter.EmitChatReceived error: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>用户通过 SMAPI 控制台或游戏内聊天下达的自然语言指令。</summary>
        public void EmitUserDirective(string directive, string source = "console")
        {
            try
            {
                var data = new Dictionary<string, object>
                {
                    ["directive"] = directive,
                    ["source"] = source
                };
                AppendEvent("user_directive", data);
                _monitor.Log($"EventEmitter: user_directive source={source} directive={directive}", LogLevel.Info);
            }
            catch (Exception ex)
            {
                _monitor.Log($"EventEmitter.EmitUserDirective error: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>季节变更。</summary>
        public void EmitSeasonChanged(string from, string to, int year)
        {
            try
            {
                var data = new Dictionary<string, object>
                {
                    ["from"] = from,
                    ["to"] = to,
                    ["year"] = year
                };
                AppendEvent("season_changed", data);
                _monitor.Log($"EventEmitter: season_changed {from} -> {to} (year {year})", LogLevel.Trace);
            }
            catch (Exception ex)
            {
                _monitor.Log($"EventEmitter.EmitSeasonChanged error: {ex.Message}", LogLevel.Error);
            }
        }

        /*────────────────────────────────────────────
         *  私有方法
         *────────────────────────────────────────────*/

        /// <summary>构建完整事件 JSON 并追加到事件文件。</summary>
        private void AppendEvent(string type, object data)
        {
            var evt = new Dictionary<string, object>
            {
                ["seq"] = _bridge.NextSeq(),
                ["ts"] = GetUnixTimestamp(),
                ["type"] = type,
                ["data"] = data
            };

            string jsonLine = BridgeManager.Serialize(evt);
            _bridge.AppendEvent(jsonLine);
        }

        /// <summary>当前 UTC 时间的 Unix 时间戳（秒）。</summary>
        private static long GetUnixTimestamp()
        {
            return DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        }

        /// <summary>将 StardewValley.Item 转换为可序列化的字典。</summary>
        private static Dictionary<string, object> ItemToDict(Item item)
        {
            var dict = new Dictionary<string, object>
            {
                ["name"] = item?.DisplayName ?? "unknown",
                ["id"] = item?.QualifiedItemId ?? "",
                ["item_id"] = item?.QualifiedItemId ?? "",
                ["stack"] = item?.Stack ?? 0,
                ["sell_price"] = item?.sellToStorePrice() ?? 0
            };

            if (item is StardewValley.Object obj)
                dict["quality"] = obj.Quality;

            return dict;
        }
    }
}
