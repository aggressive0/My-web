# -*- coding: utf-8 -*-
import codecs

# 读取 GeoJSON 文件
with codecs.open('json/china.json', 'r', 'utf-8') as f:
    data = f.read()

# 写入 JS 文件
with codecs.open('china-map-data.js', 'w', 'utf-8') as f:
    f.write('// 中国地图 GeoJSON 数据\n')
    f.write('window.chinaGeoJSON = ')
    f.write(data)
    f.write(';\n')

print('china-map-data.js 生成成功！')
