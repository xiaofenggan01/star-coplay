using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Buildings;
using StardewValley.TerrainFeatures;
using StardewValley.Tools;
using xTile.Layers;

namespace StarCoPlay
{
    /// <summary>
    /// 状态查询层 — Agent 的"眼睛"。
    /// 所有方法为只读查询，不产生任何副作用。
    /// </summary>
    public class StateReader
    {
        private readonly IMonitor _monitor;

        public StateReader(IMonitor monitor)
        {
            _monitor = monitor;
        }

        /*────────────────────────────────────────────
         *  世界状态
         *────────────────────────────────────────────*/
        public WorldState GetWorldState()
        {
            return new WorldState
            {
                Time = Game1.timeOfDay,
                Day = Game1.dayOfMonth,
                Season = Game1.currentSeason,
                Year = ((int)Game1.stats.DaysPlayed - 1) / 112 + 1, // 28 天/季 × 4 季 = 112 天/年
                DayOfWeek = GetDayOfWeek(Game1.dayOfMonth),
                Weather = GetWeatherString(),
                IsRaining = Game1.isRaining,
                IsLightning = Game1.isLightning,
                IsSnowing = Game1.isSnowing,
                IsFestivalDay = Utility.isFestivalDay()
            };
        }

        /*────────────────────────────────────────────
         *  玩家状态
         *────────────────────────────────────────────*/
        public PlayerState GetPlayerState()
        {
            var player = Game1.player;
            var tile = player.Tile;

            return new PlayerState
            {
                Name = player.Name,
                Location = player.currentLocation?.Name ?? "Unknown",
                TileX = (int)tile.X,
                TileY = (int)tile.Y,
                FacingDirection = player.FacingDirection,
                Energy = (int)player.stamina,
                MaxEnergy = player.maxStamina.Value,
                Health = player.health,
                MaxHealth = player.maxHealth,
                Gold = player.Money,
                CurrentToolName = player.CurrentTool?.DisplayName,
                CurrentItemName = player.CurrentItem?.DisplayName,
                CanMove = player.canMove,
                IsSwimming = player.swimming.Value,
                FarmingLevel = player.FarmingLevel,
                MiningLevel = player.MiningLevel,
                ForagingLevel = player.ForagingLevel,
                FishingLevel = player.FishingLevel,
                CombatLevel = player.CombatLevel
            };
        }

        /*────────────────────────────────────────────
         *  背包
         *────────────────────────────────────────────*/
        public InventoryInfo GetInventory()
        {
            var player = Game1.player;
            var items = new List<InventorySlot>();

            for (int i = 0; i < player.Items.Count; i++)
            {
                var item = player.Items[i];
                if (item == null) continue;

                items.Add(new InventorySlot
                {
                    Slot = i,
                    Name = item.DisplayName,
                    ItemId = item.QualifiedItemId,
                    Stack = item.Stack,
                    Quality = item is StardewValley.Object obj ? obj.Quality : 0
                });
            }

            return new InventoryInfo
            {
                SlotsUsed = items.Count,
                SlotsTotal = player.Items.Count,
                Items = items
            };
        }

