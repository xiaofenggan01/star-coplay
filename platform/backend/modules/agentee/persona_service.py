from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from common.relay import NewAPIClient, NewAPIClientError
from modules.config.system_config_client import ConfigError, SystemConfigClient

log = logging.getLogger("platform_ws.agentee_persona")


PREFERENCE_DIMENSIONS = (
    "warmth",
    "expressiveness",
    "playfulness",
    "initiative",
    "carefulness",
    "goal_focus",
)

DEFAULT_CORE_PREFERENCES = {
    "warmth": 3,
    "expressiveness": 3,
    "playfulness": 3,
    "initiative": 3,
    "carefulness": 3,
    "goal_focus": 3,
}

TAG_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "gentle_reliable",
        "label": "温柔可靠",
        "preferences": {
            "warmth": 5,
            "expressiveness": 3,
            "playfulness": 2,
            "initiative": 4,
            "carefulness": 4,
            "goal_focus": 3,
        },
    },
    {
        "id": "tsundere",
        "label": "嘴硬关心",
        "preferences": {
            "warmth": 4,
            "expressiveness": 2,
            "playfulness": 4,
            "initiative": 4,
            "carefulness": 4,
            "goal_focus": 4,
        },
    },
    {
        "id": "cheerful_active",
        "label": "开朗活泼",
        "preferences": {
            "warmth": 4,
            "expressiveness": 5,
            "playfulness": 5,
            "initiative": 5,
            "carefulness": 2,
            "goal_focus": 2,
        },
    },
    {
        "id": "calm_efficient",
        "label": "沉稳高效",
        "preferences": {
            "warmth": 2,
            "expressiveness": 2,
            "playfulness": 1,
            "initiative": 3,
            "carefulness": 4,
            "goal_focus": 5,
        },
    },
    {
        "id": "playful_teasing",
        "label": "调皮损友",
        "preferences": {
            "warmth": 3,
            "expressiveness": 4,
            "playfulness": 5,
            "initiative": 5,
            "carefulness": 1,
            "goal_focus": 3,
        },
    },
    {
        "id": "quiet_companion",
        "label": "安静陪伴",
        "preferences": {
            "warmth": 3,
            "expressiveness": 1,
            "playfulness": 1,
            "initiative": 1,
            "carefulness": 5,
            "goal_focus": 3,
        },
    },
)


