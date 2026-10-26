---
name: go_shopping
description: "前往指定商店购买物品。当玩家说"帮我买种子"、"去商店买东西"、你判断需要购买物资时使用。支持种子店、鱼店、酒馆、铁匠铺、动物店、木匠铺、诊所。"
category: economy
version: "1.0.0"
energy_estimate: "low"
time_estimate: "30-60s"
parameters:
  shop:
    type: string
    description: "商店 ID，如 SeedShop、FishShop、Saloon、Blacksmith、AnimalShop、Carpenter、Hospital"
    default: "SeedShop"
  items:
    type: array
    description: "购买清单，每项包含 item（SDV 内部英文名）和 qty（数量），如 [{\"item\": \"Parsnip Seeds\", \"qty\": 10}]"
    default: []
tags: [economy, shopping, navigation]
---

前往指定商店购买物品。当玩家说"帮我买种子"、"去商店买东西"、你判断需要购买物资时，都应使用此技能。支持所有主要商店：种子店、鱼店、酒馆、铁匠铺、动物店、木匠铺、诊所。

参数：shop=SeedShop(商店ID), items=[](购买清单，含 item 和 qty)
前提：商店当天营业，有足够金币，背包有空间
示例："帮我买 10 个防风草种子" → skill:go_shopping(shop="SeedShop", items=[{"item": "Parsnip Seeds", "qty": 10}])

### 支持的商店 ID

| shop 参数 | 商店 | 营业时间 |
|-----------|------|---------|
| `SeedShop` | Pierre 种子店 | 周三关门；9:00–17:00 |
| `FishShop` | Willy 鱼具店 | 9:00–17:00 |
| `Saloon` | Gus 酒馆 | 12:00–24:00 |
| `Blacksmith` | Clint 铁匠铺 | 9:00–16:00 |
| `AnimalShop` | Marnie 动物店 | 9:00–16:00；周一、周二关门 |
| `Carpenter` | Robin 木匠铺 | 9:00–17:00；周二关门 |
| `Hospital` | Harvey 诊所 | 9:00–15:30 |

### item 名称约定

item 使用 SDV **英文内部名**（ShopMenu 通过 ItemId 匹配）。常见示例：

- 种子类：`Parsnip Seeds`、`Bean Starter`、`Cauliflower Seeds`、`Potato Seeds`
- 工具类：`Fishing Rod`、`Bait`
- 食物类：`Salad`、`Beer`、`Coffee`
- 动物类：`Chicken`、`Cow`

### 前置条件

- 商店当天营业（未到营业截止时间）
- 背包有足够金币
- 背包有空余空间（非堆叠物品）

### 边界情况

- 购买清单为空 → 立即返回成功，不导航
- 部分购买成功（金币不足 / 库存不足）→ `status: partial`，返回实际购买数量
- 商店未营业 → `menu_interact` 返回失败，Skill 返回 `status: failed`
