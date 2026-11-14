# NEXUS · PLAYER INTELLIGENCE
# 首页重构开发规格（Agent 执行版）

**项目：** `aggressive0/My-web`  
**页面：** 运营总览首页 `index.html`  
**设计方向：** Futuristic Game Intelligence Command Center  
**核心布局：** 中央 3D 地球 + 左右数据面板 + 底部热点响应链

---

## 0. Agent 开发前置要求

### 0.1 先检查项目，不要直接开始改代码

执行以下检查：

1. 阅读项目根目录、README、AGENTS.md（如果存在）。
2. 检查 `package.json`、锁文件、构建工具和启动脚本。
3. 检查 `index.html`、`analysis.html`、`implementation.html` 及其引用的 CSS、JS。
4. 确认当前首页地球的实现方式：
   - Three.js
   - ECharts GL
   - globe.gl / three-globe
   - 其他方案
5. 检查已有图表库、图标库、地图数据、API 请求封装和公共组件。
6. 确认当前项目的数据来源：真实 API、Mock 数据还是本地静态数据。
7. 检查响应式布局、路由方式和页面间共享状态。

### 0.2 开发约束

- 优先在现有技术栈内完成，不要无理由引入新框架。
- 不要重建整个项目，不要随意修改其他页面。
- 不要删除现有可用的数据处理、地图、图表逻辑。
- 中央地球必须保留，并且是首页视觉中心。
- 不要把地球仅做成静态背景图片。
- 不得将演示数据伪装成真实数据。
- API 暂不可用时，使用独立 Mock 数据适配器。
- 图表、地球、指标卡必须有独立组件边界。
- 所有页面状态必须可追踪、可维护。
- 避免为了动画效果引入高频率、无意义的 DOM 更新。

**完成代码检查后，先输出一份简短的现有技术栈与改造计划，再开始实现。**

---

# 1. 页面信息架构

## 1.1 页面总体布局

首页划分为六个区域：

| 区域 | 模块 | 核心职责 |
|---|---|---|
| A | 顶部导航栏 | 品牌、页面切换、时间范围、实时状态 |
| B | 核心指标卡 | 活跃玩家、风险玩家、平均情感、召回率 |
| C | 左侧数据舱 | 玩家活跃趋势、区域流失排行 |
| D | 中央地球 | 全球玩家分布、热点、情感与风险可视化 |
| E | 右侧数据舱 | 情感趋势、AI 风险预警、行动建议 |
| F | 底部决策链 | 热点 → 情绪变化 → 玩家响应 |

页面结构建议：

```text
App
└── DashboardPage
    ├── DashboardHeader
    │   ├── BrandLogo
    │   ├── MainNavigation
    │   ├── DateRangePicker
    │   └── LiveStatus
    │
    ├── KPIOverview
    │   ├── KPICard (ActivePlayers)
    │   ├── KPICard (AtRiskPlayers)
    │   ├── KPICard (AverageSentiment)
    │   └── KPICard (RecoveryRate)
    │
    ├── DashboardMain
    │   ├── LeftAnalyticsPanel
    │   │   ├── PlayerActivityChart
    │   │   └── RegionalChurnRanking
    │   │
    │   ├── GlobeStage
    │   │   ├── GlobeRenderer
    │   │   ├── GlobeAtmosphere
    │   │   ├── PlayerMarkers
    │   │   ├── HotspotMarkers
    │   │   ├── ChurnArcs
    │   │   ├── GlobeTooltip
    │   │   └── GlobeSelectionPanel
    │   │
    │   └── RightAnalyticsPanel
    │       ├── SentimentTrendChart
    │       ├── AIRiskAlerts
    │       └── RecommendedActions
    │
    └── DashboardFooter
        ├── HotspotResponseFlow
        └── LiveActivityTicker
```

