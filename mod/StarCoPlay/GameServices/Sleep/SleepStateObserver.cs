using StardewValley;
using StardewValley.Menus;

namespace StarCoPlay.GameServices.Sleep;

internal class SleepStateObserver
{
    /// <summary>当前是否显示 Sleep 确认对话框</summary>
    public bool IsSleepPromptOpen()
    {
        return Game1.activeClickableMenu is DialogueBox db
               && Game1.currentLocation?.lastQuestionKey == "Sleep"
               && db.isQuestion;
    }

    /// <summary>游戏已进入日结流程</summary>
    public bool IsNewDayStarted()
    {
        return Game1.newDay;
    }

    /// <summary>玩家是否可操作（非 frozen、非 cutscene、非 event）</summary>
    public bool IsPlayerOperable()
    {
        return Game1.player.CanMove
               && !Game1.eventUp
               && !Game1.dialogueUp
               && Game1.activeClickableMenu == null;
    }

    /// <summary>日期是否已推进（对比记录的日期）</summary>
    public bool HasDayAdvanced(int previousDay, int previousYear)
    {
        return Game1.dayOfMonth != previousDay || Game1.year != previousYear;
    }

    /// <summary>获取当前日期信息</summary>
    public (int day, string season, int year) GetCurrentDate()
    {
        return (Game1.dayOfMonth, Game1.currentSeason, Game1.year);
    }

    /// <summary>获取当前时间</summary>
    public int GetCurrentTime() => Game1.timeOfDay;

    /// <summary>检查当前活跃菜单类型名</summary>
    public string? GetActiveMenuTypeName()
    {
        return Game1.activeClickableMenu?.GetType().Name;
    }

    /// <summary>是否 isInBed</summary>
    public bool IsPlayerInBed()
    {
        return Game1.player.isInBed.Value;
    }

    /// <summary>
    /// 严格判定睡后流程是否完全结束。
    /// 必须同时满足：日期已推进、无活跃菜单、无 farmEvent、不在 endOfNight 流程中、玩家可操作。
    /// </summary>
    public bool IsPostSleepComplete(int beforeDay, int beforeYear)
    {
        // 1. 日期已推进
        if (!HasDayAdvanced(beforeDay, beforeYear))
            return false;

        // 2. 无活跃菜单
        if (Game1.activeClickableMenu != null)
            return false;

        // 3. 无 farmEvent（齐先生、女巫、仙子等）
        if (Game1.farmEvent != null)
            return false;

        // 4. 不在 showingEndOfNightStuff 流程中
        if (Game1.showingEndOfNightStuff)
            return false;

        // 5. 玩家可操作
        if (!Game1.player.CanMove)
            return false;

        // 6. 不在事件/对话/cutscene 中
        if (Game1.eventUp || Game1.dialogueUp)
            return false;

        return true;
    }
}
