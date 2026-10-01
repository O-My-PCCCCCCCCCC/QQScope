# -*- coding: utf-8 -*-
"""给 _pack_extract.py 的 ct==8 分支加「戳一戳 / 群提醒」渲染标签（Lead 已批准）。"""
import io
p = r"E:\01-项目\QQScope\core\sources\_pack_extract.py"
src = io.open(p, encoding="utf-8").read()

HELPERS = '''def _nudge_label(d) -> str:
    """ct=8 的「戳一戳 / 拍了拍」载荷 -> "[戳一戳] <动作文案>"。

    判据来源（NT QQ 9.9.36 实测）：
      MsgContent.reply_f48271  （pb2 已定义字段号 48271，JSON 字符串）
      JSON 形如 {"items":[{"type":"qq","uid":"u_..."}, {"txt":"揉了揉","type":"nor"}, ...]}
      -> 把 items[].txt 拼起来就是「揉了揉 的头」这类动作文案。
    仅在 nc_nickname_*/nc_uid_* 都为空（即不是名片）时才使用。
    """
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
    """ct=8 的「群成员加入提醒」gtip XML -> "[群提醒] <文案>"。

    判据来源（NT QQ 9.9.36 实测）：
      MsgContent 的 **未知字段 48214**（pb2 里没有定义，只能从 wire 里读到；
      字段号是 48214 = 0xBC 0xF8 0x02）。值是 XML 字符串，形如
      <gtip align="center"><qq uin="u_..."/><nor txt="邀请"/><qq uin="u_..."/><nor txt="加入了群聊，并附带了30条聊天记录。"/></gtip>
      -> 把 <nor txt="..."> 拼起来。
    仅在 nc_nickname_*/nc_uid_* 都为空时才使用。
    """
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

assert "def _classify(ct, d, has_sticker=False):" in src
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
            # ct=8 不只承载名片：戳一戳(reply_f48271) 和群成员提醒(未知字段 48214 gtip)
            # 也是 ct=8。以前一律当名片 -> 光秃秃 [名片]（DB 里 11123 条中约 84% 属于这两类）。
            # 这里只改渲染文案，kind 仍是 'card'，不动 schema。
            rich = _nudge_label(d) or _gtip_label(raw)
            if rich:
                return {"kind": "card", "md5": None, "name": "", "size": 0,
                        "duration": None, "fallback": rich}
        out = {"kind": "card", "md5": None, "name": nick, "size": 0,
               "duration": None, "fallback": f"[名片] {nick}" if nick else "[名片]"}
        if uid:
            out["uid"] = uid
        return out'''
assert OLD8 in src
src = src.replace(OLD8, NEW8, 1)

OLD_BM = 'card = _classify(ct, MessageToDict(c, preserving_proto_field_name=True), False)'
NEW_BM = ('card = _classify(ct, MessageToDict(c, preserving_proto_field_name=True), False,\n'
          '                                 c.SerializeToString())')
assert OLD_BM in src
src = src.replace(OLD_BM, NEW_BM, 1)

io.open(p, "w", encoding="utf-8", newline="\n").write(src)
print("patched, size:", len(src))
