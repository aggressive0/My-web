# 基于热点情感的玩家流失阻断与精准召回方案

## 项目概述

本项目是一个基于大数据分析和AI技术的游戏玩家流失-召回-留存闭环分析系统。通过对玩家行为数据和情感评论的深度分析，实现精准的玩家流失风险识别和召回策略制定。

**核心技术亮点：**
- 🔥 **AI算力驱动**：基于机器学习的流失风险预测模型
- 💖 **情感计算分析**：自然语言处理技术分析玩家情感倾向
- 🎯 **精准打击召回**：个性化推荐系统实现精准玩家召回

## 项目结构

```
├── backend/                    # 后端服务
│   ├── app.py                 # Flask API 主入口
│   ├── requirements.txt       # 依赖列表
│   ├── data/                  # 数据分析结果
│   └── instance/              # 数据库文件
├── front/                     # 前端应用
│   ├── src/                   # Vue 组件
│   ├── static_html/           # 静态HTML页面（主要展示）
│   ├── images/                # 图片资源
│   └── package.json           # 前端依赖
└── README.md                  # 项目说明文档
```

## 技术栈

### 前端
| 技术 | 版本 | 用途 |
|------|------|------|
| Vue.js | 3.4.x | 前端框架 |
| ECharts | 5.4.x | 数据可视化 |
| Three.js | 0.183.x | 3D可视化 |
| Vite | 5.0.x | 构建工具 |
| Bootstrap | 5.x | UI框架 |

### 后端
| 技术 | 版本 | 用途 |
|------|------|------|
| Flask | 3.x | Web框架 |
| Flask-CORS | 4.x | 跨域支持 |
| Flask-SQLAlchemy | 3.x | ORM |
| SQLite | 内置 | 数据库 |
| Pandas | 2.x | 数据分析 |

## 快速开始

### 环境要求
- Python 3.8+
- Node.js 18+

### 后端启动

```bash
# 进入后端目录
cd backend

# 安装依赖
pip install -r requirements.txt

# 启动服务
python app.py
```

后端服务将在 `http://localhost:5000` 运行。

### 前端启动（开发模式）

```bash
# 进入前端目录
cd front

# 安装依赖
npm install

# 开发模式
npm run dev

# 构建生产版本
npm run build
```

### 静态页面预览

```bash
# 进入静态HTML目录
cd front/static_html

# 启动简单HTTP服务器
python -m http.server 8080
```

访问 `http://localhost:8080` 查看效果。

## API接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/game-data` | GET/POST | 游戏数据 |
| `/api/player-comparison` | GET | 玩家对比分析 |
| `/api/churn-retention` | GET | 流失召回策略 |
| `/api/churn-risk` | GET | 流失风险分析 |
| `/api/sentiment-trend` | GET | 情感趋势数据 |

## 功能模块

1. **首页仪表盘**：全局数据概览和核心指标展示
2. **数据分析模块**：玩家行为数据深度分析
3. **解决方案模块**：流失阻断策略展示
4. **效果验证模块**：召回效果数据验证
5. **评论区模块**：玩家情感实时监控

## 部署到GitHub Pages

### 自动部署配置

本项目使用GitHub Actions自动部署静态页面到GitHub Pages。

**配置文件：** `.github/workflows/deploy.yml`

```yaml
name: Deploy to GitHub Pages

on:
  push:
    branches:
      - main

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '18'

      - name: Install dependencies
        run: |
          cd front/static_html
          npm install

      - name: Deploy to GitHub Pages
        uses: peaceiris/actions-gh-pages@v4
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: ./front/static_html
          publish_branch: gh-pages
```

### 手动部署

```bash
# 构建前端
cd front/static_html
npm run build

# 部署到gh-pages分支
git subtree push --prefix front/static_html origin gh-pages
```

## 项目亮点

### 数据可视化
- 3D地球展示全球玩家分布
- 动态桑基图展示流失转化路径
- 情感趋势实时监控图表

### AI分析能力
- 基于情感词典的评论分析
- 流失风险评分模型
- 精准召回策略推荐

### 用户体验
- 科幻风格UI设计
- 霓虹渐变标题效果
- 交互式数据展示

## 开发说明

### 代码规范
- Python代码遵循PEP8规范
- JavaScript/Vue代码使用ESLint检查
- CSS使用BEM命名规范

### 贡献指南
1. Fork项目
2. 创建功能分支
3. 提交代码
4. 发起Pull Request

## 许可证

MIT License

## 联系方式

项目维护者：[aggressive0](https://github.com/aggressive0)

---

⭐ **如果这个项目对您有帮助，请给它一个Star！**