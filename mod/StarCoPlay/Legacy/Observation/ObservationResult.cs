using System.Collections.Generic;
using Newtonsoft.Json;

namespace StarCoPlay.V2.Models;

/// <summary>
/// ObservationResult — return value of the observe() capability.
/// Contains normalized self/world/entities state snapshot.
/// </summary>
public class ObservationResult
{
    [JsonProperty("self")]
    public SelfState Self { get; set; } = new();

    [JsonProperty("world")]
    public WorldState World { get; set; } = new();

    [JsonProperty("entities")]
    public List<EntityInfo> Entities { get; set; } = new();

    [JsonProperty("timestamp")]
    public long Timestamp { get; set; }

    /// <summary>
    /// Active dialogue/question/menu state. Null when no dialogue is open.
    /// </summary>
    [JsonProperty("active_dialogue", NullValueHandling = NullValueHandling.Ignore)]
    public ActiveDialogueInfo? ActiveDialogue { get; set; }
}

/// <summary>
/// Represents the currently active dialogue box, question box, or info popup.
/// </summary>
public class ActiveDialogueInfo
{
    /// <summary>"dialogue" | "question" | "info"</summary>
    [JsonProperty("type")]
    public string Type { get; set; } = "";

    /// <summary>The displayed text content of the dialogue.</summary>
    [JsonProperty("text")]
    public string Text { get; set; } = "";

    /// <summary>Available response choices. Empty list if no choices.</summary>
    [JsonProperty("choices")]
    public List<DialogueChoice> Choices { get; set; } = new();

    /// <summary>NPC name if this is an NPC dialogue, null otherwise.</summary>
    [JsonProperty("npc_name", NullValueHandling = NullValueHandling.Ignore)]
    public string? NpcName { get; set; }
}

/// <summary>
/// A single choice/response in a question dialogue.
/// </summary>
public class DialogueChoice
{
    [JsonProperty("index")]
    public int Index { get; set; }

    [JsonProperty("text")]
    public string Text { get; set; } = "";

    [JsonProperty("key")]
    public string Key { get; set; } = "";
}

public class SelfState
{
    [JsonProperty("position")]
    public Position Position { get; set; } = new();

    [JsonProperty("facing")]
    public string Facing { get; set; } = "down";

    /// <summary>Normalized 0-1 (health / maxHealth).</summary>
    [JsonProperty("health")]
    public float Health { get; set; }

    /// <summary>Normalized 0-1 (stamina / maxStamina).</summary>
    [JsonProperty("energy")]
    public float Energy { get; set; }

    [JsonProperty("inventory")]
    public List<InventoryItem> Inventory { get; set; } = new();

    [JsonProperty("equipped")]
    public Dictionary<string, string> Equipped { get; set; } = new();

    [JsonProperty("status_effects")]
    public List<string> StatusEffects { get; set; } = new();

    [JsonProperty("money")]
    public int Money { get; set; }
}

public class WorldState
{
    [JsonProperty("time")]
    public GameTimeInfo Time { get; set; } = new();

    [JsonProperty("weather")]
    public string Weather { get; set; } = "";

    [JsonProperty("location_name")]
    public string LocationName { get; set; } = "";

    [JsonProperty("season")]
    public string Season { get; set; } = "";

    [JsonProperty("year")]
    public int Year { get; set; }

    [JsonProperty("day_of_month")]
    public int DayOfMonth { get; set; }

    [JsonProperty("is_raining")]
    public bool IsRaining { get; set; }
}

public class GameTimeInfo
{
    [JsonProperty("game_time")]
    public int GameTime { get; set; }

    [JsonProperty("display")]
    public string Display { get; set; } = "";
}

public class Position
{
    [JsonProperty("tile_x")]
    public int TileX { get; set; }

    [JsonProperty("tile_y")]
    public int TileY { get; set; }

    [JsonProperty("location")]
    public string Location { get; set; } = "";
}

public class InventoryItem
{
    /// <summary>
    /// 0-based 槽位索引，与 player.Items[i] 严格对应；
    /// LLM 可直接用 (SlotIndex + 1) 作为 equip 命令的 1-based slot 参数。
    /// </summary>
    [JsonProperty("slot_index")]
    public int SlotIndex { get; set; }

    /// <summary>true 表示该槽位为空（player.Items[i] == null）。</summary>
    [JsonProperty("is_empty")]
    public bool IsEmpty { get; set; }

    [JsonProperty("name")]
    public string? Name { get; set; }

    [JsonProperty("count")]
    public int Count { get; set; }

    [JsonProperty("category")]
    public string? Category { get; set; }
}

public class EntityInfo
{
    [JsonProperty("id")]
    public string Id { get; set; } = "";

    /// <summary>npc | item | building | player | crop | object</summary>
    [JsonProperty("type")]
    public string Type { get; set; } = "";

    [JsonProperty("name")]
    public string Name { get; set; } = "";

    [JsonProperty("position")]
    public Position Position { get; set; } = new();

    [JsonProperty("properties")]
    public Dictionary<string, object> Properties { get; set; } = new();

    [JsonProperty("interactable")]
    public bool Interactable { get; set; }
}
