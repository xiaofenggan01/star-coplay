using System;
using System.Collections.Generic;
using System.Text;
using System.Text.Json;
using Microsoft.Xna.Framework;
using StarCoPlay.Bridge;
using StarCoPlay.Logging;
using StardewModdingAPI;
using StardewValley;

namespace StarCoPlay
{
    /// <summary>
    /// 从游戏 Farmer 对象采集外观数据，通过 WsBridge 同步到 Gateway。
    /// 触发时机：SaveLoaded（加载存档）和 DayStarted（每天开始）。
    /// </summary>
    public class AppearanceSync
    {
        private readonly IMonitor _monitor;
        private readonly ModLogger _logger;
        private readonly Func<IBridge?> _bridgeAccessor;
        private int _lastSyncHash;

        public AppearanceSync(IMonitor monitor, ModLogger logger, Func<IBridge?> bridgeAccessor)
        {
            _monitor = monitor;
            _logger = logger;
            _bridgeAccessor = bridgeAccessor;
        }

        /// <summary>
        /// 采集当前 Farmer 外观并通过 Bridge 发送。
        /// 使用 hash 去重，相同外观不会重复发送。
        /// </summary>
        public void Sync(bool force = false)
        {
            try
            {
                var farmer = Game1.player;
                if (farmer == null) return;

                var bridge = _bridgeAccessor();
                if (bridge is not WsBridge wsBridge) return;

                var appearance = BuildAppearanceData(farmer);

                // 去重：相同数据不重复发送
                int hash = ComputeHash(appearance);
                if (!force && hash == _lastSyncHash) return;
                _lastSyncHash = hash;

                var msg = new Dictionary<string, object?>
                {
                    ["type"] = WsProtocol.MsgAppearanceSync,
                    ["appearance"] = appearance
                };

                string json = JsonSerializer.Serialize(msg, new JsonSerializerOptions
                {
                    PropertyNamingPolicy = new SnakeCaseNamingPolicy()
                });

                // WsBridge 没有暴露 EnqueueRaw，通过 WriteStateAtomic 类似方式发送
                // 但 appearance_sync 不需要覆盖语义，用 AppendEvent 包装为 game_event
                // 不——直接作为顶层消息发送更好。用 reflection 或添加公开方法。
                // 最简方案：让 WsBridge 暴露一个 SendRaw 方法
                wsBridge.SendRaw(json);

                _monitor.Log($"AppearanceSync: synced gender={appearance["gender"]} skin={appearance["skin"]} hairstyle={appearance["hairstyle"]}", LogLevel.Debug);
            }
            catch (Exception ex)
            {
                _logger.LogError($"AppearanceSync.Sync error: {ex.Message}", ex);
            }
        }

        private static Dictionary<string, object?> BuildAppearanceData(Farmer farmer)
        {
            // Gender
            string gender = farmer.Gender == Gender.Male ? "Male" : "Female";

            // Color → SDV HSB (H:0-100, S:0-100, B:0-100)
            var hairColor = RgbToSdvHsb(farmer.hairstyleColor.Value);
            var eyeColor = RgbToSdvHsb(farmer.newEyeColor.Value);
            var pantsColor = RgbToSdvHsb(farmer.pantsColor.Value);

            // Hat index (null if no hat)
            int? hatIndex = TryGetItemIndex(farmer.hat.Value);

            // Boots index (null if no boots)
            int? bootsIndex = TryGetItemIndex(farmer.boots.Value);
            int? shirtIndex = TryGetItemIndex(farmer.shirtItem.Value);
            int? pantsIndex = TryGetItemIndex(farmer.pantsItem.Value);

            return new Dictionary<string, object?>
            {
                ["gender"] = gender,
                ["skin"] = farmer.skin.Value,
                ["hairstyle"] = farmer.hair.Value,
                ["hair_color"] = hairColor,
                ["eye_color"] = eyeColor,
                ["accessory"] = farmer.accessory.Value,
                ["shirt_index"] = shirtIndex,
                ["pants_index"] = pantsIndex,
                ["pants_color"] = pantsColor,
                ["hat_index"] = hatIndex,
                ["boots_index"] = bootsIndex,
            };
        }

