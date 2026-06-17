#!/bin/bash

echo "🚀 開始部署更新..."

# 1. 拉取最新程式碼
git pull origin main

# 2. 更新 Python 套件 (選擇性，如果有用 requirements.txt 的話)
# source ~/.virtualenvs/你的虛擬環境名稱/bin/activate
pip install -r requirements.txt

# 3. 資料庫遷移
python manage.py migrate

# 4. 收集靜態檔案
python manage.py collectstatic --noinput --clear

# 5. 自動重啟 PythonAnywhere 的 Web App
# 請將下方路徑替換成你在 PythonAnywhere 上的實際 WSGI 檔案路徑
touch /var/www/41243158yqy_pythonanywhere_com_wsgi.py

echo "🎉 部署完成！Web App 已自動重啟。"