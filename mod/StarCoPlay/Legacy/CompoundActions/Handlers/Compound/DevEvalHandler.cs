using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// Dev 模式运行时反射 REPL — 通过 WS 命令探索 SDV/SMAPI API。
/// 仅在 PlatformToken=="dev" 时注册。不用于生产。
///
/// 支持 3 种操作：
/// 1. get:  读取属性链（如 "Game1.currentLocation.doors.Count"）
/// 2. list: 列举对象的属性/字段名+类型+值摘要
/// 3. call: 调用方法，两种模式：
///    a. 静态方法（旧）：{"method":"MapData.FindRoute","args":["Town","SeedShop"]}
///    b. 实例方法（新）：{"expr":"Game1.currentLocation","method":"isTilePassable","args":["13","8"]}
///       expr 用 get 的属性链解析逻辑得到目标对象，然后在其类型上反射查找方法。
///       支持参数类型自动转换：string/int/long/float/double/bool/enum。
///
/// 同步执行：Start 直接返回 V2Result，无需 Tick。
/// </summary>
public class DevEvalHandler : ICompoundHandler
{
    private readonly IMonitor _monitor;

    /// <summary>最大值预览字符数，超过则截断。</summary>
    private const int MaxValueLength = 200;

    /// <summary>已知根对象白名单（安全名单，防止任意反射调用）。</summary>
    private static readonly Dictionary<string, Func<object?>> _roots = new()
    {
        ["Game1"] = () => null,                          // 静态类，特殊处理
        ["Game1.player"] = () => Game1.player,
        ["Game1.currentLocation"] = () => Game1.currentLocation,
        ["Game1.locations"] = () => Game1.locations,
        ["MapData"] = () => null,                        // 静态类，特殊处理
    };

    /// <summary>已知静态类型白名单（用于 get/list/call 解析）。</summary>
    private static readonly Dictionary<string, Type> _staticTypes = new()
    {
        ["Game1"] = typeof(Game1),
        ["MapData"] = typeof(MapData),
        ["RuntimeSnapshotProjector"] = typeof(Query.Projectors.RuntimeSnapshotProjector),
        ["InventoryProjector"] = typeof(Query.Projectors.InventoryProjector),
        ["LocationScanner"] = typeof(Query.Scanning.LocationScanner),
        ["EntityClassifier"] = typeof(Query.Classification.EntityClassifier),
        ["ContainerLocator"] = typeof(Query.Scanning.ContainerLocator),
        ["UiSnapshotProjector"] = typeof(Query.Projectors.Ui.UiSnapshotProjector),
        ["MenuClassifier"] = typeof(Query.Projectors.Ui.MenuClassifier),
        ["NpcProjector"] = typeof(Query.Projectors.Character.NpcProjector),
        ["MonsterProjector"] = typeof(Query.Projectors.Character.MonsterProjector),
        ["FarmAnimalProjector"] = typeof(Query.Projectors.Character.FarmAnimalProjector),
        ["PetProjector"] = typeof(Query.Projectors.Character.PetProjector),
        ["HorseProjector"] = typeof(Query.Projectors.Character.HorseProjector),
        ["MachineProjector"] = typeof(Query.Projectors.WorldEntity.MachineProjector),
        ["ContainerProjector"] = typeof(Query.Projectors.WorldEntity.ContainerProjector),
        ["ResourceNodeProjector"] = typeof(Query.Projectors.WorldEntity.ResourceNodeProjector),
        ["LooseItemProjector"] = typeof(Query.Projectors.WorldEntity.LooseItemProjector),
        ["ResourceClumpProjector"] = typeof(Query.Projectors.WorldEntity.ResourceClumpProjector),
        ["BedProjector"] = typeof(Query.Projectors.WorldEntity.BedProjector),
        ["FurnitureProjector"] = typeof(Query.Projectors.WorldEntity.FurnitureProjector),
        ["DoorProjector"] = typeof(Query.Projectors.WorldEntity.DoorProjector),
        ["WarpProjector"] = typeof(Query.Projectors.WorldEntity.WarpProjector),
    };

    /// <inheritdoc />
    /// <remarks>DevEvalHandler 始终同步完成，无需 Tick 驱动。</remarks>
    public bool IsActive => false;

    public DevEvalHandler(IMonitor monitor)
    {
        _monitor = monitor;
    }

