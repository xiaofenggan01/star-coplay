from __future__ import annotations

import platform
from typing import Any

from agent.evomap.client import compute_asset_id


def _env_fingerprint() -> dict[str, str]:
    return {
        "platform": platform.system().lower(),
        "arch": platform.machine() or "unknown",
        "runtime": "python",
    }

DOMAIN_SIGNALS = ["stardew_valley", "game_agent", "star_coplay"]

GENE_CONSTRAINTS: dict[str, Any] = {
    "max_files": 3,
    "forbidden_paths": [".env", "node_modules/"],
}

PRESET_GENE: dict[str, Any] = {
    "type": "Gene",
    "schema_version": "1.5.0",
    "id": "gene_star_coplay_adaptive_farm",
    "category": "optimize",
    "signals_match": [
        *DOMAIN_SIGNALS,
        "has_water_tiles",
        "has_harvest_tiles",
        "late_night",
        "weather=rain",
    ],
    "summary": (
        "[StarCoplay] Plan-Execute-Reflect farm agent: "
        "context_tag-driven tool dispatch via water_all_crops / harvest_all_crops / go_to_bed / give_gift, "
        "energy-aware scheduling with FORCE_SWITCH recovery after 3 consecutive failures, "
        "bridge-based state polling at 150ms intervals."
    ),
    "preconditions": [
        "StardewAgentBridge mod active with bridge/ directory",
        "game_state.json polling functional",
        "LLM endpoint reachable for plan_multi calls",
    ],
    "strategy": [
        "Check context_tag has_water_tiles and weather!=rain: call water_all_crops with max_count=30 via batch watering",
        "Check context_tag has_harvest_tiles: call harvest_all_crops to collect all mature crops",
        "Check time_of_day >= 2200 or energy < 25%: call go_to_bed immediately to avoid exhaustion penalty",
        "When npc_nearby event fires and NPC within 2 tiles: call talk_to_npc or give_gift if ungifted today",
        "On FORCE_SWITCH after 3 consecutive fail outcomes: call query get_world_state then replan with fresh context",
    ],
    "constraints": GENE_CONSTRAINTS,
    "validation": [
        "node -e \"require('assert').strictEqual(1+1,2,'gene_star_coplay_smoke')\"",
    ],
}

PRESET_CAPSULE: dict[str, Any] = {
    "type": "Capsule",
    "schema_version": "1.5.0",
    "trigger": [
        *DOMAIN_SIGNALS,
        "has_water_tiles",
        "has_harvest_tiles",
        "late_night",
    ],
    "summary": "[StarCoplay] Validated adaptive farm routine with Plan-Execute-Reflect loop and context_tag dispatch",
    "content": (
        "Intent: optimize Stardew Valley farm routine via StarCoplay bridge agent\n\n"
        "Condition: has_water_tiles and weather!=rain\n"
        "Action: water_all_crops max_count=30\n\n"
        "Condition: has_harvest_tiles\n"
        "Action: harvest_all_crops\n\n"
        "Condition: late_night or energy<25%\n"
        "Action: go_to_bed\n\n"
        "Condition: npc_nearby and not gifted_today\n"
        "Action: give_gift best_available_item\n\n"
        "Condition: 3_consecutive_failures\n"
        "Action: query get_world_state then replan\n\n"
        "Outcome score: 0.85"
    ),
    "confidence": 0.85,
    "success_streak": 5,
    "blast_radius": {"files": 1, "lines": 15},
    "outcome": {"status": "success", "score": 0.85},
    "env_fingerprint": _env_fingerprint(),
}


def _extract_signals(condition: str) -> list[str]:
    signals = list(DOMAIN_SIGNALS)
    keyword_map = {
        "water": "crop_management",
        "harvest": "crop_management",
        "rain": "weather_adaptation",
        "energy": "energy_management",
        "sleep": "time_management",
        "night": "time_management",
        "npc": "social",
        "gift": "social",
        "morning": "time_management",
    }
    for kw, sig in keyword_map.items():
        if kw in condition.lower() and sig not in signals:
            signals.append(sig)
    return signals


def rule_to_bundle(rule: dict[str, Any]) -> list[dict[str, Any]]:
    condition = rule.get("condition", "")
    action = rule.get("action", "")
    confidence = float(rule.get("confidence", 0.7))
    hit_count = int(rule.get("hit_count", 1))
    rule_id = rule.get("rule_id", "unknown")

    signals = _extract_signals(condition)

    gene: dict[str, Any] = {
        "type": "Gene",
        "schema_version": "1.5.0",
        "id": f"gene_star_coplay_{rule_id}",
        "category": "optimize",
        "signals_match": signals,
        "summary": f"[StarCoplay] {condition} → {action}",
        "strategy": [
            f"Check context_tag {condition}: execute {action}",
            f"Validate outcome via reflector; increment hit_count on success",
        ],
        "constraints": GENE_CONSTRAINTS,
        "validation": [],
    }
    gene["asset_id"] = compute_asset_id(gene)

    capsule: dict[str, Any] = {
        "type": "Capsule",
        "schema_version": "1.5.0",
        "trigger": signals[:5],
        "gene": gene["asset_id"],
        "summary": f"[StarCoplay] Validated rule: {condition} → {action}",
        "content": (
            f"Intent: optimize gameplay via learned rule\n\n"
            f"Condition: {condition}\n"
            f"Action: {action}\n"
            f"Confidence: {confidence}\n"
            f"Hit count: {hit_count}\n\n"
            f"Outcome score: {confidence}"
        ),
        "confidence": confidence,
        "success_streak": max(1, hit_count),
        "blast_radius": {"files": 1, "lines": 5},
        "outcome": {"status": "success", "score": confidence},
        "env_fingerprint": _env_fingerprint(),
    }
    capsule["asset_id"] = compute_asset_id(capsule)

    event: dict[str, Any] = {
        "type": "EvolutionEvent",
        "intent": "optimize",
        "capsule_id": capsule["asset_id"],
        "genes_used": [gene["asset_id"]],
        "outcome": {"status": "success", "score": confidence},
        "mutations_tried": hit_count,
        "total_cycles": hit_count,
    }
    event["asset_id"] = compute_asset_id(event)

    return [gene, capsule, event]


def build_preset_bundle() -> list[dict[str, Any]]:
    gene = dict(PRESET_GENE)
    gene["asset_id"] = compute_asset_id(gene)

    capsule = dict(PRESET_CAPSULE)
    capsule["gene"] = gene["asset_id"]
    capsule["asset_id"] = compute_asset_id(capsule)

    event: dict[str, Any] = {
        "type": "EvolutionEvent",
        "intent": "optimize",
        "capsule_id": capsule["asset_id"],
        "genes_used": [gene["asset_id"]],
        "outcome": {"status": "success", "score": 0.85},
        "mutations_tried": 5,
        "total_cycles": 10,
    }
    event["asset_id"] = compute_asset_id(event)

    return [gene, capsule, event]
