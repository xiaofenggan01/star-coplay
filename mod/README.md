# StarCoPlay Mod 目录组织

这个目录只保留一份 README。代码库本身就是主要文档：新增代码必须放进有业务语义的目录，不能靠散落说明文档解释“这是什么”。

## 组织原则

- 不使用 `V1`、`V2`、`L1V2` 这类版本命名作为当前代码路径或新类型名。
- 当前实现放在业务语义目录里；历史兼容实现只允许放进 `Legacy/`。
- `GameServices/` 只放底座服务，不放协议能力本身。
- `Capabilities/` 放对外能力原子，能力可以依赖 `GameServices/`。
- `QueryCore/RefId/` 是目标标识主线；旧 target-name、target-ref 字符串兼容和 intent registry 属于 `Legacy/DeprecatedTargeting/`。
- 除本文件外，不再新增目录级 README、GUIDE、DESIGN、REVIEW。必须说明的架构约定写进本文件；具体行为写进代码、类型名、测试名和少量必要注释。该文件不用做任何的进度管理和迭代备忘。

## 当前目录

```text
mod/
├── README.md
├── StarCoPlay/
│   ├── ModEntry.cs
│   ├── Capabilities/
│   │   └── ExecutionAtoms/        # face/equip/interact/use_tool/move_to/menu_* 等一级执行原子
│   ├── GameServices/
│   │   ├── SimulatedInput/        # 模拟按键底座：InputBridge/InputCombo/MenuNavigator/AnimationAwaiter
│   │   ├── Movement/              # ActionExecutor、移动接口、卡住检测与恢复
│   │   ├── Navigation/            # 地图出口与路径数据
│   │   ├── Inventory/             # 背包/容器底座
│   │   └── Naming/                # 名称匹配等通用服务
│   ├── QueryCore/
│   │   ├── RefId/                 # RefId 构建、解析、解析器与对象定位
│   │   ├── Handlers/              # 查询能力 handler
│   │   ├── Projectors/            # 游戏对象到查询事实的投影
│   │   ├── Scanning/              # 地图/区域扫描
│   │   └── Classification/        # 查询分类逻辑
│   ├── Transport/                 # WebSocket、文件桥、传输模型
│   ├── RuntimeState/              # game_state 快照读取与模型
│   ├── RuntimeEvents/             # 运行事件与事件模型
│   ├── Configuration/             # Mod 配置
│   ├── Ui/                        # HUD/本地显示
│   ├── DevTools/                  # 本地调试命令和报告工具
│   ├── Generated/                 # protobuf 生成代码
│   ├── Integrations/              # 外部 Mod/API 集成
│   ├── Logging/
│   └── Legacy/                    # 仍被兼容链路引用的历史实现隔离区
├── StarCoPlay.Tests/
├── bridge/
└── scripts/
```

## 代码放置规则

- 新执行原子放 `StarCoPlay/Capabilities/ExecutionAtoms/`。
- 新模拟输入底座放 `StarCoPlay/GameServices/SimulatedInput/`，不能把执行原子放进去。
- 新 RefId 相关查询代码放 `StarCoPlay/QueryCore/RefId/`。
- 新协议分发代码应先创建语义目录 `StarCoPlay/ProtocolCore/`，再放入 dispatcher、validation、result mapping 等代码。
- 新传输/桥接代码放 `StarCoPlay/Transport/`。
- 不得向 `Legacy/` 增加新能力；只能为兼容旧链路做最小修复，并应优先迁出或删除。
- 不得重新创建 `Actions/`、`Models/`、`Bridge/`、`Protocol/`、`V2/` 这类泛名或版本目录。
