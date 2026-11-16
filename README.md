# 「留声」——基于情感热点的玩家流失阻断与精准召回系统

一套把「评论情感 → 流失风险 → 召回动作」串成闭环的玩家留存可视化中枢。核心主张是：**行为数据只能告诉你谁已经走了，评论区的情绪往往在流失发生前数周就已发酵**，因此把情感与热点作为流失的先行指标。

界面由四个页面组成：总览、流失分析、召回策略、留存追踪。

---

## 目录

- [快速开始](#快速开始)
- [冒烟测试](#冒烟测试)
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

## 冒烟测试

改完页面先跑这一条，确认四个页面「还能用」，再谈提交：

```bash
cd tests && npm install     # 首次
node smoke.mjs              # 自动起服务，退出码 0 = 全绿
```

覆盖四层：资源可达（L0）、无未捕获异常与失败请求（L1）、关键锚点存在且非空（L2）、时间范围切换与降级提示（L3）。
**L1 是重点**——页面「看起来正常」但脚本死在中途时，只有控制台会告诉你。详见 [`tests/README.md`](tests/README.md)。
`.github/workflows/smoke.yml` 已在 PR 与 push 时自动执行，失败即阻断。

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
├── .github/workflows/
│   ├── deploy.yml                  # GitHub Pages 部署
│   └── smoke.yml                   # 冒烟测试（PR 卡点）
├── tests/                          # 冒烟测试（Playwright 无头渲染）
│   ├── smoke.mjs                   # 四层判定，退出码即结论
│   └── README.md                   # 跑法、判定说明与人工清单
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

### 真实数据：Steam 同时在线与成就漏斗

首页「玩家活跃趋势」与 KPI「活跃玩家」、流失分析页「成就完成漏斗」已接入 Steam 公开接口的真实数据，其余指标仍为演示数据。采集覆盖六款游戏，全部由 `backend/config/games.yaml` 定义：

| # | 游戏 | appid | 定位 |
|---|---|---|---|
| 1 | 永劫无间 | 1203220 | 主选：成就中文可读，漏斗跨度 63.1pp |
| 2 | PUBG | 578080 | 大体重对照（30–44 万在线） |
| 3 | Warframe | 230410 | 成就最多（193 个），细粒度漏斗 |
| 4 | Path of Exile | 238960 | 跨度最小（47.4pp），劝退较晚 |
| 5 | Apex Legends | 1172470 | 服务型射击，跨度 31.7pp |
| 6 | Cyberpunk 2077 | 1091500 | 单机对照，跨度最大（83.6pp） |

**新增一款游戏只需在 yaml 里追加一项**，采集、CI 发布、API、前端切换器全部自动跟随，不改任何代码。

| 环节 | 位置 |
|---|---|
| 游戏注册表 | `backend/config/games.yaml` + `backend/collectors/game_config.py`（零依赖解析，含字段校验） |
| 本地采集 | `backend/collectors/steam_collector.py`（CCU 时序 + 成就快照 → `backend/data/steam.db`，5 分钟级） |
| 表结构 | `backend/data/schema.sql`（games / ccu / achievements / collection_logs / collection_cursors） |
| 本地快照 | `backend/collectors/export_snapshot.py`（生成站点内离线兜底 JSON，也用于本地验证） |
| 线上采集 | `.github/workflows/collect-ccu.yml` 每小时运行 `backend/collectors/ci_publish.py`，写入 **`data` 分支** |
| 消费 | `dashboard-data.js` 的 `loadRealCcu()` / `loadRealFunnels()`：远程 → 仓库内快照 → 演示序列 |
| API | `backend/app.py`：`GET /api/games`、`GET /api/games/{appid}`（统一 `meta`，错误码可预期） |

`data` 分支根目录两个文件：

- `steam-ccu.json`：各游戏同时在线时序（每小时一点，窗口 72h，含 `mean` 供归一化），并内嵌游戏注册表（名称/颜色/启用状态），前端据此生成切换器
- `steam-funnels.json`：各游戏成就完成率（按完成率降序近似推进顺序）

```bash
python backend/collectors/game_config.py                             # 查看当前注册表
python backend/collectors/steam_collector.py --once                  # 按配置采一轮（计划任务用）
python backend/collectors/steam_collector.py --appids 1203220 --once # 临时只采某款
python backend/collectors/funnel.py --appid 1203220                  # 命令行看单个漏斗
python backend/collectors/export_snapshot.py                         # 刷新离线快照
```

四个必须知道的限制：

1. **口径**：CCU 是某一时刻的同时在线人数，不是 DAU；成就完成率是全量玩家累计值，用它反推「进度流失」是近似，**不等于留存率**。
2. **多游戏不做加总**：首页「指数对比」是各游戏除以自身窗口均值后的指数（100 = 该游戏窗口均值），用于比较节奏而非体量。加总会被体量大的游戏吞掉细节。
3. **离线退化**：断网或 raw 域名不可达时，页面退回仓库内的 JSON 快照（只在手动执行 `export_snapshot.py` 时更新）。
4. **横轴是时间轴**，不是等距类目轴：采样存在停机缺口（几十分钟到数小时），等距绘制会谎报事件间隔。曲线也不做平滑，避免在缺口处凭空画出趋势。

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

`.github/workflows/deploy.yml` 在 **push 到 `main`**（或手动触发）时运行，用官方 Pages 动作发布 `front/static_html`，不经过任何构建步骤：

```yaml
on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

steps:
  - uses: actions/checkout@v4
  - uses: actions/configure-pages@v5
  - uses: actions/upload-pages-artifact@v3
    with:
      path: ./front/static_html
  - uses: actions/deploy-pages@v4
```

**分支约定**：`main` 是部署分支，`dev` 是开发分支。改动在 `dev` 上完成后合回 `main` 才会生效，`dev` 上的提交不会自动上线。

站点地址：**<https://aggressive0.github.io/My-web/>**

> 仓库 Pages 的 Source 需设为「GitHub Actions」（Settings → Pages），否则该流水线不会被触发。原先基于 `gh-pages` 分支的部署方式已废弃。

---

## 已知限制

1. **数据是演示数据**，无实时数据源；页面不做「实时」宣称。
2. **后端三个 churn 接口缺数据文件**，返回 404；补齐方式见上文。
3. **召回策略页的 AI 分析面板依赖外部大模型接口**（SiliconFlow）。密钥已从代码中移除，改为首次使用时由浏览器 `localStorage` 保存（key 名 `liusheng.siliconflow.key`），仓库不再内置任何凭据。**但旧密钥仍存在于公开仓库的历史提交中，必须到服务商控制台吊销后重新签发**——移除当前文件不等于清除历史，详见 [已知限制](#已知限制) 与提交历史清理说明。
4. **后端以 `debug=True` + `host='0.0.0.0'` 启动**，仅适合本地演示，不可直接对外部署（应改用 `127.0.0.1` 并由 gunicorn / waitress 托管）。
5. **无鉴权与多租户**，数据为单份全局视图。

---

## 许可证

MIT License

## 维护者

[aggressive0](https://github.com/aggressive0)