        private static int? TryGetItemIndex(object? item)
        {
            if (item == null) return null;

            foreach (string name in new[] { "which", "indexInTileSheet", "ParentSheetIndex", "Index" })
            {
                object? value = TryGetMemberValue(item, name);
                int? parsed = TryParseItemIndex(value);
                if (parsed.HasValue) return parsed.Value;
            }

            foreach (string name in new[] { "ItemId", "QualifiedItemId" })
            {
                object? value = TryGetMemberValue(item, name);
                int? parsed = TryParseItemIndex(value);
                if (parsed.HasValue) return parsed.Value;
            }

            return null;
        }

        private static object? TryGetMemberValue(object target, string name)
        {
            var type = target.GetType();
            var prop = type.GetProperty(name);
            if (prop != null)
                return prop.GetValue(target);

            var field = type.GetField(name);
            return field?.GetValue(target);
        }

        private static int? TryParseItemIndex(object? value)
        {
            if (value == null) return null;
            if (value is int intValue) return intValue;
            if (value is Netcode.NetInt netInt) return netInt.Value;
            if (value is Netcode.NetString netString) return TryParseItemIndex(netString.Value);
            if (value is string text)
            {
                text = text.StartsWith("(H)", StringComparison.Ordinal) || text.StartsWith("(B)", StringComparison.Ordinal)
                    ? text.Substring(3)
                    : text;
                return int.TryParse(text, out int parsed) ? parsed : null;
            }
            return null;
        }

        /// <summary>
        /// 将 XNA Color (0-255 RGB) 转换为 SDV 的 HSB 格式 (H:0-100, S:0-100, B:0-100)。
        /// SDV 使用 H:0-100 而非标准 0-360。
        /// </summary>
        private static Dictionary<string, int> RgbToSdvHsb(Color color)
        {
            double r = color.R / 255.0;
            double g = color.G / 255.0;
            double b = color.B / 255.0;

            double max = Math.Max(r, Math.Max(g, b));
            double min = Math.Min(r, Math.Min(g, b));
            double delta = max - min;

            double brightness = max * 100;
            double saturation = max > 0 ? (delta / max) * 100 : 0;
            double hue = 0;

            if (delta > 0)
            {
                if (max == r)
                    hue = (g - b) / delta;
                else if (max == g)
                    hue = 2 + (b - r) / delta;
                else
                    hue = 4 + (r - g) / delta;

                hue *= 60;
                if (hue < 0) hue += 360;
            }

            // SDV scale: H is 0-100 (not 0-360)
            return new Dictionary<string, int>
            {
                ["h"] = (int)Math.Round(hue / 360.0 * 100),
                ["s"] = (int)Math.Round(saturation),
                ["b"] = (int)Math.Round(brightness)
            };
        }

        private static int ComputeHash(Dictionary<string, object?> data)
        {
            // Simple hash for dedup
            int hash = 17;
            foreach (var kv in data)
            {
                hash = hash * 31 + (kv.Value?.GetHashCode() ?? 0);
            }
            return hash;
        }

        private sealed class SnakeCaseNamingPolicy : JsonNamingPolicy
        {
            public override string ConvertName(string name)
            {
                if (string.IsNullOrEmpty(name)) return name;

                var sb = new StringBuilder();
                for (int i = 0; i < name.Length; i++)
                {
                    char ch = name[i];
                    if (char.IsUpper(ch))
                    {
                        if (i > 0) sb.Append('_');
                        sb.Append(char.ToLowerInvariant(ch));
                    }
                    else
                    {
                        sb.Append(ch);
                    }
                }
                return sb.ToString();
            }
        }
    }
}