如果项目使用原生 HTML + JavaScript，可以保留同样的组件职责，使用独立 JS 模块、HTML 模板和 CSS 文件实现，不要求为了采用上述目录结构而迁移到 React。

## 1.2 建议文件组织

先遵循项目现有目录规范；如果当前没有清晰的模块划分，可以逐步整理为：

```text
src/
├── pages/
│   └── dashboard/
│       ├── DashboardPage
│       └── dashboard.css
│
├── components/
│   ├── layout/
│   │   ├── DashboardHeader
│   │   ├── MainNavigation
│   │   └── DashboardFooter
│   │
│   ├── metrics/
│   │   ├── KPIOverview
│   │   └── KPICard
│   │
│   ├── analytics/
│   │   ├── PlayerActivityChart
│   │   ├── RegionalChurnRanking
│   │   └── SentimentTrendChart
│   │
│   ├── globe/
│   │   ├── GlobeRenderer
│   │   ├── GlobeLayers
│   │   ├── GlobeTooltip
│   │   └── GlobeSelectionPanel
│   │
│   ├── intelligence/
│   │   ├── AIRiskAlerts
│   │   └── RecommendedActions
│   │
│   └── flow/
│       ├── HotspotResponseFlow
│       └── LiveActivityTicker
│
├── services/
│   ├── dashboardService
│   ├── globeService
│   └── alertService
│
├── adapters/
│   ├── chartAdapter
│   └── globeAdapter
│
├── mocks/
│   └── dashboardMock
│
├── styles/
│   ├── tokens.css
│   ├── global.css
│   ├── components.css
│   └── animations.css
│
└── utils/
    ├── formatters
    └── dateUtils
```

注意：上述为逻辑目录示例，不是要求将现有项目强制迁移到该结构。

---

# 2. 各模块组件规格

## A. DashboardHeader 顶部导航

### 展示内容

- 左侧：NEXUS 品牌标志、PLAYER INTELLIGENCE 副标题。
- 中间：运营总览、留存追踪、情感监测。
- 右侧：时间范围、数据更新时间、实时状态。

### 数据字段

```ts
interface DashboardHeaderState {
  activePage: 'overview' | 'retention' | 'sentiment';
  dateRange: {
    start: string;
    end: string;
  };
  dataStatus: 'live' | 'delayed' | 'offline';
  lastUpdatedAt: string;
}
```

### 交互

1. 点击导航切换到对应页面。
2. 时间范围改变后，触发首页数据刷新。
3. 数据状态根据最近一次成功更新时间自动判断。
4. 鼠标悬停显示导航提示。

### 验收

- 当前页面导航状态明显。
- 时间范围改变后所有相关模块使用同一时间范围。
- 页面切换不能造成地球重复初始化或 WebGL 资源泄漏。

---

## B. KPIOverview 核心指标卡

### 组件

`KPIOverview` 管理布局，`KPICard` 负责单个指标的显示。

共四张卡片：

| 指标 | 字段 | 格式 | 说明 |
|---|---|---|---|
| 活跃玩家 | activePlayers | 整数 / K | 当前活跃玩家数 |
| 风险玩家 | atRiskPlayers | 整数 / K | 达到风险判定条件的玩家数 |
| 平均情感 | averageSentiment | -1 到 1 | 玩家整体情感指数 |
| 召回率 | recoveryRate | 百分比 | 被召回玩家占目标召回玩家的比例 |

### 数据接口类型

```ts
interface KPIItem {
  key: string;
  label: string;
  value: number;
  formattedValue?: string;
  change: number;
  changeUnit: 'percent' | 'absolute';
  trend: 'up' | 'down' | 'stable';
  sparkline: number[];
  status: 'normal' | 'warning' | 'critical';
}

interface KPIOverviewData {
  activePlayers: KPIItem;
  atRiskPlayers: KPIItem;
  averageSentiment: KPIItem;
  recoveryRate: KPIItem;
}
```

### 视觉要求

