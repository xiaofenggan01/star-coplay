

## 协议设计 

### Transport Contract（消息信封）





### Capability Contract (执行命令)

需要包括一下信息：
- capability_name string 唯一能力名
- kind 命令类型 string 枚举 action | query | control
- description_short  string 20 字以内的介绍 
- description_long string 50 字以内的介绍 
- llm_visible bool 是否可被 LLM 看到

- params_list
  - type: integer|string|... 
  - required: true|false
  - description: string 描述这个参数的含义 
  - 



## Tool 能力要求

在这个段落中我们只讨论需要 Tool 本身的能力期望。 
还需要更多时间才能定义出具体的参数等信息。 这里大致地描述一下我们的期望。 


### 执行能力

#### say - 在游戏内发送一段文本

参数
- content 需要发送的内容

实现逻辑
校验 content 长度 (本期校验字符串长度小于  50 字)

错误响应
- 文本过长
- 文本为空
- 发送频率过快
- 当前场景不允许发言

未来实现功能(本期完全不需要关心)
- 暂无


#### emote - 触发一个表情

参数
- emote_type 需要发送的表情，定义为枚举字段，具体字段枚举值待定

实现逻辑
- 枚举值有效

错误响应
- 无效的表情
- 发送频率过快
- 当前场景不允许发送表情

未来实现功能(本期完全不需要关心)
- 暂无
 

#### face - 面向某个方向

参数：
- direction 目标朝向 枚举: UP/DOWN/LEFT/RIGHT

实现逻辑
- 枚举值有效
- 判断当前是否已经面向该方向，如果本身已面向，则无需操作直接返回成功

错误想要
- 无效的方向
- 当前状态不允许转向

#### interact - 对目标 tile 执行一次交互输入


参数
- target_tile
  - X 坐标
  - Y 坐标

实现逻辑
- 解析 x, y 参数
- 检查目标 tile 是否距离用户当前的距离是否相邻 1 格（包括当前站立位置和周围一圈）
- 入队组合输入：面向目标 tile (如果就在当前格无此步骤) + 点按 X
- 注册动画等待，等待玩家从工具/动画/短暂停顿状态恢复，或等待超时

响应体
- target_x / target_y：本次交互面向的目标 tile
- stamina_before：执行前体力
- stamina_after：执行后体力
- stamina_delta：体力变化量
- waited_ticks：异步等待持续 ticks
- timed_out：是否等待超时
- completion_reason：等待结束原因

错误响应
- 参数缺失或非法
- 目标 tile 过远
- 等待超时
- 执行过程中发生异常

能力边界
- 不做距离/可达性校验，调用方应保证玩家已通过 move_to 到达目标相邻 tile
- 不处理交互后出现的菜单、对话框或确认框
- 不保证目标对象一定产生交互效果，只保证执行一次“面向 + 点按 X”的玩家式交互输入

未来实现
- 可选的交互结果观测: interact 的效果是因对象而已的。在内部语义上承认：交互成功有不同形态。因此我们一定需要在响应体中标记“交互对象及交互结果“，例如：
```
dialog_opened        打开了对话
menu_opened          打开了菜单
item_collected       拾取了物品
transition_started   触发了场景切换
object_state_changed 对象状态变化
no_visible_effect    没有观测到明显变化，但按键已执行
```
- 可选的目标 tile 可交互性检查
- 可选的 UI 类型回传


#### equip - 切换当前手持物品

参数
- slot 可选，integer，工具栏槽位编号，范围 1-12
- item_name 可选，string，精确物品名，匹配物品展示名或内部定义名
- item_category 可选，枚举字段，物品大类
  - axe
  - pickaxe
  - hoe
  - watering_can
  - scythe
  - fishing_rod
  - weapon

选择优先级
- 支持三种传参方式，优先级固定为：slot > item_name > item_category
- 如果同时传多个 selector，按优先级最高的字段执行；低优先级字段只作为冗余信息，不参与选择
- slot 对外固定为 1-based，与玩家看到的工具栏编号一致
- item_name 要求精确匹配 DisplayName 或内部 Name；大小写是否敏感由实现决定，但协议语义上不承诺模糊匹配
- item_category 表达“装备某一大类物品”，不表达后续任务意图

