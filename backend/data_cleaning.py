import pandas as pd
import numpy as np
import logging
import os
from datetime import datetime

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DataCleaner:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.cleaned_dir = os.path.join(data_dir, 'cleaned')
        os.makedirs(self.cleaned_dir, exist_ok=True)
        
    def clean_players(self):
        """清洗玩家数据"""
        logger.info("开始清洗 players.csv...")
        
        file_path = os.path.join(self.data_dir, 'players.csv')
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return
            
        df = pd.read_csv(file_path)
        original_count = len(df)
        logger.info(f"原始数据量: {original_count} 行")
        
        # 处理缺失值
        # country 列缺失值填充为 'Unknown'
        df['country'] = df['country'].fillna('Unknown')
        
        # 处理数据类型
        # playerid 转换为字符串（保持完整）
        df['playerid'] = df['playerid'].astype(str)
        
        # created 列转换为日期时间格式
        df['created'] = pd.to_datetime(df['created'], errors='coerce')
        
        # 删除 created 为空的行（无法解析的日期）
        invalid_dates = df['created'].isna().sum()
        if invalid_dates > 0:
            logger.warning(f"发现 {invalid_dates} 个无效日期，将删除这些行")
            df = df.dropna(subset=['created'])
        
        # 添加数据清洗标记
        df['cleaned_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 保存清洗后的数据
        output_path = os.path.join(self.cleaned_dir, 'cleaned_players.csv')
        df.to_csv(output_path, index=False)
        
        logger.info(f"players.csv 清洗完成")
        logger.info(f"清洗后数据量: {len(df)} 行")
        logger.info(f"删除行数: {original_count - len(df)}")
        logger.info(f"保存到: {output_path}")
        
        return df
    
    def clean_games(self):
        """清洗游戏数据"""
        logger.info("开始清洗 games.csv...")
        
        file_path = os.path.join(self.data_dir, 'games.csv')
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return
            
        df = pd.read_csv(file_path)
        original_count = len(df)
        logger.info(f"原始数据量: {original_count} 行")
        
        # 处理缺失值
        # title 列缺失值填充为 'Unknown Title'
        df['title'] = df['title'].fillna('Unknown Title')
        
        # developers, publishers, genres 列缺失值填充为空列表字符串
        df['developers'] = df['developers'].fillna('[]')
        df['publishers'] = df['publishers'].fillna('[]')
        df['genres'] = df['genres'].fillna('[]')
        
        # supported_languages 列缺失值填充为 '[]'
        df['supported_languages'] = df['supported_languages'].fillna('[]')
        
        # 处理数据类型
        # gameid 转换为字符串
        df['gameid'] = df['gameid'].astype(str)
        
        # release_date 列转换为日期格式
        df['release_date'] = pd.to_datetime(df['release_date'], errors='coerce')
        
        # 删除 release_date 为空的行
        invalid_dates = df['release_date'].isna().sum()
        if invalid_dates > 0:
            logger.warning(f"发现 {invalid_dates} 个无效发布日期，将删除这些行")
            df = df.dropna(subset=['release_date'])
        
        # 添加数据清洗标记
        df['cleaned_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 保存清洗后的数据
        output_path = os.path.join(self.cleaned_dir, 'cleaned_games.csv')
        df.to_csv(output_path, index=False)
        
        logger.info(f"games.csv 清洗完成")
        logger.info(f"清洗后数据量: {len(df)} 行")
        logger.info(f"删除行数: {original_count - len(df)}")
        logger.info(f"保存到: {output_path}")
        
        return df
    
    def clean_friends(self):
        """清洗好友数据"""
        logger.info("开始清洗 friends.csv...")
        
        file_path = os.path.join(self.data_dir, 'friends.csv')
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return
            
        df = pd.read_csv(file_path)
        original_count = len(df)
        logger.info(f"原始数据量: {original_count} 行")
        
        # 处理数据类型
        df['playerid'] = df['playerid'].astype(str)
        
        # friends 列保持原样（已经是列表格式字符串）
        df['friends'] = df['friends'].fillna('[]')
        
        # 删除 playerid 为空的行
        df = df.dropna(subset=['playerid'])
        
        # 添加数据清洗标记
        df['cleaned_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 保存清洗后的数据
        output_path = os.path.join(self.cleaned_dir, 'cleaned_friends.csv')
        df.to_csv(output_path, index=False)
        
        logger.info(f"friends.csv 清洗完成")
        logger.info(f"清洗后数据量: {len(df)} 行")
        logger.info(f"删除行数: {original_count - len(df)}")
        logger.info(f"保存到: {output_path}")
        
        return df
    
    def clean_achievements(self):
        """清洗成就数据"""
        logger.info("开始清洗 achievements.csv...")
        
        file_path = os.path.join(self.data_dir, 'achievements.csv')
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return
            
        df = pd.read_csv(file_path)
        original_count = len(df)
        logger.info(f"原始数据量: {original_count} 行")
        
        # 处理数据类型
        df['achievementid'] = df['achievementid'].astype(str)
        df['gameid'] = df['gameid'].astype(str)
        
        # 处理文本列
        df['title'] = df['title'].fillna('Unknown Achievement')
        df['description'] = df['description'].fillna('')
        
        # 删除关键列为空的行
        df = df.dropna(subset=['achievementid', 'gameid'])
        
        # 添加数据清洗标记
        df['cleaned_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 保存清洗后的数据
        output_path = os.path.join(self.cleaned_dir, 'cleaned_achievements.csv')
        df.to_csv(output_path, index=False)
        
        logger.info(f"achievements.csv 清洗完成")
        logger.info(f"清洗后数据量: {len(df)} 行")
        logger.info(f"删除行数: {original_count - len(df)}")
        logger.info(f"保存到: {output_path}")
        
        return df
    
    def clean_history(self):
        """清洗游戏历史数据"""
        logger.info("开始清洗 history.csv...")
        
        file_path = os.path.join(self.data_dir, 'history.csv')
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return
            
        df = pd.read_csv(file_path)
        original_count = len(df)
        logger.info(f"原始数据量: {original_count} 行")
        
        # 处理数据类型
        df['playerid'] = df['playerid'].astype(str)
        df['achievementid'] = df['achievementid'].astype(str)
        
        # 处理日期列
        df['date_acquired'] = pd.to_datetime(df['date_acquired'], errors='coerce')
        
        # 删除关键列为空的行
        df = df.dropna(subset=['playerid', 'achievementid'])
        
        # 添加数据清洗标记
        df['cleaned_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 保存清洗后的数据
        output_path = os.path.join(self.cleaned_dir, 'cleaned_history.csv')
        df.to_csv(output_path, index=False)
        
        logger.info(f"history.csv 清洗完成")
        logger.info(f"清洗后数据量: {len(df)} 行")
        logger.info(f"删除行数: {original_count - len(df)}")
        logger.info(f"保存到: {output_path}")
        
        return df
    
    def clean_reviews(self):
        """清洗评论数据"""
        logger.info("开始清洗 reviews.csv...")
        
        file_path = os.path.join(self.data_dir, 'reviews.csv')
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return
            
        df = pd.read_csv(file_path)
        original_count = len(df)
        logger.info(f"原始数据量: {original_count} 行")
        
        # 处理数据类型
        df['reviewid'] = df['reviewid'].astype(str)
        df['playerid'] = df['playerid'].astype(str)
        df['gameid'] = df['gameid'].astype(str)
        
        # 处理日期列
        df['posted'] = pd.to_datetime(df['posted'], errors='coerce')
        
        # 处理文本列
        df['review'] = df['review'].fillna('')
        # 清理文本中的特殊字符
        df['review'] = df['review'].str.replace(r'[^\w\s.,!?-]', '', regex=True)
        
        # 确保数值列为数值类型
        numeric_columns = ['helpful', 'funny', 'awards']
        for col in numeric_columns:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        # 删除关键列为空的行
        df = df.dropna(subset=['reviewid', 'playerid', 'gameid'])
        
        # 添加数据清洗标记
        df['cleaned_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 保存清洗后的数据
        output_path = os.path.join(self.cleaned_dir, 'cleaned_reviews.csv')
        df.to_csv(output_path, index=False)
        
        logger.info(f"reviews.csv 清洗完成")
        logger.info(f"清洗后数据量: {len(df)} 行")
        logger.info(f"删除行数: {original_count - len(df)}")
        logger.info(f"保存到: {output_path}")
        
        return df
    
    def clean_prices(self):
        """清洗价格数据"""
        logger.info("开始清洗 prices.csv...")
        
        file_path = os.path.join(self.data_dir, 'prices.csv')
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return
            
        df = pd.read_csv(file_path)
        original_count = len(df)
        logger.info(f"原始数据量: {original_count} 行")
        
        # 处理数据类型
        df['gameid'] = df['gameid'].astype(str)
        
        # 确保价格列为数值类型
        price_columns = ['usd', 'eur', 'gbp', 'jpy', 'rub']
        for col in price_columns:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # 处理日期列
        df['date_acquired'] = pd.to_datetime(df['date_acquired'], errors='coerce')
        
        # 删除关键列为空的行
        df = df.dropna(subset=['gameid'])
        
        # 添加数据清洗标记
        df['cleaned_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 保存清洗后的数据
        output_path = os.path.join(self.cleaned_dir, 'cleaned_prices.csv')
        df.to_csv(output_path, index=False)
        
        logger.info(f"prices.csv 清洗完成")
        logger.info(f"清洗后数据量: {len(df)} 行")
        logger.info(f"删除行数: {original_count - len(df)}")
        logger.info(f"保存到: {output_path}")
        
        return df
    
    def run_all_cleaning(self):
        """运行所有数据清洗任务"""
        logger.info("="*50)
        logger.info("开始数据清洗流程...")
        logger.info("="*50)
        
        # 清洗各个数据集
        self.clean_players()
        self.clean_games()
        self.clean_friends()
        self.clean_achievements()
        self.clean_history()
        self.clean_reviews()
        self.clean_prices()
        
        logger.info("="*50)
        logger.info("所有数据清洗任务完成！")
        logger.info(f"清洗后的数据保存在: {self.cleaned_dir}")
        logger.info("="*50)

if __name__ == '__main__':
    # 数据目录路径
    data_dir = os.path.join(os.path.dirname(__file__), 'data', 'kaggle')
    
    # 创建数据清洗器并运行
    cleaner = DataCleaner(data_dir)
    cleaner.run_all_cleaning()
