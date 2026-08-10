"""Migrate local SQLite data (data.db) + uploads to cloud (Supabase Postgres + Storage).

Usage:
    python migrate_to_cloud.py
Requires env vars: DATABASE_URL, SUPABASE_REF, SUPABASE_SERVICE_KEY
"""
import os
import sys
import io
import sqlite3
from datetime import date, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, 'backend'))

DB_PATH = os.path.join(BASE, '..', 'data.db')
UPLOADS_DIR = os.path.join(BASE, '..', 'uploads')

DATABASE_URL = os.environ.get('DATABASE_URL', '')
SUPABASE_REF = os.environ.get('SUPABASE_REF', '')
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')
STORAGE_BUCKET = 'images'


def main():
    if not DATABASE_URL:
        print('❌ 缺少 DATABASE_URL（Postgres 连接串）')
        return
    if not (SUPABASE_REF and SUPABASE_SERVICE_KEY):
        print('❌ 缺少 SUPABASE_REF / SUPABASE_SERVICE_KEY')
        return

    # ---- connect cloud DB ----
    from sqlalchemy import create_engine, text
    uri = DATABASE_URL
    if uri.startswith('postgres://'):
        uri = uri.replace('postgres://', 'postgresql://', 1)
    engine = create_engine(uri)

    # ---- connect local sqlite ----
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # ---- ensure schema ----
    from app import app
    app.config['SQLALCHEMY_DATABASE_URI'] = uri
    from models import db
    db.init_app(app)

    tables = ['tasks', 'notes', 'schedules', 'workouts', 'body_metrics',
              'fitness_goals', 'workout_exercises', 'diaries', 'transactions',
              'storage_items', 'anniversaries']

    with app.app_context():
        db.create_all()
        for table in tables:
            rows = conn.execute(f'SELECT * FROM {table}').fetchall()
            if not rows:
                print(f'  {table}: 0 行，跳过')
                continue
            cols = list(rows[0].keys())
            inserted = 0
            for row in rows:
                vals = {}
                for c in cols:
                    v = row[c]
                    if isinstance(v, (date, datetime)):
                        v = v.isoformat()
                    vals[c] = v
                placeholders = ', '.join([':' + c for c in cols])
                sql = f'INSERT INTO {table} ({", ".join(cols)}) VALUES ({placeholders})'
                try:
                    db.session.execute(text(sql), vals)
                    inserted += 1
                except Exception as e:
                    print(f'  ⚠️ {table} 行失败: {e}')
            db.session.commit()
            print(f'  {table}: {inserted}/{len(rows)} 行 ✅')

    conn.close()

    # ---- migrate images to Supabase Storage ----
    import requests
    count = 0
    for fname in sorted(os.listdir(UPLOADS_DIR)):
        fpath = os.path.join(UPLOADS_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        with open(fpath, 'rb') as f:
            data = f.read()
        ctype = 'image/jpeg'
        if fname.lower().endswith('.png'):
            ctype = 'image/png'
        elif fname.lower().endswith('.webp'):
            ctype = 'image/webp'
        url = f'https://{SUPABASE_REF}.supabase.co/storage/v1/object/{STORAGE_BUCKET}/{fname}'
        headers = {
            'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
            'apikey': SUPABASE_SERVICE_KEY,
            'Content-Type': ctype,
            'x-upsert': 'true',
        }
        r = requests.post(url, data=data, headers=headers, timeout=60)
        if r.status_code in (200, 201):
            count += 1
        else:
            print(f'  ⚠️ 图片 {fname} 上传失败: {r.status_code}')
    print(f'图片迁移: {count} 个 ✅')

    print('\n🎉 全部迁移完成！')


if __name__ == '__main__':
    main()