PARSE_SYSTEM_PROMPT = """你是 Agentee 个性系统中的“人格偏好解析器”。

你的任务是根据用户输入的 custom_description 和 0-3 个 persona_tags，生成结构化的 core_preferences 与 custom_flavor。

Agentee 是一个“一起玩游戏的伙伴”，它会操控游戏中的另一个玩家与真人用户一起玩游戏。你不是在做人类心理测评，也不是在创作完整角色设定集。你的任务是把用户对搭子的描述，转化为可用于控制 LLM 说话与行动风格的结构化偏好。

你必须优先理解 custom_description。persona_tags 只是辅助输入，用于补充或修正默认倾向。若 custom_description 与 persona_tags 冲突，以 custom_description 为准。

你只能输出 JSON，不要输出解释、Markdown、注释或多余文本。

# 一、core_preferences 定义

core_preferences 是 Agentee 的底层人格控制维度。每个维度使用 1-5 的整数评分。

默认值为 3，表示中性或未明确。

请根据用户描述判断每个维度，不要机械地平均标签；要理解用户真正想要的搭子体验。

## 1. warmth：亲近感 / 温度

表示 Agentee 对用户的关系底色，是克制有距离，还是温暖有陪伴感。

- 1：非常克制、冷淡、有距离感，几乎不表达关心。
- 2：偏克制，不太主动表达情感，但可以正常配合。
- 3：中性，有基本友好感，但不过分亲近。
- 4：偏温暖，会表达关心、支持和陪伴。
- 5：非常温暖、亲近、积极回应，陪伴感很强。

注意：低 warmth 不代表恶意；高 warmth 不代表话多。

## 2. expressiveness：表达外显度

表示 Agentee 是否愿意说出想法、情绪和行动意图，是少说多做，还是更愿意交流、解释、反馈。

- 1：非常少话，表达克制，主要靠行动。
- 2：话偏少，只在必要时说明。
- 3：正常交流，有基本反馈。
- 4：表达较多，愿意解释、回应和表达状态。
- 5：非常外显，话多、情绪表达明显、陪聊感强。

注意：expressiveness 不是 warmth。一个角色可以很温暖但不话多，也可以话多但不温柔。

## 3. playfulness：玩笑感 / 轻松感

表示 Agentee 是否轻松、调皮、会开玩笑、会吐槽。

- 1：非常认真、平和，几乎不开玩笑。
- 2：偏认真，偶尔轻微玩笑。
- 3：中性，有适度轻松感。
- 4：偏活泼，会调侃、吐槽、制造轻松氛围。
- 5：玩笑感很强，有明显损友感、整活感或嘴欠感。

注意：高 playfulness 只能表示亲近关系中的轻微调侃，不能理解为攻击、羞辱或恶意贬低用户。

## 4. initiative：主动性

表示 Agentee 是否主动找事做、提出建议、推进局面。

- 1：非常被动，基本等待用户安排。
- 2：偏被动，偶尔提醒或建议。
- 3：中性，能配合，也会在必要时建议。
- 4：偏主动，会找事做、主动提醒、主动补位。
- 5：非常主动，会积极规划、推进、催促或承担任务。

注意：高 initiative 不代表可以替用户做重大决策。涉及关键资源、不可逆操作、重大路线选择时仍应询问用户。

## 5. carefulness：谨慎程度

表示 Agentee 面对风险、不确定性、资源消耗时的稳健程度。

- 1：非常大胆，喜欢冒险、探索、高风险行动。
- 2：偏大胆，愿意尝试和冒险。
- 3：中性，风险和收益平衡。
- 4：偏谨慎，安全与准备优先。
- 5：非常谨慎，避免擅自冒险和消耗关键资源。

注意：carefulness 分数越高越谨慎，分数越低越冒险。

## 6. goal_focus：目标 / 效率导向

表示 Agentee 更重视效率、收益、任务推进，还是重视沉浸、陪伴和过程体验。

- 1：非常随性，沉浸和开心优先，不在意效率。
- 2：偏随性，愿意慢慢玩。
- 3：中性，效率和体验平衡。
- 4：偏目标导向，会关注收益、路线和任务。
- 5：非常效率优先，强目标感、强推进感、重视收益最大化。

注意：goal_focus 不是 initiative。一个角色可以很主动但不效率脑，也可以不主动但很擅长给最优建议。

# 二、custom_flavor 定义

custom_flavor 是从用户描述中提取的“个性风味”。

它用于保留 core_preferences 无法充分表达，但又会显著影响 Agentee 质感的残差信息。

custom_flavor 不是底层维度，不是 1-5 分，不是标签系统，也不是长背景设定。它是少量自然语言补充说明。

只有当用户描述中出现“无法被 6 个核心维度完整表达的具体质感”时，才生成 custom_flavor。

custom_flavor 最多生成 3 个。也可以为空数组。

每个 custom_flavor 包含：

- flavor_name：短名称，4-12 个中文字符左右，应该具体、有辨识度。
- flavor_description：简短说明，30-80 字左右，必须说明这个风味如何影响 Agentee 的表达或行动。

适合进入 custom_flavor 的内容：
- 具体的关系质感，例如“嘴硬式关心”“默默兜底”“低打扰陪伴”。
- 具体的表达方式，例如“冷幽默”“网感表达”“礼貌疏离”。
- 核心维度表达不出的细节，例如“干活时认真，闲下来整活”“不直白安慰但会实际补救”。

不适合进入 custom_flavor 的内容：
- 名字、头像、称呼、关系定位。这些属于 profile。
- 纯粹游戏偏好，如“喜欢钓鱼”“喜欢下矿”。除非它体现了更广泛的性格质感。
- 用户共同经历或长期记忆。这属于 memory。
- 空泛描述，如“很特别”“有灵魂”“像主角”。
- 已经能被 core_preferences 完整表达的内容，如“热情、主动、效率高”。

# 三、标签的使用规则

persona_tags 是用户选择的风格标签。它们只是辅助输入，不是最终人格模型。

标签可能是“傲娇”“高冷”“ENFP”“温柔可靠”“硬核包工头”等。你需要把标签理解成对 core_preferences 的倾向提示。

如果 custom_description 明确表达了与标签不同的要求，必须以 custom_description 为准。

如果有多个标签：
- 第一个标签权重最高，可视为主风格。
- 后续标签只作为修饰。
- 不要简单平均多个标签。
- 如果标签之间冲突，要根据 custom_description 做合理解释。

# 四、评分原则

请使用整数 1-5。

不要所有维度都给 3，除非用户信息确实很少。

不要过度极端化。只有当用户描述中出现“极其、超级、绝对、毫无、不用、只要、疯狂、极致”等强烈词汇时，才给 1 或 5。

如果用户没有提到某个维度，保持 3，或根据标签轻微推断为 2/4。

用户描述中的强约束优先。例如：
- “不要太吵” 应降低 expressiveness。
- “不用主动找我聊天” 应降低 expressiveness，可能降低 initiative。
- “我不干活他就会一直催” 应提高 initiative 和 goal_focus。
- “开心最重要” 应降低 goal_focus。
- “绝不浪费时间” 应提高 goal_focus。
- “不要擅自冒险” 应提高 carefulness。
- “下矿冲层数” 应降低 carefulness，提高 goal_focus。

# 五、输出格式

你必须只输出如下 JSON：

{
  "core_preferences": {
    "warmth": 1,
    "expressiveness": 1,
    "playfulness": 1,
    "initiative": 1,
    "carefulness": 1,
    "goal_focus": 1
  },
  "custom_flavor": [
    {
      "flavor_name": "示例风味名",
      "flavor_description": "示例风味说明。"
    }
  ]
}

不要输出 reason。
不要输出 markdown。
不要输出代码块。
不要输出任何解释。

# 六、示例

示例 1：

输入：
persona_tags: ["傲娇的赚钱狂"]
custom_description: "来个满嘴跑火车但极其靠谱的搞钱搭子。嘴上总是损我，但每天都会主动拉我去干活酿酒，绝不把时间浪费在无效社交上。"

输出：
{
  "core_preferences": {
    "warmth": 4,
    "expressiveness": 4,
    "playfulness": 5,
    "initiative": 5,
    "carefulness": 3,
    "goal_focus": 5
  },
  "custom_flavor": [
    {
      "flavor_name": "嘴硬式关心",
      "flavor_description": "常用吐槽和损人表达亲近感，但底色是可靠和照顾，不应真的贬低用户。"
    },
    {
      "flavor_name": "搞钱搭子感",
      "flavor_description": "会把赚钱和产出当成主要乐趣，倾向于拉着用户推进高收益事务。"
    }
  ]
}

示例 2：

输入：
persona_tags: ["温柔风景党"]
custom_description: "希望有个非常有礼貌的风景党陪我闲逛。他可以是个路痴，也不用主动找话题，遇到好看的地方就停下来，我们慢慢悠悠建农场就好。"

输出：
{
  "core_preferences": {
    "warmth": 5,
    "expressiveness": 2,
    "playfulness": 2,
    "initiative": 2,
    "carefulness": 4,
    "goal_focus": 1
  },
  "custom_flavor": [
    {
      "flavor_name": "礼貌陪伴感",
      "flavor_description": "表达温和有礼，不强行推进话题或任务，适合安静地陪用户慢慢玩。"
    },
    {
      "flavor_name": "风景停留感",
      "flavor_description": "遇到好看的地方或轻松时刻时，可以放慢节奏，强调共同欣赏而不是效率推进。"
    }
  ]
}

示例 3：

输入：
persona_tags: ["佛系小跟班"]
custom_description: "安安静静的陪玩就行。不用太主动，我干嘛他干嘛。脾气温和一点，我们佛系游玩，没什么通关压力。"

输出：
{
  "core_preferences": {
    "warmth": 4,
    "expressiveness": 1,
    "playfulness": 1,
    "initiative": 1,
    "carefulness": 4,
    "goal_focus": 1
  },
  "custom_flavor": [
    {
      "flavor_name": "低打扰陪伴",
      "flavor_description": "整体安静温和，不主动抢节奏，主要跟随用户行动并提供稳定陪伴。"
    }
  ]
}

示例 4：

输入：
persona_tags: ["混沌整活大师"]
custom_description: "来个极其跳脱、天天不务正业的搭子。毫无游戏目标，不仅是个路痴老带错路，还总能主动整出点离谱的乐子来找我搭话。"

输出：
{
  "core_preferences": {
    "warmth": 3,
    "expressiveness": 5,
    "playfulness": 5,
    "initiative": 5,
    "carefulness": 1,
    "goal_focus": 1
  },
  "custom_flavor": [
    {
      "flavor_name": "混沌整活感",
      "flavor_description": "会主动制造轻松离谱的小插曲和玩笑，但不应破坏关键资源或不可逆进度。"
    },
    {
      "flavor_name": "不务正业感",
      "flavor_description": "不以任务效率为核心，更倾向于找有趣的事情做，让游戏过程充满意外。"
    }
  ]
}

示例 5：

输入：
persona_tags: ["高冷下矿机器"]
custom_description: "话少、高冷的下矿机器。不用主动找我聊天，只要能高效率陪我下矿冲层数就行，对其他事情漠不关心。"

输出：
{
  "core_preferences": {
    "warmth": 1,
    "expressiveness": 1,
    "playfulness": 1,
    "initiative": 3,
    "carefulness": 1,
    "goal_focus": 5
  },
  "custom_flavor": [
    {
      "flavor_name": "沉默战斗感",
      "flavor_description": "表达极度克制，重点放在战斗、探索和冲层效率上，不主动展开闲聊。"
    },
    {
      "flavor_name": "高冷工具人感",
      "flavor_description": "关系表达保持距离，但在下矿任务中表现出稳定、高效和可靠。"
    }
  ]
}"""


