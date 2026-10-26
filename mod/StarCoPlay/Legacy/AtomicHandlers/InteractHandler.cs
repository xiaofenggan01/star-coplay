// ============================================================
// [LEGACY] 旧版交互实现（19 种 intent 路由） — 已被当前 interact 执行原子替代
// 保留原因：UseInputBridgeHandlers=false 时的 fallback
// 计划：protocol-capability-completion 迭代中评估清理
// ============================================================
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Buildings;
using StardewValley.Menus;
using StardewValley.Objects;
using StardewValley.TerrainFeatures;
using StarCoPlay;
using StarCoPlay.GameServices.Inventory;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2;

/// <summary>
/// Implements the V2 interact capability.
/// Phase 1 covers a minimal, stable subset: water, talk, answer, examine, harvest.
/// </summary>
/// <summary>
/// [已弃用] 交互意图路由层（19 种 intent → SDV API 分支）。
/// 替代方案：启用 ModConfig.UseInputBridgeHandlers=true 后使用当前 interact 执行原子。
/// 新路径不再做 intent 路由，统一 face + Tap X，让 SDV 根据上下文自动判断效果。
/// </summary>
[Obsolete("Use current interact execution atom instead. Enable via ModConfig.UseInputBridgeHandlers=true.")]
public class InteractHandler
{
    private readonly ActionExecutor _executor;
    private readonly StateReader _stateReader;
    private readonly IMonitor _monitor;
    private readonly AnimationAwaiter _animAwaiter;

    public InteractHandler(ActionExecutor executor, StateReader stateReader, IMonitor monitor, AnimationAwaiter animAwaiter)
    {
        _executor = executor;
        _stateReader = stateReader;
        _monitor = monitor;
        _animAwaiter = animAwaiter;
    }

