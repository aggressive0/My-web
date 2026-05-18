import pandas as pd
import re
import json
from collections import Counter

# 读取清洗后的评论数据
df = pd.read_csv('data/cleaned/cleaned_reviews.csv')

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
        return '中性'
    
    text = str(text).lower()
    pos_score = 0
    neg_score = 0
    
    for word in positive_words:
        pos_score += len(re.findall(r'\b' + re.escape(word) + r'\b', text))
    
    for word in negative_words:
        neg_score += len(re.findall(r'\b' + re.escape(word) + r'\b', text))
    
    if pos_score > neg_score:
        return '积极'
    elif neg_score > pos_score:
        return '消极'
    else:
        return '中性'

# 分析情感
df['sentiment'] = df['review'].apply(analyze_sentiment)

# 统计情感分布
sentiment_counts = df['sentiment'].value_counts().to_dict()

# 统计各游戏的情感分布
game_sentiment = df.groupby('gameid')['sentiment'].value_counts().unstack(fill_value=0)
game_sentiment['total'] = game_sentiment.sum(axis=1)
game_sentiment['positive_ratio'] = (game_sentiment['积极'] / game_sentiment['total'] * 100).round(2)
top_positive_games = game_sentiment.sort_values('positive_ratio', ascending=False).head(10)
top_negative_games = game_sentiment.sort_values('positive_ratio').head(10)

# 统计最常见的积极和消极词汇
all_reviews = ' '.join(df['review'].dropna().astype(str).tolist()).lower()

positive_matches = {}
for word in positive_words:
    count = len(re.findall(r'\b' + re.escape(word) + r'\b', all_reviews))
    if count > 0:
        positive_matches[word] = count

negative_matches = {}
for word in negative_words:
    count = len(re.findall(r'\b' + re.escape(word) + r'\b', all_reviews))
    if count > 0:
        negative_matches[word] = count

top_positive_words = dict(sorted(positive_matches.items(), key=lambda x: x[1], reverse=True)[:10])
top_negative_words = dict(sorted(negative_matches.items(), key=lambda x: x[1], reverse=True)[:10])

# 保存分析结果
analysis_results = {
    'sentiment_counts': sentiment_counts,
    'top_positive_games': top_positive_games.to_dict(),
    'top_negative_games': top_negative_games.to_dict(),
    'top_positive_words': top_positive_words,
    'top_negative_words': top_negative_words
}

with open('data/sentiment_analysis_results.json', 'w', encoding='utf-8') as f:
    json.dump(analysis_results, f, ensure_ascii=False, indent=2)

print("情感分析完成，结果已保存到 data/sentiment_analysis_results.json")
print(f"情感分布: {sentiment_counts}")
print("\n最积极的游戏:")
print(top_positive_games[['积极', '消极', '中性', 'positive_ratio']])
print("\n最消极的游戏:")
print(top_negative_games[['积极', '消极', '中性', 'positive_ratio']])
print("\n最常见的积极词汇:")
print(top_positive_words)
print("\n最常见的消极词汇:")
print(top_negative_words)