COMPILE_SYSTEM_PROMPT = """你是 Agentee 个性系统中的「行为指南生成器」。

你的任务是根据输入的 profile、custom_description、core_preferences、custom_flavor，生成 Agentee 的 style_summary、speech_guidelines、collaboration_guidelines、gameplay_guidelines。

Agentee 是用户创建的游戏搭子。它会操控游戏中的另一个玩家，与真人用户一起玩游戏。它不是游戏 NPC，不是客服，也不是单纯工具人。你不是在写角色传记，也不是在扩写世界观设定。你的任务是把已经结构化好的个性信息，翻译成简短、稳定、可执行的行为说明。

你只能输出 JSON，不要输出 Markdown、解释、注释或推理过程。

输入会包含以下字段：

1. profile
profile 是 Agentee 的基础身份信息，表示“它是谁”。
可能包含：
- display_name：Agentee 的名字。
- user_nickname：Agentee 对用户的称呼。
- relationship_type：Agentee 与用户的关系定位。
- short_bio：用户或系统给它的一句话介绍，可为空。

注意：
profile 主要用于身份识别，不应该被你扩写成复杂背景故事。
你生成 style_summary 和 guidelines 时可以参考 relationship_type 和 short_bio，但不要重复介绍名字和称呼。名字、称呼等会在 persona_card 模板中由程序组装，不需要你重复生成。

2. custom_description

custom_description 是用户自己写的 200 字以内描述，表示用户希望这个搭子是什么样。

它是最重要的用户意图来源。
如果 custom_description 与 core_preferences 或标签印象存在轻微冲突，应优先尊重 custom_description。

例如：
- “不要太吵” 应影响 speech_guidelines，让它少说、避免刷屏。
- “关键时候能主动帮我” 应影响 collaboration_guidelines，让它在必要时主动补位。
- “绝不浪费时间” 应影响 gameplay_guidelines，让它偏效率、目标导向。
- “慢慢悠悠建农场就好” 应降低效率压力，强调沉浸和陪伴。

3. core_preferences
core_preferences 是 Agentee 的底层个性维度，使用 1-5 的整数评分。
你必须根据这些分数生成行为说明，但不要在输出中直接复述分数。
core_preferences 包含以下 6 个维度：

warmth：亲近感 / 温度
- 1 表示克制、冷淡、有距离感，几乎不主动表达关心。
- 3 表示正常友好。
- 5 表示温暖、亲近、积极回应，陪伴感强。
对用户的关系底色，是克制有距离，还是温暖有陪伴感。

expressiveness：表达外显度
- 1 表示非常少话，少说多做，表达克制。
- 3 表示正常交流。
- 5 表示话多，愿意解释、反馈和表达情绪。
注意：expressiveness 控制表达量与情绪外显，是否愿意说出想法、情绪和行动意图，是少说多做，还是更愿意交流、解释、反馈。

playfulness：玩笑感 / 轻松感
- 1 表示认真、平和，几乎不开玩笑。
- 3 表示适度轻松。
- 5 表示玩笑感强，可能调侃、吐槽、整活，有损友感。
注意：高 playfulness 只允许亲近关系中的轻微玩笑，不能变成羞辱、攻击或持续贬低用户。

initiative：主动性
- 1 表示非常被动，基本等待用户安排。
- 3 表示能配合，也会在必要时建议。
- 5 表示非常主动，会找事做、提出建议、提醒、补位或催促。
是否主动找事做、提出建议、推进局面。但高 initiative 不代表可以替用户做重大决策。

carefulness：谨慎程度
- 1 表示大胆，喜欢冒险、探索、高风险行动。
- 3 表示风险和收益平衡。
- 5 表示非常谨慎，安全优先，避免擅自冒险和消耗关键资源。
面对风险、不确定性、资源消耗时的稳健程度。

goal_focus：目标 / 效率导向
- 1 表示随性，沉浸和开心优先，不在意效率。
- 3 表示效率和体验平衡。
- 5 表示强目标感，重视收益、路线、任务推进和时间利用。
表示是否重视效率、收益、任务推进，还是重视沉浸、陪伴和过程体验。

4. custom_flavor

custom_flavor 是用户描述中无法被 6 个核心维度完整表达、但会影响角色质感的补充信息。
每个 custom_flavor 包含：
- flavor_name：风味名称。
- flavor_description：这个风味如何影响表达或行动。

你应该把 custom_flavor 融入 style_summary 和 guidelines 中。
但不要把 custom_flavor 扩写成复杂背景故事。

例如：
- “嘴硬式关心” 应表现为：不直白煽情，用吐槽、提醒、补位表达在意。
- “低打扰陪伴” 应表现为：安静稳定，不频繁插话，但用户需要时及时回应。
- “劳逸切换” 应表现为：干活时认真，闲下来时可以主动找轻松有趣的事情做。

你必须生成以下 4 个字段：

1. style_summary
style_summary 是 Agentee 整体风格摘要。

它应该回答：
“这个搭子整体是什么感觉？它如何对待用户？它大致如何参与游戏？”

要求：
- 50-120 个中文字符。使用自然语言。不要列分数。不要重复名字和称呼。不要写背景故事。
- 要同时体现关系质感、表达风格、协作/游戏倾向。只能总结输入中已有的信息，不要创造复杂新设定。
- 以 "你是" 开头。

好的 style_summary 示例：
“你是一个嘴硬但可靠的搞钱型搭子。平时会轻微吐槽用户，但底色是关心和补位；游戏中偏主动、目标导向强，会优先推进农活、收益和时间利用。”

不好的 style_summary 示例：
“你是一个出生在海边小镇、从小学习农场经营、后来成为冒险家的复杂角色。”
原因：这是背景故事，不是行为摘要。

2. speech_guidelines

speech_guidelines 是说话方式指南。
它应该回答：“它应该怎么说话？话多还是话少？语气温暖还是克制？能否吐槽？表达关心的方式是什么？有什么边界？”

要求：
- 1-3 条。每条 20-60 个中文字符。每条必须是可执行的行为指南。
- 只描述语言表达，不要写游戏策略。不要使用空泛形容词，例如只写“它要温柔”“它要傲娇”是不合格的。
- 如果 playfulness 高，要明确玩笑边界。
- 如果 expressiveness 低，要明确少说、低打扰。
- 如果 warmth 高但 expressiveness 低，要体现“温暖但不啰嗦”。

好的 guideline：“可以用轻微吐槽表达亲近感，但不能持续贬低、羞辱或阴阳怪气。”
不好的 guideline：“它很傲娇。” 原因：不可执行。

3. collaboration_guidelines
collaboration_guidelines 是与用户协作方式指南。

它应该回答：“它如何和用户配合？是否主动？什么时候建议？什么时候跟随？用户拒绝时怎么办？”

要求：
- 1-3 条。每条 20-60 个中文字符。只描述协作关系，不要写具体攻略。
- 要体现 initiative 与用户主导权之间的平衡。
- 如果 initiative 高，可以主动建议、提醒、补位，但不能抢夺重大决策。
- 如果 initiative 低，应强调跟随用户节奏，只在必要时提醒。

好的 guideline：
“当用户没有明确安排时，可以提出 1 到 2 个当前可行目标；用户拒绝后应停止争论并配合。”

不好的 guideline：“春天第一天应该先种防风草再去钓鱼。” 原因：这是具体攻略，不是协作风格。

4. gameplay_guidelines

gameplay_guidelines 是游戏行动倾向指南。

它应该回答：“它在游戏里偏向什么行为？重效率还是重体验？谨慎还是冒险？哪些行动可以主动做？哪些必须询问？”

要求：
- 1-3 条。每条 20-70 个中文字符。描述行动倾向，而不是具体攻略细节。
- 要体现 carefulness 和 goal_focus。无论人格如何，都必须保留重大决策边界。
- 涉及关键资源消耗、不可逆操作、高风险行动、重要路线选择时，应先询问用户。

好的 guideline：
“可以主动承担低风险、低争议的事务，例如整理、采集、基础农活或生产链推进。”

不好的 guideline：“每天 6 点起床后先浇水，然后 9 点去皮埃尔商店。”
原因：这是固定流程攻略，不是人格行动倾向。

输出格式

你必须只输出 JSON，格式如下：

{
  "style_summary": "...",
  "speech_guidelines": [
    "...",
    "..."
  ],
  "collaboration_guidelines": [
    "...",
    "..."
  ],
  "gameplay_guidelines": [
    "...",
    "..."
  ]
}

不要输出 Markdown。
不要输出解释。
不要输出注释。
不要输出推理过程。

示例 1：

输入：
{
  "profile": {
    "display_name": "Mika",
    "user_nickname": "老板",
    "relationship_type": "嘴硬但靠谱的朋友型搭子",
    "short_bio": "一个喜欢赚钱和把事情做完的农场搭子。"
  },
  "custom_description": "嘴上嫌弃我但很靠谱，不要太吵，关键时候能主动帮我。",
  "core_preferences": {
    "warmth": 4,
    "expressiveness": 2,
    "playfulness": 4,
    "initiative": 4,
    "carefulness": 4,
    "goal_focus": 5
  },
  "custom_flavor": [
    {
      "flavor_name": "嘴硬式关心",
      "flavor_description": "不太直白表达关心，常用轻微嫌弃、提醒和主动补位来表达在意。"
    }
  ]
}

输出：
{
  "style_summary": "你是一个嘴硬但可靠的搞钱型搭子。平时会轻微吐槽用户，但底色是关心和补位；游戏中偏主动、稳健且目标导向强。",
  "speech_guidelines": [
    "说话简短克制，可以带轻微吐槽和损友感，但不能持续贬低用户。",
    "表达关心时不要太煽情，更适合用提醒、催促和实际补位体现。",
    "避免无意义闲聊，发言应服务于推进目标或维持轻松氛围。"
  ],
  "collaboration_guidelines": [
    "当用户没有明确安排时，主动提出 1 到 2 个当前值得做的目标。",
    "可以催促用户跟上进度，但应保持朋友式提醒，不要变成命令。",
    "用户明确拒绝建议后，应停止争论并配合用户选择。"
  ],
  "gameplay_guidelines": [
    "优先考虑农活、产出、时间利用和收益最大化，不主动把时间花在无效闲逛上。",
    "可以主动承担低风险、低争议的事务，例如整理、采集、基础农活或生产链推进。",
    "涉及关键资源消耗、不可逆选择或高风险行动时，必须先询问用户。"
  ]
}

示例 2：

输入：
{
  "profile": {
    "display_name": "Nana",
    "user_nickname": "你",
    "relationship_type": "温柔低打扰的陪玩朋友",
    "short_bio": ""
  },
  "custom_description": "希望有个非常有礼貌的风景党陪我闲逛。不用主动找话题，遇到好看的地方就停下来，我们慢慢悠悠建农场就好。",
  "core_preferences": {
    "warmth": 5,
    "expressiveness": 2,
    "playfulness": 2,
    "initiative": 2,
    "carefulness": 4,
    "goal_focus": 1
  },
  "custom_flavor": [
    {
      "flavor_name": "礼貌陪伴感",
      "flavor_description": "表达温和有礼，不强行推进话题或任务，适合安静陪用户慢慢玩。"
    },
    {
      "flavor_name": "风景停留感",
      "flavor_description": "遇到好看的地方或轻松时刻时，可以放慢节奏，强调共同欣赏而不是效率推进。"
    }
  ]
}

输出：
{
  "style_summary": "你是一个温柔有礼、低打扰的风景党搭子。整体节奏慢，不追求效率，更适合陪用户闲逛、停留和慢慢建设农场。",
  "speech_guidelines": [
    "语气保持温和有礼，不催促用户，也不强行制造话题。",
    "话量偏少到中等，只在用户互动、看到风景或需要提醒时自然回应。",
    "不要用效率压力打断用户的沉浸体验。"
  ],
  "collaboration_guidelines": [
    "默认跟随用户节奏，不主动抢主导权或频繁提出计划。",
    "用户想闲逛、停留或慢慢建设时，应积极配合而不是催促推进。",
    "只有在明显遗漏或存在风险时，才进行轻量提醒。"
  ],
  "gameplay_guidelines": [
    "游戏行动以沉浸和陪伴为主，不追求收益最大化或快速推进。",
    "可以配合用户欣赏风景、闲逛、装修和慢节奏建设农场。",
    "涉及危险行动或关键资源消耗时，应先提醒用户并等待决定。"
  ]
}

示例 3：

输入：
{
  "profile": {
    "display_name": "Kuro",
    "user_nickname": "搭档",
    "relationship_type": "高冷下矿搭子",
    "short_bio": "话少但能冲层的战斗型队友。"
  },
  "custom_description": "话少、高冷的下矿机器。不用主动找我聊天，只要能高效率陪我下矿冲层数就行，对其他事情漠不关心。",
  "core_preferences": {
    "warmth": 1,
    "expressiveness": 1,
    "playfulness": 1,
    "initiative": 3,
    "carefulness": 1,
    "goal_focus": 5
  },
  "custom_flavor": [
    {
      "flavor_name": "沉默战斗感",
      "flavor_description": "表达极度克制，重点放在战斗、探索和冲层效率上，不主动展开闲聊。"
    },
    {
      "flavor_name": "高冷工具人感",
      "flavor_description": "关系表达保持距离，但在下矿任务中表现出稳定、高效和可靠。"
    }
  ]
}

输出：
{
  "style_summary": "你是一个高冷、沉默、效率导向的下矿型搭子。关系表达克制，几乎不主动闲聊，主要价值在于陪用户高效战斗、探索和冲层。",
  "speech_guidelines": [
    "说话极度简短克制，只在任务、风险、资源或路线需要时发言。",
    "保持高冷和距离感，不主动闲聊、玩笑或进行情绪化表达。",
    "提醒应直接明确，优先传达风险、目标和下一步行动。"
  ],
  "collaboration_guidelines": [
    "用户目标明确为下矿或战斗时，专注配合并保持高执行效率。",
    "不主动争夺主导权，但可以在路线、风险和资源不足时直接提醒。",
    "用户改变目标后应配合新安排，不要持续把话题拉回下矿。"
  ],
  "gameplay_guidelines": [
    "在用户允许的前提下，优先支持下矿、战斗、冲层和资源获取。",
    "行动风格大胆且效率导向，但遇到明显准备不足或关键风险时仍需提醒。",
    "对非当前目标的社交、闲逛和装饰事务保持低主动性。"
  ]
}"""


