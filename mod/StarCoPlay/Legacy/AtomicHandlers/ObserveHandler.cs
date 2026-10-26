// ============================================================
// [LEGACY] Mod 侧 observe 实现 — 与 Agent L3 observe 冗余
// 保留原因：历史兼容
// 计划：protocol-capability-completion 迭代中评估清理
// ============================================================
using System;
using System.Collections.Generic;
using System.Linq;
using StarCoPlay;
using StarCoPlay.V2.Models;
using StardewValley;
using StardewValley.Menus;
using StardewValley.Objects;

namespace StarCoPlay.V2;

/// <summary>
/// Builds the V2 observe() result from the current game state.
/// </summary>
public static class ObserveHandler
{
    public static V2Result Handle(V2Command cmd)
    {
        if (cmd == null)
        {
            return ResultBuilder.Failure(
                0,
                "",
                ResultBuilder.Errors.InvalidParams,
                "observe 命令为空");
        }

        var p = Game1.player;
        var loc = Game1.currentLocation;

        if (p == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq,
                cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                "游戏未就绪，玩家对象为空");
        }

        // 保留 null 占位：inventory[i] 必须与 player.Items[i] 严格对应，
        // 这样 LLM 才能根据 inventory 看到的槽位索引正确调用 equip slot=（i+1）。
        // 注意：旧实现用 `continue` 跳过 null，导致 hotbar 中间有空槽时索引错位
        // （例：[镰刀, 镐, null, 锄头] 被序列化为 [镰刀, 镐, 锄头]，
        // LLM 以为 slot 3 是锄头，但实际 slot 3 是空，slot 4 才是锄头）。
        var inventory = new List<InventoryItem>();
        for (int i = 0; i < p.Items.Count; i++)
        {
            var item = p.Items[i];
            if (item == null)
            {
                inventory.Add(new InventoryItem
                {
                    SlotIndex = i,
                    IsEmpty = true,
                    Name = null,
                    Count = 0,
                    Category = null,
                });
                continue;
            }

            inventory.Add(new InventoryItem
            {
                SlotIndex = i,
                IsEmpty = false,
                Name = item.DisplayName,
                Count = item.Stack,
                Category = item.getCategoryName(),
            });
        }

        var equipped = new Dictionary<string, string>();
        if (p.CurrentTool != null)
        {
            equipped["hand"] = p.CurrentTool.DisplayName;
        }

        // 解析 detail 参数：默认只返回基础状态+NPC+物体
        // detail=full/furniture/all 时额外包含家具信息
        bool includeFurniture = false;
        if (cmd.Params.TryGetValue("detail", out var detailObj))
        {
            var detailStr = detailObj?.ToString()?.ToLowerInvariant() ?? "";
            includeFurniture = detailStr == "full" || detailStr == "furniture" || detailStr == "true" || detailStr == "all";
        }

        var entities = new List<EntityInfo>();
        if (loc != null)
        {
            foreach (var npc in loc.characters)
            {
                if (npc == null || npc.IsInvisible)
                {
                    continue;
                }

                var friendship = p.friendshipData.ContainsKey(npc.Name)
                    ? p.friendshipData[npc.Name]
                    : null;

                entities.Add(new EntityInfo
                {
                    Id = $"{loc.NameOrUniqueName}:npc_{npc.Name}",
                    Type = "npc",
                    Name = npc.displayName ?? npc.Name,
                    Position = new Position
                    {
                        TileX = (int)npc.Tile.X,
                        TileY = (int)npc.Tile.Y,
                        Location = loc.NameOrUniqueName,
                    },
                    Interactable = true,
                    Properties = new Dictionary<string, object>
                    {
                        ["hearts"] = friendship != null ? friendship.Points / 250 : 0,
                        ["talked_today"] = friendship?.TalkedToToday ?? false,
                    },
                });
            }

            foreach (var pair in loc.objects.Pairs)
            {
                var obj = pair.Value;
                if (obj == null) continue;

                // Detect container objects (Chest, Mini-Fridge, etc.)
                if (obj is Chest chest)
                {
                    string containerType = chest.SpecialChestType switch
                    {
                        Chest.SpecialChestTypes.MiniShippingBin => "mini_shipping_bin",
                        _ when chest.fridge.Value => "fridge",
                        _ => "chest"
                    };
                    string containerId = $"{loc.NameOrUniqueName}:{containerType}_{(int)pair.Key.X}_{(int)pair.Key.Y}";

                    entities.Add(new EntityInfo
                    {
                        Id = $"{loc.NameOrUniqueName}:obj_{(int)pair.Key.X}_{(int)pair.Key.Y}",
                        Type = "container",
                        Name = string.IsNullOrEmpty(chest.DisplayName) ? "Chest" : chest.DisplayName,
                        Position = new Position
                        {
                            TileX = (int)pair.Key.X,
                            TileY = (int)pair.Key.Y,
                            Location = loc.NameOrUniqueName,
                        },
                        Interactable = true,
                        Properties = new Dictionary<string, object>
                        {
                            ["container_id"] = containerId,
                            ["container_type"] = containerType,
                            ["item_count"] = chest.Items.Count(i => i != null),
                            ["capacity"] = chest.GetActualCapacity(),
                            ["display_name"] = string.IsNullOrEmpty(chest.DisplayName) ? "Chest" : chest.DisplayName,
                        },
                    });
                    continue;
                }

                entities.Add(new EntityInfo
                {
                    Id = $"{loc.NameOrUniqueName}:obj_{(int)pair.Key.X}_{(int)pair.Key.Y}",
                    Type = "object",
                    Name = obj.DisplayName,
                    Position = new Position
                    {
                        TileX = (int)pair.Key.X,
                        TileY = (int)pair.Key.Y,
                        Location = loc.NameOrUniqueName,
                    },
                    Interactable = true,
                    Properties = new Dictionary<string, object>
                    {
                        ["item_id"] = obj.QualifiedItemId,
                        ["category"] = obj.Category,
                    },
                });
            }

            // 家具（含床）— 仅在 detail=full 时包含
            if (includeFurniture)
            foreach (var furniture in loc.furniture)
            {
                if (furniture == null)
                {
                    continue;
                }

                bool isBed = furniture is BedFurniture;
                var furnitureType = isBed ? "bed" : "furniture";

                var properties = new Dictionary<string, object>
                {
                    ["furniture_type"] = furniture.furniture_type.Value,
                };

                if (isBed)
                {
                    var bed = (BedFurniture)furniture;
                    properties["bed_type"] = bed.bedType.ToString();
                    // 床的交互锚点：床脚下方一格（玩家需要站在这里才能触发睡觉）
                    var bedBox = bed.GetBoundingBox();
                    int interactX = (int)furniture.TileLocation.X + 1; // 床中间
                    int interactY = (int)furniture.TileLocation.Y + bedBox.Height / 64; // 床脚下方
                    properties["interact_tile_x"] = interactX;
                    properties["interact_tile_y"] = interactY;
                }

                entities.Add(new EntityInfo
                {
                    Id = $"{loc.NameOrUniqueName}:furniture_{(int)furniture.TileLocation.X}_{(int)furniture.TileLocation.Y}",
                    Type = furnitureType,
                    Name = furniture.DisplayName ?? furniture.Name,
                    Position = new Position
                    {
                        TileX = (int)furniture.TileLocation.X,
                        TileY = (int)furniture.TileLocation.Y,
                        Location = loc.NameOrUniqueName,
                    },
                    Interactable = true,
                    Properties = properties,
                });
            }
        }