实现逻辑
- 解析 selector，并按 slot > item_name > item_category 选择实际使用的 selector
- slot 模式：校验槽位范围，检查槽位非空，切换到对应槽位
- item_name 模式：在背包中查找 DisplayName 或内部 Name 精确匹配的物品，并装备
- item_category 模式：在背包中查找该大类下最合适的物品，并装备
- 工具类大类如果存在多个候选，优先选择更高等级工具；无法判断等级时选择第一个稳定候选 

响应体
- selected_by：本次实际采用的选择器，枚举 slot | item_name | item_category
- slot：最终装备物品所在槽位，1-based
- item_name：物品内部定义名
- item_display_name：物品展示名
- item_category：识别出的物品大类
- method：执行方式，例如 direct_api | keypress
- swapped：是否发生背包交换
- from_slot：如果发生交换，物品原槽位
- to_slot：如果发生交换，物品目标槽位
- swapped_out：如果发生交换，被换出的物品名

错误响应
- 参数缺失或非法
- slot 越界
- slot 指向空槽位
- item_name 找不到匹配物品
- item_category 非法
- item_category 下没有可装备物品
- item_name 匹配到多个不同物品，无法唯一确定
- 执行异常

能力边界
- 只负责切换当前手持物品
- 不判断该物品是否适合后续目标 tile 或后续动作
- 不执行工具使用、交互、吃东西、送礼或放置物品
- item_category 只做类别选择；例如 axe 表示“装备一把斧类工具”，不表示“砍树”
- 非幂等；重复调用本身通常不会改变世界状态，但可能改变当前手持物品或背包栏位排列

未来实现功能（本期完全不需要关心）
- 更完整的物品类别体系
- item_name 多候选时返回候选列表供 Agent 二次选择
- 支持全背包 slot 范围，例如 1-36
- 支持 preferred_rank / min_rank 等工具等级偏好


#### use_tool - 对目标 tile 使用当前手持工具执行一次工具输入

参数
- target_tile
  - X 坐标
  - Y 坐标
- charge_level 可选，默认 0；0 表示点按 C，1-4 表示长按 C 进行蓄力工具动作

实现逻辑
- 检查当前是否已装备工具，即 `Game1.player.CurrentTool != null`
- 解析并校验 x, y 参数
- 解析 charge_level，默认 0, 大于 4 则按 4 处理
- 如果 charge_level == 0，入队组合输入：面向目标 tile + 单次点按 C
- 如果 charge_level > 0，入队组合输入：面向目标 tile + 长按 C 指定 ticks (这个时间需要看一下 目前好像是有问题的)
- 注册异步等待，等待工具动作、动画或短暂停顿结束
- 蓄力模式下，等待超时窗口需要额外加上蓄力按住时长

响应体
- target_x / target_y：本次工具动作面向的目标 tile
- tool_name：执行时当前手持工具名称
- charge_level：本次工具动作使用的蓄力等级
- stamina_before：执行前体力
- stamina_after：执行后体力
- stamina_delta：体力变化量
- waited_ticks：异步等待持续 ticks
- timed_out：是否等待超时
- completion_reason：等待结束原因

错误响应
- 当前未装备物品或物品并非工具
- 参数缺失或非法
- 输入入队失败
- 等待超时
- 执行异常

能力边界
- 本 Handler 不负责装备切换；调用方必须先通过 equip 装备目标工具
- 本 Handler 不解析 tool_category，不判断当前工具是否适合目标 tile
- 本 Handler 不校验当前工具等级是否支持指定 charge_level
- 本 Handler 不做距离/可达性校验；调用方应保证玩家已移动到合理位置
- x/y 只作为面向锚点，不保证实际作用范围仅限于该 tile
- 工具实际效果由游戏根据当前工具、朝向、目标 tile、蓄力等级和游戏上下文决定
- 不保证工具动作产生业务效果，只负责执行一次“面向目标 tile + 使用当前工具”的玩家式输入
- 非幂等，重复调用可能重复消耗体力、改变地块、攻击对象或破坏资源

