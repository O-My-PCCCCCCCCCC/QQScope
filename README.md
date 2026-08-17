# QQScope —— QQ 聊天记录精神分析

> 状态：讨论/设计阶段（2026-08-17 立项，尚未开始编码）
> 仓库：https://github.com/O-My-PCCCCCCCCCC/QQScope

## 用途

检测自己的 QQ 聊天记录，分析精神状态（情绪曲线、波动幅度、行为特征、
话题漂移等），辅助**自我观察**，**非医疗诊断**。数据全本地，不出本机。

## 架构：双通道采集 + 本地分析

```
┌─ 电脑端（存量 + 分析）────────────────────────────┐
│  电脑 QQ 本地数据（NTQQ nt_msg.db / 老版 msg3.0.db）│
│    → 解密导入器（Python）→ SQLite 主库              │
│    → 分析引擎：本地统计+情绪词典 + 可选 DeepSeek     │
│    → Web 仪表盘（按设计包 anti-design 风格）         │
└─────────────────────────────────────────────────┘
        ▲ 定期同步（局域网 HTTP / 手动拷回）
┌─ 手机端（增量，24h 在线）────────────────────────┐
│  Android + Termux + proot-distro + Ubuntu        │
│    + Lagrange.OneBot（先小号试通）                 │
│    → OneBot 收实时消息 → 轻量服务 → 增量落库        │
└─────────────────────────────────────────────────┘
```

## 目录结构

- `design/` —— ui-studio 设计包（anti-design × timeline-vertical）
- `.dsh/skills/qqscope/SKILL.md` —— 项目记忆 skill
- `README.md` —— 本文档
- （代码/数据/脚本目录待开发时建立）

## 运行方式

未定（开发中补充）。

## 相关约定

- 数据全本地，不出本机；AI 分析前预览将发送的内容
- 文案注明"辅助自我观察，非医疗诊断"
- UI 风格：anti-design（用户经 ui-studio 选定），严格按
  `design/ui-design-pack-anti-design-timeline-vertical.md` 实现
- 端口：未启用，启用前按规范登记（>=15000 段）
