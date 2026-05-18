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

class DataAnalyzer:
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
        
        # 加载 prices.csv
        prices_path = os.path.join(self.cleaned_dir, 'cleaned_prices.csv')
        if os.path.exists(prices_path):
            self.prices = pd.read_csv(prices_path)
            logger.info(f"加载 prices.csv 完成，共 {len(self.prices)} 行")
        else:
            self.prices = None
            logger.warning(f"文件不存在: {prices_path}")
        
    def analyze_player_activity(self):
        """分析玩家活跃度"""
        logger.info("开始分析玩家活跃度...")
        
        activity_analysis = {
            "total_players": 0,
            "active_players": 0,
            "average_reviews_per_player": 0,
            "average_achievements_per_player": 0,
            "activity_distribution": []
        }
        
        if self.players is not None:
            activity_analysis["total_players"] = len(self.players)
        
        # 分析评论活跃度
        if self.reviews is not None:
            reviews_per_player = self.reviews.groupby('playerid').size()
            activity_analysis["average_reviews_per_player"] = float(reviews_per_player.mean())
            
            # 活跃度分布
            activity_bins = [0, 1, 5, 10, 20, float('inf')]
            activity_labels = ['无评论', '1-5条', '6-10条', '11-20条', '20条以上']
            activity_counts = pd.cut(reviews_per_player, bins=activity_bins, labels=activity_labels, include_lowest=True).value_counts().sort_index()
            
            for label, count in activity_counts.items():
                activity_analysis["activity_distribution"].append({
                    "range": label,
                    "count": int(count)
                })
        
        # 分析成就活跃度
        if self.history is not None:
            achievements_per_player = self.history.groupby('playerid').size()
            activity_analysis["average_achievements_per_player"] = float(achievements_per_player.mean())
            
            # 活跃玩家定义：至少有1个成就或1条评论
            if self.reviews is not None:
                active_players = set(self.reviews['playerid']).union(set(self.history['playerid']))
                activity_analysis["active_players"] = len(active_players)
        
        logger.info(f"玩家活跃度分析完成")
        return activity_analysis
    
    def analyze_player_social(self):
        """分析玩家社交属性"""
        logger.info("开始分析玩家社交属性...")
        
        social_analysis = {
            "total_friend_connections": 0,
            "average_friends_per_player": 0,
            "friend_distribution": []
        }
        
        if self.friends is not None:
            # 计算每个玩家的好友数量
            def count_friends(friends_str):
                if pd.isna(friends_str):
                    return 0
                try:
                    friends_list = eval(friends_str)
                    return len(friends_list)
                except:
                    return 0
            
            self.friends['friend_count'] = self.friends['friends'].apply(count_friends)
            total_friends = self.friends['friend_count'].sum()
            social_analysis["total_friend_connections"] = int(total_friends)
            social_analysis["average_friends_per_player"] = float(self.friends['friend_count'].mean())
            
            # 好友数量分布
            friend_bins = [0, 5, 10, 20, 50, float('inf')]
            friend_labels = ['0个', '1-5个', '6-10个', '11-20个', '20个以上']
            friend_counts = pd.cut(self.friends['friend_count'], bins=friend_bins, labels=friend_labels, include_lowest=True).value_counts().sort_index()
            
            for label, count in friend_counts.items():
                social_analysis["friend_distribution"].append({
                    "range": label,
                    "count": int(count)
                })
        
        logger.info(f"玩家社交属性分析完成")
        return social_analysis
    
    def analyze_game_preferences(self):
        """分析游戏偏好"""
        logger.info("开始分析游戏偏好...")
        
        game_preferences = {
            "top_games_by_reviews": [],
            "top_games_by_achievements": []
        }
        
        # 按评论数分析游戏偏好
        if self.reviews is not None:
            game_reviews = self.reviews.groupby('gameid').size().sort_values(ascending=False).head(10)
            for gameid, count in game_reviews.items():
                game_title = "Unknown"
                if self.games is not None:
                    game = self.games[self.games['gameid'] == gameid]
                    if not game.empty:
                        game_title = game.iloc[0]['title']
                game_preferences["top_games_by_reviews"].append({
                    "gameid": gameid,
                    "title": game_title,
                    "review_count": int(count)
                })
        
        # 按成就解锁数分析游戏偏好
        if self.history is not None:
            game_achievements = self.history.groupby('achievementid').size().sort_values(ascending=False).head(10)
            for achievementid, count in game_achievements.items():
                # 从成就ID中提取游戏ID
                try:
                    gameid = achievementid.split('_')[0]
                    game_title = "Unknown"
                    if self.games is not None:
                        game = self.games[self.games['gameid'] == gameid]
                        if not game.empty:
                            game_title = game.iloc[0]['title']
                    game_preferences["top_games_by_achievements"].append({
                        "gameid": gameid,
                        "title": game_title,
                        "achievement_count": int(count)
                    })
                except:
                    pass
        
        logger.info(f"游戏偏好分析完成")
        return game_preferences
    
    def analyze_player_profile(self):
        """分析玩家画像"""
        logger.info("开始分析玩家画像...")
        
        player_profile = {
            "activity": 0,
            "social": 0,
            "game_diversity": 0,
            "review_activity": 0,
            "achievement_activity": 0
        }
        
        # 活跃度得分
        if self.reviews is not None and self.history is not None:
            reviews_per_player = self.reviews.groupby('playerid').size()
            achievements_per_player = self.history.groupby('playerid').size()
            avg_reviews = reviews_per_player.mean()
            avg_achievements = achievements_per_player.mean()
            player_profile["activity"] = min(100, int((avg_reviews * 5 + avg_achievements * 0.1) * 2))
        
        # 社交得分
        if self.friends is not None:
            avg_friends = self.friends['friend_count'].mean() if 'friend_count' in self.friends.columns else 0
            player_profile["social"] = min(100, int(avg_friends * 2))
        
        # 游戏多样性得分
        if self.history is not None:
            games_per_player = self.history.groupby('playerid')['achievementid'].apply(lambda x: len(set([achievement.split('_')[0] for achievement in x])))
            avg_games = games_per_player.mean()
            player_profile["game_diversity"] = min(100, int(avg_games * 10))
        
        # 评论活跃度得分
        if self.reviews is not None:
            reviews_per_player = self.reviews.groupby('playerid').size()
            avg_reviews = reviews_per_player.mean()
            player_profile["review_activity"] = min(100, int(avg_reviews * 10))
        
        # 成就活跃度得分
        if self.history is not None:
            achievements_per_player = self.history.groupby('playerid').size()
            avg_achievements = achievements_per_player.mean()
            player_profile["achievement_activity"] = min(100, int(avg_achievements * 0.1))
        
        logger.info(f"玩家画像分析完成")
        return player_profile
    
    def run_all_analysis(self):
        """运行所有分析任务"""
        logger.info("="*50)
        logger.info("开始数据分析流程...")
        logger.info("="*50)
        
        # 加载数据
        self.load_data()
        
        # 运行分析
        activity_analysis = self.analyze_player_activity()
        social_analysis = self.analyze_player_social()
        game_preferences = self.analyze_game_preferences()
        player_profile = self.analyze_player_profile()
        
        # 整合分析结果
        analysis_results = {
            "player_activity": activity_analysis,
            "player_social": social_analysis,
            "game_preferences": game_preferences,
            "player_profile": player_profile,
            "analysis_date": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # 保存分析结果
        output_path = os.path.join(self.data_dir, 'analysis_results.json')
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(analysis_results, f, ensure_ascii=False, indent=2, cls=CustomEncoder)
        
        logger.info("="*50)
        logger.info("所有数据分析任务完成！")
        logger.info(f"分析结果保存到: {output_path}")
        logger.info("="*50)
        
        return analysis_results

if __name__ == '__main__':
    # 数据目录路径
    data_dir = os.path.join(os.path.dirname(__file__), 'data', 'kaggle')
    
    # 创建数据分析师并运行
    analyzer = DataAnalyzer(data_dir)
    analyzer.run_all_analysis()
