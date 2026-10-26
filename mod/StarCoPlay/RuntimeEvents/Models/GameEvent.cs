using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace StarCoPlay.Models
{
    /// <summary>events.jsonl 的单行事件模型。</summary>
    public class GameEvent
    {
        [JsonPropertyName("seq")]
        public int Seq { get; set; }

        [JsonPropertyName("ts")]
        public long Ts { get; set; }

        [JsonPropertyName("type")]
        public string Type { get; set; } = "";

        [JsonPropertyName("data")]
        public object? Data { get; set; }
    }

    /// <summary>day_started 事件数据。</summary>
    public class DayStartedData
    {
        [JsonPropertyName("day")]
        public int Day { get; set; }

        [JsonPropertyName("season")]
        public string Season { get; set; } = "";

        [JsonPropertyName("year")]
        public int Year { get; set; }

        [JsonPropertyName("weather")]
        public string Weather { get; set; } = "";

        [JsonPropertyName("unwatered")]
        public int Unwatered { get; set; }

        [JsonPropertyName("harvestable")]
        public int Harvestable { get; set; }
    }

    /// <summary>day_ending 事件数据。</summary>
    public class DayEndingData
    {
        [JsonPropertyName("day")]
        public int Day { get; set; }

        [JsonPropertyName("season")]
        public string Season { get; set; } = "";
    }

    /// <summary>time_changed 事件数据。</summary>
    public class TimeChangedData
    {
        [JsonPropertyName("time")]
        public int Time { get; set; }

        [JsonPropertyName("time_display")]
        public string TimeDisplay { get; set; } = "";
    }

    /// <summary>inventory_changed 事件数据。</summary>
    public class InventoryChangedData
    {
        [JsonPropertyName("added")]
        public List<ItemChangeInfo> Added { get; set; } = new();

        [JsonPropertyName("removed")]
        public List<ItemChangeInfo> Removed { get; set; } = new();

        [JsonPropertyName("source")]
        public string Source { get; set; } = "unknown";

        [JsonPropertyName("high_value_item")]
        public string? HighValueItem { get; set; }
    }

    /// <summary>背包变动中的单个物品信息。</summary>
    public class ItemChangeInfo
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("item_id")]
        public string ItemId { get; set; } = "";

        [JsonPropertyName("count")]
        public int Count { get; set; }

        [JsonPropertyName("quality")]
        public int Quality { get; set; }

        [JsonPropertyName("sell_price")]
        public int SellPrice { get; set; }
    }

    /// <summary>npc_nearby 事件数据。</summary>
    public class NpcNearbyData
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("location")]
        public string Location { get; set; } = "";

        [JsonPropertyName("tile")]
        public object? Tile { get; set; }

        [JsonPropertyName("distance")]
        public float Distance { get; set; }

        [JsonPropertyName("hearts")]
        public int Hearts { get; set; }

        [JsonPropertyName("talked_today")]
        public bool TalkedToday { get; set; }

        [JsonPropertyName("gifted_today")]
        public bool GiftedToday { get; set; }
    }

    /// <summary>player_warped 事件数据。</summary>
    public class PlayerWarpedData
    {
        [JsonPropertyName("from_location")]
        public string FromLocation { get; set; } = "";

        [JsonPropertyName("to_location")]
        public string ToLocation { get; set; } = "";

        [JsonPropertyName("tile")]
        public object? Tile { get; set; }
    }

    /// <summary>command_result 事件数据。</summary>
    public class CommandResultData
    {
        [JsonPropertyName("cmd_id")]
        public string CmdId { get; set; } = "";

        [JsonPropertyName("success")]
        public bool Success { get; set; }

        [JsonPropertyName("message")]
        public string Message { get; set; } = "";

        [JsonPropertyName("error_code")]
        public string? ErrorCode { get; set; }

        [JsonPropertyName("state_changes")]
        public object? StateChanges { get; set; }
    }

    /// <summary>energy_low 事件数据。</summary>
    public class EnergyLowData
    {
        [JsonPropertyName("current")]
        public float Current { get; set; }

        [JsonPropertyName("max")]
        public float Max { get; set; }

        [JsonPropertyName("percent")]
        public float Percent { get; set; }
    }

    /// <summary>season_changed 事件数据。</summary>
    public class SeasonChangedData
    {
        [JsonPropertyName("from")]
        public string From { get; set; } = "";

        [JsonPropertyName("to")]
        public string To { get; set; } = "";

        [JsonPropertyName("year")]
        public int Year { get; set; }
    }
}