    public V2Result? Handle(V2Command cmd)
    {
        var rawIntent = GetStringParam(cmd.Params, "intent");
        if (string.IsNullOrWhiteSpace(rawIntent))
        {
            return ResultBuilder.Failure(
                cmd.Seq,
                cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "interact 缺少 intent 参数");
        }

        var intent = IntentRegistry.Normalize(rawIntent);
        if (!IntentRegistry.IsValid(intent))
        {
            return ResultBuilder.Failure(
                cmd.Seq,
                cmd.ActionId,
                ResultBuilder.Errors.InvalidIntent,
                $"不支持的 intent: {rawIntent}");
        }

        return intent switch
        {
            IntentRegistry.Water => HandleWater(cmd),
            IntentRegistry.Harvest => HandleHarvest(cmd),
            IntentRegistry.Talk => HandleTalk(cmd),
            IntentRegistry.Answer => HandleAnswer(cmd),
            IntentRegistry.Examine => HandleExamine(cmd),
            IntentRegistry.UseTool => HandleUseTool(cmd),
            IntentRegistry.Gift => HandleGift(cmd),
            IntentRegistry.Store => HandleStore(cmd),
            IntentRegistry.Take => HandleTake(cmd),
            IntentRegistry.Drop => HandleDrop(cmd),
            IntentRegistry.Ship => HandleShip(cmd),
            IntentRegistry.Sleep => HandleSleep(cmd),
            IntentRegistry.Consume => HandleConsume(cmd),
            IntentRegistry.Pet => HandlePet(cmd),
            IntentRegistry.Place => HandlePlace(cmd),
            IntentRegistry.PickUp => HandlePickUp(cmd),
            IntentRegistry.Dismiss => HandleDismiss(cmd),
            IntentRegistry.Craft => HandleCraft(cmd),
            IntentRegistry.Exit => ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId, ResultBuilder.Errors.NotImplemented,
                "exit intent 暂未实现"),
            _ => ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId, ResultBuilder.Errors.InvalidIntent,
                $"未处理的 intent: {intent}")
        };
    }

    /// <summary>交互距离阈值（tiles）。</summary>
    private const float InteractionRange = 2f;

    private V2Result? CheckTileDistance(V2Command cmd, Point tile)
    {
        var playerTile = Game1.player.Tile;
        var distance = Vector2.Distance(playerTile, new Vector2(tile.X, tile.Y));
        if (distance > InteractionRange)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.OutOfRange,
                $"目标 ({tile.X},{tile.Y}) 距离太远 ({distance:F1})，请先靠近");
        }
        return null;
    }

    private V2Result? HandleWater(V2Command cmd)
    {
        if (!TryGetTile(cmd.Params, out var tile))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "water 需要 x/y 或 target.{x,y} 参数");
        }

        var rangeCheck = CheckTileDistance(cmd, tile);
        if (rangeCheck != null) return rangeCheck;

        if (!EnsureHoeDirtWithCrop(tile))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.TargetNotFound,
                $"({tile.X},{tile.Y}) 不是可浇水作物格");
        }

        float energyBefore = Game1.player.Stamina;
        if (!EnsureToolReady("Watering Can"))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ItemNotFound,
                "背包快捷栏中找不到可用的 Watering Can");
        }

        if (Game1.player.CurrentTool is not StardewValley.Tools.WateringCan wateringCan)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                "当前未装备 Watering Can");
        }

        if (wateringCan.WaterLeft <= 0)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                "Watering Can 没有水了");
        }

        var ok = _executor.UseToolAt(tile.X, tile.Y);
        if (!ok)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"在 ({tile.X},{tile.Y}) 浇水失败");
        }

        // Defer result until animation completes
        _animAwaiter.Begin(cmd, "water", energyBefore);
        return null;
    }

    private V2Result? HandleHarvest(V2Command cmd)
    {
        if (!TryGetTile(cmd.Params, out var tile))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "harvest 需要 x/y 或 target.{x,y} 参数");
        }

        var rangeCheck = CheckTileDistance(cmd, tile);
        if (rangeCheck != null) return rangeCheck;

        if (!IsHarvestableCrop(tile))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.TargetNotFound,
                $"({tile.X},{tile.Y}) 没有可收获作物");
        }

        float energyBefore = Game1.player.Stamina;
        var ok = _executor.InteractWithTile(tile.X, tile.Y);
        if (!ok)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"在 ({tile.X},{tile.Y}) 收获失败");
        }

        // harvest 通过 checkAction → crop 内部逻辑完成，是同步操作
        // 不通过 UsingTool 驱动动画，所以不走 AnimationAwaiter
        return ResultBuilder.Success(
                cmd.Seq,
                cmd.ActionId,
                new
                {
                    intent = "harvest",
                    target = new { x = tile.X, y = tile.Y }
                })
            .WithObservation($"已对 ({tile.X},{tile.Y}) 执行收获")
            .WithSideEffectIfEnergyChanged(energyBefore);
    }

    private V2Result HandleTalk(V2Command cmd)
    {
        var npcName = GetStringParam(cmd.Params, "npc_name")
            ?? GetNestedString(cmd.Params, "target", "name")
            ?? GetStringParam(cmd.Params, "target");

        if (string.IsNullOrWhiteSpace(npcName))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "talk 需要 npc_name 或 target.name");
        }

        var location = Game1.currentLocation;
        if (location == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                "当前没有可用地图");
        }

        var npc = FindNpc(location, npcName);
        if (npc == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.TargetNotFound,
                $"当前地图未找到 NPC: {npcName}");
        }

        var distance = Vector2.Distance(Game1.player.Tile, npc.Tile);
        if (distance > 2f)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.OutOfRange,
                $"{npcName} 距离太远 ({distance:F1})");
        }

        float energyBefore = Game1.player.Stamina;
        Game1.player.faceGeneralDirection(npc.Position);
        npc.checkAction(Game1.player, location);

        return ResultBuilder.Success(
                cmd.Seq,
                cmd.ActionId,
                new
                {
                    intent = "talk",
                    npc = npc.Name,
                    display_name = npc.displayName,
                    distance = Math.Round(distance, 1)
                })
            .WithObservation($"已与 {npc.displayName} 对话")
            .WithSideEffectIfEnergyChanged(energyBefore);
    }

    private V2Result HandleAnswer(V2Command cmd)
    {
        if (!TryGetIntParam(cmd.Params, "choice", out var choice) &&
            !TryGetIntParam(cmd.Params, "index", out choice))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "answer 需要 choice 或 index 参数");
        }

        float energyBefore = Game1.player.Stamina;
        var (success, message, errorCode) = _executor.AnswerDialog(choice);
        if (!success)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                errorCode ?? ResultBuilder.Errors.ExecutionFailed,
                message);
        }

        return ResultBuilder.Success(
                cmd.Seq,
                cmd.ActionId,
                new { intent = "answer", choice })
            .WithObservation(message)
            .WithSideEffectIfEnergyChanged(energyBefore);
    }

    private V2Result HandleDismiss(V2Command cmd)
    {
        var menu = Game1.activeClickableMenu;

        if (menu == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                "当前没有打开的对话框或菜单，无需 dismiss");
        }

        if (menu is DialogueBox dialogueBox)
        {
            // 检查是否为问题对话框（有选项）——问题对话框应该用 answer intent
            var responses = dialogueBox.responses;
            if (responses != null && responses.Length > 0)
            {
                return ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.InvalidParams,
                    $"当前对话框是问题类型（有 {responses.Length} 个选项），请使用 intent=answer + choice 参数来回应");
            }

            // 非问题对话框：模拟点击关闭/推进
            _monitor.Log("[V2] dismiss: 关闭非问题 DialogueBox", LogLevel.Debug);
            dialogueBox.receiveLeftClick(0, 0);

            // 检查对话框是否真的关闭了（多页对话只会翻页，不会关闭）
            bool stillOpen = Game1.activeClickableMenu is DialogueBox;
            var obs = stillOpen ? "已推进对话（对话框仍打开，可能有多页）" : "已关闭对话框";

            return ResultBuilder.Success(
                    cmd.Seq,
                    cmd.ActionId,
                    new { intent = "dismiss", menu_type = "DialogueBox", still_open = stillOpen })
                .WithObservation(obs);
        }

        // 非 DialogueBox 菜单：尝试通用关闭
        var menuType = menu.GetType().Name;
        _monitor.Log($"[V2] dismiss: 尝试关闭非 DialogueBox 菜单: {menuType}", LogLevel.Debug);
        menu.receiveLeftClick(0, 0);

        return ResultBuilder.Success(
                cmd.Seq,
                cmd.ActionId,
                new { intent = "dismiss", menu_type = menuType })
            .WithObservation($"已尝试关闭 {menuType}");
    }

    /// <summary>
    /// 当操作触发了意外对话框时，请求延迟清理。
    /// 对话框可能在当前帧还未创建（下一帧才出现），所以需要延迟几帧后清理。
    /// CommandProcessor.Poll 会检查此标志并在到期时强制关闭 DialogueBox。
    /// </summary>
    internal static int PendingDismissFrames;

    private void RequestDismissDialogue()
    {
        // 先尝试同帧清理
        if (Game1.activeClickableMenu is DialogueBox)
        {
            _monitor.Log("[V2] DismissAnyDialogue: 同帧强制关闭残留对话框", LogLevel.Debug);
            Game1.activeClickableMenu = null;
        }
        else
        {
            // 对话框可能下一帧才出现，设置延迟清理
            PendingDismissFrames = 5;
            _monitor.Log("[V2] DismissAnyDialogue: 请求延迟清理（5帧后）", LogLevel.Debug);
        }
    }

    private V2Result HandleExamine(V2Command cmd)
    {
        if (!TryGetTile(cmd.Params, out var tile))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "examine 需要 x/y 或 target.{x,y} 参数");
        }

        var rangeCheck = CheckTileDistance(cmd, tile);
        if (rangeCheck != null) return rangeCheck;

        var location = Game1.currentLocation;
        if (location == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                "当前没有可用地图");
        }

        var tileKey = new Vector2(tile.X, tile.Y);
        if (location.objects.TryGetValue(tileKey, out var obj) && obj is Chest)
        {
            var items = _executor.PeekChest(tile.X, tile.Y);
            if (items == null)
            {
                return ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.NotInteractable,
                    $"({tile.X},{tile.Y}) 处箱子不可读取");
            }

            return ResultBuilder.Success(
                    cmd.Seq,
                    cmd.ActionId,
                    new
                    {
                        intent = "examine",
                        target = new { x = tile.X, y = tile.Y },
                        object_type = "chest",
                        items
                    })
                .WithObservation($"已查看 ({tile.X},{tile.Y}) 处箱子，共 {items.Count} 项");
        }

        if (HasAnyInteractableTarget(location, tile))
        {
            var ok = _executor.InteractWithTile(tile.X, tile.Y);
            if (!ok)
            {
                return ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.NotInteractable,
                    $"({tile.X},{tile.Y}) 处没有可检查对象");
            }

            return ResultBuilder.Success(
                    cmd.Seq,
                    cmd.ActionId,
                    new { intent = "examine", target = new { x = tile.X, y = tile.Y } })
                .WithObservation($"已检查 ({tile.X},{tile.Y})");
        }

        return ResultBuilder.Failure(
            cmd.Seq, cmd.ActionId,
            ResultBuilder.Errors.TargetNotFound,
            $"({tile.X},{tile.Y}) 处没有可检查对象");
    }

    private V2Result? HandleUseTool(V2Command cmd)
    {
        if (!TryGetTile(cmd.Params, out var tile))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "use_tool 需要 x/y 或 target.{x,y} 参数");
        }

        var rangeCheck = CheckTileDistance(cmd, tile);
        if (rangeCheck != null) return rangeCheck;

        float energyBefore = Game1.player.Stamina;
        var ok = _executor.UseToolAt(tile.X, tile.Y);
        if (!ok)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"在 ({tile.X},{tile.Y}) 使用工具失败");
        }

        // Defer result until animation completes
        _animAwaiter.Begin(cmd, "use_tool", energyBefore);
        return null;
    }

    private V2Result? HandleConsume(V2Command cmd)
    {
        var player = Game1.player;

        // 从参数中获取物品名（支持 item / item_name / target）
        var itemName = GetStringParam(cmd.Params, "item")
            ?? GetStringParam(cmd.Params, "item_name")
            ?? GetStringParam(cmd.Params, "target");

        StardewValley.Object? targetItem = null;
        int targetSlot = -1;

        if (!string.IsNullOrWhiteSpace(itemName))
        {
            // 按名称在背包中查找
            for (int i = 0; i < player.Items.Count; i++)
            {
                if (player.Items[i] is StardewValley.Object obj &&
                    (obj.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) ||
                     obj.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)))
                {
                    targetItem = obj;
                    targetSlot = i;
                    break;
                }
            }
        }
        else
        {
            // 未指定物品名 → 使用当前手持物品
            targetItem = player.ActiveObject;
        }

        if (targetItem == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ItemNotFound,
                string.IsNullOrWhiteSpace(itemName)
                    ? "当前未持有物品，且未指定 item 参数"
                    : $"背包中找不到: {itemName}");
        }

        // 检查是否可食用（Edibility > -300 表示可食用）
        if (targetItem.Edibility <= -300)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.NotInteractable,
                $"{targetItem.DisplayName} 不是可食用物品");
        }

        // 如果物品不在手上，先装备到快捷栏
        if (targetSlot >= 0 && targetSlot != player.CurrentToolIndex)
        {
            _executor.EquipItemByName(targetItem.DisplayName);
        }

        float energyBefore = player.Stamina;
        var displayName = targetItem.DisplayName;
        var edibility = targetItem.Edibility;

        // 触发食用
        player.eatObject(targetItem, false);

        // Defer result until eat animation completes
        _animAwaiter.Begin(cmd, "consume", energyBefore, 300); // eat animation can be longer
        return null;
    }

    private V2Result? HandlePet(V2Command cmd)
    {
        // 解析 target 参数（支持 TargetRef 格式或 animal_name）
        var target = GetStringParam(cmd.Params, "target");
        var animalName = GetStringParam(cmd.Params, "animal_name");

        FarmAnimal? animal = null;

        // 优先使用 TargetRef 格式解析
        if (!string.IsNullOrWhiteSpace(target) && TargetRefResolver.IsTargetRef(target))
        {
            var resolved = TargetRefResolver.Resolve(target, _monitor);
            if (resolved?.Entity is FarmAnimal resolvedAnimal)
            {
                animal = resolvedAnimal;
            }
        }

        // TargetRef 格式解析失败 → 直接报错，不 fallback（避免用整个 TargetRef 字符串做名字搜索）
        if (animal == null && !string.IsNullOrWhiteSpace(target) && TargetRefResolver.IsTargetRef(target))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ItemNotFound,
                $"TargetRef 解析失败，找不到动物: {target}");
        }

        // Fallback: 通过 animal_name 或 target 在当前地图搜索
        if (animal == null)
        {
            var searchName = animalName ?? target;
            if (string.IsNullOrWhiteSpace(searchName))
            {
                return ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.InvalidParams,
                    "pet 需要 target（TargetRef 格式）或 animal_name 参数");
            }

            animal = FindAnimalInCurrentLocation(searchName);
        }

        if (animal == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ItemNotFound,
                $"找不到动物: {animalName ?? target}");
        }

        // 距离检查（复用 CheckTileDistance，2 tile 范围）
        var animalTile = new Point((int)animal.Tile.X, (int)animal.Tile.Y);
        var rangeCheck = CheckTileDistance(cmd, animalTile);
        if (rangeCheck != null) return rangeCheck;

        // 已经抚摸过
        if (animal.wasPet.Value)
        {
            return ResultBuilder.Success(
                    cmd.Seq,
                    cmd.ActionId,
                    new
                    {
                        intent = "pet",
                        already_petted = true,
                        animal_name = animal.Name,
                        display_name = animal.displayName,
                        animal_type = animal.type.Value,
                    })
                .WithObservation($"{animal.displayName} 今天已经被抚摸过了");
        }

        // 执行抚摸：面向动物 → InteractWithTile
        var player = Game1.player;
        float energyBefore = player.Stamina;
        // 重新读取动物当前位置（TOCTOU 缓解）
        var currentTile = animal.Tile;
        player.faceGeneralDirection(animal.Position);
        var ok = _executor.InteractWithTile((int)currentTile.X, (int)currentTile.Y);

        if (!ok)
        {
            RequestDismissDialogue();
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"抚摸 {animal.displayName} 失败，交互未触发");
        }

        // 验证抚摸是否真正生效（InteractWithTile 返回 true 不代表抚摸成功）
        bool actuallyPetted = animal.wasPet.Value;

        if (actuallyPetted)
        {
            return ResultBuilder.Success(
                    cmd.Seq,
                    cmd.ActionId,
                    new
                    {
                        intent = "pet",
                        animal_name = animal.Name,
                        display_name = animal.displayName,
                        animal_type = animal.type.Value,
                        actually_petted = true,
                    })
                .WithObservation($"已抚摸 {animal.displayName}")
                .WithSideEffectIfEnergyChanged(energyBefore);
        }

        // 抚摸未生效——诊断原因
        var timeOfDay = Game1.timeOfDay;
        bool isSleepTime = timeOfDay >= 1800 || timeOfDay < 600;
        string reason;
        if (isSleepTime)
            reason = $"动物正在睡觉（当前时间 {timeOfDay}，18:00-6:00 为睡眠时间）";
        else if (animal.controller != null)
            reason = "动物正在移动中，无法交互";
        else
            reason = "交互未触发（可能距离不够或朝向不对）";

        // 自动清理交互产生的弹出框（动物睡觉时会弹出文本框，阻塞后续操作）
        RequestDismissDialogue();

        return ResultBuilder.Failure(
            cmd.Seq, cmd.ActionId,
            ResultBuilder.Errors.ExecutionFailed,
            $"抚摸 {animal.displayName} 失败: {reason}");
    }

    /// <summary>
    /// 处理 gift intent — 向 NPC 赠送礼物。
    ///
    /// <para><b>必需参数：</b></para>
    /// <list type="bullet">
    ///   <item><c>item</c> / <c>item_name</c> (string): 要赠送的物品名称（按 DisplayName 或 Name 匹配）</item>
    ///   <item><c>npc</c> / <c>npc_name</c> / <c>target</c> (string): 目标 NPC 名称</item>
    /// </list>
    ///
    /// <para><b>前置条件：</b></para>
    /// <list type="bullet">
    ///   <item>NPC 必须在当前地图内</item>
    ///   <item>玩家必须在 NPC 2 tile 范围内</item>
    ///   <item>物品必须在玩家背包中且为 StardewValley.Object 类型</item>
    /// </list>
    ///
    /// <para><b>返回 data：</b></para>
    /// <code>{ intent: "gift", npc_name: string, item_name: string, friendship_before: int, friendship_after: int }</code>
    ///
    /// <para><b>可能的错误码：</b></para>
    /// <list type="bullet">
    ///   <item><c>INVALID_PARAMS</c>: 缺少 item 或 npc 参数</item>
    ///   <item><c>TARGET_NOT_FOUND</c>: 当前地图找不到 NPC</item>
    ///   <item><c>ITEM_NOT_FOUND</c>: 背包中找不到指定物品</item>
    ///   <item><c>OUT_OF_RANGE</c>: 距离 NPC 太远</item>
    ///   <item><c>NOT_INTERACTABLE</c>: 物品不是可赠送的 Object 类型</item>
    ///   <item><c>EXECUTION_FAILED</c>: 赠送操作失败</item>
    /// </list>
    /// </summary>
    private V2Result HandleGift(V2Command cmd)
    {
        // ── 解析物品名 ──
        var itemName = GetStringParam(cmd.Params, "item")
            ?? GetStringParam(cmd.Params, "item_name");
        if (string.IsNullOrWhiteSpace(itemName))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "gift 需要 item 或 item_name 参数");
        }

        // ── 解析 NPC 名 ──
        var npcName = GetStringParam(cmd.Params, "npc")
            ?? GetStringParam(cmd.Params, "npc_name")
            ?? GetNestedString(cmd.Params, "target", "name")
            ?? GetStringParam(cmd.Params, "target");
        if (string.IsNullOrWhiteSpace(npcName))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "gift 需要 npc 或 npc_name 参数");
        }

        var location = Game1.currentLocation;
        if (location == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                "当前没有可用地图");
        }

        // ── 查找 NPC ──
        var npc = FindNpc(location, npcName);
        if (npc == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.TargetNotFound,
                $"当前地图未找到 NPC: {npcName}");
        }

        // ── 距离检查 ──
        var distance = Vector2.Distance(Game1.player.Tile, npc.Tile);
        if (distance > InteractionRange)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.OutOfRange,
                $"{npcName} 距离太远 ({distance:F1})，请先靠近");
        }

        // ── 查找物品 ──
        var player = Game1.player;
        StardewValley.Object? giftItem = null;
        int giftSlot = -1;

        for (int i = 0; i < player.Items.Count; i++)
        {
            if (player.Items[i] is StardewValley.Object obj &&
                (obj.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) ||
                 obj.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)))
            {
                giftItem = obj;
                giftSlot = i;
                break;
            }
        }

        if (giftItem == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ItemNotFound,
                $"背包中找不到可赠送的物品: {itemName}");
        }

        // ── 记录送礼前好感度 ──
        int friendshipBefore = 0;
        if (player.friendshipData.TryGetValue(npc.Name, out var friendship))
        {
            friendshipBefore = friendship.Points;
        }

        // ── 面向 NPC ──
        player.faceGeneralDirection(npc.Position);

        // ── 设置 ActiveObject 并执行赠送 ──
        var previousActive = player.ActiveObject;
        var previousToolIndex = player.CurrentToolIndex;

        // 将礼物放到手上
        if (giftSlot != player.CurrentToolIndex)
        {
            _executor.EquipItemByName(giftItem.DisplayName);
        }
        player.ActiveObject = giftItem;

        float energyBefore = player.Stamina;

        // tryToReceiveActiveObject 是星露谷的标准送礼 API
        bool accepted = npc.tryToReceiveActiveObject(player);

        // ── 读取送礼后好感度 ──
        int friendshipAfter = 0;
        if (player.friendshipData.TryGetValue(npc.Name, out var friendshipPost))
        {
            friendshipAfter = friendshipPost.Points;
        }

        if (!accepted)
        {
            // 恢复 ActiveObject 状态
            player.ActiveObject = previousActive;
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"{npc.displayName} 拒绝了 {giftItem.DisplayName}（可能今天已送过礼或不可赠送）");
        }

        return ResultBuilder.Success(
                cmd.Seq,
                cmd.ActionId,
                new
                {
                    intent = "gift",
                    npc_name = npc.Name,
                    display_name = npc.displayName,
                    item_name = giftItem.DisplayName,
                    friendship_before = friendshipBefore,
                    friendship_after = friendshipAfter,
                })
            .WithObservation($"已向 {npc.displayName} 赠送 {giftItem.DisplayName}，好感度 {friendshipBefore} → {friendshipAfter}")
            .WithSideEffect("inventory_changed", $"背包移除 {giftItem.DisplayName} x1")
            .WithSideEffectIfEnergyChanged(energyBefore);
    }

    /// <summary>
    /// 处理 place intent — 将物品放置到指定 tile 上。
    ///
    /// <para><b>必需参数：</b></para>
    /// <list type="bullet">
    ///   <item><c>item</c> / <c>item_name</c> (string): 要放置的物品名称</item>
    ///   <item><c>x</c>, <c>y</c> 或 <c>pos.x</c>, <c>pos.y</c> 或 <c>target.x</c>, <c>target.y</c> (int): 目标 tile 坐标</item>
    /// </list>
    ///
    /// <para><b>行为说明：</b></para>
    /// <list type="bullet">
    ///   <item>种子放在耕地（HoeDirt）上时，游戏自动处理播种逻辑</item>
    ///   <item>家具/制品放在空地上时，使用 tryToPlaceItem 放置</item>
    ///   <item>放置距离不超过 2 tile</item>
    /// </list>
    ///
    /// <para><b>返回 data：</b></para>
    /// <code>{ intent: "place", item_name: string, position: { x: int, y: int } }</code>
    ///
    /// <para><b>可能的错误码：</b></para>
    /// <list type="bullet">
    ///   <item><c>INVALID_PARAMS</c>: 缺少 item 或坐标参数</item>
    ///   <item><c>ITEM_NOT_FOUND</c>: 背包中找不到指定物品</item>
    ///   <item><c>OUT_OF_RANGE</c>: 目标 tile 太远</item>
    ///   <item><c>EXECUTION_FAILED</c>: 放置操作失败（tile 被占据或不可放置）</item>
    /// </list>
    /// </summary>
    private V2Result HandlePlace(V2Command cmd)
    {
        // ── 解析物品名 ──
        var itemName = GetStringParam(cmd.Params, "item")
            ?? GetStringParam(cmd.Params, "item_name");
        if (string.IsNullOrWhiteSpace(itemName))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "place 需要 item 或 item_name 参数");
        }

        // ── 解析目标坐标（支持 x/y、pos.x/pos.y、target.x/target.y） ──
        if (!TryGetTile(cmd.Params, out var tile))
        {
            // 也支持 pos 嵌套对象
            if (!TryGetNestedPoint(cmd.Params, "pos", out tile))
            {
                return ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.InvalidParams,
                    "place 需要 x/y 或 pos.{x,y} 或 target.{x,y} 参数");
            }
        }

        // ── 距离检查 ──
        var rangeCheck = CheckTileDistance(cmd, tile);
        if (rangeCheck != null) return rangeCheck;

        var location = Game1.currentLocation;
        if (location == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                "当前没有可用地图");
        }

        // ── 查找物品 ──
        var player = Game1.player;
        StardewValley.Object? placeItem = null;
        int placeSlot = -1;

        for (int i = 0; i < player.Items.Count; i++)
        {
            if (player.Items[i] is StardewValley.Object obj &&
                (obj.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) ||
                 obj.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)))
            {
                placeItem = obj;
                placeSlot = i;
                break;
            }
        }

        if (placeItem == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ItemNotFound,
                $"背包中找不到: {itemName}");
        }

        float energyBefore = player.Stamina;

        // ── 装备物品到手上 ──
        if (placeSlot != player.CurrentToolIndex)
        {
            _executor.EquipItemByName(placeItem.DisplayName);
        }
        player.ActiveObject = placeItem;

        // ── 面向目标 tile ──
        player.faceGeneralDirection(new Vector2(tile.X * 64f + 32f, tile.Y * 64f + 32f));

        // ── 尝试放置 ──
        var tileVec = new Vector2(tile.X, tile.Y);
        bool placed = false;

        // 方式 1: Utility.tryToPlaceItem — 通用放置（处理种子→HoeDirt 播种等）
        if (Utility.tryToPlaceItem(location, placeItem, tile.X * 64, tile.Y * 64))
        {
            placed = true;
            // tryToPlaceItem 成功后物品可能已从背包移除（Stack 减少或 null）
            if (placeItem.Stack <= 0)
            {
                player.Items[placeSlot] = null;
            }
        }

        // 方式 2: 如果 tryToPlaceItem 失败且 tile 仍为空，尝试 Object.placementAction
        // 先检查 tile 未被占据，避免双重放置
        if (!placed && !location.objects.ContainsKey(new Vector2(tile.X, tile.Y))
            && placeItem.placementAction(location, tile.X * 64, tile.Y * 64, player))
        {
            placed = true;
            // 放置成功后从背包扣减
            placeItem.Stack--;
            if (placeItem.Stack <= 0)
            {
                player.Items[placeSlot] = null;
            }
        }

        if (!placed)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"无法在 ({tile.X},{tile.Y}) 放置 {placeItem.DisplayName}（目标格可能被占据或不支持放置）");
        }

        // ── ActiveObject 清理 ──
        if (player.Items[placeSlot] == null)
        {
            player.ActiveObject = null;
        }

        return ResultBuilder.Success(
                cmd.Seq,
                cmd.ActionId,
                new
                {
                    intent = "place",
                    item_name = placeItem.DisplayName,
                    position = new { x = tile.X, y = tile.Y },
                })
            .WithObservation($"已在 ({tile.X},{tile.Y}) 放置 {placeItem.DisplayName}")
            .WithSideEffect("inventory_changed", $"背包移除 {placeItem.DisplayName} x1")
            .WithSideEffectIfEnergyChanged(energyBefore);
    }

    /// <summary>
    /// 处理 pick_up intent — 拾取地面上的掉落物或可采集物品（forage）。
    ///
    /// <para><b>可选参数：</b></para>
    /// <list type="bullet">
    ///   <item><c>x</c>, <c>y</c> 或 <c>pos.x</c>, <c>pos.y</c> 或 <c>target.x</c>, <c>target.y</c> (int):
    ///         指定拾取坐标。省略时自动查找最近的可拾取物品</item>
    /// </list>
    ///
    /// <para><b>行为说明：</b></para>
    /// <list type="bullet">
    ///   <item>优先检查 tile 上的 forage 类物品（isForage == true），直接加入背包</item>
    ///   <item>也支持拾取地面上的普通 Object（如玩家放置的物品）</item>
    ///   <item>未指定坐标时，扫描玩家周围 4 tile 范围内最近的可拾取物</item>
    ///   <item>拾取前检查背包容量</item>
    /// </list>
    ///
    /// <para><b>返回 data：</b></para>
    /// <code>{ intent: "pick_up", item_name: string, count: int, position: { x: int, y: int } }</code>
    ///
    /// <para><b>可能的错误码：</b></para>
    /// <list type="bullet">
    ///   <item><c>TARGET_NOT_FOUND</c>: 指定位置或附近没有可拾取物品</item>
    ///   <item><c>OUT_OF_RANGE</c>: 目标 tile 太远</item>
    ///   <item><c>INVENTORY_FULL</c>: 背包已满，无法拾取</item>
    ///   <item><c>EXECUTION_FAILED</c>: 拾取操作失败</item>
    /// </list>
    /// </summary>
    private V2Result HandlePickUp(V2Command cmd)
    {
        var location = Game1.currentLocation;
        if (location == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                "当前没有可用地图");
        }

        var player = Game1.player;
        Point targetTile;
        StardewValley.Object? targetObj = null;

        // ── 尝试解析指定坐标 ──
        bool hasExplicitPos = TryGetTile(cmd.Params, out targetTile)
            || TryGetNestedPoint(cmd.Params, "pos", out targetTile);

        if (hasExplicitPos)
        {
            // 距离检查
            var rangeCheck = CheckTileDistance(cmd, targetTile);
            if (rangeCheck != null) return rangeCheck;

            // 在指定 tile 上查找可拾取物品
            var tileVec = new Vector2(targetTile.X, targetTile.Y);
            if (location.objects.TryGetValue(tileVec, out var obj) && IsPickable(obj))
            {
                targetObj = obj;
            }
        }
        else
        {
            // ── 未指定坐标：扫描附近最近的可拾取物 ──
            const float scanRange = 4f;
            float bestDist = float.MaxValue;
            Vector2 bestTile = Vector2.Zero;

            foreach (var pair in location.objects.Pairs)
            {
                if (!IsPickable(pair.Value)) continue;

                float dist = Vector2.Distance(player.Tile, pair.Key);
                if (dist <= scanRange && dist < bestDist)
                {
                    bestDist = dist;
                    bestTile = pair.Key;
                    targetObj = pair.Value;
                }
            }

            if (targetObj != null)
            {
                targetTile = new Point((int)bestTile.X, (int)bestTile.Y);
            }
        }

        if (targetObj == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.TargetNotFound,
                hasExplicitPos
                    ? $"({targetTile.X},{targetTile.Y}) 处没有可拾取物品"
                    : "附近没有可拾取物品");
        }

        // ── 背包容量检查 ──
        if (!player.couldInventoryAcceptThisItem(targetObj))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InventoryFull,
                $"背包已满，无法拾取 {targetObj.DisplayName}");
        }

        float energyBefore = player.Stamina;
        var pickedName = targetObj.DisplayName;
        var pickedCount = targetObj.Stack;
        var pickTileVec = new Vector2(targetTile.X, targetTile.Y);

        // ── 面向目标 ──
        player.faceGeneralDirection(new Vector2(targetTile.X * 64f + 32f, targetTile.Y * 64f + 32f));

        // ── 执行拾取 ──
        // 对于 forage 类物品，使用 checkForAction 触发标准采集逻辑（含经验值）
        if (targetObj.isForage())
        {
            bool forageOk = targetObj.checkForAction(player);
            if (forageOk)
            {
                // checkForAction 成功后物品通常已从地图移除
                return ResultBuilder.Success(
                        cmd.Seq,
                        cmd.ActionId,
                        new
                        {
                            intent = "pick_up",
                            item_name = pickedName,
                            count = 1,
                            position = new { x = targetTile.X, y = targetTile.Y },
                            pickup_type = "forage",
                        })
                    .WithObservation($"已在 ({targetTile.X},{targetTile.Y}) 采集 {pickedName}")
                    .WithSideEffect("inventory_changed", $"背包新增 {pickedName}")
                    .WithSideEffectIfEnergyChanged(energyBefore);
            }
            // forage checkForAction 失败（背包满/mod 拦截）→ 返回错误，不 fallthrough
            return ResultBuilder.Failure(
                cmd.Seq,
                cmd.ActionId,
                ResultBuilder.Errors.InventoryFull,
                $"无法采集 {pickedName}，可能背包已满或被拦截");
        }

        // ── 通用拾取（非 forage）：直接从地图移除并加入背包 ──
        if (!player.couldInventoryAcceptThisItem(targetObj))
        {
            return ResultBuilder.Failure(
                cmd.Seq,
                cmd.ActionId,
                ResultBuilder.Errors.InventoryFull,
                $"背包已满，无法拾取 {pickedName}");
        }
        location.objects.Remove(pickTileVec);
        player.addItemToInventory(targetObj);

        return ResultBuilder.Success(
                cmd.Seq,
                cmd.ActionId,
                new
                {
                    intent = "pick_up",
                    item_name = pickedName,
                    count = pickedCount,
                    position = new { x = targetTile.X, y = targetTile.Y },
                    pickup_type = "object",
                })
            .WithObservation($"已在 ({targetTile.X},{targetTile.Y}) 拾取 {pickedName} x{pickedCount}")
            .WithSideEffect("inventory_changed", $"背包新增 {pickedName} x{pickedCount}")
            .WithSideEffectIfEnergyChanged(energyBefore);
    }

    /// <summary>判断地面上的 Object 是否可拾取（forage 或非大型放置物）。</summary>
    private static bool IsPickable(StardewValley.Object obj)
    {
        // forage 类物品（野生浆果、蘑菇等）始终可拾取
        if (obj.isForage()) return true;

        // 不拾取大型制品/机器（如酒桶、保存罐等）
        if (obj.bigCraftable.Value) return false;

        // 不拾取箱子
        if (obj is Chest) return false;

        // 其他小型物品（如玩家丢弃的物品）可拾取
        return true;
    }

    /// <summary>在当前地图中按名称查找 FarmAnimal。</summary>
    private static FarmAnimal? FindAnimalInCurrentLocation(string name)
    {
        var location = Game1.currentLocation;
        if (location == null) return null;

        // Farm 户外动物
        if (location is Farm farm)
        {
            foreach (var animal in farm.animals.Values)
            {
                if (string.Equals(animal.Name, name, StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(animal.displayName, name, StringComparison.OrdinalIgnoreCase))
                    return animal;
            }
        }

        // AnimalHouse 内的动物（Barn/Coop）
        if (location is AnimalHouse animalHouse)
        {
            foreach (var animal in animalHouse.animals.Values)
            {
                if (string.Equals(animal.Name, name, StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(animal.displayName, name, StringComparison.OrdinalIgnoreCase))
                    return animal;
            }
        }

        return null;
    }

    /// <summary>
    /// 处理 drop intent — 将物品从背包丢弃到地面。
    ///
    /// <para><b>必需参数：</b></para>
    /// <list type="bullet">
    ///   <item><c>item</c> / <c>item_name</c> (string): 要丢弃的物品名称（按 DisplayName 或 Name 匹配）</item>
    /// </list>
    ///
    /// <para><b>可选参数：</b></para>
    /// <list type="bullet">
    ///   <item><c>count</c> (int): 丢弃数量（默认 1）。如果超过物品 Stack，则丢弃全部</item>
    /// </list>
    ///
    /// <para><b>行为说明：</b></para>
    /// <list type="bullet">
    ///   <item>按 DisplayName/Name 在背包中查找物品</item>
    ///   <item>使用 Game1.createItemDebris 生成掉落物</item>
    ///   <item>从背包中扣减对应数量（Stack 归零则移除）</item>
    /// </list>
    ///
    /// <para><b>返回 data：</b></para>
    /// <code>{ intent: "drop", item_name: string, count: int, position: { x: int, y: int } }</code>
    ///
    /// <para><b>可能的错误码：</b></para>
    /// <list type="bullet">
    ///   <item><c>INVALID_PARAMS</c>: 缺少 item 参数</item>
    ///   <item><c>ITEM_NOT_FOUND</c>: 背包中找不到指定物品</item>
    /// </list>
    /// </summary>
    private V2Result HandleDrop(V2Command cmd)
    {
        // ── 解析物品名 ──
        var itemName = GetStringParam(cmd.Params, "item")
            ?? GetStringParam(cmd.Params, "item_name");
        if (string.IsNullOrWhiteSpace(itemName))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "drop 需要 item 或 item_name 参数");
        }

        // ── 解析数量（默认 1） ──
        int dropCount = TryGetIntParam(cmd.Params, "count", out var c) ? c : 1;
        if (dropCount <= 0) dropCount = 1;

        // ── 查找物品 ──
        var player = Game1.player;
        StardewValley.Item? targetItem = null;
        int targetSlot = -1;

        for (int i = 0; i < player.Items.Count; i++)
        {
            var item = player.Items[i];
            if (item != null &&
                (item.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) ||
                 item.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)))
            {
                targetItem = item;
                targetSlot = i;
                break;
            }
        }

        if (targetItem == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ItemNotFound,
                $"背包中找不到: {itemName}");
        }

        // ── 计算实际丢弃数量 ──
        int actualDrop = Math.Min(dropCount, targetItem.Stack);
        var droppedName = targetItem.DisplayName;
        var location = Game1.currentLocation;
        var playerPos = player.getStandingPosition();
        int facingDir = player.FacingDirection;

        // ── 创建掉落物 ──
        if (actualDrop >= targetItem.Stack)
        {
            // 丢弃全部：直接用原物品创建 debris
            player.Items[targetSlot] = null;
            Game1.createItemDebris(targetItem, playerPos, facingDir, location);
        }
        else
        {
            // 丢弃部分：克隆一份设置 Stack，原物品减少
            var cloned = targetItem.getOne();
            cloned.Stack = actualDrop;
            targetItem.Stack -= actualDrop;
            if (targetItem.Stack <= 0)
            {
                player.Items[targetSlot] = null;
            }
            Game1.createItemDebris(cloned, playerPos, facingDir, location);
        }

        var playerTile = player.Tile;
        return ResultBuilder.Success(
                cmd.Seq,
                cmd.ActionId,
                new
                {
                    intent = "drop",
                    item_name = droppedName,
                    count = actualDrop,
                    position = new { x = (int)playerTile.X, y = (int)playerTile.Y },
                })
            .WithObservation($"已丢弃 {droppedName} x{actualDrop}")
            .WithSideEffect("inventory_changed", $"背包移除 {droppedName} x{actualDrop}");
    }

    /// <summary>
    /// 处理 craft intent — 合成物品。
    ///
    /// <para><b>参数：</b></para>
    /// <list type="bullet">
    ///   <item><c>recipe</c> 或 <c>item</c> (string, 必填): 配方/物品名称，支持模糊匹配</item>
    ///   <item><c>count</c> (int, 可选, 默认 1): 合成数量</item>
    /// </list>
    ///
    /// <para><b>行为说明：</b></para>
    /// <list type="bullet">
    ///   <item>按名称查找玩家已学会的配方（精确匹配优先，再模糊匹配）</item>
    ///   <item>检查背包材料是否充足</item>
    ///   <item>循环合成：消耗材料 → 创建物品 → 加入背包</item>
    ///   <item>背包满时中止并报告已完成数量</item>
    /// </list>
    ///
    /// <para><b>返回 data：</b></para>
    /// <code>{ intent: "craft", recipe_name: string, item_created: string, count_crafted: int }</code>
    ///
    /// <para><b>可能的错误码：</b></para>
    /// <list type="bullet">
    ///   <item><c>ITEM_NOT_FOUND</c>: 找不到配方或玩家未学会</item>
    ///   <item><c>ITEM_INSUFFICIENT</c>: 材料不足</item>
    ///   <item><c>INVENTORY_FULL</c>: 背包已满</item>
    ///   <item><c>EXECUTION_FAILED</c>: 合成操作失败</item>
    /// </list>
    /// </summary>
    private V2Result HandleCraft(V2Command cmd)
    {
        var player = Game1.player;

        // ── 解析配方名（支持 recipe / item / item_name） ──
        var recipeName = GetStringParam(cmd.Params, "recipe")
            ?? GetStringParam(cmd.Params, "item")
            ?? GetStringParam(cmd.Params, "item_name");
        if (string.IsNullOrWhiteSpace(recipeName))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "craft 需要 recipe 或 item 参数");
        }

        // ── 解析数量（默认 1） ──
        int count = TryGetIntParam(cmd.Params, "count", out var c) ? c : 1;
        if (count <= 0) count = 1;

        // ── 查找配方 ──
        var foundKey = FindRecipeByName(recipeName);
        if (foundKey == null)
        {
            // 列出前 10 个已知配方帮助用户
            var knownRecipes = player.craftingRecipes.Keys.Take(10).ToList();
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ItemNotFound,
                $"找不到配方: {recipeName}。已知配方（前10个）: {string.Join(", ", knownRecipes)}");
        }

        // ── 创建 CraftingRecipe 实例 ──
        CraftingRecipe recipe;
        try
        {
            recipe = new CraftingRecipe(foundKey);
        }
        catch (Exception ex)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"创建配方实例失败: {foundKey} — {ex.Message}");
        }

        // ── 检查材料是否充足 ──
        if (!recipe.doesFarmerHaveIngredientsInInventory())
        {
            // 构建缺少材料的详情
            var missing = new List<string>();
            foreach (var kv in recipe.recipeList)
            {
                var ingredientId = kv.Key;
                var required = kv.Value;
                int have = 0;
                foreach (var item in player.Items)
                {
                    if (item != null && item.ItemId == ingredientId)
                        have += item.Stack;
                }
                if (have < required)
                {
                    // 尝试获取材料显示名
                    var displayName = ingredientId;
                    try
                    {
                        var parsed = ItemRegistry.Create(ingredientId);
                        if (parsed != null) displayName = parsed.DisplayName;
                    }
                    catch { }
                    missing.Add($"{displayName}: 需要{required}, 拥有{have}");
                }
            }
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ItemInsufficient,
                $"合成 {recipe.DisplayName} 材料不足: {string.Join("; ", missing)}");
        }

        // ── 循环合成 ──
        int crafted = 0;
        string? createdItemName = recipe.DisplayName;
        float energyBefore = player.Stamina;

        for (int i = 0; i < count; i++)
        {
            // 每轮检查材料
            if (!recipe.doesFarmerHaveIngredientsInInventory())
                break;

            // 消耗材料
            recipe.consumeIngredients(null);

            // 创建产出物品
            Item createdItem = recipe.createItem();
            createdItemName = createdItem.DisplayName;

            // 加入背包（材料已消耗，必须确保产出物品不丢失）
            var leftover = player.addItemToInventory(createdItem);
            if (leftover != null)
            {
                // 背包满 — 将物品掉落到地面，防止材料白白消耗
                Game1.createItemDebris(leftover, player.getStandingPosition(), -1);
                _monitor.Log($"[V2] craft: 背包满，{leftover.DisplayName} 已掉落到地面", LogLevel.Warn);
                crafted++; // 合成成功了，只是放不进背包
                // 更新合成计数
                if (player.craftingRecipes.ContainsKey(foundKey))
                    player.craftingRecipes[foundKey] += 1;
                break; // 停止后续合成
            }

            crafted++;

            // 更新合成计数（成就统计）
            if (player.craftingRecipes.ContainsKey(foundKey))
                player.craftingRecipes[foundKey] += 1;
        }

        return ResultBuilder.Success(
                cmd.Seq,
                cmd.ActionId,
                new
                {
                    intent = "craft",
                    recipe_name = foundKey,
                    item_created = createdItemName,
                    count_crafted = crafted,
                    count_requested = count,
                })
            .WithObservation($"合成了 {createdItemName} x{crafted}")
            .WithSideEffect("inventory_changed", $"合成 {createdItemName} x{crafted}")
            .WithSideEffectIfEnergyChanged(energyBefore);
    }

    /// <summary>
    /// 按名称模糊查找玩家已学会的合成配方。
    /// 精确匹配优先（DisplayName 或内部名），然后部分匹配（Contains）。
    /// </summary>
    /// <returns>匹配的内部配方键名，或 null。</returns>
    private static string? FindRecipeByName(string query)
    {
        var player = Game1.player;
        string? partialMatch = null;

        foreach (var key in player.craftingRecipes.Keys)
        {
            CraftingRecipe recipe;
            try { recipe = new CraftingRecipe(key); }
            catch { continue; }

            // 精确匹配（DisplayName 或内部名）
            if (recipe.DisplayName.Equals(query, StringComparison.OrdinalIgnoreCase) ||
                key.Equals(query, StringComparison.OrdinalIgnoreCase))
            {
                return key;
            }

            // 部分匹配（记录第一个）
            if (partialMatch == null &&
                (recipe.DisplayName.Contains(query, StringComparison.OrdinalIgnoreCase) ||
                 key.Contains(query, StringComparison.OrdinalIgnoreCase)))
            {
                partialMatch = key;
            }
        }

        return partialMatch;
    }

    /// <summary>
    /// 处理 sleep intent — 在当前位置触发睡觉（必须在家中且靠近床）。
    ///
    /// <para><b>参数：</b>无</para>
    ///
    /// <para><b>前置条件：</b></para>
    /// <list type="bullet">
    ///   <item>玩家必须在 FarmHouse 或 Cabin（家）中</item>
    ///   <item>玩家必须在床的 2 tile 范围内</item>
    /// </list>
    ///
    /// <para><b>行为说明：</b></para>
    /// <list type="bullet">
    ///   <item>检查玩家是否在家中</item>
    ///   <item>在 location.furniture 中查找 BedFurniture</item>
    ///   <item>检查距离（2 tile 内）</item>
    ///   <item>调用 BedFurniture.checkForAction 触发睡觉交互</item>
    ///   <item>注意：这会打开睡觉确认对话框，需要后续 answer intent 确认</item>
    /// </list>
    ///
    /// <para><b>返回 data：</b></para>
    /// <code>{ intent: "sleep", location: string, bed_position: { x: int, y: int } }</code>
    ///
    /// <para><b>可能的错误码：</b></para>
    /// <list type="bullet">
    ///   <item><c>NOT_SUPPORTED</c>: 不在家中</item>
    ///   <item><c>TARGET_NOT_FOUND</c>: 找不到床家具</item>
    ///   <item><c>OUT_OF_RANGE</c>: 距离床太远</item>
    ///   <item><c>EXECUTION_FAILED</c>: 睡觉触发失败</item>
    /// </list>
    ///
    /// <para><b>提示：</b>如果需要自动回家并睡觉，请使用 go_bed capability。</para>
    /// </summary>
    private V2Result HandleSleep(V2Command cmd)
    {
        var location = Game1.currentLocation;
        if (location == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                "当前没有可用地图");
        }

        // ── 检查是否在家中（FarmHouse 或 Cabin） ──
        bool isHome = location is StardewValley.Locations.FarmHouse;
        if (!isHome)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.NotSupported,
                $"sleep 需要在家中（FarmHouse/Cabin）执行，当前位置: {location.NameOrUniqueName}。" +
                "请使用 go_bed capability 自动回家睡觉");
        }

        // ── 查找床 ──
        BedFurniture? bed = null;
        foreach (var furniture in location.furniture)
        {
            if (furniture is BedFurniture bedFurniture && bedFurniture.bedType != BedFurniture.BedType.Child)
            {
                bed = bedFurniture;
                break;
            }
        }

        if (bed == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.TargetNotFound,
                "在当前房屋中找不到床");
        }

        // ── 距离检查 ──
        var bedTile = bed.TileLocation;
        var playerTile = Game1.player.Tile;
        var distance = Vector2.Distance(playerTile, bedTile);
        if (distance > InteractionRange)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.OutOfRange,
                $"距离床太远 ({distance:F1} tiles)，请先走到床边。床位置: ({(int)bedTile.X},{(int)bedTile.Y})");
        }

        // ── 面向床并触发交互 ──
        Game1.player.faceGeneralDirection(new Vector2(bedTile.X * 64f + 32f, bedTile.Y * 64f + 32f));
        bool interacted = bed.checkForAction(Game1.player);

        if (!interacted)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                "无法与床交互，可能有对话框或菜单正在打开");
        }

        return ResultBuilder.Success(
                cmd.Seq,
                cmd.ActionId,
                new
                {
                    intent = "sleep",
                    location = location.NameOrUniqueName,
                    bed_position = new { x = (int)bedTile.X, y = (int)bedTile.Y },
                })
            .WithObservation("已触发睡觉交互，请确认睡觉对话框（使用 answer intent）");
    }

    private V2Result? HandleStore(V2Command cmd)
    {
        var itemName = GetStringParam(cmd.Params, "item")
            ?? GetStringParam(cmd.Params, "item_name");
        if (string.IsNullOrEmpty(itemName))
        {
            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "store 缺少 item 或 item_name 参数");
        }

        int count = TryGetIntParam(cmd.Params, "count", out var c) ? c : -1;

        // Route 1: container_id — cross-map via ContainerManager
        var containerId = GetStringParam(cmd.Params, "container_id");
        if (!string.IsNullOrEmpty(containerId))
        {
            var containerManager = new ContainerManager(_monitor);
            var result = containerManager.StoreItem(containerId, itemName, count);
            if (!result.Success)
            {
                return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
                    result.ErrorCode ?? ResultBuilder.Errors.ExecutionFailed,
                    $"存入失败: {result.ErrorCode}");
            }

            return ResultBuilder.Success(cmd.Seq, cmd.ActionId, new
            {
                intent = "store",
                container_id = containerId,
                item = itemName,
                transferred = result.Transferred,
                remaining_in_inventory = result.RemainingCount,
            })
            .WithObservation($"向容器 {containerId} 存入 {itemName} x{result.Transferred}")
            .WithSideEffect("inventory_changed", $"背包移除 {itemName} x{result.Transferred}");
        }

        // Route 2: x/y tile — current map via ActionExecutor
        if (!TryGetTile(cmd.Params, out var tile))
        {
            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "store 需要 container_id 或 x/y 参数");
        }

        var rangeCheck = CheckTileDistance(cmd, tile);
        if (rangeCheck != null) return rangeCheck;

        // Compute actual transfer count before calling executor (mirrors ActionExecutor logic)
        var player = Game1.player;
        var sourceItem = player.Items.FirstOrDefault(i =>
            i != null &&
            (i.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) ||
             i.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)));
        int actualTransferred = 0;
        if (sourceItem != null)
        {
            actualTransferred = (count <= 0 || count >= sourceItem.Stack) ? sourceItem.Stack : count;
        }

        var (success, errorCode) = _executor.StoreInChest(tile.X, tile.Y, itemName, count);
        if (!success)
        {
            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
                errorCode ?? ResultBuilder.Errors.ExecutionFailed,
                $"存入失败: {errorCode}");
        }

        return ResultBuilder.Success(cmd.Seq, cmd.ActionId, new
        {
            intent = "store",
            target = new { x = tile.X, y = tile.Y },
            item = itemName,
            transferred = actualTransferred,
        })
        .WithObservation($"向 ({tile.X},{tile.Y}) 箱子存入 {itemName} x{actualTransferred}")
        .WithSideEffect("inventory_changed", $"背包移除 {itemName} x{actualTransferred}");
    }

    private V2Result? HandleTake(V2Command cmd)
    {
        var itemName = GetStringParam(cmd.Params, "item")
            ?? GetStringParam(cmd.Params, "item_name");
        if (string.IsNullOrEmpty(itemName))
        {
            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "take 缺少 item 或 item_name 参数");
        }

        int count = TryGetIntParam(cmd.Params, "count", out var c) ? c : -1;

        // Route 1: container_id — cross-map via ContainerManager
        var containerId = GetStringParam(cmd.Params, "container_id");
        if (!string.IsNullOrEmpty(containerId))
        {
            var containerManager = new ContainerManager(_monitor);
            var result = containerManager.TakeItem(containerId, itemName, count);
            if (!result.Success)
            {
                return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
                    result.ErrorCode ?? ResultBuilder.Errors.ExecutionFailed,
                    $"取出失败: {result.ErrorCode}");
            }

            return ResultBuilder.Success(cmd.Seq, cmd.ActionId, new
            {
                intent = "take",
                container_id = containerId,
                item = itemName,
                transferred = result.Transferred,
                remaining_in_container = result.RemainingCount,
            })
            .WithObservation($"从容器 {containerId} 取出 {itemName} x{result.Transferred}")
            .WithSideEffect("inventory_changed", $"背包新增 {itemName} x{result.Transferred}");
        }

        // Route 2: x/y tile — current map via ActionExecutor
        if (!TryGetTile(cmd.Params, out var tile))
        {
            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "take 需要 container_id 或 x/y 参数");
        }

        var rangeCheck = CheckTileDistance(cmd, tile);
        if (rangeCheck != null) return rangeCheck;

        var (success, errorCode) = _executor.TakeFromChest(tile.X, tile.Y, itemName, count);
        if (!success)
        {
            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
                errorCode ?? ResultBuilder.Errors.ExecutionFailed,
                $"取出失败: {errorCode}");
        }

        return ResultBuilder.Success(cmd.Seq, cmd.ActionId, new
        {
            intent = "take",
            target = new { x = tile.X, y = tile.Y },
            item = itemName,
        })
        .WithObservation($"从 ({tile.X},{tile.Y}) 箱子取出 {itemName}")
        .WithSideEffect("inventory_changed", $"背包新增 {itemName}");
    }

    private V2Result? HandleShip(V2Command cmd)
    {
        var itemName = GetStringParam(cmd.Params, "item")
            ?? GetStringParam(cmd.Params, "item_name");
        if (string.IsNullOrEmpty(itemName))
        {
            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "ship 缺少 item 或 item_name 参数");
        }

        int count = TryGetIntParam(cmd.Params, "count", out var c) ? c : -1;

        var success = _executor.ShipItem(itemName, count);
        if (!success)
        {
            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"出货失败: 背包中找不到 '{itemName}' 或无法访问农场");
        }

        return ResultBuilder.Success(cmd.Seq, cmd.ActionId, new
        {
            intent = "ship",
            item = itemName,
            count,
        })
        .WithObservation($"已出货 {itemName}")
        .WithSideEffect("inventory_changed", $"背包移除 {itemName}");
    }

    private bool EnsureToolReady(string itemName)
    {
        var currentTool = Game1.player.CurrentTool;
        if (currentTool != null &&
            (currentTool.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) ||
             currentTool.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)))
        {
            return true;
        }

        if (_executor.EquipItemByName(itemName).Success)
        {
            return true;
        }

        _monitor.Log($"[V2] 无法装备工具: {itemName}", LogLevel.Debug);
        return false;
    }

    private static bool TryGetTile(Dictionary<string, object> parameters, out Point tile)
    {
        if (TryGetIntParam(parameters, "x", out var x) &&
            TryGetIntParam(parameters, "y", out var y))
        {
            tile = new Point(x, y);
            return true;
        }

        if (TryGetNestedPoint(parameters, "target", out tile))
        {
            return true;
        }

        if (TryGetNestedPoint(parameters, "tile", out tile))
        {
            return true;
        }

        tile = default;
        return false;
    }

    private static bool TryGetNestedPoint(Dictionary<string, object> parameters, string key, out Point tile)
    {
        tile = default;
        if (!TryGetObject(parameters, key, out var nested))
        {
            return false;
        }

        if (!TryGetIntParam(nested, "x", out var x) || !TryGetIntParam(nested, "y", out var y))
        {
            return false;
        }

        tile = new Point(x, y);
        return true;
    }

    private static string? GetStringParam(Dictionary<string, object> parameters, string key)
    {
        if (!parameters.TryGetValue(key, out var raw))
        {
            return null;
        }

        return TryReadString(raw, out var value) ? value : raw?.ToString();
    }

    private static string? GetNestedString(Dictionary<string, object> parameters, string outerKey, string innerKey)
    {
        if (!TryGetObject(parameters, outerKey, out var nested))
        {
            return null;
        }

        if (!nested.TryGetValue(innerKey, out var innerRaw))
        {
            return null;
        }

        return TryReadString(innerRaw, out var value) ? value : innerRaw?.ToString();
    }

    private static bool TryGetIntParam(Dictionary<string, object> parameters, string key, out int value)
    {
        if (!parameters.TryGetValue(key, out var raw))
        {
            value = default;
            return false;
        }

        return TryConvertToInt(raw, out value);
    }

    private static bool TryConvertToInt(object? raw, out int value)
    {
        try
        {
            switch (raw)
            {
                case int i:
                    value = i;
                    return true;
                case long l when l >= int.MinValue && l <= int.MaxValue:
                    value = (int)l;
                    return true;
                case float f when f >= int.MinValue && f <= int.MaxValue:
                    value = (int)f;
                    return true;
                case double d when d >= int.MinValue && d <= int.MaxValue:
                    value = (int)d;
                    return true;
                case string s when int.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed):
                    value = parsed;
                    return true;
                case JsonElement element when element.ValueKind == JsonValueKind.Number:
                    if (element.TryGetInt32(out var intValue))
                    {
                        value = intValue;
                        return true;
                    }

                    if (element.TryGetInt64(out var longValue) && longValue >= int.MinValue && longValue <= int.MaxValue)
                    {
                        value = (int)longValue;
                        return true;
                    }

                    var dbl = element.GetDouble();
                    if (dbl >= int.MinValue && dbl <= int.MaxValue)
                    {
                        value = (int)dbl;
                        return true;
                    }

                    value = default;
                    return false;
                case JsonElement element when element.ValueKind == JsonValueKind.String:
                    if (int.TryParse(element.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsedJson))
                    {
                        value = parsedJson;
                        return true;
                    }

                    value = default;
                    return false;
                default:
                    value = default;
                    return false;
            }
        }
        catch (OverflowException)
        {
            value = default;
            return false;
        }
    }

    private static bool TryGetObject(Dictionary<string, object> parameters, string key, out Dictionary<string, object> nested)
    {
        nested = new Dictionary<string, object>();
        if (!parameters.TryGetValue(key, out var raw))
        {
            return false;
        }

        if (raw is Dictionary<string, object> dict)
        {
            nested = dict;
            return true;
        }

        if (raw is JsonElement element && element.ValueKind == JsonValueKind.Object)
        {
            nested = DeserializeObject(element);
            return true;
        }

        return false;
    }

    private static bool TryReadString(object? raw, out string? value)
    {
        switch (raw)
        {
            case null:
                value = null;
                return false;
            case string s:
                value = s;
                return true;
            case JsonElement element when element.ValueKind == JsonValueKind.String:
                value = element.GetString();
                return true;
            case JsonElement element when element.ValueKind == JsonValueKind.Number:
                value = element.ToString();
                return true;
            case JsonElement element when element.ValueKind == JsonValueKind.True:
                value = bool.TrueString;
                return true;
            case JsonElement element when element.ValueKind == JsonValueKind.False:
                value = bool.FalseString;
                return true;
            default:
                value = raw.ToString();
                return !string.IsNullOrWhiteSpace(value);
        }
    }

    private static Dictionary<string, object> DeserializeObject(JsonElement element)
    {
        var result = new Dictionary<string, object>();
        foreach (var prop in element.EnumerateObject())
        {
            var value = DeserializeValue(prop.Value);
            if (value != null)
            {
                result[prop.Name] = value;
            }
        }

        return result;
    }

    private static object? DeserializeValue(JsonElement element)
    {
        return element.ValueKind switch
        {
            JsonValueKind.String => element.GetString(),
            JsonValueKind.Number when element.TryGetInt64(out var longValue) => longValue,
            JsonValueKind.Number => element.GetDouble(),
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.Object => DeserializeObject(element),
            JsonValueKind.Array => element.EnumerateArray().Select(DeserializeValue).Where(value => value != null).ToList(),
            _ => null,
        };
    }

    private static bool EnsureHoeDirtWithCrop(Point tile)
    {
        var location = Game1.currentLocation;
        if (location == null)
        {
            return false;
        }

        var key = new Vector2(tile.X, tile.Y);
        if (!location.terrainFeatures.TryGetValue(key, out var feature) || feature is not HoeDirt dirt)
        {
            return false;
        }

        return dirt.crop != null;
    }

    private static bool IsHarvestableCrop(Point tile)
    {
        var location = Game1.currentLocation;
        if (location == null)
        {
            return false;
        }

        var key = new Vector2(tile.X, tile.Y);
        if (!location.terrainFeatures.TryGetValue(key, out var feature) || feature is not HoeDirt dirt)
        {
            return false;
        }

        return dirt.crop != null && dirt.readyForHarvest();
    }

    private static bool HasAnyInteractableTarget(GameLocation location, Point tile)
    {
        var key = new Vector2(tile.X, tile.Y);
        if (location.objects.ContainsKey(key))
        {
            return true;
        }

        return location.characters.Any(npc =>
            !npc.IsInvisible &&
            (int)npc.Tile.X == tile.X &&
            (int)npc.Tile.Y == tile.Y);
    }

    private static NPC? FindNpc(GameLocation location, string npcName)
    {
        var npc = location.characters.FirstOrDefault(npc =>
            npc.Name.Equals(npcName, StringComparison.OrdinalIgnoreCase) ||
            npc.displayName.Equals(npcName, StringComparison.OrdinalIgnoreCase));

        if (npc != null)
        {
            return npc;
        }

        var globalNpc = Game1.getCharacterFromName(npcName);
        return globalNpc != null && globalNpc.currentLocation == location ? globalNpc : null;
    }
}

internal static class V2ResultExtensions
{
    public static V2Result WithSideEffectIfEnergyChanged(this V2Result result, float energyBefore)
    {
        var energyAfter = Game1.player.Stamina;
        if (Math.Abs(energyAfter - energyBefore) > 0.0001f)
        {
            result.WithSideEffect(
                "energy_changed",
                $"Stamina {energyBefore} -> {energyAfter}");
        }

        return result;
    }
}
