import json

from text_sanitize import repair_mojibake_text, sanitize_for_utf8_json


def test_sanitize_replaces_lone_surrogate():
    s = "a\udc00b"
    assert sanitize_for_utf8_json(s) == "a\ufffdb"


def test_ndjson_payload_utf8_bytes_roundtrip_emoji():
    frame = {"t": "x", "m": "ok\u263a"}
    data = (json.dumps(sanitize_for_utf8_json(frame), ensure_ascii=False) + "\n").encode("utf-8")
    assert data.decode("utf-8").strip() == json.dumps(
        {"t": "x", "m": "ok\u263a"}, ensure_ascii=False
    )


def test_repair_chinese_mojibake_text():
    assert repair_mojibake_text("鎴戞槸璇翠綘") == "我是说你"
