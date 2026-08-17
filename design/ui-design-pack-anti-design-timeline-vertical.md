# UI 设计包：反设计（Anti-Design） × 纵向时间线（Timeline Vertical）

> 由 UI Studio 生成（Project\ui-studio），数据本地化。

## 一、风格
- 名称：反设计（Anti-Design）
- slug：`anti-design`
- 简介：故意打破传统UI规范的粗野主义实验风格，极粗边框、高饱和色彩与不规则排版
- 色板：主色 `#000000` / 次色 `#FFFFFF` / 强调色 #FF0000 / #0000FF / #FFFF00 / #FF00FF / #00FFFF / #00FF00

## 二、布局骨架
- 布局：纵向时间线（`timeline-vertical`）
- 说明：时间/步骤纵向排列
- 骨架 HTML：

```html
<nav class="ui-nav"><div class="ui-nav-in"><span class="ui-brand">数据中心</span><div class="ui-nav-links"><span>导航一</span><span>导航二</span><span>导航三</span><button class="ui-btn">开始</button></div></div></nav><div class="b-timeline"><div class="b-tl-item"><span class="b-tl-dot"></span><div class="ui-card"><h3>阶段一</h3><p>第一阶段说明。</p></div></div><div class="b-tl-item"><span class="b-tl-dot"></span><div class="ui-card"><h3>阶段二</h3><p>第二阶段说明。</p></div></div><div class="b-tl-item"><span class="b-tl-dot"></span><div class="ui-card"><h3>阶段三</h3><p>第三阶段说明。</p></div></div></div><footer class="ui-footer"><div class="ui-footer-in"><span>© 2026 · 本地设计工作台</span><span>数据本地化 · 双击即用</span></div></footer>
```

## 三、排版参数
- 字号比例：1.2
- 间距基准：8dp
- 字体：`'Trebuchet MS',sans-serif`

## 四、全局 CSS（复制进项目 <style>）

```css
/* Anti-Design Global Styles */

:root {
  --anti-black: #000000;
  --anti-white: #FFFFFF;
  --anti-red: #FF0000;
  --anti-blue: #0000FF;
  --anti-yellow: #FFFF00;
  --anti-magenta: #FF00FF;
  --anti-cyan: #00FFFF;
  --anti-green: #00FF00;
}

@keyframes anti-shake {
  0%, 100% { transform: rotate(0deg); }
  25% { transform: rotate(-1deg); }
  75% { transform: rotate(1deg); }
}

@keyframes anti-blink {
  0%, 49% { opacity: 1; }
  50%, 100% { opacity: 0; }
}

@keyframes anti-marquee {
  0% { transform: translateX(100%); }
  100% { transform: translateX(-100%); }
}

/* Thick asymmetric borders */
.anti-border-asymmetric {
  border-right-width: 6px;
  border-bottom-width: 6px;
  border-left-width: 4px;
  border-top-width: 4px;
}
.anti-design-card {
  position: relative;
  overflow: hidden;
}

.anti-design-card::before {
  content: "";
  position: absolute;
  inset: 0;
  opacity: 0;
  transition: opacity 0.3s ease;
  background: linear-gradient(135deg, rgba(0, 0, 0, 0.05), transparent);
  pointer-events: none;
}

.anti-design-card:hover::before {
  opacity: 1;
}

.anti-design-gradient {
  background: linear-gradient(135deg, #000000, #FF0000);
}

.anti-design-gradient-text {
  background: linear-gradient(135deg, #000000, #FF0000);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.anti-design-frosted {
  backdrop-filter: blur(12px) saturate(180%);
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  background: rgba(0, 0, 0, 0.08);
}

.anti-design-accent-corner {
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 2rem), calc(100% - 2rem) 100%, 0 100%);
}

.anti-design-animate-in {
  animation: anti-design-fade-in 0.5s ease-out both;
}
```

## 五、组件模板

### 按钮（button）

Anti-Design 风格按钮 - 极粗黑边、硬偏移阴影、高饱和色

```jsx
<button className="
  px-6 py-3
  bg-[#FF0000] text-white
  font-black text-xl uppercase
  border-4 border-black
  rounded-none
  shadow-[8px_8px_0_#000]
  hover:bg-[#0000FF] hover:text-[#FFFF00]
  hover:border-8 hover:shadow-[12px_12px_0_#000]
  hover:-translate-x-2 hover:rotate-3
  active:bg-[#FFFF00] active:text-black
  active:shadow-none active:translate-x-[8px] active:translate-y-[8px]
  transition-none
  cursor-help
">
  CLICK ME
</button>
```

### 卡片（card）

Anti-Design 风格卡片 - 白底粗黑框硬偏移阴影

