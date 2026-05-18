# 读取 GeoJSON 文件
$data = Get-Content 'json/china.json' -Raw -Encoding UTF8

# 创建 JS 文件内容
$jsContent = "// 中国地图 GeoJSON 数据`nwindow.chinaGeoJSON = " + $data + ";`n"

# 写入 JS 文件
[System.IO.File]::WriteAllText("$PSScriptRoot\china-map-data.js", $jsContent, [System.Text.Encoding]::UTF8)

Write-Host "china-map-data.js 生成成功！"
