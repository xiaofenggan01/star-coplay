// TargetRefResolverParseTests.cs
//
// 只测试 TargetRefResolver 的静态解析逻辑（字符串 split / 格式校验）。
// 实际 Resolve() 依赖 Game1.locations / getFarm() 等运行时状态，
// 无法在容器内独立运行——相关用例全部跳过，标注 P1。
//
// 可独立测试的方法：TargetRefResolver.IsTargetRef（公开）
// 解析逻辑（colonIndex / underscoreIndex split）通过 IsTargetRef 间接覆盖。
//
// 框架：NUnit 3.x

using NUnit.Framework;
using StarCoPlay.V2;

namespace StarCoPlay.Tests.V2;

/// <summary>
/// 测试 TargetRefResolver 中可独立运行的纯字符串解析逻辑。
/// </summary>
[TestFixture]
public class TargetRefResolverParseTests
{
    // ═══════════════════════════════════════════════════════════════
    // IsTargetRef — 格式识别
    // ═══════════════════════════════════════════════════════════════

    [Test]
    public void IsTargetRef_ValidAnimal_ReturnsTrue()
    {
        Assert.That(TargetRefResolver.IsTargetRef("Farm:animal_甜豆"), Is.True);
    }

    [Test]
    public void IsTargetRef_ValidNpc_ReturnsTrue()
    {
        Assert.That(TargetRefResolver.IsTargetRef("Town:npc_Abigail"), Is.True);
    }

    [Test]
    public void IsTargetRef_ValidCrop_ReturnsTrue()
    {
        Assert.That(TargetRefResolver.IsTargetRef("Farm:crop_12_15"), Is.True);
    }

    [Test]
    public void IsTargetRef_ValidTile_ReturnsTrue()
    {
        Assert.That(TargetRefResolver.IsTargetRef("Farm:tile_5_8"), Is.True);
    }

    [Test]
    public void IsTargetRef_GuidLocationName_ReturnsTrue()
    {
        // 含 GUID 的完整地图名
        Assert.That(
            TargetRefResolver.IsTargetRef(
                "Coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2:animal_樱桃"),
            Is.True);
    }

    [Test]
    public void IsTargetRef_NoColon_ReturnsFalse()
    {
        Assert.That(TargetRefResolver.IsTargetRef("Farm_animal_甜豆"), Is.False);
    }

    [Test]
    public void IsTargetRef_NoUnderscore_ReturnsFalse()
    {
        Assert.That(TargetRefResolver.IsTargetRef("Farm:animal"), Is.False);
    }

    [Test]
    public void IsTargetRef_ColonAtEnd_ReturnsFalse()
    {
        // 冒号在末尾，冒号后无内容
        Assert.That(TargetRefResolver.IsTargetRef("Farm:"), Is.False);
    }

    [Test]
    public void IsTargetRef_NullInput_ReturnsFalse()
    {
        Assert.That(TargetRefResolver.IsTargetRef(null), Is.False);
    }

    [Test]
    public void IsTargetRef_EmptyString_ReturnsFalse()
    {
        Assert.That(TargetRefResolver.IsTargetRef(""), Is.False);
    }

    [Test]
    public void IsTargetRef_WhitespaceOnly_ReturnsFalse()
    {
        Assert.That(TargetRefResolver.IsTargetRef("   "), Is.False);
    }

    [Test]
    public void IsTargetRef_UnderscoreBeforeColon_ReturnsFalse()
    {
        // 格式要求：冒号后才能有下划线
        Assert.That(TargetRefResolver.IsTargetRef("Farm_loc:animal"), Is.False);
    }

    // ═══════════════════════════════════════════════════════════════
    // Resolve — 格式错误快路径（不依赖运行时，直接返回 null）
    // ═══════════════════════════════════════════════════════════════

    [Test]
    public void Resolve_NullInput_ReturnsNull()
    {
        var result = TargetRefResolver.Resolve(null!);
        Assert.That(result, Is.Null);
    }

    [Test]
    public void Resolve_EmptyString_ReturnsNull()
    {
        var result = TargetRefResolver.Resolve("");
        Assert.That(result, Is.Null);
    }

    [Test]
    public void Resolve_WhitespaceOnly_ReturnsNull()
    {
        var result = TargetRefResolver.Resolve("   ");
        Assert.That(result, Is.Null);
    }

    [Test]
    public void Resolve_NoColon_ReturnsNull()
    {
        var result = TargetRefResolver.Resolve("Farm_animal_甜豆");
        Assert.That(result, Is.Null);
    }

    [Test]
    public void Resolve_NoUnderscore_ReturnsNull()
    {
        // entityPart 中无下划线 → 无法解析 entityKind
        var result = TargetRefResolver.Resolve("Farm:animal");
        Assert.That(result, Is.Null);
    }

    // ═══════════════════════════════════════════════════════════════
    // P1 占位：需要 Game1 运行时的用例（不可在容器内独立运行）
    // ═══════════════════════════════════════════════════════════════

    // [Test] — P1，需要 Game1.getFarm()
    // public void Resolve_Animal_KnownName_ReturnsResult() { /* TODO */ }

    // [Test] — P1，需要 Game1.getCharacterFromName
    // public void Resolve_Npc_KnownName_ReturnsResult() { /* TODO */ }

    // [Test] — P1，需要 Game1.locations
    // public void Resolve_CoordEntity_ValidFormat_ReturnsResult() { /* TODO */ }

    // [Test] — P1
    // public void Resolve_UnknownKind_ReturnsNull() { /* TODO（但格式有效时才能进到 switch）*/ }
}