- 每张卡片采用深色玻璃面板。
- 左侧放置语义图标。
- 中部展示数值和变化幅度。
- 右侧展示 Sparkline 微型趋势图。
- 风险玩家卡片允许使用红色风险强调。
- 不要只凭数值涨跌决定颜色，应根据指标含义判断好坏。

例如：风险玩家增加通常是负向变化，而召回率增加通常是正向变化。

### 交互

- 鼠标悬停显示指标定义、统计口径和更新时间。
- 点击指标可跳转到对应分析页面。
- 数据加载时显示 Skeleton，而不是先显示 0。

---

## C. LeftAnalyticsPanel 左侧分析区

### C1. PlayerActivityChart 玩家活跃趋势

展示活跃玩家数与新增玩家数随时间的变化。

数据结构：

```ts
interface PlayerActivityPoint {
  timestamp: string;
  activePlayers: number;
  newPlayers: number;
}

interface PlayerActivityData {
  points: PlayerActivityPoint[];
  interval: 'hour' | 'day';
}
```

### 图表配置

推荐使用项目已有的 ECharts 实例；若项目尚未使用 ECharts，再评估引入。

```js
const option = {
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'line' }
  },
  legend: {
    data: ['活跃玩家', '新增玩家'],
    textStyle: { color: '#A9B8CE' }
  },
  grid: {
    left: 48,
    right: 18,
    top: 48,
    bottom: 36
  },
  xAxis: {
    type: 'category',
    boundaryGap: false,
    axisLabel: { color: '#8FA4BE' },
    axisLine: { lineStyle: { color: '#29425E' } }
  },
  yAxis: {
    type: 'value',
    axisLabel: { color: '#8FA4BE' },
    splitLine: {
      lineStyle: {
        color: 'rgba(77, 135, 180, 0.15)',
        type: 'dashed'
      }
    }
  },
  series: [
    {
      name: '活跃玩家',
      type: 'line',
      smooth: true,
      showSymbol: false,
      areaStyle: { opacity: 0.12 }
    },
    {
      name: '新增玩家',
      type: 'line',
      smooth: true,
      showSymbol: false,
      areaStyle: { opacity: 0.08 }
    }
  ]
};
```

颜色由统一主题配置注入，不能在不同页面随意改变。

### 交互

- Hover 显示时间、活跃玩家数、新增玩家数。
- 支持 7 天、30 天、90 天时间切换。
- 点击某个时间点，联动地球和风险信息。
- 空数据时显示明确的空状态。

### C2. RegionalChurnRanking 区域流失排行

展示区域、流失率、变化幅度和排名。

数据结构：

```ts
interface RegionalChurnItem {
  regionId: string;
  regionName: string;
  churnRate: number;
  previousChurnRate: number;
  change: number;
  atRiskPlayers: number;
  rank: number;
  latitude: number;
  longitude: number;
}
```

展示形式：

```text
01  North America  ████████  7.8%  +1.2%
02  Europe         ██████    6.1%  +0.8%
03  Asia           █████     4.9%  -0.3%
```

### 交互

- 点击区域，地球自动旋转到该区域。
- 地球同步显示该区域的风险标记。
- 右侧情感趋势切换到对应区域。
- Hover 展示区域详细数据。

---

## D. GlobeStage 中央地球

这是整个首页最重要的模块，开发优先级最高。

### D1. 技术选择原则

先检查现有实现：

| 现有技术 | 建议 |
|---|---|
| Three.js | 保留现有场景，增加数据图层 |
| globe.gl | 继续复用其地球图层能力 |
| three-globe | 继续使用现有 Three.js 场景 |
| ECharts GL | 根据当前性能和视觉需求决定是否保留 |
| 静态图片 | 评估替换为真正可交互的 3D 地球 |

不要同时引入多个地球渲染引擎。

如果目前使用原生 HTML + JS，且没有可复用的 3D 地球，可以优先评估：