未来实现功能（本期完全不需要关心）
- 可选的工具等级与 charge_level 匹配校验
- 可选的目标 tile 可作用性检查
- 可选的工具使用结果观测
- 可选的范围工具作用区域预估


#### move_to - 当前地图定位移动

这个代码写的很烂，最好重写掉。

参数
- mode 移动模式，枚举字段：
  - exact：精准移动到目标 tile
  - edge_warp：移动到指定出口触发点，并持续按方向键触发边界 warp
  - adjacent ：移动到目标指定侧的相邻 tile，并面朝目标
- x 目标 tile X
- y 目标 tile Y
- direction 边界 warp 方向，Direction 枚举；仅 edge_warp 模式需要
- face_direction 到达后转向，Direction 枚举；仅 exact 模式可选
- adjacent_side 玩家最终站在目标哪一侧，Direction 枚举；仅 adjacent 模式需要；允许为 0 则视为找到任意一个即可
- timeout_frames 可选超时帧数，默认 1800

参数校验
- exact：允许 x, y, face_direction；不允许 direction, adjacent_side：必须有。
- edge_warp：必须有 x, y, direction；不允许 face_direction, adjacent_side：必须有。
- adjacent：必须有 x, y, adjacent_side（可为 空值）；不允许 direction, face_direction。
- 出现冲突组合时返回参数非法。


实现逻辑
- 解析参数并确定 mode。
- 读取当前地图、当前位置、当前移动状态。
- 根据 mode 解析最终移动目标：
  - exact：destination = (x, y)
  - edge_warp：destination = (x, y)
  - adjacent_side：destination = 根据 stand_side 计算出的相邻 tile
- 检查 destination 是否可站立、当前地图内是否可达。
- 规划路径并执行移动。
- 到达 destination 后执行模式末端动作：
  - exact：可选 face_direction
  - edge_warp：持续按 direction 触发 warp
  - adjacent_side：自动面朝目标 tile
- 等待完成、超时或异常。
- 返回 commandResult。

成功标准
- exact：到达目标 tile；若指定 face_direction，则完成转向。
- edge_warp：到达 staging tile，并检测到 warp / map 变化。
- adjacent_side：到达指定侧相邻 tile，并面朝目标 tile。

错误响应
- 参数缺失或非法
- 参数组合冲突
- 方向枚举非法
- 当前状态不允许移动
- 目标 tile 非法
- 目标站位不可站立
- 当前地图内不可达
- 移动超时
- 移动中卡住
- warp 触发超时
- 非预期地图变化
- 执行异常

响应体 commandResult
- mode
- start_map
- start_x / start_y
- target_x / target_y
- resolved_destination_x / resolved_destination_y
- final_map
- final_x / final_y
- requested_direction
- requested_face_direction
- stand_side
- final_facing
- reached_destination
- warp_triggered
- elapsed_frames
- completion_reason

非目标
- 不做跨地图 BFS 路由
- 不做动态实体追踪
- 不自动选择相邻侧
- 不处理交互、菜单、工具使用
- 不向 LLM 暴露 tp fallback

#### go_to - 跨地图移动到目标位置

参数
- map_id：目标地图 ID
- x：目标 tile X
- y：目标 tile Y
- arrival_mode：最终到达模式，枚举字段：
  - exact：到达目标 tile
  - adjacent_side：站到目标指定侧相邻 tile，并面朝目标
- stand_side：Direction 枚举，仅 arrival_mode=adjacent_side 需要
- face_direction：Direction 枚举，仅 arrival_mode=exact 可选
- timeout_frames：可选，总超时帧数

实现逻辑
- 读取当前地图与玩家位置
- 如果当前地图等于目标地图，直接执行最终 move_to
- 如果当前地图不同：
  - 查询内部 MapGraph，规划从当前地图到目标地图的地图级路线
  - 将地图路线转换为一组 move_to(edge_warp) step
  - 逐步执行每个 step
  - 每次 warp 后校验当前地图是否符合预期