        /*────────────────────────────────────────────
         *  当前地图物品（简化版：仅返回玩家周围区域）
         *────────────────────────────────────────────*/
        public List<TileObject> GetNearbyObjects(int radius = 5)
        {
            var location = Game1.currentLocation;
            if (location == null) return new List<TileObject>();

            var playerTile = Game1.player.Tile;
            var results = new List<TileObject>();

            // 放置的物品
            foreach (var pair in location.objects.Pairs)
            {
                if (Vector2.Distance(pair.Key, playerTile) > radius) continue;

                results.Add(new TileObject
                {
                    TileX = (int)pair.Key.X,
                    TileY = (int)pair.Key.Y,
                    Name = pair.Value.DisplayName,
                    ItemId = pair.Value.QualifiedItemId,
                    Category = "object"
                });
            }

            // 地形特征（作物、树等）
            foreach (var pair in location.terrainFeatures.Pairs)
            {
                if (Vector2.Distance(pair.Key, playerTile) > radius) continue;

                if (pair.Value is HoeDirt dirt)
                {
                    var cropName = dirt.crop?.indexOfHarvest.Value ?? "empty";
                    results.Add(new TileObject
                    {
                        TileX = (int)pair.Key.X,
                        TileY = (int)pair.Key.Y,
                        Name = dirt.crop != null ? $"Crop ({cropName})" : "Tilled Soil",
                        Category = "terrain",
                        IsWatered = dirt.state.Value == 1,
                        IsHarvestable = dirt.crop?.currentPhase.Value >= dirt.crop?.phaseDays.Count - 1
                    });
                }
                else if (pair.Value is Tree tree)
                {
                    results.Add(new TileObject
                    {
                        TileX = (int)pair.Key.X,
                        TileY = (int)pair.Key.Y,
                        Name = $"Tree (stage {tree.growthStage.Value})",
                        Category = "tree"
                    });
                }
            }

            return results;
        }

        /*────────────────────────────────────────────
         *  当前地图 NPC
         *────────────────────────────────────────────*/
        public List<NpcInfo> GetNearbyNpcs(int radius = 20)
        {
            var location = Game1.currentLocation;
            if (location == null) return new List<NpcInfo>();

            var playerTile = Game1.player.Tile;

            return location.characters
                .Where(npc => Vector2.Distance(npc.Tile, playerTile) <= radius)
                .Select(npc =>
                {
                    var friendship = Game1.player.friendshipData.ContainsKey(npc.Name)
                        ? Game1.player.friendshipData[npc.Name]
                        : null;

                    return new NpcInfo
                    {
                        Name = npc.Name,
                        DisplayName = npc.displayName,
                        TileX = (int)npc.Tile.X,
                        TileY = (int)npc.Tile.Y,
                        Hearts = friendship != null ? friendship.Points / 250 : 0,
                        TalkedToday = friendship?.TalkedToToday ?? false
                    };
                })
                .ToList();
        }

        /*────────────────────────────────────────────
         *  农场概况
         *────────────────────────────────────────────*/
        public FarmSummary? GetFarmSummary()
        {
            var farm = Game1.getFarm();
            if (farm == null) return null;

            int cropCount = 0;
            int unwateredCount = 0;
            int harvestableCount = 0;
            int emptyTilledTiles = 0;

            foreach (var pair in farm.terrainFeatures.Pairs)
            {
                if (pair.Value is not HoeDirt dirt) continue;

                if (dirt.crop == null)
                {
                    emptyTilledTiles++;
                    continue;
                }

                cropCount++;
                if (dirt.state.Value != 1) unwateredCount++;
                if (dirt.crop.currentPhase.Value >= dirt.crop.phaseDays.Count - 1)
                    harvestableCount++;
            }

            // 动物统计
            int totalAnimals = farm.animals.Count();
            int unpettedAnimals = 0;
            int productReady = 0;
            foreach (var pair in farm.animals.Pairs)
            {
                var animal = pair.Value;
                if (!animal.wasPet.Value) unpettedAnimals++;
                if (!string.IsNullOrEmpty(animal.currentProduce.Value)) productReady++;
            }

            // 机器统计（农场上放置的加工机器）
            int machinesReady = 0;
            int machinesProcessing = 0;
            int machinesEmpty = 0;
            foreach (var pair in farm.objects.Pairs)
            {
                var obj = pair.Value;
                if (!obj.bigCraftable.Value) continue; // 大型制作品 = 机器
                if (obj.MinutesUntilReady > 0)
                    machinesProcessing++;
                else if (obj.heldObject.Value != null)
                    machinesReady++;
                else
                    machinesEmpty++;
            }

            return new FarmSummary
            {
                CropCount = cropCount,
                UnwateredCount = unwateredCount,
                HarvestableCount = harvestableCount,
                EmptyTilledTiles = emptyTilledTiles,
                TotalAnimals = totalAnimals,
                UnfedAnimals = unpettedAnimals,
                ProductReady = productReady,
                MachinesReady = machinesReady,
                MachinesProcessing = machinesProcessing,
                MachinesEmpty = machinesEmpty
            };
        }

