# OPC Marketing Site

OPC 的官方营销官网 —— 纯静态站，单页 5 sections，Apple 风格调性。

## 结构

- `index.html` — 主页 (Hero / How it works / Live demo / Use cases / Get started + Footer)
- `favicon.svg` — 站点图标

## 预览

```bash
# 任选一种起本地服务
python -m http.server 8080
# 或
npx serve .

# 浏览器打开
open http://localhost:8080
```

## 部署到 GitHub Pages

1. 把这个目录推到 `gh-pages` 分支
2. Settings → Pages → Source: `gh-pages` branch, root
3. 几分钟后访问 `https://<user>.github.io/<repo>/`

## 设计原则

- **极简留白** + massive display typography
- **单一蓝色 accent** (`#0a84ff`)
- **mono 字体终端** (JetBrains Mono) 营造 "engineering" 感
- **滚动 reveal** + IntersectionObserver 触发的终端模拟器
- **响应式** —— 移动端单栏, 桌面端多栏

## 自定义

要改文案直接编辑 `index.html`，要改色板编辑顶部的 Tailwind config：

```js
colors: {
  ink: '#0a0a0a',
  paper: '#fbfbfd',
  mist: '#f5f5f7',
  line: '#d2d2d7',
},
```