- `globe.gl`：<https://github.com/vasturiano/globe.gl>
- `three-globe`：<https://github.com/vasturiano/three-globe>

如果使用 React：

- `react-globe.gl`：<https://github.com/vasturiano/react-globe.gl>

### D2. 地球图层

建议拆分为以下图层：

| 图层 | 组件 | 表现 |
|---|---|---|
| 地球本体 | GlobeBaseLayer | 深蓝地球纹理 |
| 大气层 | AtmosphereLayer | 蓝色边缘辉光 |
| 玩家分布 | PlayerMarkerLayer | 青色点位 |
| 热点区域 | HotspotLayer | 紫色或橙色脉冲光环 |
| 流失风险 | ChurnRiskLayer | 红色风险区域 |
| 迁移路径 | ChurnArcLayer | 动态弧线 |
| 信息标签 | GlobeLabelLayer | 区域名称和指标 |

### D3. 数据字段

```ts
interface GlobePlayerPoint {
  id: string;
  latitude: number;
  longitude: number;
  playerCount: number;
  sentiment: number;
  riskLevel: 'low' | 'medium' | 'high';
}

interface GlobeHotspot {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  heat: number;
  sentiment: number;
  relatedPlayers: number;
  category: string;
}

interface GlobeChurnArc {
  id: string;
  sourceRegionId: string;
  targetRegionId: string;
  startLat: number;
  startLng: number;
  endLat: number;
  endLng: number;
  playerCount: number;
  riskLevel: 'low' | 'medium' | 'high';
}

interface GlobeData {
  playerPoints: GlobePlayerPoint[];
  hotspots: GlobeHotspot[];
  churnArcs: GlobeChurnArc[];
  selectedRegionId?: string;
}
```

### D4. 交互行为

**行为一：点击区域**

1. 选中区域。
2. 地球平滑旋转至目标经纬度。
3. 展示区域信息卡。
4. 左侧区域排行高亮。
5. 右侧情感趋势更新。

**行为二：点击热点**

1. 高亮热点标记。
2. 展示热点名称、热度、相关玩家数、情感指数。
3. 显示关联风险。
4. 底部热点响应链切换为该热点的数据。

**行为三：点击流失路径**

1. 高亮对应弧线。
2. 展示来源区域、目标区域和相关玩家数。
3. 显示路径关联的风险等级。

**行为四：基础操作**

- 鼠标拖动旋转地球。
- 滚轮缩放。
- 双击恢复默认视角。
- 鼠标悬停显示 Tooltip。

### D5. 地球视觉规范

- 地球直径占中央舞台可用高度的约 68%–78%。
- 大气层要有明显但克制的辉光。
- 地球周围可以增加轨道环和扫描线。
- 数据点不能过度密集。
- 弧线透明度和宽度应根据数据量变化。
- 动态效果不能影响地图交互和图表可读性。

不要用随机粒子假装真实玩家分布；地理坐标必须来自数据，演示数据需要明确标识。

### D6. 性能要求

- 地球初始化只执行一次。
- 数据更新尽量更新图层，不要反复销毁整个 WebGL 场景。
- 页面离开时正确释放事件监听、动画帧和 WebGL 资源。
- 使用设备像素比上限，避免高分辨率屏幕过度消耗 GPU。
- 支持 WebGL 不可用时的降级提示。
- 地球动画在页面不可见时应暂停或降频。
- 优先保证低中端设备可操作性。

---

## E. RightAnalyticsPanel 右侧分析区

### E1. SentimentTrendChart 玩家情感趋势

展示正面、中性、负面情感占比随时间变化。

数据结构：

```ts
interface SentimentPoint {
  timestamp: string;
  positive: number;
  neutral: number;
  negative: number;
}

interface SentimentTrendData {
  points: SentimentPoint[];
  regionId?: string;
}
```

要求：

```ts
positive + neutral + negative = 100
```