        /*────────────────────────────────────────────
         *  可用动作检测
         *────────────────────────────────────────────*/
        public ActionAvailability GetAvailableActions()
        {
            return new ActionAvailability
            {
                CanMove = Context.CanPlayerMove,
                IsPlayerFree = Context.IsPlayerFree,
                IsMenuOpen = Game1.activeClickableMenu != null,
                IsInCutscene = Game1.eventUp,
                IsInDialogue = Game1.dialogueUp
            };
        }

        /*────────────────────────────────────────────
         *  工具状态查询
         *────────────────────────────────────────────*/

        /// <summary>查询指定工具的状态（水壶水量、升级等级等）。</summary>
        public ToolState? GetToolState(string toolName)
        {
            var tool = Game1.player.Items
                .OfType<Tool>()
                .FirstOrDefault(t =>
                    t.DisplayName.Equals(toolName, StringComparison.OrdinalIgnoreCase) ||
                    t.Name.Equals(toolName, StringComparison.OrdinalIgnoreCase));

            if (tool == null) return null;

            var state = new ToolState
            {
                ToolName = tool.DisplayName,
                InternalName = tool.Name,
                SlotIndex = Game1.player.Items.IndexOf(tool),
                UpgradeLevel = tool.UpgradeLevel
            };

            if (tool is WateringCan can)
            {
                state.WaterLevel = can.WaterLeft;
                state.WaterCapacity = can.waterCanMax;
            }

            return state;
        }

        /*────────────────────────────────────────────
         *  物品价值查询
         *────────────────────────────────────────────*/

        /// <summary>按名称查询背包中物品的售价信息。</summary>
        public ItemValueInfo? GetItemValue(string itemName)
        {
            var item = Game1.player.Items.FirstOrDefault(i =>
                i?.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) == true ||
                i?.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase) == true);

            if (item == null) return null;

