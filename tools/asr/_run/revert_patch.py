# -*- coding: utf-8 -*-
import io
p = r"E:\01-项目\QQScope\core\sources\_pack_extract.py"
src = io.open(p, encoding="utf-8").read()
i = src.index("def _nudge_label(d) -> str:")
j = src.index("def _classify(ct, d, has_sticker=False, raw=b\"\"):")
src = src[:i] + "def _classify(ct, d, has_sticker=False):" + src[j + len("def _classify(ct, d, has_sticker=False, raw=b\"\"):"):]
SRC_NEW = '''        if not nick and not uid:
            # ct=8 不只承载名片：戳一戳/拍了拍(reply_f48271) 和群成员加入提醒(f48214)
            # 也是 ct=8，以前一律当名片 -> 光秃秃 [名片]。这里按载荷给人话 fallback。
            rich = _nudge_label(d) or _gtip_label(raw)
            if rich:
                return {"kind": "card", "md5": None, "name": "", "size": 0,
                        "duration": None, "fallback": rich}
'''
assert SRC_NEW in src
src = src.replace(SRC_NEW, "", 1)
OLD_BM = ('card = _classify(ct, MessageToDict(c, preserving_proto_field_name=True), False,\n'
          '                                 c.SerializeToString())')
assert OLD_BM in src
src = src.replace(OLD_BM, 'card = _classify(ct, MessageToDict(c, preserving_proto_field_name=True), False)', 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(src)
print("reverted; _nudge_label present:", "_nudge_label" in src, "| raw=b present:", "raw=b" in src)
