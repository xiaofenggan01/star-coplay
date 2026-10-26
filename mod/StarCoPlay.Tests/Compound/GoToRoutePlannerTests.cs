// GoToRoutePlannerTests.cs
//
// 注意：GoToRoutePlanner.Plan 对跨地图路径直接调用 StarCoPlay.MapData.FindRoute（静态方法），
// 无接口隔离，无法在容器内不依赖 Game1 的情况下 mock MapData。
//
// 因此本文件只测试"同地图直达快路径"（不触碰 MapData）。
// 跨地图用例标注为 P1，留待后续抽象出 IRouteProvider 接口后补充。
//
// 框架：NUnit 3.x（与 NameSimilarityTests 保持一致）

using System.Collections.Generic;
using NUnit.Framework;
using StarCoPlay.V2.Compound;

namespace StarCoPlay.Tests.Compound;

/// <summary>
/// GoToRoutePlanner 单元测试。
/// 仅覆盖无需 MapData / Game1 的纯计算路径。
/// </summary>
[TestFixture]
public class GoToRoutePlannerTests
{
    // ═══════════════════════════════════════════════════════════════
    // P0：同地图直达（fromMap == toMap）
    // ═══════════════════════════════════════════════════════════════

    [Test]
    public void Plan_SameMap_Returns1IntraMapSegment()
    {
        var segments = GoToRoutePlanner.Plan(
            "Farm", 10, 10,
            "Farm", 20, 30);

        Assert.That(segments, Is.Not.Null);
        Assert.That(segments!.Count, Is.EqualTo(1));
        Assert.That(segments[0].Type, Is.EqualTo(SegmentType.IntraMap));
    }

    [Test]
    public void Plan_SameMap_SegmentHasCorrectCoords()
    {
        var segments = GoToRoutePlanner.Plan(
            "Town", 5, 5,
            "Town", 15, 25);

        var seg = segments![0];
        Assert.That(seg.SourceMap, Is.EqualTo("Town"));
        Assert.That(seg.TargetMap, Is.EqualTo("Town"));
        Assert.That(seg.SourceX, Is.EqualTo(5));
        Assert.That(seg.SourceY, Is.EqualTo(5));
        Assert.That(seg.TargetX, Is.EqualTo(15));
        Assert.That(seg.TargetY, Is.EqualTo(25));
    }

    [Test]
    public void Plan_SameMap_CaseInsensitive()
    {
        // fromMap/toMap 大小写不敏感
        var segments = GoToRoutePlanner.Plan(
            "farm", 0, 0,
            "FARM", 5, 5);

        Assert.That(segments, Is.Not.Null);
        Assert.That(segments!.Count, Is.EqualTo(1));
        Assert.That(segments[0].Type, Is.EqualTo(SegmentType.IntraMap));
    }

    [Test]
    public void Plan_SameMap_ZeroDistance_ReturnsSegmentWithSameStartEnd()
    {
        // 起终坐标相同（零距离），应仍然返回 1 个段（执行层自行跳过）
        var segments = GoToRoutePlanner.Plan(
            "FarmHouse", 7, 7,
            "FarmHouse", 7, 7);

        Assert.That(segments, Is.Not.Null);
        Assert.That(segments!.Count, Is.EqualTo(1));
        var seg = segments[0];
        Assert.That(seg.SourceX, Is.EqualTo(seg.TargetX));
        Assert.That(seg.SourceY, Is.EqualTo(seg.TargetY));
    }

    [Test]
    public void Plan_SameMap_DirectionIsNull()
    {
        // IntraMap 段不需要方向
        var segments = GoToRoutePlanner.Plan("Farm", 0, 0, "Farm", 10, 10);
        Assert.That(segments![0].Direction, Is.Null);
    }

    // ═══════════════════════════════════════════════════════════════
    // P1 占位（依赖 MapData，暂跳过）
    // ═══════════════════════════════════════════════════════════════

    // [Test] — P1，需要 IRouteProvider 接口后补充
    // public void Plan_CrossMap_3Hops_Returns5Segments() { /* TODO */ }

    // [Test] — P1
    // public void Plan_CrossMap_FindRouteReturnsNull_Returns_Null() { /* TODO */ }

    // [Test] — P1
    // public void Plan_CrossMap_EdgeWarpSegment_HasDirection() { /* TODO */ }
}
