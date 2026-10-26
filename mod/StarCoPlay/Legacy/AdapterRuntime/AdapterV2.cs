using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using StardewModdingAPI;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2;

/// <summary>
/// 能力参数的 OpenAI JSON Schema 定义。
/// 用于 Function Calling 模式下直接作为 tool parameters。
/// </summary>
public class ParamSchema
{
    public string Type { get; set; } = "string";
    public string? Description { get; set; }
    public string[]? Enum { get; set; }
}

/// <summary>
/// 能力元数据：描述一个原子能力的名称、描述、参数签名。
/// 用于动态生成 adapter_info.json，供 Agent 侧发现能力。
/// params 使用 OpenAI JSON Schema 格式（type/properties/required）。
/// </summary>
public class CapabilityMeta
{
    public string Name { get; set; } = "";
    public string Description { get; set; } = "";

    /// <summary>参数定义列表，每个元素描述一个参数的名称、类型、描述、枚举值、是否必填。</summary>
    public List<(string name, ParamSchema schema, bool required)> ParamDefs { get; set; } = new();

    /// <summary>可选的额外元数据（如 emote_types、directions 等枚举值列表）。</summary>
    public Dictionary<string, object>? Extra { get; set; }

    /// <summary>
    /// 生成 OpenAI JSON Schema 格式的 params 字典。
    /// </summary>
    public Dictionary<string, object> ToJsonSchemaParams()
    {
        var properties = new Dictionary<string, object>();
        var required = new List<string>();

        foreach (var (name, schema, isRequired) in ParamDefs)
        {
            var prop = new Dictionary<string, object> { ["type"] = schema.Type };
            if (!string.IsNullOrEmpty(schema.Description))
                prop["description"] = schema.Description!;
            if (schema.Enum != null && schema.Enum.Length > 0)
                prop["enum"] = schema.Enum;
            properties[name] = prop;
            if (isRequired)
                required.Add(name);
        }

        var result = new Dictionary<string, object>
        {
            ["type"] = "object",
            ["properties"] = properties
        };
        if (required.Count > 0)
            result["required"] = required;
        return result;
    }
}

/// <summary>
/// V2 Adapter — routes V2 commands to registered capability handlers.
/// Wraps observe() result into V2Result.Data.
/// </summary>
public class AdapterV2
{
    // ── Capability names ────────────────────────────────────────────────

    // Action capabilities
    public const string CapabilityObserve = "observe";
    public const string CapabilityMoveTo = "move_to";
    public const string CapabilityFace = "face";
    public const string CapabilityDo = "do";
    public const string CapabilityUseTool = "use_tool";
    public const string CapabilityEquip = "equip";
    public const string CapabilitySay = "say";
    public const string CapabilityEmote = "emote";
    public const string CapabilityTp = "tp";

    // Compound capabilities (multi-step, state-machine driven)
    public const string CapabilityGoTo = "go_to";
    public const string CapabilityDevEval = "dev_eval";
    public const string CapabilityWaterCrops = "water_crops";
    public const string CapabilityHarvestCrops = "harvest_crops";
    public const string CapabilityPlantCrops = "plant_crops";
    public const string CapabilityChopTrees = "chop_trees";
    public const string CapabilityMineRocks = "mine_rocks";
    public const string CapabilityGoBed = "go_bed";
    public const string CapabilityShipItems = "ship_items";
    public const string CapabilityBuyItems = "buy_items";
    public const string CapabilityExecuteOnTargets = "execute_on_targets";
    public const string CapabilityMenuInteract = "menu_interact";

    public const string AdapterId = "stardew-smapi-adapter";
    public const string AdapterVersion = "2.2.0";
    public const string ProtocolVersion = "2.0";

    /// <summary>Mod version from manifest.json, set by ModEntry at init.</summary>
    public string ModVersion { get; set; } = "unknown";

    private readonly Dictionary<string, Func<V2Command, V2Result?>> _handlers = new();
    private readonly Dictionary<string, CapabilityMeta> _metadata = new();
    private readonly IMonitor _monitor;

    public AdapterV2(IMonitor monitor)
    {
        _monitor = monitor;
        InitializeMetadata();
    }

