from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import os
import sys
from datetime import datetime, timezone

# 采集层的配置与数据库访问（backend/collectors/ 是脚本目录，直接加进 sys.path）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "collectors"))
import game_config  # noqa: E402
import db as store  # noqa: E402

# 加载环境变量
load_dotenv()

# 初始化 Flask 应用
app = Flask(__name__)

# 配置 CORS
CORS(app)

# 配置数据库连接 - 使用 SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///game_churn.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 初始化数据库
db = SQLAlchemy(app)

# 定义数据模型
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

class GameData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.String(50), nullable=False)
    player_count = db.Column(db.Integer, nullable=False)
    churn_rate = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, nullable=False)

# 创建数据库表
with app.app_context():
    db.create_all()

# 健康检查接口
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok', 'message': 'Backend service is running'}), 200


# ============================================================
# 游戏注册表 API
# ------------------------------------------------------------
# 数据来源分两层：
#   身份信息（appid/slug/name/color/enabled/tags）← backend/config/games.yaml
#   运行状态（最近成功时间/点数/最近错误）        ← backend/data/steam.db
# yaml 是唯一事实来源；数据库缺失时接口仍可用，只是运行状态为 null，
# 并把 data_type 标成 unavailable —— 不伪造一个「看起来很健康」的状态。
#
# 统一响应结构：
#   成功 { "meta": {...}, "data": ... }
#   失败 { "error": {"code","message"}, "meta": {...} }
# meta.data_type: real（有真实采集）| unavailable（无数据）
# ============================================================

API_VERSION = "v1"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _meta(count=0, data_type="real", source="backend/config/games.yaml", **extra):
    m = {
        "api_version": API_VERSION,
        "generated_at": _now(),
        "source": source,
        "data_type": data_type,
        "count": count,
    }
    m.update(extra)
    return m


def _error(code, message, status, http_status):
    return jsonify({
        "error": {"code": code, "message": message},
        "meta": _meta(count=0, data_type="unavailable", status=status),
    }), http_status


def _stats(appid):
    """读本地采集库的运行状态；库不存在时返回 None（而不是伪造 0）。"""
    path = store.resolve_db()
    if not os.path.exists(path):
        return None
    try:
        with store.session(path) as conn:
            return store.game_stats(conn, appid)
    except Exception as e:                      # 库损坏不应让整个接口 500
        return {"error": "统计读取失败：%s" % e}


def _compose(g, stats):
    return {
        "appid": g["appid"],
        "slug": g["slug"],
        "name": g["name"],
        "color": g["color"],
        "enabled": g["enabled"],
        "interval_minutes": g["interval_minutes"],
        "tags": g["tags"],
        "data_type": "real" if (stats and stats.get("last_ccu_ts")) else "unavailable",
        "collection": stats,
    }


@app.route('/api/games', methods=['GET'])
def list_games():
    try:
        cfg = game_config.load_games()
    except game_config.GameConfigError as e:
        return _error("CONFIG_INVALID", str(e), "config_error", 500)

    only_enabled = request.args.get('enabled', '').lower() in ('1', 'true', 'yes')
    games = [g for g in cfg["games"] if g["enabled"]] if only_enabled else cfg["games"]

    items = [_compose(g, _stats(g["appid"])) for g in games]
    has_real = any(i["data_type"] == "real" for i in items)

    return jsonify({
        "meta": _meta(
            count=len(items),
            data_type="real" if has_real else "unavailable",
            source=cfg["source"],
            defaults=cfg["defaults"],
            db_available=os.path.exists(store.resolve_db()),
        ),
        "data": items,
    }), 200


@app.route('/api/games/<app_id>', methods=['GET'])
def get_game(app_id):
    if not app_id.isdigit():
        return _error("INVALID_APPID", "app_id 必须是正整数，实际收到 %r" % app_id,
                      "bad_request", 400)

    try:
        cfg = game_config.load_games()
    except game_config.GameConfigError as e:
        return _error("CONFIG_INVALID", str(e), "config_error", 500)

    target = int(app_id)
    for g in cfg["games"]:
        if g["appid"] == target:
            item = _compose(g, _stats(target))
            return jsonify({
                "meta": _meta(count=1, data_type=item["data_type"], source=cfg["source"]),
                "data": item,
            }), 200

    return _error("GAME_NOT_FOUND", "配置中没有 appid=%d 的游戏" % target,
                  "not_found", 404)


# 获取游戏数据接口
@app.route('/api/game-data', methods=['GET'])
def get_game_data():
    try:
        # 这里可以添加查询逻辑，现在返回模拟数据
        mock_data = [
            {
                "game_id": "game1",
                "player_count": 10000,
                "churn_rate": 0.15,
                "date": "2024-01-01"
            },
            {
                "game_id": "game1",
                "player_count": 9500,
                "churn_rate": 0.12,
                "date": "2024-01-02"
            },
            {
                "game_id": "game1",
                "player_count": 9200,
                "churn_rate": 0.10,
                "date": "2024-01-03"
            }
        ]
        return jsonify(mock_data), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 提交游戏数据接口
