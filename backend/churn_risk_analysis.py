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

class ChurnRiskAnalyzer:
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
    
    def calculate_risk_index(self):
        """计算流失风险指数"""
        logger.info("计算流失风险指数...")
        
        # 获取最新日期
        latest_date = self.history['date_acquired'].max()
        
        # 计算每个玩家的活跃度指标
        player_metrics = []
        
        # 1. 计算最后一次活动时间
        last_activity = self.history.groupby('playerid')['date_acquired'].max().reset_index()
        last_activity.columns = ['playerid', 'last_active']
        
        # 2. 计算活动频率（最近30天的活动次数）
        thirty_days_ago = latest_date - timedelta(days=30)
        recent_activity = self.history[
            self.history['date_acquired'] >= thirty_days_ago
        ].groupby('playerid').size().reset_index()
        recent_activity.columns = ['playerid', 'recent_activity_count']
        
        # 3. 计算总活动次数
        total_activity = self.history.groupby('playerid').size().reset_index()
        total_activity.columns = ['playerid', 'total_activity_count']
        
        # 4. 计算社交活跃度
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
            social_activity = self.friends[['playerid', 'friend_count']]
        else:
            social_activity = pd.DataFrame({'playerid': [], 'friend_count': []})
        
        # 5. 计算评论活跃度
        if self.reviews is not None:
            review_activity = self.reviews.groupby('playerid').size().reset_index()
            review_activity.columns = ['playerid', 'review_count']
        else:
            review_activity = pd.DataFrame({'playerid': [], 'review_count': []})
        
        # 合并所有指标
        metrics = last_activity.merge(recent_activity, on='playerid', how='left')
        metrics = metrics.merge(total_activity, on='playerid', how='left')
        metrics = metrics.merge(social_activity, on='playerid', how='left')
        metrics = metrics.merge(review_activity, on='playerid', how='left')
        
        # 填充缺失值
        metrics = metrics.fillna(0)
        
        # 计算风险指标
        # 1. 活动间隔（天数）
        metrics['days_since_last_activity'] = (latest_date - metrics['last_active']).dt.days
        
        # 2. 活跃度分数（0-100）
        def calculate_activity_score(row):
            score = 100
            
            # 活动间隔惩罚
            if row['days_since_last_activity'] > 30:
                score -= min(50, row['days_since_last_activity'] * 1.5)
            elif row['days_since_last_activity'] > 14:
                score -= min(30, row['days_since_last_activity'] * 2)
            
            # 最近活动奖励
            if row['recent_activity_count'] > 10:
                score += 10
            elif row['recent_activity_count'] > 5:
                score += 5
            
            # 总活动奖励
            if row['total_activity_count'] > 50:
                score += 15
            elif row['total_activity_count'] > 20:
                score += 10
            elif row['total_activity_count'] > 5:
                score += 5
            
            # 社交奖励
            if row['friend_count'] > 10:
                score += 10
            elif row['friend_count'] > 5:
                score += 5
            
            # 评论奖励
            if row['review_count'] > 5:
                score += 5
            
            return max(0, min(100, score))
        
        metrics['activity_score'] = metrics.apply(calculate_activity_score, axis=1)
        
        # 3. 风险指数（100 - 活跃度分数）
        metrics['risk_index'] = 100 - metrics['activity_score']
        
        # 计算总体风险指数
        overall_risk = metrics['risk_index'].mean()
        
        # 风险等级分布
        risk_levels = {
            'low': len(metrics[metrics['risk_index'] < 30]),
            'medium': len(metrics[(metrics['risk_index'] >= 30) & (metrics['risk_index'] < 70)]),
            'high': len(metrics[metrics['risk_index'] >= 70])
        }
        
        # 按风险指数排序，获取高风险玩家
        high_risk_players = metrics.sort_values('risk_index', ascending=False).head(10)
        
        return {
            'overall_risk': round(overall_risk, 1),
            'risk_levels': risk_levels,
            'total_players': len(metrics),
            'high_risk_count': risk_levels['high'],
            'medium_risk_count': risk_levels['medium'],
            'low_risk_count': risk_levels['low']
        }
    
    def analyze_risk_factors(self):
        """分析风险因素"""
        logger.info("分析风险因素...")
        
        # 计算各因素的风险贡献
        factors = []
        
        # 1. 活动频率因素
        latest_date = self.history['date_acquired'].max()
        thirty_days_ago = latest_date - timedelta(days=30)
        recent_activity = self.history[
            self.history['date_acquired'] >= thirty_days_ago
        ].groupby('playerid').size().reset_index()
        recent_activity.columns = ['playerid', 'recent_activity_count']
        
        low_activity_count = len(recent_activity[recent_activity['recent_activity_count'] <= 1])
        total_players = len(self.history['playerid'].unique())
        activity_risk = (low_activity_count / total_players) * 100
        
        factors.append({
            'factor': '低活动频率',
            'contribution': round(activity_risk, 1),
            'description': '最近30天活动次数≤1的玩家'
        })
        
        # 2. 社交孤立因素
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
            isolated_count = len(self.friends[self.friends['friend_count'] == 0])
            social_risk = (isolated_count / len(self.friends)) * 100
            
            factors.append({
                'factor': '社交孤立',
                'contribution': round(social_risk, 1),
                'description': '无好友的玩家'
            })
        
        # 3. 评论活跃度因素
        if self.reviews is not None:
            review_activity = self.reviews.groupby('playerid').size().reset_index()
            no_reviews_count = total_players - len(review_activity)
            review_risk = (no_reviews_count / total_players) * 100
            
            factors.append({
                'factor': '无评论行为',
                'contribution': round(review_risk, 1),
                'description': '从未发表评论的玩家'
            })
        
        # 按贡献度排序
        factors.sort(key=lambda x: x['contribution'], reverse=True)
        
        return factors
    
    def generate_risk_indicators(self):
        """生成风险指标"""
        logger.info("生成风险指标...")
        
        # 计算不同时间维度的风险
        latest_date = self.history['date_acquired'].max()
        
        indicators = []
        
        # 7天风险
        day7_threshold = latest_date - timedelta(days=7)
        day7_inactive = len(self.history[
            self.history['date_acquired'] < day7_threshold
        ]['playerid'].unique())
        day7_total = len(self.history['playerid'].unique())
        day7_risk = (day7_inactive / day7_total) * 100
        
        indicators.append({
            'period': '7天',
            'risk': round(day7_risk, 1),
            'status': 'warning' if day7_risk > 50 else 'normal'
        })
        
        # 30天风险
        day30_threshold = latest_date - timedelta(days=30)
        day30_inactive = len(self.history[
            self.history['date_acquired'] < day30_threshold
        ]['playerid'].unique())
        day30_risk = (day30_inactive / day7_total) * 100
        
        indicators.append({
            'period': '30天',
            'risk': round(day30_risk, 1),
            'status': 'danger' if day30_risk > 70 else 'warning' if day30_risk > 40 else 'normal'
        })
        
        # 90天风险
        day90_threshold = latest_date - timedelta(days=90)
        day90_inactive = len(self.history[
            self.history['date_acquired'] < day90_threshold
        ]['playerid'].unique())
        day90_risk = (day90_inactive / day7_total) * 100
        
        indicators.append({
            'period': '90天',
            'risk': round(day90_risk, 1),
            'status': 'danger' if day90_risk > 80 else 'warning' if day90_risk > 60 else 'normal'
        })
        
        return indicators
    
    def run_analysis(self):
        """运行完整分析"""
        logger.info("="*50)
        logger.info("开始玩家流失风险分析...")
        logger.info("="*50)
        
        self.load_data()
        
        # 计算风险指数
        risk_index = self.calculate_risk_index()
        
        # 分析风险因素
        risk_factors = self.analyze_risk_factors()
        
        # 生成风险指标
        risk_indicators = self.generate_risk_indicators()
        
        # 整合结果
        self.results = {
            'risk_index': risk_index,
            'risk_factors': risk_factors,
            'risk_indicators': risk_indicators,
            'analysis_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # 保存结果
        output_path = os.path.join(self.data_dir, 'churn_risk_results.json')
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2, cls=CustomEncoder)
        
        logger.info("="*50)
        logger.info("分析完成！")
        logger.info(f"结果保存到: {output_path}")
        logger.info("="*50)
        
        return self.results

if __name__ == '__main__':
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    analyzer = ChurnRiskAnalyzer(data_dir)
    analyzer.run_analysis()
