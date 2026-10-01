# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"E:\01-项目\QQScope\tools\nt_msg_db_util")
from msgdb.proto import c2c_40800_pb2 as pb
for f in pb.MsgContent.DESCRIPTOR.fields:
    if f.number >= 48000:
        print("%-24s %6d  type=%d" % (f.name, f.number, f.type))