    /// <summary>
    /// Initialize capability metadata (name, description, param signatures).
    /// Used to dynamically generate adapter_info.json for Agent-side discovery.
    /// </summary>
    private void InitializeMetadata()
    {
        // ── Action capabilities ──────────────────────────────────────────

        _metadata[CapabilityObserve] = new CapabilityMeta
        {
            Name = CapabilityObserve,
            Description = "Get a full perception snapshot of the current game state (self/world/nearby entities/active dialogue)",
            ParamDefs = new()
        };

        _metadata[CapabilityMoveTo] = new CapabilityMeta
        {
            Name = CapabilityMoveTo,
            Description = "Walk to a target tile, map location, or tracked entity. Supports TargetRef format (e.g. 'Farm:animal_White Cow') for real-time entity tracking — the path auto-updates as the entity moves.",
            ParamDefs = new()
            {
                ("x", new ParamSchema { Type = "number", Description = "target tile X (required for same-map)" }, false),
                ("y", new ParamSchema { Type = "number", Description = "target tile Y (required for same-map)" }, false),
                ("location", new ParamSchema { Type = "string", Description = "target map name for cross-map travel" }, false),
                ("target", new ParamSchema { Type = "string", Description = "TargetRef for entity tracking (e.g. 'Farm:animal_White Cow'). Overrides x/y/location." }, false),
                ("arrive_distance", new ParamSchema { Type = "number", Description = "arrival threshold in tiles for tracking mode (default: 2)" }, false),
            }
        };

        _metadata[CapabilityFace] = new CapabilityMeta
        {
            Name = CapabilityFace,
            Description = "Turn the player to face a specified direction",
            ParamDefs = new()
            {
                ("direction", new ParamSchema { Type = "string", Description = "one of: up, down, left, right", Enum = new[] { "up", "down", "left", "right" } }, true),
            },
            Extra = new Dictionary<string, object>
            {
                ["directions"] = new[] { "up", "down", "left", "right" }
            }
        };

        _metadata[CapabilityDo] = new CapabilityMeta
        {
            Name = CapabilityDo,
            Description = "Press the action button — the game engine decides the effect based on context (talk, harvest, open chest, etc.)",
            ParamDefs = new()
            {
                ("x", new ParamSchema { Type = "number", Description = "target tile X (optional, defaults to faced tile)" }, false),
                ("y", new ParamSchema { Type = "number", Description = "target tile Y (optional, defaults to faced tile)" }, false),
            }
        };

        _metadata[CapabilityUseTool] = new CapabilityMeta
        {
            Name = CapabilityUseTool,
            Description = "Use the currently equipped tool. Effect depends on context: water crops, chop trees, mine rocks, etc.",
            ParamDefs = new()
            {
                ("x", new ParamSchema { Type = "number", Description = "target tile X (optional, defaults to faced tile)" }, false),
                ("y", new ParamSchema { Type = "number", Description = "target tile Y (optional, defaults to faced tile)" }, false),
            }
        };

        _metadata[CapabilityEquip] = new CapabilityMeta
        {
            Name = CapabilityEquip,
            Description = "Switch the held item by name or inventory slot index",
            ParamDefs = new()
            {
                ("item", new ParamSchema { Type = "string", Description = "item name to equip (fuzzy match)" }, false),
                ("slot", new ParamSchema { Type = "number", Description = "inventory slot index (0-based)" }, false),
            }
        };

        _metadata[CapabilitySay] = new CapabilityMeta
        {
            Name = CapabilitySay,
            Description = "Send a chat message visible to all players in multiplayer",
            ParamDefs = new()
            {
                ("message", new ParamSchema { Type = "string", Description = "the message text to send" }, true),
            }
        };

        _metadata[CapabilityEmote] = new CapabilityMeta
        {
            Name = CapabilityEmote,
            Description = "Play an emote bubble animation above the player",
            ParamDefs = new()
            {
                ("type", new ParamSchema { Type = "string", Description = "emote type", Enum = new[] { "happy", "sad", "angry", "heart", "exclamation", "question", "sleep", "sick", "laugh", "note", "music", "blush" } }, true),
            },
            Extra = new Dictionary<string, object>
            {
                ["emote_types"] = new[] { "happy", "sad", "angry", "heart", "exclamation", "question", "sleep", "sick", "laugh", "note", "music", "blush" }
            }
        };

        _metadata[CapabilityTp] = new CapabilityMeta
        {
            Name = CapabilityTp,
            Description = "Teleport directly to a map location. Only use when move_to fails with NO_PATH error. Prefer move_to for normal movement.",
            ParamDefs = new()
            {
                ("location", new ParamSchema { Type = "string", Description = "target map name (e.g. \"Farm\", \"Town\", \"FarmHouse\")" }, true),
                ("x", new ParamSchema { Type = "number", Description = "target tile X (optional, defaults to map entry point)" }, false),
                ("y", new ParamSchema { Type = "number", Description = "target tile Y (optional, defaults to map entry point)" }, false),
            }
        };

        // ── Compound capabilities ───────────────────────────────────────

        _metadata[CapabilityGoTo] = new CapabilityMeta
        {
            Name = CapabilityGoTo,
            // K4 schema：单参 target（必填）+ 可选 face_direction
            // target 支持以下格式：
            //   - 语义名称：NPC 名（"Lewis"）、动物名（"甜豆"）、地图名（"Beach"）、建筑短名（"Coop"/"鸡舍"）
            //   - ObserveId 格式：[npc:Lewis] / [animal:甜豆] / [building:Coop]
            //   - 坐标格式：单地图 "25,60" 或跨地图 "Town:25,60"
            //   - 特殊值："player"（原地停留/面朝玩家）
            // face_direction（可选）：到达后朝向调整，north/south/east/west
            Description = "Navigate to a named target (K4 schema). target supports: NPC name (e.g. 'Lewis'), animal name (e.g. '甜豆'), map name (e.g. 'Beach'), building type (e.g. 'Coop'/'鸡舍'), building GUID (e.g. 'Coop82b38ef3-...'), ObserveId format ('[npc:Lewis]'/'[animal:甜豆]'/'[building:Coop]'), coordinate (e.g. '25,60' or 'Town:25,60'), or 'player'. Resolves target location, routes across maps, tracks moving entities. face_direction adjusts final facing after arrival. On success: returns final_location/final_tile/facing.",
            ParamDefs = new()
            {
                ("target", new ParamSchema { Type = "string", Description = "目标标识（必填）：NPC名/动物名/地图名/建筑短名/建筑GUID/ObserveId（[npc:Lewis]/[animal:甜豆]/[building:Coop]）/坐标（25,60 或 Town:25,60）/'player'" }, true),
                ("face_direction", new ParamSchema { Type = "string", Description = "到达后的朝向（可选）：north / south / east / west", Enum = new[] { "north", "south", "east", "west" } }, false),
            }
        };

        _metadata[CapabilityDevEval] = new CapabilityMeta
        {
            Name = CapabilityDevEval,
            Description = "[Dev only] Runtime reflection REPL. Explore SDV/SMAPI API at runtime. Only available when PlatformToken='dev'.",
            ParamDefs = new()
            {
                ("op", new ParamSchema { Type = "string", Description = "Operation: 'get' (read property chain), 'list' (enumerate members), 'call' (invoke static method)" }, true),
                ("expr", new ParamSchema { Type = "string", Description = "Property chain expression (e.g. 'Game1.currentLocation.doors')" }, false),
                ("method", new ParamSchema { Type = "string", Description = "Static method path for 'call' op (e.g. 'MapData.FindRoute')" }, false),
                ("args", new ParamSchema { Type = "array", Description = "Method arguments for 'call' op" }, false),
                ("depth", new ParamSchema { Type = "number", Description = "Expand depth for 'list' op (default: 1)" }, false),
                ("max_items", new ParamSchema { Type = "number", Description = "Max collection items to show (default: 20)" }, false),
            }
        };

        _metadata[CapabilityWaterCrops] = new CapabilityMeta
        {
            Name = CapabilityWaterCrops,
            Description = "Water all unwatered crops on the current map. Automatically equips watering can, navigates to each crop using greedy nearest path, and refills from nearest water source when empty. Returns count of watered/skipped crops and stop reason.",
            ParamDefs = new()
            {
                ("max_count", new ParamSchema { Type = "number", Description = "maximum number of crops to water (default: 50)" }, false),
            }
        };

        _metadata[CapabilityHarvestCrops] = new CapabilityMeta
        {
            Name = CapabilityHarvestCrops,
            Description = "Harvest all ready crops on the current map. Navigates to each harvestable crop using greedy nearest path. Optionally replants seeds after harvesting. Returns count of harvested/replanted crops and stop reason.",
            ParamDefs = new()
            {
                ("max_count", new ParamSchema { Type = "number", Description = "maximum number of crops to harvest (default: 50)" }, false),
                ("replant_seed", new ParamSchema { Type = "string", Description = "seed name to replant after harvesting (e.g. 'Parsnip Seeds'). If omitted, no replanting." }, false),
            }
        };

        _metadata[CapabilityPlantCrops] = new CapabilityMeta
        {
            Name = CapabilityPlantCrops,
            Description = "Plant seeds on all available tilled (hoed) soil in the current map. Automatically equips the seed, navigates to each empty HoeDirt tile using greedy nearest path, and plants. Returns count of planted/skipped tiles and stop reason.",
            ParamDefs = new()
            {
                ("seed", new ParamSchema { Type = "string", Description = "seed name to plant (e.g. 'Parsnip Seeds', 'Cauliflower Seeds')" }, true),
                ("max_count", new ParamSchema { Type = "number", Description = "maximum number of tiles to plant (default: 50)" }, false),
            }
        };

        _metadata[CapabilityChopTrees] = new CapabilityMeta
        {
            Name = CapabilityChopTrees,
            Description = "Chop down mature trees on the current map. Automatically equips axe, navigates to each tree using greedy nearest path, and strikes repeatedly until the tree falls. Validates axe is available and energy is sufficient before starting. Returns count of chopped trees, total strikes, and stop reason.",
            ParamDefs = new()
            {
                ("max_count", new ParamSchema { Type = "number", Description = "maximum number of trees to chop (default: 10)" }, false),
                ("include_stumps", new ParamSchema { Type = "boolean", Description = "also chop tree stumps (default: false)" }, false),
            }
        };

        _metadata[CapabilityMineRocks] = new CapabilityMeta
        {
            Name = CapabilityMineRocks,
            Description = "Mine/break stones on the current map. Automatically equips pickaxe, navigates to each stone using greedy nearest path, and strikes until broken. Validates pickaxe is available and energy is sufficient before starting. Returns count of mined rocks, total strikes, and stop reason.",
            ParamDefs = new()
            {
                ("max_count", new ParamSchema { Type = "number", Description = "maximum number of rocks to mine (default: 20)" }, false),
            }
        };

        _metadata[CapabilityGoBed] = new CapabilityMeta
        {
            Name = CapabilityGoBed,
            Description = "Go home and sleep. Automatically navigates to FarmHouse/Cabin (supports farmhand), finds the bed, interacts, and confirms the sleep dialog. Returns method used (walk/warp) and home location name.",
            ParamDefs = new()
            {
                ("force_tp", new ParamSchema { Type = "boolean", Description = "if true, teleport directly instead of walking (default: false)" }, false),
            }
        };

        _metadata[CapabilityShipItems] = new CapabilityMeta
        {
            Name = CapabilityShipItems,
            Description = "Batch-ship items from inventory to the shipping bin. Plan mode previews items and estimated income without side effects. Execute mode actually ships items. Supports quality filtering and item selection.",
            ParamDefs = new()
            {
                ("mode", new ParamSchema { Type = "string", Description = "\"plan\" (default) to preview, \"execute\" to actually ship", Enum = new[] { "plan", "execute" } }, false),
                ("items", new ParamSchema { Type = "string", Description = "comma-separated item names to ship (omit = all shippable products)" }, false),
                ("exclude", new ParamSchema { Type = "string", Description = "comma-separated item names to exclude" }, false),
                ("min_quality", new ParamSchema { Type = "string", Description = "keep items at or above this quality: silver/gold/iridium", Enum = new[] { "silver", "gold", "iridium" } }, false),
            }
        };

        _metadata[CapabilityBuyItems] = new CapabilityMeta
        {
            Name = CapabilityBuyItems,
            Description = "Navigate to a shop, open it, and purchase specified items. Handles walking/warping to the NPC, opening the shop menu, and buying items by name.",
            ParamDefs = new()
            {
                ("shop", new ParamSchema { Type = "string", Description = "shop ID", Enum = new[] { "SeedShop", "FishShop", "Saloon", "Blacksmith", "AnimalShop", "Carpenter", "Hospital", "JojaMart" } }, true),
                ("items", new ParamSchema { Type = "string", Description = "JSON array of items to buy, e.g. [{\"name\":\"Parsnip Seeds\",\"quantity\":5}]" }, true),
            }
        };

        _metadata[CapabilityExecuteOnTargets] = new CapabilityMeta
        {
            Name = CapabilityExecuteOnTargets,
            Description = "Execute an action (use_tool or do) on a list of TargetRef targets. Generic batch handler: navigates to each target, faces it, executes the action, waits for animation, checks stop conditions, and moves to the next. Supports repeat modes (once, until_gone, count:N) and stop conditions (stamina, time).",
            ParamDefs = new()
            {
                ("targets", new ParamSchema { Type = "array", Description = "list of TargetRef strings from query_world_region / query_inspect_refs (e.g. ['Farm:tile_10_15', 'Farm:animal_White Cow'])" }, true),
                ("action", new ParamSchema { Type = "string", Description = "action type", Enum = new[] { "use_tool", "do" } }, true),
                ("tool", new ParamSchema { Type = "string", Description = "tool name for action='use_tool' (e.g. 'Axe', 'Watering Can', 'Pickaxe')" }, false),
                ("intent", new ParamSchema { Type = "string", Description = "intent for action='do' (e.g. 'harvest', 'pet', 'pick_up', 'place')" }, false),
                ("intent_args", new ParamSchema { Type = "object", Description = "extra arguments for intent (e.g. {\"item\": \"Parsnip Seeds\"} for place)" }, false),
                ("repeat_per_target", new ParamSchema { Type = "string", Description = "repeat mode: 'once' (default), 'until_gone', or 'count:N'" }, false),
                ("max_count", new ParamSchema { Type = "number", Description = "max number of targets to process (default: all)" }, false),
                ("stop_conditions", new ParamSchema { Type = "object", Description = "stop conditions: {stamina_below?: int, time_after?: int}" }, false),
            }
        };
    }