如果实际数据不是百分比，需要在数据适配层完成归一化，不允许图表直接展示不一致的口径。

### 图表配置

- 使用 ECharts 折线图或堆叠面积图。
- X 轴为时间。
- Y 轴为 0%–100%。
- 正面、中性、负面三条序列。
- Tooltip 显示三类情感比例。
- 支持按地区、时间范围筛选。

颜色语义：

- 正面：绿色 `#35D399`
- 中性：蓝色 `#4F8CC9`
- 负面：红色 `#FF5D73`

### E2. AIRiskAlerts AI 风险预警

数据结构：

```ts
interface RiskAlert {
  id: string;
  title: string;
  description: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  regionId?: string;
  hotspotId?: string;
  metric?: {
    name: string;
    currentValue: number;
    previousValue?: number;
    unit: string;
  };
  createdAt: string;
  status: 'active' | 'acknowledged' | 'resolved';
}
```

### 展示

- 风险等级。
- 预警标题。
- 风险原因。
- 关联地区或热点。
- 触发时间。

### 交互

- 点击预警，定位到地球上的关联地区。
- 点击查看详情，跳转到流失分析页面或展开详情面板。
- 支持标记为已确认。
- 已解决的预警不应继续显示为活动风险。

风险等级必须由后端或明确的规则引擎提供，前端不得自行猜测。

### E3. RecommendedActions AI 行动建议

数据结构：

```ts
interface RecommendedAction {
  id: string;
  title: string;
  reason: string;
  expectedImpact?: string;
  targetRegionId?: string;
  targetSegmentId?: string;
  relatedAlertId?: string;
  priority: 'low' | 'medium' | 'high';
  status: 'pending' | 'approved' | 'executing' | 'completed';
}
```

### 交互

- 查看策略详情。
- 跳转到策略中心。
- 如果项目已有策略执行接口，才显示实际执行操作。
- 未接入执行接口时，按钮应改为「查看策略」，不得假装已经执行成功。

---

## F. DashboardFooter 底部决策链

### F1. HotspotResponseFlow

展示：

```text
热点事件
   ↓
玩家情绪变化
   ↓
玩家流失风险
   ↓
召回策略
```

数据结构：

```ts
interface HotspotResponseFlowData {
  hotspot: {
    id: string;
    name: string;
    heat: number;
  };
  sentimentChange: {
    negativeRate: number;
    change: number;
  };
  churnRisk: {
    affectedPlayers: number;
    riskLevel: string;
  };
  response: {
    strategyId: string;
    name: string;
    expectedRecoveryRate?: number;
  };
}
```

### 交互

- 点击任意节点显示详情。
- 点击热点节点定位地球热点。
- 点击召回策略跳转到策略中心。
- 节点之间通过动画连线表达关系。

### F2. LiveActivityTicker

展示实时动态：

- 新增活跃玩家。
- 热点热度变化。
- 区域流失率变化。
- 玩家情感趋势变化。

数据结构：

```ts
interface LiveActivity {
  id: string;
  type: 'player' | 'hotspot' | 'churn' | 'sentiment';
  message: string;
  timestamp: string;
  regionId?: string;
}
```

注意：如果当前没有 WebSocket 或 SSE 服务，先使用轮询或静态 Mock，不要在前端伪造实时数据状态。

---

# 3. 数据接口设计

## 3.1 统一请求参数

首页所有数据应使用同一时间范围和筛选条件。

```ts
interface DashboardQuery {
  startDate: string;
  endDate: string;
  regionId?: string;
  refresh?: boolean;
}
```

建议日期采用 ISO 8601 格式，明确时区；统计区间采用统一的闭区间或左闭右开规则，并在后端统一执行。

## 3.2 建议 API

以下为建议接口命名，Agent 必须先检查现有后端路由，再决定复用或适配，不得直接假定这些接口已经存在。