    // ════════════════════════════════════════════════════════
    //  ICompoundHandler 接口实现
    // ════════════════════════════════════════════════════════

    /// <inheritdoc />
    /// <remarks>直接同步执行并返回结果，调用方无需 Tick。</remarks>
    public V2Result? Start(V2Command cmd)
    {
        int seq = cmd.Seq;
        string actionId = cmd.ActionId;

        if (!CompoundUtils.TryGetString(cmd.Params, "op", out var op) ||
            string.IsNullOrWhiteSpace(op))
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.InvalidParams,
                "dev_eval 需要 op 参数（get/list/call）");
        }

        int maxItems = CompoundUtils.TryGetInt(cmd.Params, "max_items", out var mi) ? mi : 20;
        if (maxItems <= 0) maxItems = 20;

        try
        {
            return op.ToLowerInvariant() switch
            {
                "get" => HandleGet(seq, actionId, cmd.Params, maxItems),
                "list" => HandleList(seq, actionId, cmd.Params, maxItems),
                "call" => HandleCall(seq, actionId, cmd.Params),
                _ => ResultBuilder.Failure(seq, actionId,
                    ResultBuilder.Errors.InvalidParams,
                    $"未知 op: {op}，支持 get/list/call"),
            };
        }
        catch (Exception ex)
        {
            _monitor.Log($"[V2][DevEval] 未处理异常: {ex.Message}", LogLevel.Error);
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"执行异常: {ex.GetType().Name}: {ex.Message}");
        }
    }

    /// <inheritdoc />
    public V2Result? Tick() => null;

    /// <inheritdoc />
    public V2Result Cancel()
    {
        // DevEvalHandler 同步执行，不需要取消
        return ResultBuilder.Failure(0, "", ResultBuilder.Errors.Cancelled, "dev_eval 无需取消");
    }

    // ════════════════════════════════════════════════════════
    //  get — 读取属性链
    // ════════════════════════════════════════════════════════

    /// <summary>读取属性链并返回值摘要。</summary>
    private V2Result HandleGet(int seq, string actionId,
        Dictionary<string, object> args, int maxItems)
    {
        if (!CompoundUtils.TryGetString(args, "expr", out var expr) ||
            string.IsNullOrWhiteSpace(expr))
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.InvalidParams,
                "get 操作需要 expr 参数");
        }

        var (obj, type, err) = ResolveExpr(expr);
        if (err != null)
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.ExecutionFailed, err);
        }

        string typeName = type?.Name ?? (obj?.GetType().Name ?? "null");
        string valueSummary = BuildValueSummary(obj, type, maxItems);

        _monitor.Log($"[V2][DevEval] get: {expr} → {typeName} = {valueSummary}", LogLevel.Debug);

        return ResultBuilder.Success(seq, actionId, new Dictionary<string, object?>
        {
            ["op"] = "get",
            ["expr"] = expr,
            ["type"] = typeName,
            ["value"] = valueSummary,
        });
    }

    // ════════════════════════════════════════════════════════
    //  list — 列举对象的公开成员
    // ════════════════════════════════════════════════════════

    /// <summary>列举对象/类型的所有公开属性和字段，返回名称+类型+值摘要。</summary>
    private V2Result HandleList(int seq, string actionId,
        Dictionary<string, object> args, int maxItems)
    {
        if (!CompoundUtils.TryGetString(args, "expr", out var expr) ||
            string.IsNullOrWhiteSpace(expr))
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.InvalidParams,
                "list 操作需要 expr 参数");
        }

        var (obj, type, err) = ResolveExpr(expr);
        if (err != null)
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.ExecutionFailed, err);
        }

        // 确定要反射的实际类型
        Type targetType = obj?.GetType() ?? type ?? typeof(object);
        bool isStatic = obj == null && type != null;

        var flags = isStatic
            ? BindingFlags.Public | BindingFlags.Static
            : BindingFlags.Public | BindingFlags.Instance | BindingFlags.NonPublic;

        var members = new List<Dictionary<string, string>>();

        // 属性
        foreach (var prop in targetType.GetProperties(flags).OrderBy(p => p.Name))
        {
            var entry = BuildMemberEntry(prop.Name, prop.PropertyType, () =>
            {
                try { return prop.GetValue(obj); }
                catch { return null; }
            }, maxItems);
            members.Add(entry);
        }

        // 字段（非属性后备字段 — 过滤 k__BackingField）
        foreach (var field in targetType.GetFields(flags)
            .Where(f => !f.Name.Contains("k__BackingField"))
            .OrderBy(f => f.Name))
        {
            var entry = BuildMemberEntry(field.Name, field.FieldType, () =>
            {
                try { return field.GetValue(obj); }
                catch { return null; }
            }, maxItems);
            members.Add(entry);
        }

        _monitor.Log(
            $"[V2][DevEval] list: {expr} → {targetType.Name}，{members.Count} 个成员",
            LogLevel.Debug);

        return ResultBuilder.Success(seq, actionId, new Dictionary<string, object>
        {
            ["op"] = "list",
            ["expr"] = expr,
            ["type"] = targetType.FullName ?? targetType.Name,
            ["members"] = members,
        });
    }

    // ════════════════════════════════════════════════════════
    //  call — 调用静态方法或实例方法
    // ════════════════════════════════════════════════════════

    /// <summary>
    /// 调用方法并返回结果。支持两种方式：
    /// 1. 静态方法（旧逻辑）：{"method":"MapData.FindRoute","args":[...]}
    /// 2. 实例方法（新增）：{"expr":"Game1.currentLocation","method":"isTilePassable","args":[...]}
    /// </summary>
    private V2Result HandleCall(int seq, string actionId,
        Dictionary<string, object> args)
    {
        if (!CompoundUtils.TryGetString(args, "method", out var method) ||
            string.IsNullOrWhiteSpace(method))
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.InvalidParams,
                "call 操作需要 method 参数");
        }

        // 提取 args 数组参数
        object?[]? callArgs = null;
        if (args.TryGetValue("args", out var rawArgs))
        {
            callArgs = ExtractCallArgs(rawArgs);
        }

        int argCount = callArgs?.Length ?? 0;

        // ── 分支：有 expr 参数时走实例方法路径 ──
        bool hasExpr = CompoundUtils.TryGetString(args, "expr", out var expr) &&
                       !string.IsNullOrWhiteSpace(expr);

        if (hasExpr)
        {
            return HandleCallOnExpr(seq, actionId, expr!, method, callArgs, argCount);
        }

        // ── 旧逻辑：静态方法（向后兼容）──
        var dotIdx = method.LastIndexOf('.');
        if (dotIdx < 0)
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.InvalidParams,
                $"静态 call 的 method 格式错误，需要 ClassName.MethodName，得到: {method}");
        }

        string className = method[..dotIdx];
        string methodName = method[(dotIdx + 1)..];

        if (!_staticTypes.TryGetValue(className, out var targetType))
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.InvalidParams,
                $"不支持的类名: {className}，已知类型: {string.Join(", ", _staticTypes.Keys)}");
        }

        // 查找匹配的静态方法（参数数量匹配优先）
        var candidates = targetType.GetMethods(BindingFlags.Public | BindingFlags.Static)
            .Where(m => string.Equals(m.Name, methodName, StringComparison.OrdinalIgnoreCase))
            .ToList();

        if (candidates.Count == 0)
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"{className} 中找不到静态方法 {methodName}");
        }

        // 优先选参数数量匹配的重载
        var methodInfo = candidates
            .FirstOrDefault(m => m.GetParameters().Length == argCount)
            ?? candidates.First();

        try
        {
            var parameters = methodInfo.GetParameters();
            var typedArgs = ConvertArgs(callArgs ?? Array.Empty<object?>(), parameters);

            object? result = methodInfo.Invoke(null, typedArgs);

            string resultSummary = BuildValueSummary(result, result?.GetType(), 20);
            _monitor.Log(
                $"[V2][DevEval] call(static): {method}({argCount} args) → {resultSummary}",
                LogLevel.Debug);

            return ResultBuilder.Success(seq, actionId, new Dictionary<string, object?>
            {
                ["op"] = "call",
                ["method"] = method,
                ["result_type"] = result?.GetType().Name ?? "null",
                ["result"] = resultSummary,
            });
        }
        catch (TargetInvocationException tie)
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"调用 {method} 抛出异常: {tie.InnerException?.Message ?? tie.Message}");
        }
        catch (Exception ex)
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"调用 {method} 失败: {ex.Message}");
        }
    }

    /// <summary>
    /// 在 expr 解析出的对象上调用实例方法（或该对象类型上的静态方法）。
    /// </summary>
    private V2Result HandleCallOnExpr(int seq, string actionId,
        string expr, string methodName, object?[]? callArgs, int argCount)
    {
        // 1. 解析 expr 到目标对象
        var (targetObj, staticType, resolveErr) = ResolveExpr(expr);
        if (resolveErr != null)
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"解析 expr '{expr}' 失败: {resolveErr}");
        }

        // 确定要在哪个类型上搜索方法
        Type searchType;
        object? invokeTarget;

        if (targetObj != null)
        {
            // 解析到了实例对象
            searchType = targetObj.GetType();
            invokeTarget = targetObj;
        }
        else if (staticType != null)
        {
            // 解析到静态类（如 expr="Game1"、"MapData"）
            searchType = staticType;
            invokeTarget = null;
        }
        else
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"expr '{expr}' 解析结果为 null，无法调用方法");
        }

        // 2. 搜索方法（实例 + 静态 + 非公开，优先参数数量匹配）
        var bindingFlags = BindingFlags.Public | BindingFlags.Instance |
                           BindingFlags.Static | BindingFlags.NonPublic;

        var candidates = searchType
            .GetMethods(bindingFlags)
            .Where(m => string.Equals(m.Name, methodName, StringComparison.OrdinalIgnoreCase))
            .ToList();

        if (candidates.Count == 0)
        {
            // 诊断：列出同名前缀的方法，帮助调试
            var similar = searchType.GetMethods(bindingFlags)
                .Where(m => m.Name.StartsWith(methodName[..Math.Min(methodName.Length, 4)],
                    StringComparison.OrdinalIgnoreCase))
                .Select(m => $"{m.Name}({m.GetParameters().Length})")
                .Take(8)
                .ToList();
            string hint = similar.Count > 0
                ? $"，相似方法: {string.Join(", ", similar)}"
                : "";
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"类型 {searchType.Name} 中找不到方法 {methodName}{hint}");
        }

        // 优先选参数数量完全匹配的重载；其次取第一个
        var methodInfo = candidates
            .FirstOrDefault(m => m.GetParameters().Length == argCount)
            ?? candidates.First();

        var parameters = methodInfo.GetParameters();

        // 3. 参数类型转换
        object?[] typedArgs;
        try
        {
            typedArgs = ConvertArgsTyped(callArgs ?? Array.Empty<object?>(), parameters);
        }
        catch (Exception ex)
        {
            string paramSig = string.Join(", ",
                parameters.Select(p => $"{p.ParameterType.Name} {p.Name}"));
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"参数转换失败（签名: {methodName}({paramSig})）: {ex.Message}");
        }

        // 4. 调用方法
        try
        {
            // 静态类根但方法是实例方法时，invokeTarget 为 null 会导致报错
            // 此处 invokeTarget 正确：实例方法传实例，静态方法传 null
            object? result = methodInfo.Invoke(invokeTarget, typedArgs);

            string resultSummary = result == null
                ? "null"
                : BuildValueSummary(result, result.GetType(), 20);

            _monitor.Log(
                $"[V2][DevEval] call(instance): {expr}.{methodName}({argCount} args) → {resultSummary}",
                LogLevel.Debug);

            return ResultBuilder.Success(seq, actionId, new Dictionary<string, object?>
            {
                ["op"] = "call",
                ["expr"] = expr,
                ["method"] = methodName,
                ["args"] = callArgs?.Select(a => a?.ToString() ?? "null").ToArray()
                           ?? Array.Empty<string>(),
                ["result_type"] = result?.GetType().Name ?? "null",
                ["result"] = resultSummary,
            });
        }
        catch (TargetInvocationException tie)
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"调用 {expr}.{methodName} 抛出异常: {tie.InnerException?.Message ?? tie.Message}");
        }
        catch (Exception ex)
        {
            return ResultBuilder.Failure(seq, actionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"调用 {expr}.{methodName} 失败: {ex.GetType().Name}: {ex.Message}");
        }
    }

    // ════════════════════════════════════════════════════════
    //  属性链解析
    // ════════════════════════════════════════════════════════

    /// <summary>
    /// 解析属性链表达式，返回 (对象实例, 类型, 错误信息)。
    /// 对静态类，obj 为 null，type 为该静态类类型。
    /// </summary>
    private (object? obj, Type? type, string? error) ResolveExpr(string expr)
    {
        // 最长前缀匹配根对象
        string? bestRoot = null;
        foreach (var key in _roots.Keys.OrderByDescending(k => k.Length))
        {
            if (expr.Equals(key, StringComparison.OrdinalIgnoreCase) ||
                expr.StartsWith(key + ".", StringComparison.OrdinalIgnoreCase))
            {
                bestRoot = key;
                break;
            }
        }

        if (bestRoot == null)
        {
            return (null, null,
                $"未知根对象: {expr.Split('.')[0]}，已知根: {string.Join(", ", _roots.Keys)}");
        }

        // 判断是否静态类根
        bool isStaticRoot = _staticTypes.ContainsKey(bestRoot) && _roots[bestRoot]() == null;
        Type? currentType = isStaticRoot ? _staticTypes[bestRoot] : null;
        object? currentObj = isStaticRoot ? null : _roots[bestRoot]();

        // 剩余路径
        string remaining = expr.Length > bestRoot.Length
            ? expr[(bestRoot.Length + 1)..]
            : "";

        if (string.IsNullOrEmpty(remaining))
        {
            return (currentObj, currentType, null);
        }

        // 逐级读取成员
        var parts = remaining.Split('.');
        foreach (var part in parts)
        {
            if (currentType == null && currentObj == null)
                return (null, null, $"无法继续解析，前一步结果为 null，停在 {part}");

            Type resolveType = currentType ?? currentObj!.GetType();
            bool resolveStatic = currentObj == null;

            var bindingFlags = resolveStatic
                ? BindingFlags.Public | BindingFlags.Static
                : BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;

            // 属性优先
            var propInfo = resolveType.GetProperty(part, bindingFlags);
            if (propInfo != null)
            {
                try
                {
                    currentObj = propInfo.GetValue(currentObj);
                    currentObj = UnwrapNetField(currentObj);
                    currentType = null;
                }
                catch (Exception ex)
                {
                    return (null, null, $"读取属性 {part} 失败: {ex.Message}");
                }
                continue;
            }

            // 字段
            var fieldInfo = resolveType.GetField(part, bindingFlags);
            if (fieldInfo != null)
            {
                try
                {
                    currentObj = fieldInfo.GetValue(currentObj);
                    currentObj = UnwrapNetField(currentObj);
                    currentType = null;
                }
                catch (Exception ex)
                {
                    return (null, null, $"读取字段 {part} 失败: {ex.Message}");
                }
                continue;
            }

            return (null, null,
                $"在类型 {resolveType.Name} 上找不到属性或字段: {part}");
        }

        return (currentObj, currentType, null);
    }

    // ════════════════════════════════════════════════════════
    //  值摘要构建
    // ════════════════════════════════════════════════════════

    /// <summary>构建单个值的摘要字符串。</summary>
    private string BuildValueSummary(object? obj, Type? type, int maxItems)
    {
        if (obj == null) return "null";

        // 自动展开 NetField
        obj = UnwrapNetField(obj) ?? obj;

        var actualType = obj.GetType();

        // 基础类型直接 ToString
        if (actualType.IsPrimitive || actualType.IsEnum ||
            obj is string || obj is decimal || obj is DateTime)
        {
            string raw = obj.ToString() ?? "null";
            return raw.Length > MaxValueLength ? raw[..MaxValueLength] + "..." : raw;
        }

        // 集合类型
        if (obj is IEnumerable enumerable && obj is not string)
        {
            var items = new List<string>();
            int count = 0;
            foreach (var item in enumerable)
            {
                if (count >= maxItems) break;
                items.Add(item?.ToString() ?? "null");
                count++;
            }

            // 尝试获取真实 Count
            int totalCount = TryGetCount(obj);
            string countStr = totalCount >= 0 ? $"{totalCount}" : $"{count}+";
            string preview = items.Count > 0
                ? $"[{countStr} items: {string.Join(", ", items.Take(3))}...]"
                : $"[{countStr} items]";
            return preview;
        }

        // 复杂对象：类型名 + 前几个属性摘要
        var sb = new StringBuilder();
        sb.Append('{');
        sb.Append(actualType.Name);
        sb.Append(": ");

        var props = actualType.GetProperties(BindingFlags.Public | BindingFlags.Instance)
            .Take(4)
            .ToList();

        var previews = new List<string>();
        foreach (var p in props)
        {
            try
            {
                var val = p.GetValue(obj);
                val = UnwrapNetField(val) ?? val;
                string valStr = val?.ToString() ?? "null";
                if (valStr.Length > 50) valStr = valStr[..50] + "...";
                previews.Add($"{p.Name}={valStr}");
            }
            catch
            {
                previews.Add($"{p.Name}=?");
            }
        }

        sb.Append(string.Join(", ", previews));
        if (previews.Count < props.Count) sb.Append(", ...");
        sb.Append('}');

        string result = sb.ToString();
        return result.Length > MaxValueLength ? result[..MaxValueLength] + "..." : result;
    }

    /// <summary>构建成员条目 Dictionary。</summary>
    private Dictionary<string, string> BuildMemberEntry(
        string name, Type memberType, Func<object?> getValue, int maxItems)
    {
        string typeName = memberType.Name;
        string valuePreview;

        try
        {
            var val = getValue();
            val = UnwrapNetField(val) ?? val;

            if (val == null)
            {
                valuePreview = "null";
            }
            else if (val is string s)
            {
                valuePreview = s.Length > 60 ? s[..60] + "..." : s;
            }
            else if (val.GetType().IsPrimitive || val.GetType().IsEnum || val is decimal)
            {
                valuePreview = val.ToString() ?? "null";
            }
            else if (val is IEnumerable enumerable && val is not string)
            {
                int count = TryGetCount(val);
                valuePreview = count >= 0 ? $"[{count} items]" : "[items]";
            }
            else
            {
                valuePreview = $"{{{val.GetType().Name}}}";
            }
        }
        catch (Exception ex)
        {
            valuePreview = $"<err: {ex.Message[..Math.Min(ex.Message.Length, 40)]}>";
        }

        return new Dictionary<string, string>
        {
            ["name"] = name,
            ["type"] = typeName,
            ["value"] = valuePreview,
        };
    }

    // ════════════════════════════════════════════════════════
    //  辅助工具
    // ════════════════════════════════════════════════════════

    /// <summary>
    /// 自动展开 NetFieldBase&lt;T,TSelf&gt; — SDV 网络同步字段。
    /// 读取其 .Value 属性获取实际值。
    /// </summary>
    private static object? UnwrapNetField(object? obj)
    {
        if (obj == null) return null;

        var type = obj.GetType();
        // NetFieldBase<T,TSelf> 的命名空间是 Netcode
        if (type.Namespace?.StartsWith("Netcode") == true || type.Name.StartsWith("Net"))
        {
            var valueProp = type.GetProperty("Value",
                BindingFlags.Public | BindingFlags.Instance);
            if (valueProp != null)
            {
                try { return valueProp.GetValue(obj); }
                catch { /* 展开失败，返回原对象 */ }
            }
        }

        return obj;
    }

    /// <summary>尝试获取集合的元素数量。优先使用 Count 属性，失败则返回 -1。</summary>
    private static int TryGetCount(object obj)
    {
        // ICollection 接口
        if (obj is ICollection col) return col.Count;

        // 反射读取 Count 属性
        var countProp = obj.GetType().GetProperty("Count",
            BindingFlags.Public | BindingFlags.Instance);
        if (countProp != null)
        {
            try
            {
                var val = countProp.GetValue(obj);
                if (val is int i) return i;
            }
            catch { /* 忽略 */ }
        }

        return -1;
    }

    /// <summary>从 JSON 解析的原始参数中提取 call args 数组。</summary>
    private static object?[] ExtractCallArgs(object rawArgs)
    {
        if (rawArgs is System.Text.Json.JsonElement je &&
            je.ValueKind == System.Text.Json.JsonValueKind.Array)
        {
            var list = new List<object?>();
            foreach (var item in je.EnumerateArray())
            {
                list.Add(item.ValueKind switch
                {
                    System.Text.Json.JsonValueKind.String => item.GetString(),
                    System.Text.Json.JsonValueKind.Number when item.TryGetInt32(out var iv) => (object?)iv,
                    System.Text.Json.JsonValueKind.Number => item.GetDouble(),
                    System.Text.Json.JsonValueKind.True => true,
                    System.Text.Json.JsonValueKind.False => false,
                    _ => item.ToString(),
                });
            }
            return list.ToArray();
        }

        if (rawArgs is IList list2)
        {
            return list2.Cast<object?>().ToArray();
        }

        return Array.Empty<object?>();
    }

    /// <summary>将字符串/基础类型参数转换为方法签名所需的参数类型（旧静态方法路径使用）。</summary>
    private static object?[] ConvertArgs(object?[] args, ParameterInfo[] parameters)
    {
        var result = new object?[parameters.Length];
        for (int i = 0; i < parameters.Length; i++)
        {
            if (i >= args.Length)
            {
                result[i] = parameters[i].HasDefaultValue ? parameters[i].DefaultValue : null;
                continue;
            }

            var targetType = parameters[i].ParameterType;
            var arg = args[i];

            if (arg == null)
            {
                result[i] = null;
                continue;
            }

            try
            {
                result[i] = Convert.ChangeType(arg, targetType);
            }
            catch
            {
                result[i] = arg; // 转换失败，原样传入，让运行时报错
            }
        }
        return result;
    }

    /// <summary>
    /// 将参数列表转换为方法签名所需类型（实例方法路径使用）。
    /// 支持 string→基础类型、enum、bool，不支持的结构体原样传入。
    /// </summary>
    private static object?[] ConvertArgsTyped(object?[] args, ParameterInfo[] parameters)
    {
        var result = new object?[parameters.Length];
        for (int i = 0; i < parameters.Length; i++)
        {
            if (i >= args.Length)
            {
                result[i] = parameters[i].HasDefaultValue ? parameters[i].DefaultValue : null;
                continue;
            }

            var targetType = parameters[i].ParameterType;
            var arg = args[i];

            if (arg == null)
            {
                result[i] = null;
                continue;
            }

            // 如果实参已经是目标类型，直接用
            if (targetType.IsInstanceOfType(arg))
            {
                result[i] = arg;
                continue;
            }

            // 将实参转成字符串后用类型感知转换
            string argStr = arg.ToString() ?? "";
            result[i] = ConvertArg(argStr, targetType);
        }
        return result;
    }

    /// <summary>
    /// 将字符串参数转换为目标类型。
    /// 支持：string、int、long、float、double、bool、enum。
    /// 不支持的类型（如 Point、Vector2）原样返回字符串。
    /// </summary>
    private static object? ConvertArg(string argStr, Type targetType)
    {
        // 处理可空类型
        var underlying = Nullable.GetUnderlyingType(targetType);
        if (underlying != null)
        {
            if (string.IsNullOrEmpty(argStr)) return null;
            targetType = underlying;
        }

        if (targetType == typeof(string))
            return argStr;

        if (targetType == typeof(int))
            return int.TryParse(argStr, out var i) ? i : 0;

        if (targetType == typeof(long))
            return long.TryParse(argStr, out var l) ? l : 0L;

        if (targetType == typeof(float))
            return float.TryParse(argStr,
                System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture,
                out var f) ? f : 0f;

        if (targetType == typeof(double))
            return double.TryParse(argStr,
                System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture,
                out var d) ? d : 0.0;

        if (targetType == typeof(bool))
        {
            if (bool.TryParse(argStr, out var b)) return b;
            // 兼容 "1"/"0" 写法
            if (argStr == "1") return true;
            if (argStr == "0") return false;
            return false;
        }

        if (targetType == typeof(short))
            return short.TryParse(argStr, out var s) ? s : (short)0;

        if (targetType == typeof(byte))
            return byte.TryParse(argStr, out var by) ? by : (byte)0;

        if (targetType.IsEnum)
        {
            if (Enum.TryParse(targetType, argStr, ignoreCase: true, out var e))
                return e;
            // 枚举数值解析
            if (int.TryParse(argStr, out var iv) &&
                Enum.IsDefined(targetType, iv))
                return Enum.ToObject(targetType, iv);
            // 返回第一个枚举值作为默认
            var values = Enum.GetValues(targetType);
            return values.Length > 0 ? values.GetValue(0) : null;
        }

        // Point、Vector2 等结构体：暂不支持，原样返回字符串（运行时会报类型不匹配）
        return argStr;
    }
}
