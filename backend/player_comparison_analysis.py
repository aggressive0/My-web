import pandas as pd
import numpy as np
import logging
import os
import json
from datetime import datetime

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 自定义 JSON 编码器类，处理 pandas 数据类型
class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(CustomEncoder, self).default(obj)

class PlayerComparisonAnalyzer:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.cleaned_dir = os.path.join(data_dir, 'cleaned')
        
    def load_data(self):
        """加载清洗后的数据"""
        logger.info("开始加载清洗后的数据...")
        
        # 加载 players.csv
        players_path = os.path.join(self.cleaned_dir, 'cleaned_players.csv')
        if os.path.exists(players_path):
            self.players = pd.read_csv(players_path)
            logger.info(f"加载 players.csv 完成，共 {len(self.players)} 行")
        else:
            self.players = None
            logger.warning(f"文件不存在: {players_path}")
        
        # 加载 friends.csv
        friends_path = os.path.join(self.cleaned_dir, 'cleaned_friends.csv')
        if os.path.exists(friends_path):
            self.friends = pd.read_csv(friends_path)
            logger.info(f"加载 friends.csv 完成，共 {len(self.friends)} 行")
        else:
            self.friends = None
            logger.warning(f"文件不存在: {friends_path}")
        
        # 加载 reviews.csv
        reviews_path = os.path.join(self.cleaned_dir, 'cleaned_reviews.csv')
        if os.path.exists(reviews_path):
            self.reviews = pd.read_csv(reviews_path)
            logger.info(f"加载 reviews.csv 完成，共 {len(self.reviews)} 行")
        else:
            self.reviews = None
            logger.warning(f"文件不存在: {reviews_path}")
        
        # 加载 history.csv
        history_path = os.path.join(self.cleaned_dir, 'cleaned_history.csv')
        if os.path.exists(history_path):
            self.history = pd.read_csv(history_path)
            logger.info(f"加载 history.csv 完成，共 {len(self.history)} 行")
        else:
            self.history = None
            logger.warning(f"文件不存在: {history_path}")
        
        # 加载 games.csv
        games_path = os.path.join(self.cleaned_dir, 'cleaned_games.csv')
        if os.path.exists(games_path):
            self.games = pd.read_csv(games_path)
            logger.info(f"加载 games.csv 完成，共 {len(self.games)} 行")
        else:
            self.games = None
            logger.warning(f"文件不存在: {games_path}")
    
    def identify_china_players(self):
        """识别中国玩家"""
        if self.players is None:
            return None, None
        
        # 根据国家字段识别中国玩家
        china_keywords = ['China', 'Chinese', 'CN', '中国']
        china_mask = self.players['country'].str.contains('|'.join(china_keywords), case=False, na=False)
        
        china_players = self.players[china_mask]
        global_players = self.players[~china_mask]
        
        logger.info(f"中国玩家数量: {len(china_players)}")
        logger.info(f"全球玩家数量: {len(global_players)}")
        
        return china_players, global_players
    
    def calculate_metrics(self, player_ids, label):
        """计算指定玩家群体的指标"""
        metrics = {}
        
        # 将 player_ids 转换为整数列表（因为 DataFrame 中的 playerid 是整数类型）
        player_ids_list = [int(pid) for pid in player_ids]
        
        # 1. 社交活跃度 - 基于好友数量（覆盖率100%）
        if self.friends is not None:
            player_friends = self.friends[self.friends['playerid'].isin(player_ids_list)]
            
            def count_friends(friends_str):
                if pd.isna(friends_str):
                    return 0
                try:
                    friends_list = eval(friends_str)
                    return len(friends_list)
                except:
                    return 0
            
            if len(player_friends) > 0:
                player_friends['friend_count'] = player_friends['friends'].apply(count_friends)
                metrics['social_activity'] = float(player_friends['friend_count'].mean())
                metrics['social_coverage'] = len(player_friends) / len(player_ids) * 100 if len(player_ids) > 0 else 0
            else:
                metrics['social_activity'] = 0
                metrics['social_coverage'] = 0
        else:
            metrics['social_activity'] = 0
            metrics['social_coverage'] = 0
        
        # 2. 评论活跃度 - 基于评论数量（中国玩家覆盖率58.85%，全球玩家46.42%）
        if self.reviews is not None:
            player_reviews = self.reviews[self.reviews['playerid'].isin(player_ids_list)]
            reviews_per_player = player_reviews.groupby('playerid').size()
            metrics['review_activity'] = float(reviews_per_player.mean()) if len(reviews_per_player) > 0 else 0
            metrics['review_coverage'] = len(reviews_per_player) / len(player_ids) * 100 if len(player_ids) > 0 else 0
        else:
            metrics['review_activity'] = 0
            metrics['review_coverage'] = 0
        
        # 3. 社区参与度 - 基于评论的helpful和awards数量
        if self.reviews is not None and len(player_reviews) > 0:
            metrics['community_engagement'] = float(player_reviews['helpful'].mean()) if 'helpful' in player_reviews.columns else 0
        else:
            metrics['community_engagement'] = 0
        
        # 4. 游戏多样性 - 基于评论的不同游戏数量
        if self.reviews is not None and len(player_reviews) > 0:
            games_per_player = player_reviews.groupby('playerid')['gameid'].nunique()
            metrics['game_diversity'] = float(games_per_player.mean()) if len(games_per_player) > 0 else 0
        else:
            metrics['game_diversity'] = 0
        
        # 5. 本土大作影响力 - 基于对特定热门游戏的评论比例
        if self.reviews is not None and len(player_reviews) > 0:
            # 计算对热门游戏的评论比例
            top_games = [730, 570, 578080, 440, 271590]  # CS2, Dota2, PUBG, TF2, GTA5
            top_game_reviews = player_reviews[player_reviews['gameid'].astype(str).isin([str(g) for g in top_games])]
            metrics['local_influence'] = len(top_game_reviews) / len(player_reviews) * 100 if len(player_reviews) > 0 else 0
        else:
            metrics['local_influence'] = 0
        
        # 6. 成就活跃度 - 基于成就数量（覆盖率较低，但如果有数据则计算）
        if self.history is not None:
            player_history = self.history[self.history['playerid'].isin(player_ids_list)]
            if len(player_history) > 0:
                achievements_per_player = player_history.groupby('playerid').size()
                metrics['achievement_activity'] = float(achievements_per_player.mean()) if len(achievements_per_player) > 0 else 0
                metrics['achievement_coverage'] = len(achievements_per_player) / len(player_ids) * 100 if len(player_ids) > 0 else 0
            else:
                metrics['achievement_activity'] = 0
                metrics['achievement_coverage'] = 0
        else:
            metrics['achievement_activity'] = 0
            metrics['achievement_coverage'] = 0
        
        logger.info(f"{label} 指标计算完成")
        logger.info(f"  社交活跃度: {metrics['social_activity']:.2f} (覆盖率: {metrics['social_coverage']:.2f}%)")
        logger.info(f"  评论活跃度: {metrics['review_activity']:.2f} (覆盖率: {metrics['review_coverage']:.2f}%)")
        logger.info(f"  社区参与度: {metrics['community_engagement']:.2f}")
        logger.info(f"  游戏多样性: {metrics['game_diversity']:.2f}")
        logger.info(f"  本土大作影响力: {metrics['local_influence']:.2f}%")
        logger.info(f"  成就活跃度: {metrics['achievement_activity']:.2f} (覆盖率: {metrics['achievement_coverage']:.2f}%)")
        
        return metrics
    
    def normalize_scores(self, china_metrics, global_metrics):
        """归一化得分到0-100分"""
        normalized = {
            'china': {},
            'global': {}
        }
        
        # 定义每个指标的最大值（用于归一化）
        max_values = {
            'social_activity': max(china_metrics['social_activity'], global_metrics['social_activity']) * 1.2,
            'review_activity': max(china_metrics['review_activity'], global_metrics['review_activity']) * 1.2,
            'community_engagement': max(china_metrics['community_engagement'], global_metrics['community_engagement']) * 1.2,
            'game_diversity': max(china_metrics['game_diversity'], global_metrics['game_diversity']) * 1.2,
            'local_influence': 100,  # 百分比已经是0-100
            'achievement_activity': max(china_metrics['achievement_activity'], global_metrics['achievement_activity']) * 1.2 if max(china_metrics['achievement_activity'], global_metrics['achievement_activity']) > 0 else 1
        }
        
        # 确保最大值不为0
        for key in max_values:
            if max_values[key] == 0:
                max_values[key] = 1
        
        # 归一化
        for metric in ['social_activity', 'review_activity', 'community_engagement', 'game_diversity', 'local_influence', 'achievement_activity']:
            normalized['china'][metric] = min(100, int((china_metrics[metric] / max_values[metric]) * 100))
            normalized['global'][metric] = min(100, int((global_metrics[metric] / max_values[metric]) * 100))
        
        return normalized
    
    def analyze_player_comparison(self):
        """分析中国玩家 vs 全球玩家对比"""
        logger.info("开始分析中国玩家 vs 全球玩家对比...")
        
        # 识别中国玩家和全球玩家
        china_players, global_players = self.identify_china_players()
        
        if china_players is None or global_players is None:
            logger.error("无法识别玩家群体")
            return None
        
        # 获取玩家ID（保持为整数）
        china_player_ids = set(china_players['playerid'].astype(int))
        global_player_ids = set(global_players['playerid'].astype(int))
        
        # 计算指标
        china_metrics = self.calculate_metrics(china_player_ids, "中国玩家")
        global_metrics = self.calculate_metrics(global_player_ids, "全球玩家")
        
        # 归一化得分
        normalized_scores = self.normalize_scores(china_metrics, global_metrics)
        
        # 构建分析结果 - 选择6个核心指标
        comparison_result = {
            "metrics": {
                "social_activity": {
                    "name": "社交活跃度",
                    "description": "玩家平均好友数量，反映社交属性",
                    "china_value": china_metrics['social_activity'],
                    "global_value": global_metrics['social_activity'],
                    "china_score": normalized_scores['china']['social_activity'],
                    "global_score": normalized_scores['global']['social_activity'],
                    "coverage": f"中国: {china_metrics['social_coverage']:.1f}%, 全球: {global_metrics['social_coverage']:.1f}%"
                },
                "review_activity": {
                    "name": "评论活跃度",
                    "description": "玩家平均发表评论数量，反映社区参与度",
                    "china_value": china_metrics['review_activity'],
                    "global_value": global_metrics['review_activity'],
                    "china_score": normalized_scores['china']['review_activity'],
                    "global_score": normalized_scores['global']['review_activity'],
                    "coverage": f"中国: {china_metrics['review_coverage']:.1f}%, 全球: {global_metrics['review_coverage']:.1f}%"
                },
                "community_engagement": {
                    "name": "社区参与度",
                    "description": "评论获得的 helpful 数量，反映内容质量",
                    "china_value": china_metrics['community_engagement'],
                    "global_value": global_metrics['community_engagement'],
                    "china_score": normalized_scores['china']['community_engagement'],
                    "global_score": normalized_scores['global']['community_engagement']
                },
                "game_diversity": {
                    "name": "游戏多样性",
                    "description": "玩家平均评论的不同游戏数量",
                    "china_value": china_metrics['game_diversity'],
                    "global_value": global_metrics['game_diversity'],
                    "china_score": normalized_scores['china']['game_diversity'],
                    "global_score": normalized_scores['global']['game_diversity']
                },
                "local_influence": {
                    "name": "本土大作影响力",
                    "description": "对热门游戏（CS2、Dota2等）的关注度占比",
                    "china_value": china_metrics['local_influence'],
                    "global_value": global_metrics['local_influence'],
                    "china_score": normalized_scores['china']['local_influence'],
                    "global_score": normalized_scores['global']['local_influence']
                },
                "achievement_activity": {
                    "name": "成就活跃度",
                    "description": "玩家平均解锁成就数量，反映游戏投入度",
                    "china_value": china_metrics['achievement_activity'],
                    "global_value": global_metrics['achievement_activity'],
                    "china_score": normalized_scores['china']['achievement_activity'],
                    "global_score": normalized_scores['global']['achievement_activity'],
                    "coverage": f"中国: {china_metrics['achievement_coverage']:.1f}%, 全球: {global_metrics['achievement_coverage']:.1f}%"
                }
            },
            "player_counts": {
                "china": len(china_player_ids),
                "global": len(global_player_ids),
                "total": len(china_player_ids) + len(global_player_ids),
                "china_percentage": len(china_player_ids) / (len(china_player_ids) + len(global_player_ids)) * 100
            },
            "radar_data": {
                "indicators": [
                    {"name": "社交活跃度", "max": 100},
                    {"name": "评论活跃度", "max": 100},
                    {"name": "社区参与度", "max": 100},
                    {"name": "游戏多样性", "max": 100},
                    {"name": "本土大作影响力", "max": 100},
                    {"name": "成就活跃度", "max": 100}
                ],
                "china_scores": [
                    normalized_scores['china']['social_activity'],
                    normalized_scores['china']['review_activity'],
                    normalized_scores['china']['community_engagement'],
                    normalized_scores['china']['game_diversity'],
                    normalized_scores['china']['local_influence'],
                    normalized_scores['china']['achievement_activity']
                ],
                "global_scores": [
                    normalized_scores['global']['social_activity'],
                    normalized_scores['global']['review_activity'],
                    normalized_scores['global']['community_engagement'],
                    normalized_scores['global']['game_diversity'],
                    normalized_scores['global']['local_influence'],
                    normalized_scores['global']['achievement_activity']
                ]
            },
            "key_insights": {
                "china_advantages": [],
                "global_advantages": []
            },
            "analysis_date": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # 生成关键洞察
        for metric_key, metric_data in comparison_result["metrics"].items():
            if metric_data['china_score'] > metric_data['global_score']:
                advantage = f"{metric_data['name']}: 中国玩家({metric_data['china_score']}分) > 全球玩家({metric_data['global_score']}分)"
                comparison_result["key_insights"]["china_advantages"].append(advantage)
            elif metric_data['global_score'] > metric_data['china_score']:
                advantage = f"{metric_data['name']}: 全球玩家({metric_data['global_score']}分) > 中国玩家({metric_data['china_score']}分)"
                comparison_result["key_insights"]["global_advantages"].append(advantage)
        
        return comparison_result
    
    def run_analysis(self):
        """运行分析"""
        logger.info("="*50)
        logger.info("开始中国玩家 vs 全球玩家对比分析...")
        logger.info("="*50)
        
        # 加载数据
        self.load_data()
        
        # 运行对比分析
        comparison_result = self.analyze_player_comparison()
        
        if comparison_result:
            # 保存分析结果
            output_path = os.path.join(self.data_dir, 'player_comparison_results.json')
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(comparison_result, f, ensure_ascii=False, indent=2, cls=CustomEncoder)
            
            logger.info("="*50)
            logger.info("分析完成！")
            logger.info(f"中国玩家数量: {comparison_result['player_counts']['china']} ({comparison_result['player_counts']['china_percentage']:.2f}%)")
            logger.info(f"全球玩家数量: {comparison_result['player_counts']['global']}")
            logger.info(f"\n中国玩家优势:")
            for advantage in comparison_result['key_insights']['china_advantages']:
                logger.info(f"  - {advantage}")
            logger.info(f"\n全球玩家优势:")
            for advantage in comparison_result['key_insights']['global_advantages']:
                logger.info(f"  - {advantage}")
            logger.info(f"\n结果保存到: {output_path}")
            logger.info("="*50)
        
        return comparison_result

if __name__ == '__main__':
    # 数据目录路径
    data_dir = os.path.join(os.path.dirname(__file__), 'data', 'kaggle')
    
    # 创建分析器并运行
    analyzer = PlayerComparisonAnalyzer(data_dir)
    analyzer.run_analysis()
