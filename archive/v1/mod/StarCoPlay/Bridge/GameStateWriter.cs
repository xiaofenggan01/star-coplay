using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Tools;
using StarCoPlay.Models;

namespace StarCoPlay.Bridge
{
    /// <summary>
    /// 状态写入层 — 从 StateReader + ActionExecutor 采集状态，
    /// 构建 GameStateSnapshot 后通过 BridgeManager 原子写入 game_state.json。
    /// </summary>
    public class GameStateWriter
    {
        private readonly IMonitor _monitor;
        private readonly BridgeManager _bridge;
        private readonly StateReader _stateReader;
        private readonly ActionExecutor _actionExecutor;

        public GameStateWriter(IMonitor monitor, BridgeManager bridge, StateReader stateReader, ActionExecutor actionExecutor)
        {
            _monitor = monitor;
            _bridge = bridge;
            _stateReader = stateReader;
            _actionExecutor = actionExecutor;
        }

        /// <summary>
        /// 采集当前完整游戏状态并写入文件桥。
        /// 由 ModEntry 的定时回调驱动（如每秒或每次时间变化时）。
        /// </summary>
        public void WriteSnapshot()
        {
            try
            {
                var worldState = _stateReader.GetWorldState();
                var playerState = _stateReader.GetPlayerState();
                var inventory = _stateReader.GetInventory();
                var farmSummary = _stateReader.GetFarmSummary();
                var nearbyNpcs = _stateReader.GetNearbyNpcs(999); // 全地图 NPC
                var nearbyObjects = _stateReader.GetNearbyObjects(8);
                var player = Game1.player;

                var snapshot = new GameStateSnapshot
                {
                    Status = "running",
                    GameDayStarted = Context.IsWorldReady,

                    World = new Models.WorldInfo
                    {
                        Time = worldState.Time,
                        TimeDisplay = Game1.getTimeOfDayString(Game1.timeOfDay),
                        Day = worldState.Day,
                        Season = worldState.Season,
                        Year = worldState.Year,
                        DayOfWeek = worldState.DayOfWeek,
                        Weather = new WeatherInfo
                        {
                            Current = worldState.Weather,
                            IsRaining = worldState.IsRaining,
                            IsSnowing = worldState.IsSnowing,
                            IsLightning = worldState.IsLightning,
                            TomorrowForecast = null // TODO: 读取明日天气预报
                        },
                        IsFestivalDay = worldState.IsFestivalDay,
                        FestivalName = worldState.IsFestivalDay
                            ? Game1.CurrentEvent?.FestivalName
                            : null
                    },

                    Player = new Models.PlayerInfo
                    {
                        Name = playerState.Name,
                        Location = playerState.Location,
                        Tile = new TilePosition
                        {
                            X = playerState.TileX,
                            Y = playerState.TileY
                        },
                        FacingDirection = MapFacingDirection(playerState.FacingDirection),
                        Energy = new StatBar
                        {
                            Current = playerState.Energy,
                            Max = playerState.MaxEnergy
                        },
                        Health = new StatBar
                        {
                            Current = playerState.Health,
                            Max = playerState.MaxHealth
                        },
                        Gold = playerState.Gold,
                        EquippedItem = BuildEquippedItem(player),
                        CurrentToolIndex = player.CurrentToolIndex,
                        Skills = new SkillLevels
                        {
                            Farming = playerState.FarmingLevel,
                            Mining = playerState.MiningLevel,
                            Foraging = playerState.ForagingLevel,
                            Fishing = playerState.FishingLevel,
                            Combat = playerState.CombatLevel
                        },
                        StatusEffects = new StatusEffects
                        {
                            IsExhausted = playerState.Energy <= 0,
                            IsSwimming = playerState.IsSwimming,
                            IsRidingHorse = player.isRidingHorse(),
                            CanMove = playerState.CanMove
                        }
                    },

                    Inventory = new Models.InventoryInfo
                    {
                        SlotsUsed = inventory.SlotsUsed,
                        SlotsTotal = inventory.SlotsTotal,
                        Items = inventory.Items.Select(slot => new InventoryItemInfo
                        {
                            Slot = slot.Slot,
                            Name = slot.Name,
                            ItemId = slot.ItemId,
                            Stack = slot.Stack,
                            Quality = slot.Quality,
                            Type = GetItemType(slot.ItemId, slot.Name),
                            QualityName = GetQualityName(slot.Quality)
                        }).ToList()
                    },

                    FarmSummary = farmSummary != null
                        ? new Models.FarmInfo
                        {
                            Crops = new FarmCropSummary
                            {
                                TotalPlanted = farmSummary.CropCount,
                                UnwateredCount = farmSummary.UnwateredCount,
                                HarvestableCount = farmSummary.HarvestableCount,
                                EmptyTilledTiles = farmSummary.EmptyTilledTiles
                            },
                            Animals = new FarmAnimalSummary
                            {
                                Total = farmSummary.TotalAnimals,
                                Unpetted = farmSummary.UnfedAnimals,
                                ProductReady = farmSummary.ProductReady
                            },
                            Machines = new FarmMachineSummary
                            {
                                ReadyToCollect = farmSummary.MachinesReady,
                                Processing = farmSummary.MachinesProcessing,
                                Empty = farmSummary.MachinesEmpty
                            }
                        }
                        : new Models.FarmInfo(), // 不在农场时写空对象

                    Surroundings = new SurroundingsInfo
                    {
                        Radius = 8,
                        Npcs = nearbyNpcs.Select(npc =>
                        {
                            var playerTile = Game1.player.Tile;
                            var npcTile = new Vector2(npc.TileX, npc.TileY);
                            return new Models.NpcInfo
                            {
                                Name = npc.Name,
                                Tile = new TilePosition
                                {
                                    X = npc.TileX,
                                    Y = npc.TileY
                                },
                                Hearts = npc.Hearts,
                                Distance = Vector2.Distance(playerTile, npcTile)
                            };
                        }).ToList(),
                        Objects = nearbyObjects
                            .Where(o => o.Category == "object")
                            .Select(o => new NearbyObjectInfo
                            {
                                Tile = new TilePosition { X = o.TileX, Y = o.TileY },
                                Name = o.Name,
                                Type = o.ItemId ?? "unknown"
                            }).ToList(),
                        Crops = nearbyObjects
                            .Where(o => o.Category == "terrain" && o.IsHarvestable.HasValue)
                            .Select(o => new NearbyCropInfo
                            {
                                Tile = new TilePosition { X = o.TileX, Y = o.TileY },
                                CropName = o.Name,
                                Watered = o.IsWatered,
                                Harvestable = o.IsHarvestable ?? false
                            }).ToList(),
                        Terrain = nearbyObjects
                            .Where(o => o.Category == "tree")
                            .Select(o => new NearbyTerrainInfo
                            {
                                Tile = new TilePosition { X = o.TileX, Y = o.TileY },
                                Type = "tree",
                                State = o.Name
                            }).ToList()
                    }
                };

                string json = BridgeManager.Serialize(snapshot);
                _bridge.WriteStateAtomic(json);

                _monitor.Log("GameStateWriter: snapshot written", LogLevel.Trace);
            }
            catch (Exception ex)
            {
                _monitor.Log($"GameStateWriter.WriteSnapshot error: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>将面朝方向整数映射为字符串。</summary>
        private static string MapFacingDirection(int facing)
        {
            return facing switch
            {
                0 => "up",
                1 => "right",
                2 => "down",
                3 => "left",
                _ => "down"
            };
        }

        /// <summary>构建已装备物品信息。</summary>
        private static EquippedItemInfo? BuildEquippedItem(Farmer player)
        {
            var item = player.CurrentItem;
            if (item == null) return null;

            var info = new EquippedItemInfo
            {
                Name = item.DisplayName,
                Type = GetItemType(item.QualifiedItemId, item.DisplayName),
                ItemId = item.QualifiedItemId
            };

            if (item is WateringCan can)
            {
                info.WaterLevel = can.WaterLeft;
                info.WaterCapacity = can.waterCanMax;
            }

            return info;
        }

        /// <summary>根据品质数值返回品质名称。</summary>
        private static string? GetQualityName(int quality)
        {
            return quality switch
            {
                1 => "Silver",
                2 => "Gold",
                4 => "Iridium",
                _ => null
            };
        }

        /// <summary>根据 item_id 前缀和名称推断物品类型。</summary>
        private static string GetItemType(string itemId, string name)
        {
            if (itemId.StartsWith("(T)")) return "tool";
            if (itemId.StartsWith("(W)")) return "weapon";
            if (name.Contains("Seeds") || name.Contains("Starter")) return "seed";
            return "object";
        }
    }
}
