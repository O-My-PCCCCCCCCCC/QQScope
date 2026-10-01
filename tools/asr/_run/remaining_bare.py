# -*- coding: utf-8 -*-
"""剩余 833 条光秃 [名片] 的最终分类账。"""
import sys, json
from pathlib import Path
from collections import Counter
ROOT = Path(r"E:\01-项目\QQScope")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nt_msg_db_util"))
from core import store
from core.sources._pack_extract import open_enc, build_media
from msgdb.c2c import parser as c2c_parser
from msgdb.group import exporter as group_exporter
from msgdb.proto.c2c_40800_parser import parse_40800
QQ = 1605289411
con, _ = open_enc(ROOT/"data"/"decrypt"/str(QQ)/"nt_msg_clear.db", [(ROOT/"data"/"keys"/(str(QQ)+".key")).read_text(encoding="utf-8").strip()])
nt = {}
def scan(sql, kind):
    cur = con.execute(sql); cols=[d[0] for d in cur.description]
    for raw in cur:
        r=dict(zip(cols,raw)); blob=r.get("blob")
        if not blob: continue
        try: cs=list(parse_40800(blob).contents)
        except Exception: continue
        if kind=="c2c":
            try: text=getattr(c2c_parser.parse_row(r),"text",None)
            except Exception: text=None
            try: sqq=int(r.get("sender_qq") or 0)
            except Exception: sqq=0
            try: pq=int(r.get("peer_qq") or 0)
            except Exception: pq=0
            peer=str(pq) if pq else (str(r.get("peer_uid") or "").strip() or "0")
            key=("c2c",peer,int(r.get("timestamp") or 0),1 if (sqq and sqq==QQ) else 0,text or "")
        else:
            try: rec=group_exporter.parse_row(r)
            except Exception: continue
            try: sqq=int(rec.get("sender_qq") or 0)
            except Exception: sqq=0
            gi=str(rec.get("group_id") or "").strip()
            try: gq=int(rec.get("group_qq") or 0)
            except Exception: gq=0
            if gq<=0 and gi.isdigit(): gq=int(gi)
            key=("group",gi or str(gq),int(rec.get("timestamp") or 0),1 if (sqq and sqq==QQ) else 0,rec.get("text") or "")
        nt[key]=cs
scan(c2c_parser.SELECT_SQL,"c2c"); scan(group_exporter.SELECT_SQL,"group")
con.close()
sq = store.connect()
rows = sq.execute("SELECT id, kind, peer_id, ts, direction, text, source, content, media FROM messages WHERE account_qq=? AND json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback')='[名片]'", (QQ,)).fetchall()
ctr = Counter(); unmatched_content = Counter(); unmatched_src = Counter()
for r in rows:
    key=(r["kind"], r["peer_id"], int(r["ts"]), int(r["direction"]), r["text"] or "")
    cs = nt.get(key)
    if cs is None:
        try: d=json.loads(r["content"] or "{}")
        except Exception: d={}
        t = d.get("type") if isinstance(d, dict) else None
        unmatched_content[t] += 1
        unmatched_src[r["source"]] += 1
        ctr["未匹配到 NT 行（旧导入/去重残留）"] += 1
        continue
    media = build_media(cs)
    fb = str((media or {}).get("fallback") or "?")
    ctr[fb.split(" ")[0] if fb else "?"] += 1
print("剩余光秃总数:", len(rows))
for k,v in ctr.most_common():
    print("   %-32s %d" % (k, v))
print("未匹配行的 content.type 分布:", dict(unmatched_content.most_common(8)))
print("未匹配行的 source 分布:", dict(unmatched_src))
# 匹配到 NT 但 build_media 现在给出 [名片] 的：看 media_sub / 字段
print("--- 抽样：仍未还原的 5 条 ---")
n=0
for r in rows:
    key=(r["kind"], r["peer_id"], int(r["ts"]), int(r["direction"]), r["text"] or "")
    cs = nt.get(key)
    if cs is None: continue
    media = build_media(cs)
    if str((media or {}).get("fallback") or "") != "[名片]": continue
    c8=[c for c in cs if int(c.content_type or 0)==8]
    sub = c8[0].media_sub if c8 else None
    print("   id=%s mt=media_sub=%s content=%s" % (r["id"], sub, (r["content"] or "")[:90]))
    n+=1
    if n>=5: break
sq.close()
