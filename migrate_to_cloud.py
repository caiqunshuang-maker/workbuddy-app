"""Migrate local SQLite data (data.db) + uploads to cloud (Supabase Postgres + Storage).

Usage:
    python migrate_to_cloud.py
Requires env vars: DATABASE_URL, S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET
(or edit the config block below).
"""
import os
import sys
import json
import io
import sqlite3
from datetime import date, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, 'backend'))

DB_PATH = os.path.join(BASE, '..', 'data.db')
UPLOADS_DIR = os.path.join(BASE, '..', 'uploads')

# Cloud config (set via env or edit here)
DATABASE_URL = os.environ.get('DATABASE_URL', '')
S3_ENDPOINT = os.environ.get('S3_ENDPOINT', '')
S3_KEY = os.environ.get('S3_ACCESS_KEY', '')
S3_SECRET = os.environ.get('S3_SECRET_KEY', '')
S3_BUCKET = os.environ.get('S3_BUCKET', '')


def main():
    if not DATABASE_URL:
        print('❌ 缺少 DATABASE_URL（Postgres 连接串）')
        return
    if not (S3_ENDPOINT and S3_KEY and S3_SECRET and S3_BUCKET):
        print('❌ 缺少 S3 配置（Supabase Storage）')
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
    from models import db
    from app import app
    app.config['SQLALCHEMY_DATABASE_URI'] = uri
    db.init_app(app)
    with app.app_context():
        db.create_all()

    tables = ['tasks', 'notes', 'schedules', 'workouts', 'body_metrics',
              'fitness_goals', 'workout_exercises', 'diaries', 'transactions',
              'storage_items', 'anniversaries']

    with app.app_context():
        for table in tables:
            rows = conn.execute(f'SELECT * FROM {table}').fetchall()
            if not rows:
                print(f'  {table}: 0 行，跳过')
                continue
            cols = list(rows[0].keys())
            for row in rows:
                vals = []
                for c in cols:
                    v = row[c]
                    if isinstance(v, (date, datetime)):
                        v = v.isoformat()
                    vals.append(v)
                placeholders = ', '.join([':' + c for c in cols])
                sql = f'INSERT INTO {table} ({", ".join(cols)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
                db.session.execute(text(sql), {c: row[c] for c in cols})
            db.session.commit()
            print(f'  {table}: {len(rows)} 行 ✅')

    conn.close()

    # ---- migrate images to S3 ----
    import boto3
    from botocore.config import Config
    s3 = boto3.client('s3', endpoint_url=S3_ENDPOINT,
                      aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET,
                      region_name='auto', config=Config(signature_version='s3v4'))
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
        s3.put_object(Bucket=S3_BUCKET, Key=fname, Body=data, ContentType=ctype)
        count += 1
    print(f'图片迁移: {count} 个 ✅')

    print('\n🎉 全部迁移完成！')


if __name__ == '__main__':
    main()