| 方法 | 接口 | 用途 |
|---|---|---|
| GET | `/api/dashboard/overview` | 首页 KPI |
| GET | `/api/dashboard/activity` | 玩家活跃趋势 |
| GET | `/api/dashboard/regional-churn` | 区域流失排行 |
| GET | `/api/dashboard/globe` | 地球数据 |
| GET | `/api/dashboard/sentiment` | 情感趋势 |
| GET | `/api/dashboard/alerts` | AI 风险预警 |
| GET | `/api/dashboard/actions` | 推荐行动 |
| GET | `/api/dashboard/hotspot-flow` | 热点响应链 |
| GET | `/api/dashboard/live-activities` | 实时动态 |

统一查询参数示例：

```http
GET /api/dashboard/overview?startDate=2026-10-01&endDate=2026-10-31
```

## 3.3 统一响应结构

```ts
interface ApiResponse<T> {
  success: boolean;
  data: T;
  meta: {
    requestId: string;
    generatedAt: string;
    dataSource: 'live' | 'mock' | 'cache';
    timeRange: {
      start: string;
      end: string;
    };
  };
  error?: {
    code: string;
    message: string;
  };
}
```

### 数据约定

1. 所有计数使用整数。
2. 比率统一明确是 `0–1` 还是 `0–100`，推荐 API 使用 `0–100` 百分数。
3. 情感指数统一使用 `-1–1`。
4. 时间统一使用 ISO 8601。
5. 经纬度使用十进制度。
6. 所有区域必须有稳定的 `regionId`。
7. 所有热点必须有稳定的 `hotspotId`。
8. API 必须提供数据生成时间。
9. 前端应区分实时数据、缓存数据和 Mock 数据。

## 3.4 前端数据管理

建议创建统一数据入口：

```ts
interface DashboardData {
  kpis: KPIOverviewData;
  activity: PlayerActivityData;
  regionalChurn: RegionalChurnItem[];
  globe: GlobeData;
  sentiment: SentimentTrendData;
  alerts: RiskAlert[];
  actions: RecommendedAction[];
  hotspotFlow: HotspotResponseFlowData[];
  liveActivities: LiveActivity[];
}
```

数据加载顺序：

1. 页面初始化时并行请求独立接口。
2. 先展示骨架屏。
3. 某一模块加载失败时，只显示该模块错误，不应导致整个首页空白。
4. 时间范围变化后，取消或忽略过期请求。
5. 对地球数据和图表数据进行必要的格式转换。
6. 统一处理空值、异常值和缺失字段。

---

# 4. CSS 与视觉设计规范

## 4.1 主题变量

建立统一的 Design Tokens，禁止每个组件自行定义一套颜色。

```css
:root {
  /* 背景 */
  --bg-primary: #070B16;
  --bg-secondary: #0B1424;
  --bg-panel: rgba(13, 27, 47, 0.82);

  /* 品牌色 */
  --color-cyan: #35D9F2;
  --color-blue: #318BFF;
  --color-purple: #9275FF;

  /* 状态色 */
  --color-success: #35D399;
  --color-warning: #FFB45F;
  --color-danger: #FF6B75;

  /* 文本 */
  --text-primary: #EAF3FF;
  --text-secondary: #9EB1CA;
  --text-muted: #637A96;

  /* 边框 */
  --border-default: rgba(68, 155, 209, 0.22);
  --border-active: rgba(53, 217, 242, 0.65);

  /* 发光 */
  --glow-cyan:
    0 0 18px rgba(53, 217, 242, 0.18);

  --glow-purple:
    0 0 18px rgba(146, 117, 255, 0.18);

  /* 圆角 */
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;

  /* 间距 */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;

  /* 动画 */
  --duration-fast: 150ms;
  --duration-normal: 240ms;
  --duration-slow: 500ms;
}
```

## 4.2 字体

优先使用：

```css
font-family:
  Inter,
  "Noto Sans SC",
  "Microsoft YaHei",
  sans-serif;
```