- 到达目标地图后，执行最终 move_to：
  - arrival_mode=exact：move_to(mode=exact, x, y, face_direction?)
  - arrival_mode=adjacent_side：move_to(mode=adjacent_side, x, y, stand_side)
- 聚合所有 step 的结果并返回 commandResult

成功标准
- 当前地图为目标 map_id
- 最终定位满足 arrival_mode 对应的成功条件

错误响应
- 参数缺失或非法
- 目标地图未知
- 当前地图未知
- 当前地图到目标地图无可用路线
- 路由配置缺失或非法
- 中间 move_to 失败
- warp 后地图不符合预期
- 最终定位失败
- go_to 总超时
- 执行异常

响应体 commandResult
- action
- start_map
- target_map
- start_x / start_y
- target_x / target_y
- final_map
- final_x / final_y
- arrival_mode
- stand_side
- face_direction
- route_maps
- steps_total
- steps_completed
- failed_step_index
- failed_step_result
- final_move_result
- elapsed_frames
- completion_reason

能力边界
- 负责编排跨地图路线，但不实现底层移动输入
- 底层移动统一委托 move_to
- 不负责动态目标追踪
- 不负责寻找 NPC、物品或可交互对象
- 不负责交互、工具使用、菜单处理
- 不负责业务流程，例如睡觉、购买、对话选择
- tp 仅作为系统内部兜底，不对 LLM 暴露

#### open_menu - 打开 GameMenu

参数
- 暂无

实现逻辑
- 若当前没有菜单，入队 E 键打开 GameMenu
- 注册异步等待，等待输入队列消化及菜单打开
- 若当前已有菜单，返回状态错误，避免 E 键产生关闭/切换歧义

错误响应
- 当前已有菜单打开
- 输入入队失败
- 打开菜单超时
- 执行异常

响应体
- action
- menu_type_before
- menu_type_after
- ui_open_before
- ui_open_after
- elapsed_frames
- completion_reason

能力边界
- 只负责打开 GameMenu
- 不负责切换 tab
- 不负责关闭已有菜单再打开

#### query_menu - 查询当前菜单快照

参数
- 暂无

实现逻辑
- 读取当前 activeClickableMenu
- 生成 MenuSnapshot
- 无菜单时返回空快照
- 不注入任何输入

响应体
- menu_type
- active_tab
- components
- snapshot_id（未来可选）

#### menu_click - DPad 导航到指定组件并确认

参数
- component_id：目标组件 myID，应来自最近一次 query_menu 返回的 MenuSnapshot
- source：组件来源，枚举字段：
  - page：当前页面内组件
  - tab：GameMenu 顶部 tab 组件

实现逻辑
- 检查当前存在 activeClickableMenu
- 校验 component_id
- 校验 source 枚举值
- 调用 MenuNavigator.Click(component_id, source)
- MenuNavigator 基于当前菜单组件图计算 DPad 可达路径
- 入队 DPad 序列并按 A 确认
- 注册异步等待，等待输入队列消化

错误响应
- 当前没有打开菜单
- 参数缺失或非法
- source 非法
- 目标组件不存在
- 目标组件不可达
- 输入执行超时
- 执行异常

响应体
- action
- component_id
- source
- menu_type_before
- menu_type_after
- ui_open_before
- ui_open_after
- elapsed_frames
- completion_reason

能力边界
- 不使用像素坐标
- 不负责打开菜单
- 不负责理解组件业务含义
- 不保证点击后的业务效果；调用方应通过后续 query_menu / observe 验证
- component_id 只在当前菜单快照上下文中可靠，不应长期缓存

#### menu_close - 关闭当前菜单

参数
- 暂无

实现逻辑
- 如果当前没有菜单，直接返回成功
- 如果当前有菜单，入队 ESC 关闭
- 注册异步等待，等待菜单关闭