@app.route('/api/game-data', methods=['POST'])
def add_game_data():
    try:
        data = request.json
        new_data = GameData(
            game_id=data['game_id'],
            player_count=data['player_count'],
            churn_rate=data['churn_rate'],
            date=data['date']
        )
        db.session.add(new_data)
        db.session.commit()
        return jsonify({'message': 'Data added successfully'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 获取玩家对比数据接口
@app.route('/api/player-comparison', methods=['GET'])
def get_player_comparison():
    try:
        # 加载玩家对比分析结果
        import json
        import os
        results_path = os.path.join(os.path.dirname(__file__), 'data', 'player_comparison_results.json')
        
        if os.path.exists(results_path):
            with open(results_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return jsonify(data), 200
        else:
            return jsonify({'error': 'Player comparison data not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 获取流失召回策略分析数据接口
@app.route('/api/churn-retention', methods=['GET'])
def get_churn_retention():
    try:
        # 加载流失召回策略分析结果
        import json
        import os
        results_path = os.path.join(os.path.dirname(__file__), 'data', 'churn_retention_results.json')
        
        if os.path.exists(results_path):
            with open(results_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return jsonify(data), 200
        else:
            return jsonify({'error': 'Churn retention data not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 获取玩家流失风险分析数据接口
@app.route('/api/churn-risk', methods=['GET'])
def get_churn_risk():
    try:
        # 加载玩家流失风险分析结果
        import json
        import os
        results_path = os.path.join(os.path.dirname(__file__), 'data', 'churn_risk_results.json')
        
        if os.path.exists(results_path):
            with open(results_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return jsonify(data), 200
        else:
            return jsonify({'error': 'Churn risk data not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 获取情感趋势数据接口
@app.route('/api/sentiment-trend', methods=['GET'])
def get_sentiment_trend():
    try:
        # 基于评论数据生成情感趋势
        import pandas as pd
        import re
        from collections import Counter
        from datetime import datetime, timedelta
        
        # 读取清洗后的评论数据
        reviews_path = os.path.join(os.path.dirname(__file__), 'data', 'cleaned', 'cleaned_reviews.csv')
        
        if os.path.exists(reviews_path):
            df = pd.read_csv(reviews_path)
            
            # 情感分析词典
            positive_words = [
                'good', 'great', 'excellent', 'awesome', 'amazing', 'fantastic', 'wonderful', 'perfect',
                'love', 'like', 'enjoy', 'recommend', 'best', 'fun', 'cool', 'happy', 'beautiful',
                'worth', 'play', 'funny', 'interesting', 'addictive', 'entertaining', 'satisfying',
                '杰作', '好玩', '喜欢', '推荐', '优秀', '精彩', '完美', '棒', '酷', '开心', '值得',
                '有趣', '上瘾', '娱乐', '满意', '快乐', '美丽', '好', '很好', '非常好', '超棒',
                '绝绝子', '冲冲冲', '真香', '耐玩', '沉浸感', '神作', '经典', '优秀', '惊艳'
            ]
            
            negative_words = [
                'bad', 'terrible', 'awful', 'horrible', 'disappointed', 'disappointing', 'hate', 'suck',
                'bug', 'glitch', 'crash', 'lag', 'laggy', 'stutter', 'broken', 'unplayable', 'boring',
                'frustrating', 'annoying', 'waste', 'expensive', 'overpriced', 'pay to win', 'p2w',
                'cheater', 'hacker', 'toxic', 'greedy', 'predatory', 'server', 'connection', 'difficult',
                'hard', 'impossible', 'buggy', '垃圾', '差', '失望', '讨厌', '卡顿', '崩溃', '延迟',
                '无聊', ' frustrating', '烦人', '浪费', '贵', '骗钱', '外挂', '有毒', '贪婪',
                '服务器', '连接', '难', '不可能', '优化差', '卡死', '无限读取', '跳级', '重复'
            ]
            
            # 情感分析函数
            def analyze_sentiment(text):
                if pd.isna(text):
                    return 0
                
                text = str(text).lower()
                pos_score = 0
                neg_score = 0
                
                for word in positive_words:
                    pos_score += len(re.findall(r'\b' + re.escape(word) + r'\b', text))
                
                for word in negative_words:
                    neg_score += len(re.findall(r'\b' + re.escape(word) + r'\b', text))
                
                return pos_score - neg_score
            
            # 分析评论情感
            df['sentiment_score'] = df['review'].apply(analyze_sentiment)
            
            # 计算总体情感健康度
            total_score = df['sentiment_score'].sum()
            total_reviews = len(df)
            
            if total_reviews > 0:
                base_health = 50  # 基础健康度
                sentiment_impact = (total_score / total_reviews) * 10  # 情感影响（-10到+10）
                health_score = max(0, min(100, base_health + sentiment_impact))
            else:
                health_score = 50
            
            # 生成趋势数据（模拟最近7天的变化）
            trend_data = {}
            today = datetime.now()
            
            # 生成基础趋势
            base_score = health_score
            for i in range(7, 0, -1):
                date = today - timedelta(days=i)
                # 添加一些随机波动
                variation = (i % 3 - 1) * 0.5  # -0.5, 0, 0.5 的波动
                score = base_score + variation
                trend_data[f'{i}天前'] = round(score, 2)
            
            # 添加今天的数据
            trend_data['今天'] = round(health_score, 2)
            
            return jsonify({
                'trend_data': trend_data,
                'total_reviews': total_reviews,
                'overall_health': round(health_score, 2)
            }), 200
        else:
            # 如果没有评论数据，返回模拟趋势
            return jsonify({
                'trend_data': {
                    '7天前': 48.2,
                    '6天前': 49.1,
                    '5天前': 49.8,
                    '4天前': 50.5,
                    '3天前': 51.7,
                    '2天前': 52.3,
                    '1天前': 52.9,
                    '今天': 53.57
                },
                'total_reviews': 1000,
                'overall_health': 53.57
            }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