要求：

- 指标数字使用清晰的无衬线字体。
- 数字建议启用等宽数字特性。
- 标题、正文、辅助信息建立统一字号层级。
- 不要使用过多未来科技风字体，避免影响中文可读性。

## 4.3 面板样式

```css
.dashboard-panel {
  background:
    linear-gradient(
      145deg,
      rgba(16, 31, 52, 0.94),
      rgba(7, 16, 31, 0.88)
    );

  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);

  box-shadow:
    inset 0 1px 0 rgba(255,255,255,0.035),
    0 12px 36px rgba(0,0,0,0.24);

  backdrop-filter: blur(14px);
}
```

面板高光可以使用伪元素，但不能让边框和光效过强，以至于抢过数据本身。

## 4.4 页面尺寸和网格

桌面端建议：

```css
.dashboard {
  min-height: 100vh;
  display: grid;
  grid-template-rows:
    64px
    auto
    minmax(620px, 1fr)
    56px;
  gap: 12px;
  padding: 16px 24px 12px;
}

.dashboard-main {
  display: grid;
  grid-template-columns:
    minmax(280px, 0.95fr)
    minmax(440px, 1.35fr)
    minmax(300px, 0.95fr);
  gap: 16px;
}

.left-panel,
.right-panel {
  display: grid;
  gap: 16px;
  min-width: 0;
}

.globe-stage {
  position: relative;
  min-width: 0;
  min-height: 620px;
  overflow: hidden;
}
```

实际尺寸应根据现有页面和浏览器测试调整，不允许通过固定宽度导致横向溢出。

## 4.5 响应式规则

- 大屏：三列数据舱布局，中央地球保持最大视觉权重。
- 中屏：左右面板适当收窄，允许地球区域保持最小可用宽度。
- 小屏：切换为纵向模块布局，地球优先展示，其他图表依次排列。
- 小屏下关闭不必要的轨道粒子和复杂动画。
- 图表必须监听容器尺寸变化并正确 resize。

## 4.6 动画规范

允许：

- 地球缓慢自转。
- 大气层呼吸光效。
- 热点脉冲。
- 数据弧线流动。
- 卡片 hover 高光。
- 页面切换淡入。

禁止：

- 所有面板同时闪烁。
- 大面积持续强烈发光。
- 高频随机数字跳动。
- 影响点击操作的动画覆盖层。

必须支持 `prefers-reduced-motion`，减少动态效果。

---

# 5. 全局交互状态

建议建立统一的首页状态对象：

```ts
interface DashboardState {
  dateRange: {
    start: string;
    end: string;
  };

  selectedRegionId: string | null;
  selectedHotspotId: string | null;
  selectedAlertId: string | null;

  globeView: {
    latitude: number;
    longitude: number;
    altitude: number;
  };

  loading: boolean;
  dataStatus: 'live' | 'delayed' | 'mock' | 'offline';
}
```

### 联动规则

| 用户操作 | 应更新的模块 |
|---|---|
| 修改时间范围 | KPI、活跃趋势、流失排行、地球、情感趋势 |
| 点击区域排行 | 地球、区域信息、情感趋势 |
| 点击地球热点 | 热点详情、情感趋势、底部响应链 |
| 点击风险预警 | 地球定位、风险详情 |
| 点击召回策略 | 策略详情或策略中心 |
| 点击底部热点节点 | 地球热点、关联风险 |

状态管理必须有单一数据来源，不能让每个组件各自保存一份互相冲突的筛选状态。

---

# 6. 开发任务拆分与优先级

## P0：项目检查与基础框架

- [ ] 检查现有项目结构与技术栈。
- [ ] 确认首页现有图表、地球和数据接口。
- [ ] 输出技术栈检查报告。
- [ ] 确认改造文件清单。
- [ ] 建立统一 CSS Tokens。
- [ ] 建立首页布局与响应式骨架。

