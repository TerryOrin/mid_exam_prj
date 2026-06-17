#!/bin/bash

echo "🚀 開始部署更新..."

# 1. 抓取 GitHub 上的最新狀態（但不主動合併，所以不會報衝突錯誤）
git fetch --all

# 2. 強制將線上的檔案同步成跟 GitHub 的 main 分支一模一樣
# ⚠️ 注意：這會直接蓋掉線上所有未 commit 的改動（例如 db.sqlite3）
git reset --hard origin/main

# 3. 更新 Python 套件 (如果有需要的話再取消註解)
# source ~/.virtualenvs/你的虛擬環境名稱/bin/activate
pip install -r requirements.txt

# 4. 資料庫遷移
python manage.py migrate

# 5. 收集靜態檔案
python manage.py collectstatic --noinput --clear

# 6. 自動重啟 PythonAnywhere 的 Web App
# 請將下方路徑替換成你在 PythonAnywhere 上的實際 WSGI 檔案路徑
touch /var/www/41243158yqy_pythonanywhere_com_wsgi.py

echo "🎉 部署完成！Web App 已自動重啟。"