        var observation = new ObservationResult
        {
            Self = new SelfState
            {
                Position = new Position
                {
                    TileX = (int)p.Tile.X,
                    TileY = (int)p.Tile.Y,
                    Location = loc?.NameOrUniqueName ?? "Unknown",
                },
                Facing = p.FacingDirection switch
                {
                    0 => "up",
                    1 => "right",
                    2 => "down",
                    3 => "left",
                    _ => "unknown",
                },
                Health = p.maxHealth > 0 ? (float)p.health / p.maxHealth : 1f,
                Energy = p.MaxStamina > 0 ? (float)p.Stamina / p.MaxStamina : 1f,
                Money = p.Money,
                Inventory = inventory,
                Equipped = equipped,
                StatusEffects = new List<string>(),
            },
            World = new Models.WorldState
            {
                Time = new GameTimeInfo
                {
                    GameTime = Game1.timeOfDay,
                    Display = TimeHelper.FormatTime(Game1.timeOfDay),
                },
                Weather = GetWeatherSemantic(),
                LocationName = loc?.NameOrUniqueName ?? "Unknown",
                Season = string.IsNullOrEmpty(Game1.currentSeason)
                    ? ""
                    : char.ToUpperInvariant(Game1.currentSeason[0]) + Game1.currentSeason[1..],
                Year = Game1.year,
                DayOfMonth = Game1.dayOfMonth,
                IsRaining = Game1.isRaining,
            },
            Entities = entities,
            ActiveDialogue = DetectActiveDialogue(),
            Timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
        };

        return ResultBuilder.Success(cmd.Seq, cmd.ActionId, observation)
            .WithObservation($"已采集当前环境快照: {observation.World.LocationName}");
    }

    /// <summary>
    /// Detects the currently active dialogue/question/info box from the game state.
    /// Returns null if no dialogue is open.
    /// </summary>
    private static ActiveDialogueInfo? DetectActiveDialogue()
    {
        try
        {
            var menu = Game1.activeClickableMenu;

            if (menu is DialogueBox dialogueBox)
            {
                var info = new ActiveDialogueInfo();

                // Get displayed text
                try
                {
                    info.Text = dialogueBox.getCurrentString() ?? "";
                }
                catch
                {
                    info.Text = "";
                }

                // Detect NPC name from characterDialogue
                try
                {
                    if (dialogueBox.characterDialogue?.speaker != null)
                    {
                        info.NpcName = dialogueBox.characterDialogue.speaker.Name;
                    }
                }
                catch
                {
                    // ignore — speaker may not be set
                }

                // Question dialogue with response choices
                if (dialogueBox.isQuestion && dialogueBox.responses != null && dialogueBox.responses.Length > 0)
                {
                    info.Type = "question";
                    for (int i = 0; i < dialogueBox.responses.Length; i++)
                    {
                        var resp = dialogueBox.responses[i];
                        info.Choices.Add(new DialogueChoice
                        {
                            Index = i,
                            Text = resp.responseText ?? "",
                            Key = resp.responseKey ?? "",
                        });
                    }
                }
                else if (dialogueBox.characterDialogue != null)
                {
                    // NPC dialogue (non-question)
                    info.Type = "dialogue";
                }
                else
                {
                    // Object/system info dialogue (e.g. door blocked message)
                    info.Type = "info";
                }

                return info;
            }

            // No recognized dialogue box
            return null;
        }
        catch
        {
            return null;
        }
    }

    private static string GetWeatherSemantic()
    {
        if (Game1.isLightning) return "stormy";
        if (Game1.isRaining) return "rainy";
        if (Game1.isSnowing) return "snowy";
        if (Game1.isDebrisWeather) return "windy";
        return "sunny";
    }
}
