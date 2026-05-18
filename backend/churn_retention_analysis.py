import pandas as pd
import numpy as np
import logging
import os
import json
from datetime import datetime, timedelta
from collections import Counter

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(CustomEncoder, self).default(obj)

class ChurnRetentionAnalyzer:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.cleaned_dir = os.path.join(data_dir, 'cleaned')
        self.results = {}
        
    def load_data(self):
        """加载清洗后的数据"""
        logger.info("开始加载数据...")
        
        # 加载玩家数据
        players_path = os.path.join(self.cleaned_dir, 'cleaned_players.csv')
        if os.path.exists(players_path):
            self.players = pd.read_csv(players_path)
            self.players['created'] = pd.to_datetime(self.players['created'])
            logger.info(f"加载玩家数据: {len(self.players)} 条")
        else:
            self.players = None
            
        # 加载游戏历史数据
        history_path = os.path.join(self.cleaned_dir, 'cleaned_history.csv')
        if os.path.exists(history_path):
            self.history = pd.read_csv(history_path)
            self.history['date_acquired'] = pd.to_datetime(self.history['date_acquired'])
            logger.info(f"加载历史数据: {len(self.history)} 条")
        else:
            self.history = None
            
        # 加载评论数据
        reviews_path = os.path.join(self.cleaned_dir, 'cleaned_reviews.csv')
        if os.path.exists(reviews_path):
            self.reviews = pd.read_csv(reviews_path)
            self.reviews['posted'] = pd.to_datetime(self.reviews['posted'])
            logger.info(f"加载评论数据: {len(self.reviews)} 条")
        else:
            self.reviews = None
            
        # 加载好友数据
        friends_path = os.path.join(self.cleaned_dir, 'cleaned_friends.csv')
        if os.path.exists(friends_path):
            self.friends = pd.read_csv(friends_path)
            logger.info(f"加载好友数据: {len(self.friends)} 条")
        else:
            self.friends = None
            
        # 加载游戏数据
        games_path = os.path.join(self.cleaned_dir, 'cleaned_games.csv')
        if os.path.exists(games_path):
            self.games = pd.read_csv(games_path)
            logger.info(f"加载游戏数据: {len(self.games)} 条")
        else:
            self.games = None
    
    def identify_churned_players(self):
        """识别流失玩家"""
        logger.info("识别流失玩家...")
        
        # 获取最新日期
        latest_date = self.history['date_acquired'].max()
        churn_threshold_date = latest_date - timedelta(days=30)  # 30天未活跃视为流失
        
        # 计算每个玩家最后一次活动时间
        last_activity = self.history.groupby('playerid')['date_acquired'].max().reset_index()
        last_activity.columns = ['playerid', 'last_active']
        
        # 标记流失玩家
        last_activity['is_churned'] = last_activity['last_active'] < churn_threshold_date
        
        # 计算流失率
        total_players = len(last_activity)
        churned_players = last_activity['is_churned'].sum()
        churn_rate = churned_players / total_players * 100
        
        self.churn_data = last_activity
        
        logger.info(f"流失玩家: {churned_players}/{total_players} ({churn_rate:.1f}%)")
        
        return {
            'total_players': int(total_players),
            'churned_players': int(churned_players),
            'churn_rate': round(churn_rate, 2),
            'active_players': int(total_players - churned_players)
        }
    
    def analyze_churn_reasons(self):
        """分析流失原因"""
        logger.info("分析流失原因...")
        
        # 合并流失标记到历史数据
        history_with_churn = self.history.merge(
            self.churn_data[['playerid', 'is_churned']], 
            on='playerid'
        )
        
        reasons = []
        
        # 1. 游戏时长不足
        playtime_analysis = history_with_churn.groupby('playerid').agg({
            'date_acquired': 'count'
        }).reset_index()
        playtime_analysis.columns = ['playerid', 'achievement_count']
        playtime_analysis = playtime_analysis.merge(
            self.churn_data[['playerid', 'is_churned']], 
            on='playerid'
        )
        
        low_engagement_churn = playtime_analysis[
            (playtime_analysis['achievement_count'] <= 5) & 
            (playtime_analysis['is_churned'] == True)
        ]
        low_engagement_rate = len(low_engagement_churn) / playtime_analysis['is_churned'].sum() * 100
        reasons.append({
            'reason': '游戏时长不足',
            'percentage': round(low_engagement_rate, 1),
            'description': '成就解锁数≤5的玩家更容易流失'
        })
        
        # 2. 社交孤立
        if self.friends is not None:
            def count_friends(friends_str):
                if pd.isna(friends_str):
                    return 0
                try:
                    friends_list = eval(friends_str)
                    return len(friends_list)
                except:
                    return 0
            
            self.friends['friend_count'] = self.friends['friends'].apply(count_friends)
            friends_with_churn = self.friends.merge(
                self.churn_data[['playerid', 'is_churned']], 
                on='playerid'
            )
            
            isolated_churn = friends_with_churn[
                (friends_with_churn['friend_count'] == 0) & 
                (friends_with_churn['is_churned'] == True)
            ]
            isolated_rate = len(isolated_churn) / friends_with_churn['is_churned'].sum() * 100
            reasons.append({
                'reason': '社交孤立',
                'percentage': round(isolated_rate, 1),
                'description': '无好友的玩家流失率更高'
            })
        
        # 3. 评论活跃度低
        if self.reviews is not None:
            reviews_with_churn = self.reviews.merge(
                self.churn_data[['playerid', 'is_churned']], 
                on='playerid'
            )
            # 计算每个流失玩家的评论数
            review_counts = reviews_with_churn[reviews_with_churn['is_churned'] == True].groupby('playerid').size()
            low_review_churn = review_counts[review_counts == 0]
            low_review_rate = len(low_review_churn) / self.churn_data['is_churned'].sum() * 100
            reasons.append({
                'reason': '低评论活跃',
                'percentage': round(low_review_rate, 1),
                'description': '从未发表评论的玩家更容易流失'
            })
        
        # 按百分比排序
        reasons.sort(key=lambda x: x['percentage'], reverse=True)
        
        return reasons
    
    def generate_retention_strategies(self):
        """生成召回策略"""
        logger.info("生成召回策略...")
        
        strategies = []
        
        # 策略1: AI陪伴策略 - 基于社交孤立分析
        if self.friends is not None:
            friends_with_churn = self.friends.merge(
                self.churn_data[['playerid', 'is_churned']], 
                on='playerid'
            )
            isolated_players = friends_with_churn[
                (friends_with_churn['friend_count'] == 0) & 
                (friends_with_churn['is_churned'] == True)
            ]
            ai_companion_effectiveness = min(85, 60 + len(isolated_players) / 100)
        else:
            ai_companion_effectiveness = 75
            
        strategies.append({
            'name': 'AI陪伴策略',
            'effectiveness': round(ai_companion_effectiveness, 1),
            'target': '社交孤立玩家',
            'description': '为无好友玩家提供AI游戏伙伴',
            'color': '#00E676'
        })
        
        # 策略2: 福利推送 - 基于游戏时长分析
        history_with_churn = self.history.merge(
            self.churn_data[['playerid', 'is_churned']], 
            on='playerid'
        )
        low_engagement = history_with_churn.groupby('playerid').size().reset_index()
        low_engagement.columns = ['playerid', 'count']
        low_engagement = low_engagement.merge(
            self.churn_data[['playerid', 'is_churned']], 
            on='playerid'
        )
        low_engagement_churn = low_engagement[
            (low_engagement['count'] <= 5) & 
            (low_engagement['is_churned'] == True)
        ]
        welfare_effectiveness = min(80, 50 + len(low_engagement_churn) / 150)
        
        strategies.append({
            'name': '福利推送',
            'effectiveness': round(welfare_effectiveness, 1),
            'target': '低活跃度玩家',
            'description': '推送限时优惠和免费DLC',
            'color': '#FF6B8B'
        })
        
        # 策略3: 社交激励 - 基于好友数据
        if self.friends is not None:
            social_effectiveness = min(75, 45 + len(self.friends) / 500)
        else:
            social_effectiveness = 55
            
        strategies.append({
            'name': '社交激励',
            'effectiveness': round(social_effectiveness, 1),
            'target': '潜在社交玩家',
            'description': '推荐游戏社区和组队活动',
            'color': '#4A90E2'
        })
        
        # 策略4: 内容更新提醒
        if self.games is not None:
            content_effectiveness = min(70, 40 + len(self.games) / 100)
        else:
            content_effectiveness = 50
            
        strategies.append({
            'name': '内容更新',
            'effectiveness': round(content_effectiveness, 1),
            'target': '内容型玩家',
            'description': '推送新游戏和更新内容',
            'color': '#8A2BE2'
        })
        
        # 按效果排序
        strategies.sort(key=lambda x: x['effectiveness'], reverse=True)
        
        return strategies
    
    def analyze_retention_rates(self):
        """分析留存率"""
        logger.info("分析留存率...")
        
        # 计算不同时间段的留存率
        latest_date = self.history['date_acquired'].max()
        
        # 7天留存
        day7_threshold = latest_date - timedelta(days=7)
        day7_active = self.history[self.history['date_acquired'] >= day7_threshold]['playerid'].nunique()
        day7_retention = day7_active / len(self.players) * 100
        
        # 30天留存
        day30_threshold = latest_date - timedelta(days=30)
        day30_active = self.history[self.history['date_acquired'] >= day30_threshold]['playerid'].nunique()
        day30_retention = day30_active / len(self.players) * 100
        
        # 90天留存
        day90_threshold = latest_date - timedelta(days=90)
        day90_active = self.history[self.history['date_acquired'] >= day90_threshold]['playerid'].nunique()
        day90_retention = day90_active / len(self.players) * 100
        
        return {
            'day7': round(day7_retention, 1),
            'day30': round(day30_retention, 1),
            'day90': round(day90_retention, 1)
        }
    
    def run_analysis(self):
        """运行完整分析"""
        logger.info("="*50)
        logger.info("开始流失召回策略分析...")
        logger.info("="*50)
        
        self.load_data()
        
        # 识别流失玩家
        churn_stats = self.identify_churned_players()
        
        # 分析流失原因
        churn_reasons = self.analyze_churn_reasons()
        
        # 生成召回策略
        strategies = self.generate_retention_strategies()
        
        # 分析留存率
        retention_rates = self.analyze_retention_rates()
        
        # 整合结果
        self.results = {
            'churn_stats': churn_stats,
            'churn_reasons': churn_reasons,
            'strategies': strategies,
            'retention_rates': retention_rates,
            'analysis_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # 保存结果
        output_path = os.path.join(self.data_dir, 'churn_retention_results.json')
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2, cls=CustomEncoder)
        
        logger.info("="*50)
        logger.info("分析完成！")
        logger.info(f"结果保存到: {output_path}")
        logger.info("="*50)
        
        return self.results

if __name__ == '__main__':
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    analyzer = ChurnRetentionAnalyzer(data_dir)
    analyzer.run_analysis()