            int sellPrice = item.sellToStorePrice();
            return new ItemValueInfo
            {
                Name = item.DisplayName,
                InternalName = item.Name,
                SellPrice = sellPrice,
                Stack = item.Stack,
                TotalValue = sellPrice * item.Stack,
                Quality = item is StardewValley.Object obj ? obj.Quality : 0
            };
        }

        /*────────────────────────────────────────────
         *  地图信息查询
         *────────────────────────────────────────────*/

        /// <summary>获取当前地图元信息：名称、尺寸、传送点/出口、建筑。</summary>
        public LocationInfo? GetLocationInfo()
        {
            var location = Game1.currentLocation;
            if (location == null) return null;

            var map = location.Map;
            var backLayer = map?.GetLayer("Back");
            int mapWidth = backLayer?.LayerWidth ?? 0;
            int mapHeight = backLayer?.LayerHeight ?? 0;

            // 收集传送点（warps）
            var warps = new List<WarpInfo>();
            if (location.warps != null)
            {
                foreach (var warp in location.warps)
                {
                    warps.Add(new WarpInfo
                    {
                        SourceX = warp.X,
                        SourceY = warp.Y,
                        TargetLocation = warp.TargetName,
                        TargetX = warp.TargetX,
                        TargetY = warp.TargetY
                    });
                }
            }

            // 收集门（doors → 室内入口）— SerializableDictionary 用 Keys+索引器遍历
            if (location.doors != null && location.doors.Count() > 0)
            {
                foreach (var point in location.doors.Keys)
                {
                    warps.Add(new WarpInfo
                    {
                        SourceX = point.X,
                        SourceY = point.Y,
                        TargetLocation = location.doors[point],
                        TargetX = -1,
                        TargetY = -1
                    });
                }
            }

            // 去重（同一个出口坐标可能有多个 warp 条目）
            var uniqueWarps = warps
                .GroupBy(w => new { w.SourceX, w.SourceY })
                .Select(g => g.First())
                .ToList();

            // 建筑信息
            var buildings = new List<BuildingInfo>();
            if (location is StardewValley.Farm farm)
            {
                foreach (var building in farm.buildings)
                {
                    buildings.Add(new BuildingInfo
                    {
                        Name = building.buildingType.Value ?? "unknown",
                        TileX = building.tileX.Value,
                        TileY = building.tileY.Value,
                        Width = building.tilesWide.Value,
                        Height = building.tilesHigh.Value,
                        DoorX = building.tileX.Value + building.humanDoor.X,
                        DoorY = building.tileY.Value + building.humanDoor.Y
                    });
                }
            }

            // 收集 NPC 列表
            var npcs = new List<string>();
            foreach (var npc in location.characters)
            {
                if (npc.IsVillager)
                    npcs.Add(npc.displayName ?? npc.Name);
            }

            return new LocationInfo
            {
                Name = location.Name,
                DisplayName = location.DisplayName ?? location.Name,
                MapWidth = mapWidth,
                MapHeight = mapHeight,
                IsOutdoors = location.IsOutdoors,
                IsFarm = location is StardewValley.Farm,
                Warps = uniqueWarps,
                Buildings = buildings,
                PlayerCount = location.farmers.Count(),
                NpcCount = location.characters.Count,
                NpcNames = npcs,
                Hint = "这是地图概览。使用 get_map_objects 查看所有可交互物品（默认返回统计摘要，加 detail=true 获取完整坐标列表）；使用 get_walkable_area 查看可行走范围和相邻地图。"
            };
        }

        /// <summary>获取当前地图可交互对象。detail=false 返回分类统计+可操作高亮；detail=true 返回全量坐标。</summary>
        public object? GetMapObjects(bool detail = false)
        {
            var location = Game1.currentLocation;
            if (location == null) return null;

            var objects = new List<MapObjectEntry>();
            var terrainFeatures = new List<MapTerrainEntry>();
            var resourceClumps = new List<ResourceClumpEntry>();

            // 分类计数器
            int storageCount = 0, machineCount = 0, forageCount = 0, objectCount = 0;
            int machineReady = 0, machineProcessing = 0;

            // 可操作高亮列表（summary 模式使用）
            var actionable = new List<MapObjectEntry>();

            // 所有放置物品
            foreach (var pair in location.objects.Pairs)
            {
                var obj = pair.Value;
                string category;
                string state = "";

                if (obj.Name.Contains("Chest") || obj.Name == "Mini-Fridge")
                {
                    category = "storage";
                    storageCount++;
                }
                else if (obj.bigCraftable.Value)
                {
                    category = "machine";
                    machineCount++;
                    if (obj.MinutesUntilReady > 0) { state = "processing"; machineProcessing++; }
                    else if (obj.heldObject.Value != null) { state = "ready"; machineReady++; }
                    else state = "empty";
                }
                else if (obj.IsSpawnedObject)
                {
                    category = "forage";
                    forageCount++;
                }
                else
                {
                    category = "object";
                    objectCount++;
                }

                var entry = new MapObjectEntry
                {
                    TileX = (int)pair.Key.X,
                    TileY = (int)pair.Key.Y,
                    Name = obj.DisplayName,
                    Category = category,
                    State = state
                };

                objects.Add(entry);

                // 可操作项：storage / ready 机器 / forage
                if (category == "storage" || category == "forage" || state == "ready")
                    actionable.Add(entry);
            }

            // 作物统计
            int cropCount = 0, harvestableCount = 0, unwateredCount = 0;
            int treeCount = 0, tappableCount = 0;
            var harvestableItems = new List<MapTerrainEntry>();

            // 地形特征
            foreach (var pair in location.terrainFeatures.Pairs)
            {
                if (pair.Value is HoeDirt dirt)
                {
                    if (dirt.crop != null)
                    {
                        cropCount++;
                        int daysToHarvest = 0;
                        var crop = dirt.crop;
                        if (crop.phaseDays != null && crop.phaseDays.Count > 0)
                        {
                            for (int i = crop.currentPhase.Value; i < crop.phaseDays.Count - 1; i++)
                                daysToHarvest += crop.phaseDays[i];
                            daysToHarvest -= crop.dayOfCurrentPhase.Value;
                            if (daysToHarvest < 0) daysToHarvest = 0;
                        }

                        bool isWatered = dirt.state.Value == 1;
                        bool isHarvestable = crop.phaseDays != null && crop.currentPhase.Value >= crop.phaseDays.Count - 1;
                        if (isHarvestable) harvestableCount++;
                        if (!isWatered) unwateredCount++;

                        var entry = new MapTerrainEntry
                        {
                            TileX = (int)pair.Key.X,
                            TileY = (int)pair.Key.Y,
                            Type = "crop",
                            Name = crop.indexOfHarvest.Value ?? "unknown",
                            Watered = isWatered,
                            Harvestable = isHarvestable,
                            DaysToHarvest = daysToHarvest
                        };
                        terrainFeatures.Add(entry);
                        if (isHarvestable) harvestableItems.Add(entry);
                    }
                }
                else if (pair.Value is Tree tree)
                {
                    treeCount++;
                    bool canTap = tree.growthStage.Value >= 5 && !tree.tapped.Value;
                    if (canTap) tappableCount++;

                    terrainFeatures.Add(new MapTerrainEntry
                    {
                        TileX = (int)pair.Key.X,
                        TileY = (int)pair.Key.Y,
                        Type = "tree",
                        Name = tree.treeType.Value ?? "unknown",
                        TreeStage = tree.growthStage.Value,
                        Tappable = canTap
                    });
                }
                else if (pair.Value is GiantCrop)
                {
                    terrainFeatures.Add(new MapTerrainEntry
                    {
                        TileX = (int)pair.Key.X,
                        TileY = (int)pair.Key.Y,
                        Type = "giant_crop",
                        Name = "Giant Crop"
                    });
                }
            }

            // 资源簇（大石头、大树桩等）
            foreach (var clump in location.resourceClumps)
            {
                string clumpType = clump.parentSheetIndex.Value switch
                {
                    600 => "large_stump",
                    602 => "large_log",
                    622 => "meteorite",
                    672 or 752 or 754 or 756 or 758 => "large_rock",
                    _ => $"resource_{clump.parentSheetIndex.Value}"
                };

                resourceClumps.Add(new ResourceClumpEntry
                {
                    TileX = (int)clump.Tile.X,
                    TileY = (int)clump.Tile.Y,
                    Type = clumpType,
                    Width = clump.width.Value,
                    Height = clump.height.Value
                });
            }

            // detail 模式：返回全量数据
            if (detail)
            {
                return new MapObjectsDetail
                {
                    Location = location.Name,
                    Objects = objects,
                    TerrainFeatures = terrainFeatures,
                    ResourceClumps = resourceClumps
                };
            }

            // 默认 summary 模式：返回统计+可操作高亮
            return new MapObjectsSummary
            {
                Location = location.Name,
                Counts = new MapObjectCounts
                {
                    Storage = storageCount,
                    Machine = machineCount,
                    MachineReady = machineReady,
                    MachineProcessing = machineProcessing,
                    Forage = forageCount,
                    Object = objectCount,
                    Crop = cropCount,
                    CropHarvestable = harvestableCount,
                    CropUnwatered = unwateredCount,
                    Tree = treeCount,
                    TreeTappable = tappableCount,
                    ResourceClump = resourceClumps.Count
                },
                Actionable = actionable,
                HarvestableNow = harvestableItems,
                Hint = "这是摘要视图。使用 query {\"tool\":\"get_map_objects\", \"detail\":true} 获取所有对象的完整坐标列表。"
            };
        }

        /// <summary>获取当前地图可行走区域信息（地图边界 + 不可通行瓦片摘要）。</summary>
        public WalkableAreaInfo? GetWalkableArea()
        {
            var location = Game1.currentLocation;
            if (location == null) return null;

            var map = location.Map;
            var backLayer = map?.GetLayer("Back");
            int mapWidth = backLayer?.LayerWidth ?? 0;
            int mapHeight = backLayer?.LayerHeight ?? 0;

            // 统计可行走瓦片数量和收集不可通行区域的边界
            int walkableCount = 0;
            int blockedCount = 0;

            // 收集所有被物品/建筑占据的瓦片
            var occupiedTiles = new HashSet<string>();
            foreach (var pair in location.objects.Pairs)
            {
                occupiedTiles.Add($"{(int)pair.Key.X},{(int)pair.Key.Y}");
            }

            // 扫描地图瓦片，判断可通行性
            var buildingsLayer = map?.GetLayer("Buildings");
            for (int x = 0; x < mapWidth; x++)
            {
                for (int y = 0; y < mapHeight; y++)
                {
                    var tile = backLayer?.Tiles[x, y];
                    if (tile != null)
                    {
                        var buildingTile = buildingsLayer?.Tiles[x, y];
                        if (buildingTile != null || occupiedTiles.Contains($"{x},{y}"))
                            blockedCount++;
                        else
                            walkableCount++;
                    }
                    else
                    {
                        blockedCount++;
                    }
                }
            }

            // 收集所有可达的位置列表
            var reachableLocations = new List<string>();
            if (location.warps != null)
            {
                foreach (var warp in location.warps)
                {
                    if (!reachableLocations.Contains(warp.TargetName))
                        reachableLocations.Add(warp.TargetName);
                }
            }
            if (location.doors != null && location.doors.Count() > 0)
            {
                foreach (var point in location.doors.Keys)
                {
                    string target = location.doors[point];
                    if (!reachableLocations.Contains(target))
                        reachableLocations.Add(target);
                }
            }

            return new WalkableAreaInfo
            {
                Location = location.Name,
                MapWidth = mapWidth,
                MapHeight = mapHeight,
                WalkableTiles = walkableCount,
                BlockedTiles = blockedCount,
                ReachableLocations = reachableLocations,
                Hint = $"地图有效范围 0~{mapWidth - 1} x 0~{mapHeight - 1}。move_to 坐标不要超出此范围。使用 get_location_info 查看传送点详情。"
            };
        }

        /// <summary>获取当前地图的静态连通数据（出口列表 + 可传送目标）。</summary>
        public object? GetMapExits()
        {
            var location = Game1.currentLocation;
            if (location == null) return null;

            var mapEntry = MapData.GetMap(location.Name);

            // 即使不在白名单中，也返回基本信息
            var allMaps = MapData.GetAllMapNames()
                .Select(name => MapData.GetMap(name))
                .Where(m => m != null)
                .Select(m => new { name = m!.Name, display_name = m.DisplayName })
                .ToList();

            if (mapEntry == null)
            {
                return new
                {
                    current_map = location.Name,
                    known = false,
                    hint = "当前地图不在已知地图表中，跨地图传送不可用。请走到地图边缘触发自然传送。",
                    all_known_maps = allMaps
                };
            }

            var exits = mapEntry.Exits.Select(e => new
            {
                direction = e.Direction,
                walk_to = new { x = e.SourceX, y = e.SourceY },
                target_map = e.TargetMap,
                target_display = MapData.GetMap(e.TargetMap)?.DisplayName ?? e.TargetMap,
                target_safe_point = new { x = e.TargetX, y = e.TargetY }
            }).ToList();

            return new
            {
                current_map = mapEntry.Name,
                display_name = mapEntry.DisplayName,
                map_size = new { width = mapEntry.Width, height = mapEntry.Height },
                safe_point = new { x = mapEntry.SafeX, y = mapEntry.SafeY },
                known = true,
                exits,
                hint = "可通过 navigate_to + location 参数直接传送到已知地图的安全点，或走到 walk_to 坐标自然触发地图切换。"
            };
        }

        /*────────────────────────────────────────────
         *  辅助方法
         *────────────────────────────────────────────*/
        private static string GetWeatherString()
        {
            if (Game1.isLightning) return "stormy";
            if (Game1.isRaining) return "rainy";
            if (Game1.isSnowing) return "snowy";
            return "sunny";
        }

        private static string GetDayOfWeek(int dayOfMonth)
        {
            return ((dayOfMonth - 1) % 7) switch
            {
                0 => "Monday",
                1 => "Tuesday",
                2 => "Wednesday",
                3 => "Thursday",
                4 => "Friday",
                5 => "Saturday",
                6 => "Sunday",
                _ => "Unknown"
            };
        }
    }

    /*────────────────────────────────────────────
     *  数据模型
     *────────────────────────────────────────────*/

    public class WorldState
    {
        public int Time { get; set; }
        public int Day { get; set; }
        public string Season { get; set; } = "";
        public int Year { get; set; }
        public string DayOfWeek { get; set; } = "";
        public string Weather { get; set; } = "";
        public bool IsRaining { get; set; }
        public bool IsLightning { get; set; }
        public bool IsSnowing { get; set; }
        public bool IsFestivalDay { get; set; }
    }

    public class PlayerState
    {
        public string Name { get; set; } = "";
        public string Location { get; set; } = "";
        public int TileX { get; set; }
        public int TileY { get; set; }
        public int FacingDirection { get; set; }
        public int Energy { get; set; }
        public int MaxEnergy { get; set; }
        public int Health { get; set; }
        public int MaxHealth { get; set; }
        public int Gold { get; set; }
        public string? CurrentToolName { get; set; }
        public string? CurrentItemName { get; set; }
        public bool CanMove { get; set; }
        public bool IsSwimming { get; set; }
        public int FarmingLevel { get; set; }
        public int MiningLevel { get; set; }
        public int ForagingLevel { get; set; }
        public int FishingLevel { get; set; }
        public int CombatLevel { get; set; }
    }

    public class InventoryInfo
    {
        public int SlotsUsed { get; set; }
        public int SlotsTotal { get; set; }
        public List<InventorySlot> Items { get; set; } = new();
    }

    public class InventorySlot
    {
        public int Slot { get; set; }
        public string Name { get; set; } = "";
        public string ItemId { get; set; } = "";
        public int Stack { get; set; }
        public int Quality { get; set; }
    }

    public class TileObject
    {
        public int TileX { get; set; }
        public int TileY { get; set; }
        public string Name { get; set; } = "";
        public string? ItemId { get; set; }
        public string Category { get; set; } = "";
        public bool IsWatered { get; set; }
        public bool? IsHarvestable { get; set; }
    }

    public class NpcInfo
    {
        public string Name { get; set; } = "";
        public string DisplayName { get; set; } = "";
        public int TileX { get; set; }
        public int TileY { get; set; }
        public int Hearts { get; set; }
        public bool TalkedToday { get; set; }
    }

    public class FarmSummary
    {
        public int CropCount { get; set; }
        public int UnwateredCount { get; set; }
        public int HarvestableCount { get; set; }
        public int EmptyTilledTiles { get; set; }
        public int TotalAnimals { get; set; }
        public int UnfedAnimals { get; set; }
        public int ProductReady { get; set; }
        public int MachinesReady { get; set; }
        public int MachinesProcessing { get; set; }
        public int MachinesEmpty { get; set; }
    }

    public class ActionAvailability
    {
        public bool CanMove { get; set; }
        public bool IsPlayerFree { get; set; }
        public bool IsMenuOpen { get; set; }
        public bool IsInCutscene { get; set; }
        public bool IsInDialogue { get; set; }
    }

    public class ToolState
    {
        public string ToolName { get; set; } = "";
        public string InternalName { get; set; } = "";
        public int SlotIndex { get; set; }
        public int UpgradeLevel { get; set; }  // 0=基础, 1=铜, 2=铁, 3=金, 4=铱
        public int? WaterLevel { get; set; }     // 仅浇水壶
        public int? WaterCapacity { get; set; }  // 仅浇水壶

        public string UpgradeName => UpgradeLevel switch
        {
            0 => "基础", 1 => "铜", 2 => "铁", 3 => "金", 4 => "铱", _ => "?"
        };
    }

    public class ItemValueInfo
    {
        public string Name { get; set; } = "";
        public string InternalName { get; set; } = "";
        public int SellPrice { get; set; }
        public int Stack { get; set; }
        public int TotalValue { get; set; }
        public int Quality { get; set; }
    }

    /*────────────────────────────────────────────
     *  地图感知数据模型
     *────────────────────────────────────────────*/

    public class LocationInfo
    {
        public string Name { get; set; } = "";
        public string DisplayName { get; set; } = "";
        public int MapWidth { get; set; }
        public int MapHeight { get; set; }
        public bool IsOutdoors { get; set; }
        public bool IsFarm { get; set; }
        public List<WarpInfo> Warps { get; set; } = new();
        public List<BuildingInfo> Buildings { get; set; } = new();
        public int PlayerCount { get; set; }
        public int NpcCount { get; set; }
        public List<string> NpcNames { get; set; } = new();
        public string Hint { get; set; } = "";
    }

    public class WarpInfo
    {
        public int SourceX { get; set; }
        public int SourceY { get; set; }
        public string TargetLocation { get; set; } = "";
        public int TargetX { get; set; }
        public int TargetY { get; set; }
    }

    public class BuildingInfo
    {
        public string Name { get; set; } = "";
        public int TileX { get; set; }
        public int TileY { get; set; }
        public int Width { get; set; }
        public int Height { get; set; }
        public int DoorX { get; set; }
        public int DoorY { get; set; }
    }

    public class MapObjectsSummary
    {
        public string Location { get; set; } = "";
        public MapObjectCounts Counts { get; set; } = new();
        public List<MapObjectEntry> Actionable { get; set; } = new();
        public List<MapTerrainEntry> HarvestableNow { get; set; } = new();
        public string Hint { get; set; } = "";
    }

    public class MapObjectsDetail
    {
        public string Location { get; set; } = "";
        public List<MapObjectEntry> Objects { get; set; } = new();
        public List<MapTerrainEntry> TerrainFeatures { get; set; } = new();
        public List<ResourceClumpEntry> ResourceClumps { get; set; } = new();
    }

    public class MapObjectCounts
    {
        public int Storage { get; set; }
        public int Machine { get; set; }
        public int MachineReady { get; set; }
        public int MachineProcessing { get; set; }
        public int Forage { get; set; }
        public int Object { get; set; }
        public int Crop { get; set; }
        public int CropHarvestable { get; set; }
        public int CropUnwatered { get; set; }
        public int Tree { get; set; }
        public int TreeTappable { get; set; }
        public int ResourceClump { get; set; }
    }

    public class MapObjectEntry
    {
        public int TileX { get; set; }
        public int TileY { get; set; }
        public string Name { get; set; } = "";
        public string Category { get; set; } = "";
        public string State { get; set; } = "";
    }

    public class MapTerrainEntry
    {
        public int TileX { get; set; }
        public int TileY { get; set; }
        public string Type { get; set; } = "";
        public string Name { get; set; } = "";
        public bool Watered { get; set; }
        public bool Harvestable { get; set; }
        public int DaysToHarvest { get; set; }
        public int TreeStage { get; set; }
        public bool Tappable { get; set; }
    }

    public class ResourceClumpEntry
    {
        public int TileX { get; set; }
        public int TileY { get; set; }
        public string Type { get; set; } = "";
        public int Width { get; set; }
        public int Height { get; set; }
    }

    public class WalkableAreaInfo
    {
        public string Location { get; set; } = "";
        public int MapWidth { get; set; }
        public int MapHeight { get; set; }
        public int WalkableTiles { get; set; }
        public int BlockedTiles { get; set; }
        public List<string> ReachableLocations { get; set; } = new();
        public string Hint { get; set; } = "";
    }
}
