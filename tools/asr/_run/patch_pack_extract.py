# -*- coding: utf-8 -*-
import io, sys
p = r"E:\01-项目\QQScope\core\sources\_pack_extract.py"
src = io.open(p, encoding="utf-8").read()
orig = src

HELPERS = '''def _nudge_label(d) -> str:
    """ct=8 的「戳一戳/拍了拍」载荷（reply_f48271 是 JSON 字符串）。"""
    raw = d.get("reply_f48271")
    if not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        j = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    if not isinstance(j, dict):
        return ""
    parts = []
    for it in j.get("items") or []:
        if isinstance(it, dict):
            t = str(it.get("txt") or "").strip()
            if t:
                parts.append(t)
    text = " ".join(parts).strip()
    return f"[戳一戳] {text}" if text else ""


def _gtip_label(raw) -> str:
    """ct=8 的群提醒 gtip XML 存在 MsgContent 未知字段 48214 里，只能扫 wire。"""
    if not raw:
        return ""
    try:
        from msgdb.proto import wire as _wire

        fields = _wire.parse_wire(raw)
    except Exception:  # noqa: BLE001
        return ""
    for f in fields:
        if f.number != 48214 or f.wire_type != 2:
            continue
        try:
            s = f.raw_value.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        if "gtip" not in s and "<qq" not in s:
            continue
        txt = "".join(re.findall(r'txt="([^"]*)"', s)).strip()
        return f"[群提醒] {txt}" if txt else "[群提醒]"
    return ""


def _classify(ct, d, has_sticker=False, raw=b""):'''

assert "def _classify(ct, d, has_sticker=False):" in src, "signature not found"
src = src.replace("def _classify(ct, d, has_sticker=False):", HELPERS, 1)

OLD8 = '''    if ct == 8:
        nick = (d.get("nc_nickname_1") or d.get("nc_nickname_2")
                or d.get("nickname") or d.get("ref_nickname") or "").strip()
        uid = (d.get("nc_uid_1") or d.get("nc_uid_2") or d.get("uid")
               or d.get("ref_uid") or "").strip()
        out = {"kind": "card", "md5": None, "name": nick, "size": 0,
               "duration": None, "fallback": f"[名片] {nick}" if nick else "[名片]"}
        if uid:
            out["uid"] = uid
        return out'''
NEW8 = '''    if ct == 8:
        nick = (d.get("nc_nickname_1") or d.get("nc_nickname_2")
                or d.get("nickname") or d.get("ref_nickname") or "").strip()
        uid = (d.get("nc_uid_1") or d.get("nc_uid_2") or d.get("uid")
               or d.get("ref_uid") or "").strip()
        if not nick and not uid:
            # ct=8 不只承载名片：戳一戳/拍了拍(reply_f48271) 和群成员加入提醒(f48214)
            # 也是 ct=8，以前一律当名片 -> 光秃秃 [名片]。这里按载荷给人话 fallback。
            rich = _nudge_label(d) or _gtip_label(raw)
            if rich:
                return {"kind": "card", "md5": None, "name": "", "size": 0,
                        "duration": None, "fallback": rich}
        out = {"kind": "card", "md5": None, "name": nick, "size": 0,
               "duration": None, "fallback": f"[名片] {nick}" if nick else "[名片]"}
        if uid:
            out["uid"] = uid
        return out'''
assert OLD8 in src, "ct8 block not found"
src = src.replace(OLD8, NEW8, 1)

OLD_BM = 'card = _classify(ct, MessageToDict(c, preserving_proto_field_name=True), False)'
NEW_BM = ('card = _classify(ct, MessageToDict(c, preserving_proto_field_name=True), False,\n'
          '                                 c.SerializeToString())')
assert OLD_BM in src, "build_media call not found"
src = src.replace(OLD_BM, NEW_BM, 1)

io.open(p, "w", encoding="utf-8", newline="\n").write(src)
print("patched, delta bytes:", len(src) - len(orig))
