# -*- coding: utf-8 -*-
"""抽查：修复后的 5 条，回到 NT blob 验证昵称来源（ms4 新布局 / 老布局 uid 解析）。"""
import sys, json
from pathlib import Path
ROOT = Path(r"E:\01-项目\QQScope")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nt_msg_db_util"))
from core import store
from core.sources._pack_extract import open_enc
from msgdb.proto import wire
from msgdb.proto.c2c_40800_parser import parse_40800
from msgdb.group import exporter as group_exporter
from msgdb.c2c import parser as c2c_parser
QQ = 1605289411
con, _ = open_enc(ROOT/"data"/"decrypt"/str(QQ)/"nt_msg_clear.db",
                  [(ROOT/"data"/"keys"/(str(QQ)+".key")).read_text(encoding="utf-8").strip()])
sq = store.connect()
want_ids = [547328, 547323, 547309, 534067, 533927]
rows = {r["id"]: dict(r) for r in sq.execute("SELECT id,kind,peer_id,ts,direction,text,media FROM messages WHERE id IN (%s)" % ",".join(map(str,want_ids)))}
def wget(raw, num):
    for f in wire.parse_wire(raw):
        if f.number==num and f.wire_type==2:
            try: return f.raw_value.decode("utf-8","replace")
            except Exception: return ""
    return ""
def scan(sql, kind):
    cur = con.execute(sql); cols=[d[0] for d in cur.description]
    for raw in cur:
        r = dict(zip(cols, raw)); blob = r.get("blob")
        if not blob: continue
        if kind=="c2c":
            try: m=c2c_parser.parse_row(r)
            except Exception: continue
            try: pq=int(r.get("peer_qq") or 0)
            except Exception: pq=0
            try: sqq=int(r.get("sender_qq") or 0)
            except Exception: sqq=0
            peer=str(pq) if pq else (str(r.get("peer_uid") or "").strip() or "0")
            key=("c2c",peer,int(r.get("timestamp") or 0),1 if (sqq and sqq==QQ) else 0,getattr(m,"text",None) or "")
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
        for mid in want_ids:
            rr = rows.get(mid)
            if not rr: continue
            if (rr["kind"],rr["peer_id"],int(rr["ts"]),int(rr["direction"]),rr["text"] or "") != key: continue
            for c in parse_40800(blob).contents:
                if int(c.content_type or 0)!=8: continue
                rawb=c.SerializeToString()
                print("id=%s mt=%s media_sub=%s" % (mid, rr.get("text"), c.media_sub))
                print("   old nick47705=%r uid47703=%r | new 48504=%r 48505=%r 48503=%r 48506=%r 48507=%r" % (
                    wget(rawb,47705), wget(rawb,47703), wget(rawb,48504), wget(rawb,48505), wget(rawb,48503), wget(rawb,48506), wget(rawb,48507)))
                print("   DB after:", rr["media"][:140])
scan(c2c_parser.SELECT_SQL,"c2c"); scan(group_exporter.SELECT_SQL,"group")
con.close(); sq.close()
