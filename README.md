# 「留声」——基于情感热点的玩家流失阻断与精准召回系统

一套把「评论情感 → 流失风险 → 召回动作」串成闭环的玩家留存可视化中枢。核心主张是：**行为数据只能告诉你谁已经走了，评论区的情绪往往在流失发生前数周就已发酵**，因此把情感与热点作为流失的先行指标。

界面由四个页面组成：总览、流失分析、召回策略、留存追踪。

---

## 目录

- [快速开始](#快速开始)
- [页面结构](#页面结构)
- [目录结构](#目录结构)
- [技术栈](#技术栈)
- [数据说明](#数据说明)
- [后端接口](#后端接口)
- [交互说明](#交互说明)
- [部署](#部署)
- [已知限制](#已知限制)

---

## 快速开始

### 只看界面（推荐，零依赖）

静态站点不依赖任何构建工具，起一个 HTTP 服务即可：

```bash
cd front/static_html
python -m http.server 8080
```

浏览器打开 <http://127.0.0.1:8080/index.html>。

> 直接双击 HTML 文件无法正常运行：页面通过 `fetch` / `XMLHttpRequest` 加载本地 JS 与贴图，`file://` 协议会被浏览器拦截。

### 连后端（可选）

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r backend/requirements.txt pandas

cd backend
../.venv/Scripts/python.exe app.py     # http://127.0.0.1:5000
```

后端启动后，静态页面中的部分数据接口才有响应；但**多数接口仍需先补齐数据文件**，见 [后端接口](#后端接口)。

---

## 页面结构

| 页面 | 文件 | 职责 |
|---|---|---|
| 总览 | `index.html` | 中央三维地球 + 两翼数据舱 + 底部四段决策链（热点识别 → 玩家反应 → 影响范围 → 召回策略） |
| 流失分析 | `analysis.html` | 流失率与风险区域、30 天趋势叠加风险阈值、流失原因结构、玩家分群画像与热点关联 |
| 召回策略 | `solution.html` | 策略排行榜（跑马灯）、召回率对比、策略详情与依据、AI 分析面板 |
| 留存追踪 | `validation.html` | 次日/7 日/30 日留存与时长、留存趋势三曲线、注册周 cohort 热力表、分群留存、影响因素环 |

四个页面共用一套顶部导航（`ops-nav.css` + `js/ops-nav.js`）与底部实时动态条（`live-ticker.css` + `js/live-ticker.js`）。

---

## 目录结构

```
├── backend/                        # Flask 后端
│   ├── app.py                      # API 主入口
│   ├── requirements.txt            # 依赖列表
│   ├── data_cleaning.py            # 评论数据清洗
│   ├── data_analysis.py            # 情感与热点分析
│   ├── churn_risk_analysis.py      # 流失风险测算
│   ├── churn_retention_analysis.py # 召回策略测算
│   ├── player_comparison_analysis.py
│   ├── data/                       # 原始/清洗后数据（未纳入版本控制）
│   └── instance/                   # SQLite 文件
├── front/
│   ├── static_html/                # 静态站点（主要展示形态，零构建）
│   │   ├── index.html / analysis.html / solution.html / validation.html
│   │   ├── ops-nav.css             # 四页共用导航样式
│   │   ├── live-ticker.css         # 底部实时动态条样式
│   │   ├── js/
│   │   │   ├── dashboard-data.js   # 数据适配层（唯一取数出口）
│   │   │   ├── globe-stage.js      # 三维地球舞台（globe.gl 封装）
│   │   │   ├── ops-nav.js          # 导航时钟与日期范围下拉
│   │   │   └── live-ticker.js      # 实时动态条（自动挂载）
│   │   ├── libs/                   # 本地化的第三方库与贴图
│   │   └── images/                 # 页面配图
│   └── src/                        # Vue 试验版（非展示路径，未接入静态站）
├── .github/workflows/deploy.yml    # GitHub Pages 部署
└── writing-block.md                # 首页开发规格（信息架构 / Tokens / 联动矩阵）
```

---

## 技术栈

### 前端（静态站，零构建）

| 技术 | 版本 | 用途 |
|---|---|---|
| 原生 HTML / CSS / JS | — | 页面与交互，无框架、无打包 |
| ECharts | 5.6.1（本地） | 折线、环形、条形、热力等图表 |
| globe.gl | 2.46.2（本地，内置 three.js） | 三维地球、点位、脉冲环、迁移弧线 |
| WebGL | — | 地球渲染；不可用时自动降级为提示文案 |

**所有前端依赖均已本地化到 `front/static_html/libs/`，不依赖任何 CDN**，断网可运行。

### 后端

| 技术 | 版本 | 用途 |
|---|---|---|
| Flask | 3.x | Web 框架 |
| Flask-CORS / Flask-SQLAlchemy | 4.x / 3.x | 跨域与 ORM |
| Pandas | 2.x | 数据分析 |
| SQLite | 内置 | 存储 |

---

## 数据说明

**当前页面上的全部数值来自演示数据集，不是实时线上数据。** 数据统一由 `front/static_html/js/dashboard-data.js` 提供，其 `meta.dataSource` 明确标记为 `'mock'`。

要接入真实数据，只需替换该文件的四个出口，页面代码无需改动：

| 出口 | 返回 |
|---|---|
| `windowOf(range)` | 时间窗口文案 |
| `kpis(range)` | 顶部指标卡数值与环比 |
| `series(range, regionId)` | 趋势序列（情感三项已归一化，合计恒为 100） |
| `regionsFor(range)` | 区域排行与高危人数 |

导航上的日期范围下拉（近 7 / 30 / 90 天）与页面内分段控件双向联动，切换后指标、趋势、排行同步刷新。

---

## 后端接口

| 接口 | 方法 | 状态 |
|---|---|---|
| `/api/health` | GET | 可用 |
| `/api/sentiment-trend` | GET | 可用，但缺数据时返回内置占位趋势 |
| `/api/churn-risk` | GET | **404**，需 `backend/data/` 下的分析结果文件 |
| `/api/churn-retention` | GET | **404**，同上 |
| `/api/player-comparison` | GET | **404**，同上 |
| `/api/game-data` | GET/POST | 数据读写 |

补齐数据的顺序：

```bash
cd backend
python data_cleaning.py            # 原始评论 → 清洗结果
python data_analysis.py            # 情感与热点
python churn_risk_analysis.py      # 流失风险
python churn_retention_analysis.py # 召回策略
python player_comparison_analysis.py
```

`backend/data/before/`、`backend/data/cleaned/` 与 `backend/data/*.json` 已在 `.gitignore` 中排除，仓库不含原始数据与中间结果，需要自备评论数据后按上述顺序重跑。

---

## 交互说明

| 操作 | 结果 |
|---|---|
| 点击区域排行某一行 | 地球转到该区域并高亮、其余点位压暗；信息卡切区域详情；情感趋势切到该区域 |
| 点击地球热点标签 | 定位热点、信息卡切热点详情、底部决策链整体切换 |
| 点击 AI 预警 | 定位关联热点并切换详情，可展开触发规则与处置建议 |
| 悬停策略排行 | 跑马灯暂停（`prefers-reduced-motion` 下改为静态可滚动） |
| 拖动 / 滚轮 / 双击地球 | 旋转 / 缩放 / 复位 |
| 导航右侧日期范围 | 切换 7 / 30 / 90 天，联动图表与指标 |

---

## 部署

`.github/workflows/deploy.yml` 在 **push 到 `main`** 时触发，把 `front/static_html` 发布到 `gh-pages` 分支：

```yaml
on:
  push:
    branches: [main]

steps:
  - uses: actions/checkout@v4
  - uses: peaceiris/actions-gh-pages@v4
    with:
      publish_dir: ./front/static_html
      publish_branch: gh-pages
```

**分支约定**：`main` 是部署分支，`dev` 是开发分支。改动在 `dev` 上完成后合回 `main` 才会生效，`dev` 上的提交不会自动上线。

发布后访问 `https://<用户名>.github.io/My-web/`。

---

## 已知限制

1. **数据是演示数据**，无实时数据源；页面不做「实时」宣称。
2. **后端三个 churn 接口缺数据文件**，返回 404；补齐方式见上文。
3. **召回策略页的 AI 分析面板依赖外部大模型接口**（SiliconFlow），当前密钥以明文写在 `solution.html` 中。**该密钥自首次提交起即存在于公开仓库历史，应立即在服务商侧吊销并轮换**，随后改为从本地配置或运行时输入读取。
4. **后端以 `debug=True` + `host='0.0.0.0'` 启动**，仅适合本地演示，不可直接对外部署（应改用 `127.0.0.1` 并由 gunicorn / waitress 托管）。
5. **无鉴权与多租户**，数据为单份全局视图。

---

## 许可证

MIT License

## 维护者

[aggressive0](https://github.com/aggressive0)