```jsx
<div className="
  bg-white
  border-4 border-black
  p-6
  rounded-none
  shadow-[8px_8px_0_#000]
  hover:bg-[#FFFF00]
  hover:border-8
  hover:shadow-[12px_12px_0_#000]
  hover:-translate-x-2 hover:-translate-y-2
  hover:rotate-1
  transition-none
">
  <h3 className="text-2xl font-black uppercase mb-2">CARD TITLE</h3>
  <p className="text-sm font-bold text-black/70">Raw brutalist content block</p>
</div>
```

### 输入框（input）

Anti-Design 风格输入框 - 极粗黑边、蓝色聚焦态

```jsx
<input
  type="text"
  placeholder="TYPE HERE..."
  className="
    w-full px-4 py-3
    bg-white
    border-4 border-black
    rounded-none
    text-black font-black
    placeholder:text-gray-400
    focus:outline-none
    focus:bg-[#FFFF00]
    focus:border-8 focus:border-[#FF0000]
    focus:shadow-[16px_16px_0_#0000FF]
    focus:-translate-y-2 focus:rotate-1
    transition-none
  "
/>
```

### 导航栏（nav）

Anti-Design 风格导航 - 白底粗黑下边框

```jsx
<nav className="
  bg-white
  border-b-4 border-black
  px-6 py-4
  flex items-center justify-between
">
  <span className="font-black text-xl uppercase">ANTI-DESIGN</span>
  <div className="flex gap-4">
    <a className="font-black text-sm uppercase hover:text-[#FF0000]">LINK</a>
  </div>
</nav>
```

### Hero 区块（hero）

Anti-Design 风格 Hero - 黄色底、巨大倾斜黑色标题、粗边框

```jsx
<section className="
  bg-[#FFFF00]
  border-b-4 border-black
  py-20 px-6
">
  <h1 className="
    text-6xl md:text-9xl
    font-black uppercase
    text-black
    -rotate-2
  ">
    ANTI-DESIGN
  </h1>
  <p className="text-xl font-bold text-black/70 mt-4 max-w-xl">
    BREAK EVERY RULE. REJECT EVERY CONVENTION.
  </p>
</section>
```

### 页脚（footer）

Anti-Design 风格页脚 - 黑底白字粗上边框

```jsx
<footer className="
  bg-black text-white
  border-t-4 border-white
  px-6 py-8
">
  <p className="font-black text-sm uppercase">ANTI-DESIGN STUDIO</p>
</footer>
```


## 六、AI 规则（粘贴给 AI 用于生成功能代码）

```
You are an Anti-Design style frontend development expert. All generated code must strictly follow these constraints:

## Absolutely Forbidden

- Rounded corners of any kind (rounded-sm, rounded-md, rounded-lg, rounded-xl, rounded-full)
- Subtle or muted colors (grays, pastels, earth tones)
- Soft shadows (shadow-sm, shadow-md, shadow-lg, shadow-xl)
- Gradients of any kind (all colors must be flat high-saturation)
- Backdrop blur or translucency effects
- Consistent spacing or alignment that looks "designed"
- Harmonious color combinations

## Must Follow

- Borders: Always 4-8px solid black. Thicker on right and bottom for depth
- Border-radius: ALWAYS 0. Never round anything
- Colors: Only high-saturation primaries - #FF0000, #0000FF, #FFFF00, #FF00FF, #00FF00, #00FFFF
- Backgrounds: Alternate between white, yellow, and other bright colors per section
- Shadows: Hard offset only (e.g., shadow-[8px_8px_0_#000]). No soft shadows
- Text: Mix sizes dramatically. Use font-black weight. Uppercase for emphasis
- Layout: Intentionally break grid alignment. Rotate elements (-3deg to 5deg)
- Fonts: Bold sans-serif. Mix sizes within sections for visual tension
- White space: Can be either very tight or exaggerated - never "just right"

## Animation & Interaction Rules

- Aggressive hover only: abrupt color collisions, border-thickness jumps, and harsh offsets
- Broken layout on interaction is encouraged: alignment can intentionally fail on hover/focus
- Zero polish: use transition-none or linear with near-zero duration; no smooth easing curves
- Focus states should be louder than default states (thicker borders, stronger shadows, rotation/shift)

## Color Palette

Primary:
- Pure Black: #000000 (borders, text, shadows)
- Pure White: #FFFFFF (backgrounds)
- Red: #FF0000 (primary accent, buttons)
- Blue: #0000FF (secondary accent, focus states)
- Yellow: #FFFF00 (section backgrounds, highlights)
- Magenta: #FF00FF (accent)
- Cyan: #00FFFF (accent)
- Green: #00FF00 (accent)

## Special Elements

- Ultra-thick borders on every element
- Hard offset shadows with no blur
- Rotated/tilted elements for visual disruption
- Dramatically mixed font sizes
- Asymmetric border widths
- Alternating high-saturation section backgrounds
```

## 七、使用步骤
1. 把「全局 CSS」放入项目 <style>，色板变量按「风格」一节定义；
2. 按「布局骨架」搭建页面结构；
3. 组件直接用「组件模板」的代码；
4. 开发具体功能时，把「AI 规则」作为系统约束粘贴给 AI。
