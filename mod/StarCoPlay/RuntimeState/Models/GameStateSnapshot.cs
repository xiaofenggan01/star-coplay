using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace StarCoPlay.Models
{
    /// <summary>瓦片坐标。</summary>
    public class TilePosition
    {
        [JsonPropertyName("x")]
        public int X { get; set; }

        [JsonPropertyName("y")]
        public int Y { get; set; }
    }

    /// <summary>数值条（体力/生命）。</summary>
    public class StatBar
    {
        [JsonPropertyName("current")]
        public int Current { get; set; }

        [JsonPropertyName("max")]
        public int Max { get; set; }
    }

    /// <summary>天气信息。</summary>
    public class WeatherInfo
    {
        [JsonPropertyName("current")]
        public string Current { get; set; } = "";

        [JsonPropertyName("is_raining")]
        public bool IsRaining { get; set; }

        [JsonPropertyName("is_snowing")]
        public bool IsSnowing { get; set; }

        [JsonPropertyName("is_lightning")]
        public bool IsLightning { get; set; }

        [JsonPropertyName("tomorrow_forecast")]
        public string? TomorrowForecast { get; set; }
    }

    /// <summary>已装备物品。</summary>
    public class EquippedItemInfo
    {
        [JsonPropertyName("name")]
        public string? Name { get; set; }

        [JsonPropertyName("type")]
        public string? Type { get; set; }

        [JsonPropertyName("item_id")]
        public string? ItemId { get; set; }

        [JsonPropertyName("water_level")]
        public int? WaterLevel { get; set; }

        [JsonPropertyName("water_capacity")]
        public int? WaterCapacity { get; set; }
    }

    /// <summary>技能等级。</summary>
    public class SkillLevels
    {
        [JsonPropertyName("farming")]
        public int Farming { get; set; }

        [JsonPropertyName("mining")]
        public int Mining { get; set; }

        [JsonPropertyName("foraging")]
        public int Foraging { get; set; }

        [JsonPropertyName("fishing")]
        public int Fishing { get; set; }

        [JsonPropertyName("combat")]
        public int Combat { get; set; }
    }

    /// <summary>状态效果。</summary>
    public class StatusEffects
    {
        [JsonPropertyName("is_exhausted")]
        public bool IsExhausted { get; set; }

        [JsonPropertyName("is_swimming")]
        public bool IsSwimming { get; set; }

        [JsonPropertyName("is_riding_horse")]
        public bool IsRidingHorse { get; set; }

        [JsonPropertyName("can_move")]
        public bool CanMove { get; set; }
    }

    /// <summary>game_state.json 的完整数据模型。</summary>
    public class GameStateSnapshot
    {
        [JsonPropertyName("status")]
        public string Status { get; set; } = "running";

        [JsonPropertyName("game_day_started")]
        public bool GameDayStarted { get; set; }

        [JsonPropertyName("world")]
        public WorldInfo? World { get; set; }

        [JsonPropertyName("player")]
        public PlayerInfo? Player { get; set; }

        [JsonPropertyName("players")]
        public List<PlayerInfo>? Players { get; set; }

        [JsonPropertyName("inventory")]
        public InventoryInfo? Inventory { get; set; }

        [JsonPropertyName("farm_summary")]
        public FarmInfo? FarmSummary { get; set; }

        [JsonPropertyName("surroundings")]
        public SurroundingsInfo? Surroundings { get; set; }
    }

    /// <summary>世界时间与天气信息。</summary>
    public class WorldInfo
    {
        [JsonPropertyName("time")]
        public int Time { get; set; }

        [JsonPropertyName("time_display")]
        public string TimeDisplay { get; set; } = "";

        [JsonPropertyName("day")]
        public int Day { get; set; }

        [JsonPropertyName("season")]
        public string Season { get; set; } = "";

        [JsonPropertyName("year")]
        public int Year { get; set; }

        [JsonPropertyName("day_of_week")]
        public string DayOfWeek { get; set; } = "";

        [JsonPropertyName("weather")]
        public WeatherInfo Weather { get; set; } = new();

        [JsonPropertyName("is_festival_day")]
        public bool IsFestivalDay { get; set; }

        [JsonPropertyName("festival_name")]
        public string? FestivalName { get; set; }
    }

    /// <summary>玩家状态信息。</summary>
    public class PlayerInfo
    {
        [JsonPropertyName("player_id")]
        public ulong PlayerId { get; set; }

        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("location")]
        public string Location { get; set; } = "";

        [JsonPropertyName("tile")]
        public TilePosition Tile { get; set; } = new();

        [JsonPropertyName("facing_direction")]
        public string FacingDirection { get; set; } = "down";

        [JsonPropertyName("energy")]
        public StatBar Energy { get; set; } = new();

        [JsonPropertyName("health")]
        public StatBar Health { get; set; } = new();

        [JsonPropertyName("gold")]
        public int Gold { get; set; }

        [JsonPropertyName("equipped_item")]
        public EquippedItemInfo? EquippedItem { get; set; }

        [JsonPropertyName("current_tool_index")]
        public int CurrentToolIndex { get; set; }

        [JsonPropertyName("skills")]
        public SkillLevels? Skills { get; set; }

        [JsonPropertyName("status_effects")]
        public StatusEffects? StatusEffects { get; set; }
    }

    /// <summary>背包信息。</summary>
    public class InventoryInfo
    {
        [JsonPropertyName("slots_used")]
        public int SlotsUsed { get; set; }

        [JsonPropertyName("slots_total")]
        public int SlotsTotal { get; set; }

        [JsonPropertyName("items")]
        public List<InventoryItemInfo> Items { get; set; } = new();
    }

    /// <summary>单个背包物品。</summary>
    public class InventoryItemInfo
    {
        [JsonPropertyName("slot")]
        public int Slot { get; set; }

        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("item_id")]
        public string ItemId { get; set; } = "";

        [JsonPropertyName("stack")]
        public int Stack { get; set; }

        [JsonPropertyName("quality")]
        public int Quality { get; set; }

        [JsonPropertyName("type")]
        public string Type { get; set; } = "object";

        [JsonPropertyName("quality_name")]
        public string? QualityName { get; set; }
    }

    /// <summary>农场摘要。</summary>
    public class FarmInfo
    {
        [JsonPropertyName("crops")]
        public FarmCropSummary Crops { get; set; } = new();

        [JsonPropertyName("animals")]
        public FarmAnimalSummary Animals { get; set; } = new();

        [JsonPropertyName("machines")]
        public FarmMachineSummary Machines { get; set; } = new();
    }

    public class FarmCropSummary
    {
        [JsonPropertyName("total_planted")]
        public int TotalPlanted { get; set; }

        [JsonPropertyName("unwatered_count")]
        public int UnwateredCount { get; set; }

        [JsonPropertyName("harvestable_count")]
        public int HarvestableCount { get; set; }

        [JsonPropertyName("empty_tilled_tiles")]
        public int EmptyTilledTiles { get; set; }
    }

    public class FarmAnimalSummary
    {
        [JsonPropertyName("total")]
        public int Total { get; set; }

        [JsonPropertyName("unpetted")]
        public int Unpetted { get; set; }

        [JsonPropertyName("product_ready")]
        public int ProductReady { get; set; }
    }

    public class FarmMachineSummary
    {
        [JsonPropertyName("ready_to_collect")]
        public int ReadyToCollect { get; set; }

        [JsonPropertyName("processing")]
        public int Processing { get; set; }

        [JsonPropertyName("empty")]
        public int Empty { get; set; }
    }

    /// <summary>玩家周围环境信息。</summary>
    public class SurroundingsInfo
    {
        [JsonPropertyName("radius")]
        public int Radius { get; set; } = 8;

        [JsonPropertyName("objects")]
        public List<NearbyObjectInfo> Objects { get; set; } = new();

        [JsonPropertyName("crops")]
        public List<NearbyCropInfo> Crops { get; set; } = new();

        [JsonPropertyName("npcs")]
        public List<NpcInfo> Npcs { get; set; } = new();

        [JsonPropertyName("terrain")]
        public List<NearbyTerrainInfo> Terrain { get; set; } = new();
    }

    /// <summary>附近物品对象。</summary>
    public class NearbyObjectInfo
    {
        [JsonPropertyName("tile")]
        public TilePosition Tile { get; set; } = new();

        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("type")]
        public string Type { get; set; } = "";
    }

    /// <summary>附近作物。</summary>
    public class NearbyCropInfo
    {
        [JsonPropertyName("tile")]
        public TilePosition Tile { get; set; } = new();

        [JsonPropertyName("crop_name")]
        public string CropName { get; set; } = "";

        [JsonPropertyName("watered")]
        public bool Watered { get; set; }

        [JsonPropertyName("harvestable")]
        public bool Harvestable { get; set; }

        [JsonPropertyName("days_until_harvest")]
        public int DaysUntilHarvest { get; set; }
    }

    /// <summary>附近地形特征。</summary>
    public class NearbyTerrainInfo
    {
        [JsonPropertyName("tile")]
        public TilePosition Tile { get; set; } = new();

        [JsonPropertyName("type")]
        public string Type { get; set; } = "";

        [JsonPropertyName("tree_type")]
        public string? TreeType { get; set; }

        [JsonPropertyName("state")]
        public string? State { get; set; }

        [JsonPropertyName("has_crop")]
        public bool? HasCrop { get; set; }
    }

    /// <summary>附近 NPC 信息。</summary>
    public class NpcInfo
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("tile")]
        public TilePosition Tile { get; set; } = new();

        [JsonPropertyName("hearts")]
        public int Hearts { get; set; }

        [JsonPropertyName("distance")]
        public float Distance { get; set; }
    }
}
