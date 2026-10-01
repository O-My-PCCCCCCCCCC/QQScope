# UI 设计包：仪表盘布局（Dashboard Layout） × 仪表盘布局（Dashboard）

> 由 UI Studio 生成（Project\ui-studio），数据本地化。用户于 2026-08-17 重新导出，
> 取代此前 anti-design 方案。完整规范见对话记录与下方要点。

## 一、风格
- 名称：仪表盘布局（Dashboard Layout）
- slug：`dashboard-layout`
- 简介：数据驱动的仪表盘布局，侧边导航 + 顶部工具栏 + 多模块数据面板 + 图表区域
- 色板：主色 `#111827` / 次色 `#f9fafb` / 强调色 #6366f1 / #10b981 / #f59e0b / #ef4444

## 二、布局
- 顶栏（白底灰下边框）+ 侧栏（深色 #111827，256px，移动端隐藏）+ 统计卡 + 图表区
- KPI 卡片：grid 4 列（桌面）/ 2 列（平板）/ 1 列（手机）；增长绿 #10b981 / 下降红 #ef4444 / 平稳黄 #f59e0b
- 图表区：主图 2/3 宽 + 辅图 1/3 宽
- 响应式：768px 以下隐藏侧栏

## 三、排版
- 字号比例 1.25，间距 8dp，字体 -apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',system-ui

## 四、关键约束（AI Rules 摘要）
- KPI 卡片：标签 + 数值 + 变化趋势，颜色编码状态
- 微交互：duration-150 + ease-out；KPI hover 轻微上浮；按钮 active:scale-0.97 + focus ring
- 面板：白底 rounded-xl shadow-sm border #f3f4f6；hover 底色反馈
- 强调色 #6366f1（按钮/高亮/图表主线）

## 五、使用
- 全局 CSS / 组件模板 / 完整示例见对话中的设计包原文
- 本包为 QQScope 当前 UI 的唯一依据（2026-08-17 起）