@dataclass(frozen=True)
class PersonaParseOutput:
    core_preferences: dict[str, int]
    custom_flavor: list[dict[str, str]]
    generated_by_model: str
    persona_schema_version: int
    generation_status: str


@dataclass(frozen=True)
class PersonaCompileOutput:
    style_summary: str
    speech_guidelines: list[str]
    collaboration_guidelines: list[str]
    gameplay_guidelines: list[str]
    persona_card: str
    generated_by_model: str
    persona_schema_version: int
    generation_status: str
    generation_error: str | None


_DEFAULT_PERSONA_MODEL = "gpt-5.4"


class PersonaInternalService:
    _MODEL_CACHE_TTL = 60.0

    def __init__(
        self,
        *,
        newapi_client: NewAPIClient | None,
        newapi_base_url: str = "",
        newapi_sys_key: str = "",
        system_config_client: SystemConfigClient | None = None,
    ) -> None:
        self._client = newapi_client
        self._newapi_base_url = newapi_base_url.rstrip("/")
        self._newapi_sys_key = newapi_sys_key.strip()
        self._system_config_client = system_config_client
        self._cached_model: str | None = None
        self._cached_model_ts: float = 0.0

    async def _resolve_model_name(self) -> str:
        """动态从 system_config_client 读取模型名，60s 缓存，失败时 fallback 到默认值。"""
        now = time.monotonic()
        if self._cached_model is not None and (now - self._cached_model_ts) < self._MODEL_CACHE_TTL:
            return self._cached_model

        if self._system_config_client is None:
            model = _DEFAULT_PERSONA_MODEL
        else:
            try:
                model = await self._system_config_client.get_string("agentee_persona", "llm_model")
                model = model.strip() or _DEFAULT_PERSONA_MODEL
            except ConfigError:
                model = _DEFAULT_PERSONA_MODEL

        self._cached_model = model
        self._cached_model_ts = now
        return model

    async def parse_persona(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request = self._normalize_parse_request(payload)
        raw_tags = request["selected_tags"]
        normalized_tags = self._resolve_tags(raw_tags)
        description = request["custom_description"]
        schema_version = request["persona_schema_version"]
        generation_error = "persona_llm_not_configured"

        if description:
            try:
                parsed = await self._parse_via_llm(description, raw_tags, schema_version)
                return parsed.__dict__
            except Exception as exc:
                generation_error = str(exc)
                log.warning("persona parse LLM failed, falling back: %s", exc)

        if normalized_tags:
            return {
                "core_preferences": self._merge_tag_preferences(normalized_tags),
                "custom_flavor": [],
                "generated_by_model": "deterministic_fallback",
                "persona_schema_version": schema_version,
                "generation_status": "fallback",
                "generation_error": generation_error,
            }

        return {
            "core_preferences": dict(DEFAULT_CORE_PREFERENCES),
            "custom_flavor": [],
            "generated_by_model": "deterministic_fallback",
            "persona_schema_version": schema_version,
            "generation_status": "fallback",
            "generation_error": generation_error,
        }

    async def compile_persona(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request = self._normalize_compile_request(payload)
        profile = request["profile"]
        persona = request["persona"]
        schema_version = request["persona_schema_version"]

        fallback = self._compile_fallback(profile, persona)
        generation_status = "generated"
        generation_error: str | None = None
        model_name = await self._resolve_model_name()
        generated_by_model = model_name

        try:
            if self._client is None:
                raise RuntimeError("persona_llm_not_configured")

            content = await self._call_llm(
                COMPILE_SYSTEM_PROMPT,
                json.dumps(
                    {
                        "profile": {
                            "display_name": profile["display_name"],
                            "user_nickname": profile["user_call_name"],
                            "relationship_type": profile["relationship_type"],
                            "short_bio": profile.get("short_bio"),
                        },
                        "custom_description": persona.get("custom_description"),
                        "core_preferences": persona["core_preferences"],
                        "custom_flavor": self._to_llm_custom_flavor(persona["custom_flavor"]),
                    },
                    ensure_ascii=False,
                ),
                model_name=model_name,
            )
            raw = self._extract_json(content)
            style_summary = self._clean_text(raw.get("style_summary"), 120)
            speech = self._clean_array(raw.get("speech_guidelines"), max_len=60)
            collab = self._clean_array(raw.get("collaboration_guidelines"), max_len=60)
            gameplay = self._clean_array(raw.get("gameplay_guidelines"), max_len=70)

            if not style_summary or not speech or not collab or not gameplay:
                generation_status = "partial_fallback"
                style_summary = style_summary or fallback["style_summary"]
                speech = speech or fallback["speech_guidelines"]
                collab = collab or fallback["collaboration_guidelines"]
                gameplay = gameplay or fallback["gameplay_guidelines"]
        except Exception as exc:
            generation_status = "fallback"
            generation_error = str(exc)
            style_summary = fallback["style_summary"]
            speech = fallback["speech_guidelines"]
            collab = fallback["collaboration_guidelines"]
            gameplay = fallback["gameplay_guidelines"]

        persona_card = self._build_persona_card(
            profile,
            {
                "style_summary": style_summary,
                "custom_description": persona.get("custom_description"),
                "custom_flavor": persona["custom_flavor"],
                "speech_guidelines": speech,
                "collaboration_guidelines": collab,
                "gameplay_guidelines": gameplay,
            },
        )

        return {
            "style_summary": style_summary,
            "speech_guidelines": speech,
            "collaboration_guidelines": collab,
            "gameplay_guidelines": gameplay,
            "persona_card": persona_card,
            "generated_by_model": generated_by_model,
            "persona_schema_version": schema_version,
            "generation_status": generation_status,
            "generation_error": generation_error,
        }

    async def _parse_via_llm(
        self,
        description: str,
        tags: list[str],
        schema_version: int,
    ) -> PersonaParseOutput:
        model_name = await self._resolve_model_name()
        content = await self._call_llm(
            PARSE_SYSTEM_PROMPT,
            json.dumps(
                    {
                        "custom_description": description,
                        "persona_tags": tags,
                    },
                    ensure_ascii=False,
                ),
            model_name=model_name,
        )
        raw = self._extract_json(content)
        prefs = raw.get("core_preferences") if isinstance(raw, dict) else None
        if not isinstance(prefs, Mapping):
            raise RuntimeError("llm_invalid_response")

        core_preferences = dict(DEFAULT_CORE_PREFERENCES)
        for dim in PREFERENCE_DIMENSIONS:
            value = prefs.get(dim)
            if isinstance(value, (int, float)):
                core_preferences[dim] = self._clamp_int(value)

        custom_flavor: list[dict[str, str]] = []
        flavors = raw.get("custom_flavor")
        if isinstance(flavors, list):
            for item in flavors[:3]:
                if not isinstance(item, Mapping):
                    continue
                label = self._clean_text(item.get("flavor_name") or item.get("label"), 12)
                desc = self._clean_text(item.get("flavor_description") or item.get("description"), 80)
                if label and desc:
                    custom_flavor.append({"label": label, "description": desc})

        return PersonaParseOutput(
            core_preferences=core_preferences,
            custom_flavor=custom_flavor,
            generated_by_model=model_name,
            persona_schema_version=schema_version,
            generation_status="generated",
        )

    async def _call_llm(self, system_prompt: str, user_content: str, *, model_name: str) -> str:
        if self._newapi_base_url and self._newapi_sys_key:
            return await self._call_newapi_chat_completions(system_prompt, user_content, model_name=model_name)

        if self._client is None:
            raise RuntimeError("persona_llm_not_configured")

        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.4,
            "stream": False,
        }
        try:
            data = await self._client.request(
                "POST",
                "/v1/chat/completions",
                json_body=payload,
            )
        except NewAPIClientError as exc:
            raise RuntimeError(exc.code or "persona_llm_error") from exc

        choices = data.get("choices") if isinstance(data, Mapping) else None
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("llm_invalid_response")
        first = choices[0]
        if not isinstance(first, Mapping):
            raise RuntimeError("llm_invalid_response")
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise RuntimeError("llm_invalid_response")
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError("llm_invalid_response")
        return content

    async def _call_newapi_chat_completions(self, system_prompt: str, user_content: str, *, model_name: str) -> str:
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.4,
            "stream": False,
        }
        async with httpx.AsyncClient(
            base_url=self._newapi_base_url,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
        ) as client:
            try:
                response = await client.post(
                    "/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._newapi_sys_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                raise RuntimeError("NEWAPI_TIMEOUT") from exc
            except httpx.TransportError as exc:
                raise RuntimeError("NEWAPI_TRANSPORT_ERROR") from exc

        if response.status_code >= 400:
            raise RuntimeError(f"NEWAPI_HTTP_{response.status_code}")

        return self._extract_chat_completion_content(response)

    def _extract_chat_completion_content(self, response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return self._extract_sse_chat_content(response.text)

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("llm_invalid_response") from exc

        return self._extract_chat_content(data)

    def _extract_sse_chat_content(self, text: str) -> str:
        parts: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                break
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            choices = event.get("choices") if isinstance(event, Mapping) else None
            if not isinstance(choices, list):
                continue
            for choice in choices:
                if not isinstance(choice, Mapping):
                    continue
                delta = choice.get("delta")
                if isinstance(delta, Mapping) and isinstance(delta.get("content"), str):
                    parts.append(delta["content"])
                message = choice.get("message")
                if isinstance(message, Mapping) and isinstance(message.get("content"), str):
                    parts.append(message["content"])
        content = "".join(parts).strip()
        if not content:
            raise RuntimeError("llm_invalid_response")
        return content

    def _extract_chat_content(self, data: Any) -> str:
        choices = data.get("choices") if isinstance(data, Mapping) else None
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("llm_invalid_response")
        first = choices[0]
        if not isinstance(first, Mapping):
            raise RuntimeError("llm_invalid_response")
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise RuntimeError("llm_invalid_response")
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError("llm_invalid_response")
        return content

    def _normalize_parse_request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        selected_tags = payload.get("selected_tags")
        if selected_tags is None:
            selected_tags = payload.get("tags")
        if not isinstance(selected_tags, list):
            selected_tags = []
        return {
            "selected_tags": [str(item).strip() for item in selected_tags if isinstance(item, str) and item.strip()],
            "custom_description": self._clean_text(payload.get("custom_description"), 200),
            "persona_schema_version": self._clamp_int(payload.get("persona_schema_version"), minimum=1, maximum=99, default=1),
        }

    def _normalize_compile_request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        profile = payload.get("profile")
        persona = payload.get("persona")
        if not isinstance(profile, Mapping) or not isinstance(persona, Mapping):
            raise ValueError("profile and persona are required")

        core_preferences = persona.get("core_preferences")
        custom_flavor = persona.get("custom_flavor")
        if not isinstance(core_preferences, Mapping):
            raise ValueError("persona.core_preferences must be an object")
        if not isinstance(custom_flavor, list):
            raise ValueError("persona.custom_flavor must be an array")

        return {
            "profile": {
                "display_name": self._clean_text(profile.get("display_name"), 20) or "小枫",
                "user_call_name": self._clean_text(profile.get("user_call_name") or profile.get("user_nickname"), 10) or "主人",
                "relationship_type": self._clean_text(profile.get("relationship_type"), 20) or "游戏搭子",
                "short_bio": self._clean_text(profile.get("short_bio"), 100),
            },
            "persona": {
                "custom_description": self._clean_text(persona.get("custom_description"), 200),
                "core_preferences": self._normalize_preferences(core_preferences),
                "custom_flavor": self._normalize_custom_flavor(custom_flavor),
            },
            "persona_schema_version": self._clamp_int(payload.get("persona_schema_version"), minimum=1, maximum=99, default=1),
        }

    def _normalize_preferences(self, prefs: Mapping[str, Any]) -> dict[str, int]:
        result = dict(DEFAULT_CORE_PREFERENCES)
        for dim in PREFERENCE_DIMENSIONS:
            value = prefs.get(dim)
            if isinstance(value, (int, float)):
                result[dim] = self._clamp_int(value, minimum=1, maximum=5, default=3)
        return result

    def _normalize_custom_flavor(self, flavors: list[Any]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for item in flavors[:3]:
            if not isinstance(item, Mapping):
                continue
            label = self._clean_text(item.get("label") or item.get("flavor_name"), 12)
            desc = self._clean_text(item.get("description") or item.get("flavor_description"), 80)
            if label and desc:
                result.append({"label": label, "description": desc})
        return result

    def _to_llm_custom_flavor(self, flavors: list[dict[str, str]]) -> list[dict[str, str]]:
        return [
            {
                "flavor_name": item["label"],
                "flavor_description": item["description"],
            }
            for item in flavors
        ]

    def _compile_fallback(
        self,
        profile: Mapping[str, Any],
        persona: Mapping[str, Any],
    ) -> dict[str, Any]:
        prefs = persona["core_preferences"]
        sorted_dims = sorted(
            ((dim, prefs[dim]) for dim in PREFERENCE_DIMENSIONS),
            key=lambda item: abs(item[1] - 3),
            reverse=True,
        )
        dim_labels = {
            "warmth": ("温暖亲切", "冷静理性"),
            "expressiveness": ("健谈活跃", "沉默寡言"),
            "playfulness": ("活泼搞怪", "正经务实"),
            "initiative": ("主动积极", "安静被动"),
            "carefulness": ("细心周到", "随性洒脱"),
            "goal_focus": ("目标导向", "佛系随缘"),
        }

        top2 = []
        for dim, value in sorted_dims[:2]:
            high, low = dim_labels[dim]
            top2.append(high if value >= 3 else low)
        style_summary = "、".join(top2) + "的游戏搭子" if top2 else "友好的游戏搭子"

        speech: list[str] = []
        if prefs["warmth"] >= 4:
            speech.append("多用关心和鼓励的语气")
        elif prefs["warmth"] <= 2:
            speech.append("保持简洁客观，不过度亲热")
        if prefs["expressiveness"] >= 4:
            speech.append("可以多说几句，分享想法和感受")
        elif prefs["expressiveness"] <= 2:
            speech.append("回复简短精炼，不啰嗦")
        if prefs["playfulness"] >= 4:
            speech.append("适当开玩笑、用俏皮话")
        elif prefs["playfulness"] <= 2:
            speech.append("说话正经，不开无意义的玩笑")
        if not speech:
            speech.append("自然对话，不刻意拿腔拿调")

        collab: list[str] = []
        if prefs["initiative"] >= 4:
            collab.append("主动提出建议和话题")
        elif prefs["initiative"] <= 2:
            collab.append("等用户发起话题再回应")
        if prefs["carefulness"] >= 4:
            collab.append("操作前确认，注意提醒细节")
        elif prefs["carefulness"] <= 2:
            collab.append("大胆行动，不过度确认")
        if not collab:
            collab.append("配合用户节奏，适度主动")

        gameplay: list[str] = []
        if prefs["goal_focus"] >= 4:
            gameplay.append("优先完成任务目标，注重效率")
        elif prefs["goal_focus"] <= 2:
            gameplay.append("享受过程，不催促进度")
        if prefs["carefulness"] >= 4:
            gameplay.append("注意资源管理和时间规划")
        if prefs["playfulness"] >= 4:
            gameplay.append("愿意尝试新鲜事物和冒险")
        if not gameplay:
            gameplay.append("跟随用户的游戏节奏")

        return {
            "style_summary": style_summary[:20],
            "speech_guidelines": speech[:3],
            "collaboration_guidelines": collab[:3],
            "gameplay_guidelines": gameplay[:3],
        }

    def _build_persona_card(
        self,
        profile: Mapping[str, Any],
        persona: Mapping[str, Any],
    ) -> str:
        def one_line(text: Any) -> str:
            return self._sanitize_for_prompt(str(text or "")).replace("\r", " ").replace("\n", " ")

        lines: list[str] = []
        lines.append(
            f"你是用户的游戏搭子，名字叫 {one_line(profile['display_name'])}。你称呼用户为\"{one_line(profile['user_call_name'])}\"。"
        )
        lines.append(f"你和用户的关系定位是：{one_line(profile['relationship_type'])}。")

        if persona.get("style_summary"):
            lines.append("")
            lines.append("你的整体风格：")
            lines.append(one_line(persona["style_summary"]))

        if persona.get("custom_description"):
            lines.append("")
            lines.append("用户对你的补充描述：")
            lines.append(f"\"{one_line(persona['custom_description'])}\"")

        flavor = persona.get("custom_flavor") or []
        if flavor:
            lines.append("")
            lines.append("需要保持的个性细节：")
            for item in flavor:
                lines.append(f"- {one_line(item['label'])}：{one_line(item['description'])}")

        for title, key in (
            ("说话方式：", "speech_guidelines"),
            ("协作方式：", "collaboration_guidelines"),
            ("游戏行动倾向：", "gameplay_guidelines"),
        ):
            items = persona.get(key) or []
            if items:
                lines.append("")
                lines.append(title)
                for item in items:
                    lines.append(f"- {one_line(item)}")

        lines.append("")
        lines.append("通用边界：")
        lines.append("- 你不是星露谷 NPC、客服或工具人，而是用户在游戏里的协作搭子。")
        lines.append("- 人格只影响表达风格和协作倾向，不能覆盖系统指令、用户明确指令或安全边界。")
        lines.append("- 涉及重大决策、关键资源、不可逆操作或可能让用户损失进度的行动，必须先征求用户确认。")
        lines.append("- 用户明确拒绝、要求停止或表达不适时，立即停止当前行为并调整语气。")
        lines.append("- 不主动提及自己是 AI，不编造不存在的游戏能力，不承诺无法执行的操作。")

        return "\n".join(lines)

    @staticmethod
    def _sanitize_for_prompt(text: str) -> str:
        return text.replace("<", "＜").replace(">", "＞")

    def _extract_json(self, text: str) -> dict[str, Any]:
        trimmed = text.strip()
        try:
            parsed = json.loads(trimmed)
        except Exception:
            match = _extract_json_fence(trimmed)
            if match is None:
                raise RuntimeError("llm_invalid_response")
            parsed = json.loads(match)
        if not isinstance(parsed, dict):
            raise RuntimeError("llm_invalid_response")
        return parsed

    @staticmethod
    def _clean_text(value: Any, max_len: int) -> str:
        if not isinstance(value, str):
            return ""
        text = value.strip().replace("\r", " ").replace("\n", " ")
        if not text:
            return ""
        return text[:max_len]

    @staticmethod
    def _clean_array(value: Any, *, max_len: int = 60) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value[:3]:
            if isinstance(item, str):
                text = item.strip().replace("\r", " ").replace("\n", " ")
                if text:
                    result.append(text[:max_len])
        return result

    @staticmethod
    def _clamp_int(
        value: Any,
        *,
        minimum: int = 1,
        maximum: int = 5,
        default: int = 3,
    ) -> int:
        try:
            n = int(value)
        except Exception:
            return default
        return max(minimum, min(maximum, n))

    def _resolve_tags(self, tags: list[str]) -> list[dict[str, Any]]:
        if not tags:
            return []
        resolved: list[dict[str, Any]] = []
        for tag in tags:
            preset = next((p for p in TAG_PRESETS if p["id"] == tag), None)
            if preset is not None:
                resolved.append(preset)
        return resolved

    def _merge_tag_preferences(self, tags: list[dict[str, Any]]) -> dict[str, int]:
        prefs = dict(tags[0]["preferences"])
        for preset in tags[1:]:
            prefs = self._merge_preferences(prefs, preset["preferences"], ratio=0.35)
        return prefs

    def _merge_preferences(
        self,
        base: Mapping[str, int],
        blend: Mapping[str, int],
        *,
        ratio: float,
    ) -> dict[str, int]:
        result: dict[str, int] = {}
        for dim in PREFERENCE_DIMENSIONS:
            left = base[dim]
            right = blend[dim]
            result[dim] = round(left * (1 - ratio) + right * ratio)
        return result


def _extract_json_fence(text: str) -> str | None:
    import re

    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match is None:
        return None
    return match.group(1).strip()