**验收：** 不破坏现有页面，能够正常启动和访问。

## P1：首页核心视觉与地球

- [ ] 重构 DashboardHeader。
- [ ] 实现 KPIOverview。
- [ ] 实现中央 GlobeStage。
- [ ] 保留并增强原有地球。
- [ ] 增加玩家点位、热点和流失弧线。
- [ ] 实现地球 Tooltip 和选中状态。

**验收：** 地球可旋转、缩放、定位，数据点与真实数据字段关联。

## P2：图表与运营分析

- [ ] PlayerActivityChart。
- [ ] RegionalChurnRanking。
- [ ] SentimentTrendChart。
- [ ] AIRiskAlerts。
- [ ] RecommendedActions。

**验收：** 图表可以正确加载、更新和响应筛选；各模块独立处理异常。

## P3：跨组件联动

- [ ] 区域排行与地球联动。
- [ ] 热点与情感趋势联动。
- [ ] 风险预警与地球联动。
- [ ] 底部热点响应链。
- [ ] 实时动态信息流。

**验收：** 用户点击任意关键数据后，相关模块能够同步变化。

## P4：性能、兼容性和收尾

- [ ] 检查 WebGL 资源释放。
- [ ] 检查图表实例是否重复创建。
- [ ] 检查请求取消与过期响应。
- [ ] 检查页面响应式布局。
- [ ] 检查空数据、错误数据和加载状态。
- [ ] 检查键盘可操作性和减少动态效果设置。
- [ ] 检查控制台错误和网络请求错误。

**验收：** 页面持续操作无明显卡顿、内存持续增长或布局错乱。

---

# 7. Agent 测试清单

## 功能测试

- [ ] 首页可以正常加载。
- [ ] 四个 KPI 数值正确显示。
- [ ] 时间范围切换生效。
- [ ] 活跃趋势图正确渲染。
- [ ] 区域排行正确排序。
- [ ] 地球可以旋转和缩放。
- [ ] 地球点位与接口数据对应。
- [ ] 点击区域可以触发跨组件联动。
- [ ] 点击热点可以查看详情。
- [ ] 情感趋势可以按地区筛选。
- [ ] 风险预警可以查看详情。
- [ ] 推荐策略不会在无后端接口时假装执行成功。

## 异常测试

- [ ] API 超时。
- [ ] API 返回空数组。
- [ ] API 返回部分缺失字段。
- [ ] API 返回异常数值。
- [ ] WebGL 不可用。
- [ ] 用户快速切换时间范围。
- [ ] 用户连续点击多个地区。
- [ ] 窗口改变大小。
- [ ] 页面切换后再次返回首页。

## 视觉测试

- [ ] 主题色统一。
- [ ] 面板间距统一。
- [ ] 图表字体和文本颜色统一。
- [ ] 中央地球视觉权重最高。
- [ ] 关键指标有足够对比度。
- [ ] 不存在面板重叠和横向溢出。
- [ ] 小屏布局可正常操作。

---

# 8. 最终交付要求

Agent 完成后必须提交：

1. 修改文件清单。
2. 实际组件结构说明。
3. 数据接口与字段映射说明。
4. 地球技术方案及复用情况。
5. CSS Tokens 和主题规范。
6. 已完成的交互列表。
7. 测试结果与未解决问题。
8. 首页实际运行截图。
9. Mock 数据与真实数据的区分说明。
10. 后续需要后端配合的接口清单。

**最终验收标准：**

这不是一个只追求视觉效果的静态大屏，而是一个可以真实操作、数据可追溯、模块可维护的游戏运营分析首页。

用户应当能够从全局指标进入区域分析，再从地球热点定位到玩家情绪和流失风险，最终找到可以执行的召回策略。

首页必须围绕这条业务链组织信息：

**玩家行为 → 热点变化 → 情感变化 → 流失风险 → 精准召回。**