错误响应
- 输入入队失败
- 关闭菜单超时
- 执行异常

响应体
- action
- already_closed
- menu_type_before
- menu_type_after
- ui_open_before
- ui_open_after
- elapsed_frames
- completion_reason

能力边界
- 幂等命令：无菜单时也视为成功
- 不负责保存、提交或选择菜单内容


#### go_to_bed - 睡觉并进入下一天

参数
- timeout_frames：可选，总超时帧数

实现逻辑
- 记录睡前日期、时间、地图、体力等状态
- 解析床位置
- 使用内部受限 tp 到床附近安全点
- 调用 move_to 定位到床触发位置
- 触发睡觉确认弹窗
- 确认睡觉
- 等待睡眠、保存、日结、升级等过场流程
- 自动推进无分支的继续/确认类 UI
- 如遇到需要选择的 UI(比如选择职业)，停止并返回需要人工/LLM上层决策
- 检测日期推进、时间回到早晨、玩家可移动、无阻塞 UI
- 返回 commandResult

成功标准
- 日期已推进
- 当前时间为新一天早晨
- 玩家位于醒来地图
- 玩家可移动
- 当前无阻塞菜单/对话/过场 UI

错误响应
- 床位置未知
- 内部 tp 失败
- 床附近定位失败
- 睡觉弹窗未出现
- 睡觉确认失败
- 睡眠流程超时
- 睡眠后 UI 处理超时
- 睡眠后出现需要选择的 UI
- 日期未推进
- 醒来后状态不可操作
- 执行异常

响应体
- date_before / date_after
- time_before / time_after
- map_before / map_after
- stamina_before / stamina_after
- money_before / money_after（可选）
- bed_map / bed_x / bed_y
- used_tp
- move_to_bed_result
- sleep_prompt_seen
- sleep_confirmed
- post_sleep_ui_steps
- decision_required
- day_advanced
- player_can_move_after
- elapsed_frames
- completion_reason

能力边界
- 这是专用日终流程能力，不是通用跨地图移动能力
- 内部 tp 只允许用于床附近受限目标，不对 LLM 暴露
- 调用该能力即表示确认睡觉
- 不解析每日结算、升级、收入等业务内容
- 不自动处理需要选择的睡眠后 UI


#### tp - 内部强制传送

能力定义
- 将玩家强制移动到指定地图与 tile。
- 仅用于系统内部恢复、测试和少数专用流程。
- 不属于真实玩家行为。
- 不对 LLM 暴露，不生成 FunctionSchema。

参数
- map_id：目标地图 ID
- x：目标 tile X
- y：目标 tile Y
- face_direction：可选，传送后朝向，Direction 枚举
- reason：TeleportReason 枚举，必须记录传送原因

实现逻辑
- 校验调用来源为内部授权调用
- 解析并校验参数
- 确认目标地图存在
- 确认目标 tile 可作为安全传送点
- 记录传送前地图、位置、状态
- 调用内部传送能力
- 等待游戏状态稳定
- 读取传送后地图与玩家位置
- 校验传送结果
- 返回 commandResult

成功标准
- 当前地图为目标 map_id
- 玩家位于目标 tile
- 如果指定 face_direction，则朝向正确
- 游戏状态可继续观测和执行

错误响应
- 非授权调用
- 参数缺失或非法
- 目标地图不存在
- 目标 tile 非法
- 目标 tile 不可站立
- 传送 API 调用失败
- 传送后地图不匹配
- 传送后位置不匹配
- 传送后状态不可用
- 执行异常

响应体
- action
- reason
- from_map
- from_x / from_y
- to_map
- to_x / to_y
- final_map
- final_x / final_y
- face_direction
- authorized
- used_by
- elapsed_frames
- completion_reason

能力边界
- 不用于正常导航
- 不替代 move_to / go_to 的常规路径能力
- 只能由内部 RecoveryPolicy、测试流程或专用 L2 能力触发
- 所有调用必须可审计
- LLM 只能看到上层能力是否 used_tp，不能直接调用 tp