    /// <summary>
    /// Dispatch a V2 command to the appropriate capability handler.
    /// Returns null if an error occurs (null result means no result written).
    /// </summary>
    public V2Result? Dispatch(V2Command cmd)
    {
        if (!_handlers.TryGetValue(cmd.Capability, out var handler))
        {
            _monitor.Log($"[V2] Unknown capability: {cmd.Capability}", LogLevel.Debug);
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.UnknownCapability,
                $"Unknown capability: {cmd.Capability}"
            );
        }

        try
        {
            return handler(cmd);
        }
        catch (Exception ex)
        {
            _monitor.Log($"[V2] Handler {cmd.Capability} threw: {ex.Message}", LogLevel.Error);
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed, ex.Message
            );
        }
    }

    /// <summary>Register a capability handler. Throws if already registered.</summary>
    public void Register(string capability, Func<V2Command, V2Result?> handler)
    {
        if (!_handlers.TryAdd(capability, handler))
        {
            throw new InvalidOperationException($"Capability already registered: {capability}");
        }
        _monitor.Log($"[V2] Adapter: registered {capability}", LogLevel.Debug);
    }

    public IEnumerable<string> RegisteredCapabilities =>
        _handlers.Keys;

    /// <summary>
    /// Dynamically generate adapter_info.json content.
    /// Only includes capabilities that have been Register()-ed, ensuring JSON matches runtime.
    /// </summary>
    public string GenerateAdapterInfoJson()
    {
        var capabilities = new List<object>();

        foreach (var name in _handlers.Keys)
        {
            if (_metadata.TryGetValue(name, out var meta))
            {
                var capObj = new Dictionary<string, object>
                {
                    ["name"] = meta.Name,
                    ["category"] = (name.StartsWith("query_") || name == "find_targets") ? "query"
                                 : (name == CapabilityGoTo || name == CapabilityDevEval || name == CapabilityWaterCrops || name == CapabilityHarvestCrops || name == CapabilityPlantCrops || name == CapabilityChopTrees || name == CapabilityMineRocks || name == CapabilityGoBed || name == CapabilityShipItems || name == CapabilityBuyItems || name == CapabilityExecuteOnTargets) ? "compound"
                                 : "action",
                    ["description"] = meta.Description,
                    ["params"] = meta.ToJsonSchemaParams()
                };

                // Attach extra metadata (e.g. directions, emote_types)
                if (meta.Extra != null)
                {
                    foreach (var kv in meta.Extra)
                    {
                        capObj[kv.Key] = kv.Value;
                    }
                }

                capabilities.Add(capObj);
            }
            else
            {
                // Handler registered but no metadata → skip in adapter_info.json (e.g. tp)
                _monitor.Log($"[V2] Capability {name} has no metadata, skipping adapter_info.json", LogLevel.Debug);
            }
        }

        var adapterInfo = new Dictionary<string, object>
        {
            ["adapter_id"] = AdapterId,
            ["adapter_version"] = AdapterVersion,
            ["protocol_version"] = ProtocolVersion,
            ["mod_version"] = ModVersion,
            ["game"] = "Stardew Valley",
            ["capabilities"] = capabilities,
            ["communication"] = new Dictionary<string, object>
            {
                ["type"] = "file_polling",
                ["bridge_dir"] = "bridge/",
                ["poll_interval_ms"] = 500,
                ["input"] = "commands.jsonl",
                ["output"] = "results.jsonl",
                ["state"] = "game_state.json",
                ["events"] = "events.jsonl",
                ["status"] = "agent_status.json"
            }
        };

        var opts = new JsonSerializerOptions
        {
            WriteIndented = true,
            Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping
        };
        return JsonSerializer.Serialize(adapterInfo, opts);
    }
}
