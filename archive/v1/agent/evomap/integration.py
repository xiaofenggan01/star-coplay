from __future__ import annotations

import json
import logging
import threading
from typing import Any

from agent.debug.runtime import sanitize_value
from agent.evomap.client import GEPClient
from agent.evomap.gene_builder import build_preset_bundle, rule_to_bundle
from agent.memory.rules import LearnedRules


def _agent() -> logging.Logger:
    return logging.getLogger("agent")

MIN_CONFIDENCE = 0.8
MIN_HIT_COUNT = 3
HEARTBEAT_INTERVAL = 300


def _parse_decision(resp: dict[str, Any]) -> str:
    if resp.get("error"):
        return "error"
    payload = resp.get("payload", resp)
    return payload.get("decision", "accepted")


class EvoMapIntegration:
    def __init__(self, client: GEPClient, rules: LearnedRules) -> None:
        self.client = client
        self.rules = rules
        self._published_ids: set[str] = set()
        self._heartbeat_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def ensure_registered(self) -> bool:
        cred_path = (self.client.store_dir / "node.json").resolve()
        if self.client.registered:
            _agent().info(
                json.dumps(
                    {
                        "evomap_event": "session_cached",
                        "node_id": self.client.node_id,
                        "credentials_path": str(cred_path),
                    },
                    ensure_ascii=False,
                )
            )
            print("evomap: using saved node", self.client.node_id)
            print("evomap: credentials", cred_path)
            return True
        self.client.hello()
        return self.client.registered

    def publish_preset(self) -> dict[str, Any]:
        if not self.client.registered:
            return {"error": True, "detail": "not registered"}
        bundle = build_preset_bundle()

        resp = self.client.publish(bundle)
        decision = _parse_decision(resp)
        safe = sanitize_value(resp)
        _agent().info(
            json.dumps({"evomap_event": "publish_baseline", "decision": decision, "response": safe}, ensure_ascii=False)
        )
        print(f"evomap: publish_baseline [{decision}]", json.dumps(safe, ensure_ascii=False))
        return resp

    def publish_mature_rules(self) -> int:
        if not self.client.registered:
            return 0
        all_rules = self.rules.all()
        count = 0
        for rule in all_rules:
            conf = float(rule.get("confidence", 0))
            hits = int(rule.get("hit_count", 0))
            rule_id = rule.get("rule_id", "")
            if conf < MIN_CONFIDENCE or hits < MIN_HIT_COUNT:
                continue
            if rule_id in self._published_ids:
                continue
            bundle = rule_to_bundle(rule)
            resp = self.client.publish(bundle)
            decision = _parse_decision(resp)
            if decision == "accepted":
                self._published_ids.add(rule_id)
                count += 1
                _agent().info(json.dumps({"evomap_event": "publish_rule", "rule_id": rule_id, "decision": decision}, ensure_ascii=False))
            elif decision == "quarantine":
                _agent().info("evomap publish quarantined for %s, will retry next cycle", rule_id)
            else:
                _agent().warning("evomap publish failed for %s: %s", rule_id, resp)
        return count

    @staticmethod
    def _has_context_tag(text: str) -> bool:
        markers = ("location=", "weather=", "has_water", "has_harvest", "late_night",
                   "early_morning", "npc_nearby", "energy", "time_of_day")
        low = text.lower()
        return any(m in low for m in markers)

    def _parse_capsule_rules(self, asset: dict[str, Any]) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        content = asset.get("content", "")
        lines = content.split("\n")
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped.startswith("Condition:"):
                cond = stripped[len("Condition:"):].strip()
                act = ""
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if next_line.startswith("Action:"):
                        act = next_line[len("Action:"):].strip()
                        i += 1
                if cond and act:
                    pairs.append((cond, act))
            i += 1

        if not pairs:
            strategy = asset.get("strategy") or []
            for step in strategy:
                s = str(step)
                if ": " in s:
                    parts = s.split(": ", 1)
                    cond_part = parts[0].strip()
                    act_part = parts[1].strip()
                    for prefix in ("Check context_tag ", "Check ", "When "):
                        if cond_part.startswith(prefix):
                            cond_part = cond_part[len(prefix):]
                            break
                    for prefix in ("call ", "execute "):
                        if act_part.lower().startswith(prefix):
                            act_part = act_part[len(prefix):]
                            break
                    if cond_part and act_part:
                        pairs.append((cond_part, act_part))

        return pairs

    def fetch_community_rules(self) -> int:
        if not self.client.registered:
            return 0
        resp = self.client.fetch(signals=["stardew_valley"], asset_type="Capsule")
        assets = resp.get("payload", {}).get("assets") or resp.get("assets") or []
        count = 0
        for asset in assets:
            if asset.get("type") != "Capsule":
                continue
            pairs = self._parse_capsule_rules(asset)
            conf = float(asset.get("confidence", 0.7))
            for cond, act in pairs:
                if not self._has_context_tag(cond):
                    continue
                self.rules.add(cond, act, conf)
                count += 1
        if count:
            _agent().info(
                json.dumps({"evomap_event": "fetch_community", "imported_rules": count}, ensure_ascii=False)
            )
            print("evomap: fetch_community imported", count, "rules — see agent/debug/store/agent.log")
        return count

    def start_heartbeat(self) -> None:
        if self._heartbeat_thread is not None:
            return
        self._stop_event.clear()
        t = threading.Thread(target=self._heartbeat_loop, daemon=True)
        t.start()
        self._heartbeat_thread = t

    def stop_heartbeat(self) -> None:
        self._stop_event.set()
        self._heartbeat_thread = None

    def _heartbeat_loop(self) -> None:
        interval = HEARTBEAT_INTERVAL
        while not self._stop_event.is_set():
            try:
                resp = self.client.heartbeat()
                next_ms = resp.get("next_heartbeat_ms")
                if isinstance(next_ms, (int, float)) and next_ms > 0:
                    interval = next_ms / 1000
            except Exception as e:
                _agent().warning("evomap heartbeat error: %s", e)
            self._stop_event.wait(interval)
