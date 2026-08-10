import os
import sys
import re
from datetime import datetime, date
from flask import Flask, request, jsonify, send_from_directory

# Ensure backend package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from models import (db, Task, Note, Schedule, Workout, WorkoutExercise,
                    BodyMetric, FitnessGoal, Diary, Transaction, StorageItem, Anniversary)

app = Flask(__name__, static_folder=None)

# Configuration
basedir = os.path.dirname(os.path.abspath(__file__))
# Cloud (Render): DATABASE_URL env var; local dev falls back to SQLite
_db_uri = os.environ.get('DATABASE_URL', '').strip()
if not _db_uri:
    _db_uri = 'sqlite:///' + os.path.join(basedir, '..', 'data.db')
# Render provides postgres:// URLs; SQLAlchemy 2.x wants postgresql://
if _db_uri.startswith('postgres://'):
    _db_uri = _db_uri.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JSON_AS_ASCII'] = False

# Cloud image storage via Supabase Storage REST API when configured
SUPABASE_REF = os.environ.get('SUPABASE_REF', '').strip()
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '').strip()
USE_SUPABASE_STORAGE = bool(SUPABASE_REF and SUPABASE_SERVICE_KEY)
STORAGE_BUCKET = 'images'

def _migrate_db():
    """Add new columns to existing tables (simple SQLite ALTER TABLE migration)."""
    import sqlite3
    db_path = os.path.join(basedir, '..', 'data.db')
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Get existing columns of tasks table
    cols = {row[1] for row in cur.execute("PRAGMA table_info(tasks)")}
    migrations = {
        'due_time': "ALTER TABLE tasks ADD COLUMN due_time VARCHAR(10) DEFAULT ''",
        'repeat': "ALTER TABLE tasks ADD COLUMN repeat VARCHAR(20) DEFAULT 'none'",
        'repeat_days': "ALTER TABLE tasks ADD COLUMN repeat_days VARCHAR(20) DEFAULT ''",
        'last_completed_at': "ALTER TABLE tasks ADD COLUMN last_completed_at DATETIME",
    }
    for col, stmt in migrations.items():
        if col not in cols:
            try:
                cur.execute(stmt)
            except Exception:
                pass

    # Migrate old categories to the two new ones: work / life
    # general/health/study -> life, work stays work
    try:
        cur.execute("UPDATE tasks SET category='life' WHERE category IN ('general','health','study','') OR category IS NULL")
    except Exception:
        pass

    # Migrate old expense categories to the new set: 吃喝/交通/购物/变美/玩乐/生活
    try:
        cur.execute("UPDATE transactions SET category='吃喝' WHERE category IN ('餐饮','外卖','零食','饮料','水果')")
        cur.execute("UPDATE transactions SET category='交通' WHERE category='出行'")
        cur.execute("UPDATE transactions SET category='变美' WHERE category IN ('护肤','美妆','理发')")
        cur.execute("UPDATE transactions SET category='玩乐' WHERE category IN ('娱乐','游戏','旅游','运动')")
        cur.execute("UPDATE transactions SET category='生活' WHERE category IN ('居住','医疗','宠物','日用','其他','水电','房租','话费')")
    except Exception:
        pass

    conn.commit()
    conn.close()


def _migrate_pg_categories():
    """Postgres-safe version of category migrations (no sqlite3 dependency)."""
    try:
        from sqlalchemy import text
        with db.engine.connect() as conn:
            conn.execute(text("UPDATE tasks SET category='life' WHERE category IN ('general','health','study','') OR category IS NULL"))
            conn.execute(text("UPDATE transactions SET category='吃喝' WHERE category IN ('餐饮','外卖','零食','饮料','水果')"))
            conn.execute(text("UPDATE transactions SET category='交通' WHERE category='出行'"))
            conn.execute(text("UPDATE transactions SET category='变美' WHERE category IN ('护肤','美妆','理发')"))
            conn.execute(text("UPDATE transactions SET category='玩乐' WHERE category IN ('娱乐','游戏','旅游','运动')"))
            conn.execute(text("UPDATE transactions SET category='生活' WHERE category IN ('居住','医疗','宠物','日用','其他','水电','房租','话费')"))
            conn.commit()
    except Exception:
        pass


# Init DB
db.init_app(app)
with app.app_context():
    db.create_all()
    if _db_uri.startswith('sqlite'):
        _migrate_db()
    else:
        _migrate_pg_categories()


# ==================== Static Files ====================

@app.route('/')
def index():
    resp = send_from_directory(os.path.join(basedir, '..', 'frontend'), 'index.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp

@app.route('/<path:filename>')
def static_files(filename):
    resp = send_from_directory(os.path.join(basedir, '..', 'frontend'), filename)
    # Don't cache HTML/JS/CSS so updates show immediately; allow caching for images
    if filename.endswith(('.html', '.js', '.css')):
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


# ==================== Tasks API ====================

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    category = request.args.get('category')
    status = request.args.get('status')
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)

    query = Task.query.order_by(Task.created_at.desc())

    if category and category != 'all':
        query = query.filter_by(category=category)
    if status == 'active':
        query = query.filter_by(completed=False)
    elif status == 'completed':
        query = query.filter_by(completed=True)
    # Filter tasks by due date month (for calendar display).
    # Repeating tasks are always returned (their single due_date is only the
    # next occurrence) so the expander can list every date in the month.
    if year and month:
        non_repeat_in_month = db.and_(
            db.or_(Task.repeat.is_(None), Task.repeat.in_(['none', ''])),
            db.extract('year', Task.due_date) == year,
            db.extract('month', Task.due_date) == month,
        )
        any_repeat = db.and_(
            Task.repeat.isnot(None),
            Task.repeat.notin_(['none', '']),
        )
        query = query.filter(db.or_(non_repeat_in_month, any_repeat))

    tasks = query.all()
    result = []
    for t in tasks:
        d = t.to_dict()
        # Expand repeating tasks across the requested month (for calendar view)
        if year and month and d.get('repeat') and d['repeat'] != 'none':
            occurrences = _expand_repeat_in_month(t, year, month)
            if occurrences:
                # Each occurrence gets its own dict with same id (calendar groups by due_date)
                # Occurrences BEFORE the current base due_date already happened -> mark done
                base_date = d.get('due_date')
                for occ_date in occurrences:
                    copy = dict(d)
                    copy['due_date'] = occ_date.isoformat()
                    if base_date and occ_date.isoformat() < base_date:
                        copy['completed'] = True
                    result.append(copy)
                continue
        result.append(d)
    return jsonify(result)


def _expand_repeat_in_month(task, year, month):
    """Return all dates in (year, month) where a repeating task occurs.

    For 'custom' tasks with repeat_days (e.g. every Wednesday), lists every
    matching weekday in the month. For 'weekly' from base date, lists every 7-day
    occurrence. Returns [] if none.
    """
    from datetime import timedelta
    from calendar import monthrange
    base = task.due_date or date.today()
    r = task.repeat or 'none'
    occurrences = []

    if r == 'custom' and task.repeat_days:
        try:
            days = {int(x) for x in task.repeat_days.split(',') if x.strip() != ''}
        except ValueError:
            days = set()
        if not days:
            return occurrences
        # List every weekday in the month that matches
        last_day = monthrange(year, month)[1]
        for dd in range(1, last_day + 1):
            d = date(year, month, dd)
            if d.weekday() in days:
                occurrences.append(d)
    elif r == 'weekly':
        # 7-day cycle anchored at base
        last_day = monthrange(year, month)[1]
        d0 = base
        # step back to first occurrence on/after month start
        while d0 > date(year, month, 1):
            d0 -= timedelta(days=7)
        while d0.year == year and d0.month == month and d0 <= date(year, month, last_day):
            occurrences.append(d0)
            d0 += timedelta(days=7)
    elif r == 'daily':
        last_day = monthrange(year, month)[1]
        d0 = date(year, month, 1)
        if base > d0:
            d0 = base
        while d0.year == year and d0.month == month and d0 <= date(year, month, last_day):
            occurrences.append(d0)
            d0 += timedelta(days=1)
    elif r == 'weekdays':
        last_day = monthrange(year, month)[1]
        d0 = date(year, month, 1)
        if base > d0:
            d0 = base
        while d0.year == year and d0.month == month and d0 <= date(year, month, last_day):
            if d0.weekday() < 5:
                occurrences.append(d0)
            d0 += timedelta(days=1)

    return occurrences


@app.route('/api/tasks', methods=['POST'])
def create_task():
    data = request.get_json()
    task = Task(
        title=data['title'],
        description=data.get('description', ''),
        priority=data.get('priority', 'medium'),
        category=data.get('category', 'general'),
        due_date=datetime.fromisoformat(data['due_date']).date() if data.get('due_date') else None,
        due_time=data.get('due_time', ''),
        repeat=data.get('repeat', 'none'),
        repeat_days=data.get('repeat_days', ''),
    )
    db.session.add(task)
    db.session.commit()
    return jsonify(task.to_dict()), 201


@app.route('/api/tasks/<int:task_id>', methods=['GET'])
def get_task(task_id):
    task = Task.query.get_or_404(task_id)
    return jsonify(task.to_dict())


@app.route('/api/tasks/<int:task_id>', methods=['PUT'])
def update_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json()

    if 'title' in data:
        task.title = data['title']
    if 'description' in data:
        task.description = data['description']
    if 'priority' in data:
        task.priority = data['priority']
    if 'category' in data:
        task.category = data['category']
    if 'due_date' in data:
        task.due_date = datetime.fromisoformat(data['due_date']).date() if data['due_date'] else None
    if 'due_time' in data:
        task.due_time = data['due_time']
    if 'repeat' in data:
        task.repeat = data['repeat']
    if 'repeat_days' in data:
        task.repeat_days = data['repeat_days']

    # Handle completion: for repeating tasks, roll to next occurrence
    if 'completed' in data:
        task.completed = data['completed']
        if task.completed and task.repeat != 'none':
            task.last_completed_at = datetime.now()
            next_date = _next_repeat_date(task)
            if next_date:
                task.due_date = next_date
                task.completed = False  # stays active for next occurrence

    db.session.commit()
    return jsonify(task.to_dict())


def _next_repeat_date(task):
    """Compute the next occurrence date for a repeating task."""
    from datetime import timedelta
    base = task.due_date or date.today()
    r = task.repeat

    if r == 'daily':
        return base + timedelta(days=1)
    if r == 'weekdays':
        d = base + timedelta(days=1)
        while d.weekday() >= 5:  # skip Sat/Sun
            d += timedelta(days=1)
        return d
    if r == 'weekly':
        return base + timedelta(days=7)
    if r == 'monthly':
        # Same day next month (clamp to month end)
        year, month = base.year, base.month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
        from calendar import monthrange
        last_day = monthrange(year, month)[1]
        return date(year, month, min(base.day, last_day))
    if r == 'custom' and task.repeat_days:
        # Move to the next selected weekday (0=Monday ... 6=Sunday)
        try:
            days = {int(x) for x in task.repeat_days.split(',') if x.strip() != ''}
        except ValueError:
            days = set()
        if days:
            d = base + timedelta(days=1)
            for _ in range(15):  # safety limit
                if d.weekday() in days:
                    return d
                d += timedelta(days=1)
    return None


@app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    return jsonify({'message': '已删除'}), 200


# ==================== Notes API ====================

@app.route('/api/notes', methods=['GET'])
def get_notes():
    notes = Note.query.order_by(Note.pinned.desc(), Note.updated_at.desc()).all()
    return jsonify([n.to_dict() for n in notes])


@app.route('/api/notes/<int:note_id>', methods=['GET'])
def get_note(note_id):
    note = Note.query.get_or_404(note_id)
    return jsonify(note.to_dict())


@app.route('/api/notes', methods=['POST'])
def create_note():
    data = request.get_json()
    note = Note(
        title=data.get('title', ''),
        content=data.get('content', ''),
        color=data.get('color', 'default'),
    )
    db.session.add(note)
    db.session.commit()
    return jsonify(note.to_dict()), 201


@app.route('/api/notes/<int:note_id>', methods=['PUT'])
def update_note(note_id):
    note = Note.query.get_or_404(note_id)
    data = request.get_json()

    if 'title' in data:
        note.title = data['title']
    if 'content' in data:
        note.content = data['content']
    if 'color' in data:
        note.color = data['color']
    if 'pinned' in data:
        note.pinned = data['pinned']

    note.updated_at = datetime.now()
    db.session.commit()
    return jsonify(note.to_dict())


@app.route('/api/notes/<int:note_id>', methods=['DELETE'])
def delete_note(note_id):
    note = Note.query.get_or_404(note_id)
    db.session.delete(note)
    db.session.commit()
    return jsonify({'message': '已删除'}), 200


# ==================== Schedules API ====================

@app.route('/api/schedules', methods=['GET'])
def get_schedules():
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)

    query = Schedule.query
    if year and month:
        query = query.filter(
            db.extract('year', Schedule.date) == year,
            db.extract('month', Schedule.date) == month,
        )

    schedules = query.order_by(Schedule.date, Schedule.time).all()
    return jsonify([s.to_dict() for s in schedules])


@app.route('/api/schedules', methods=['POST'])
def create_schedule():
    data = request.get_json()
    schedule = Schedule(
        title=data['title'],
        date=datetime.fromisoformat(data['date']).date(),
        time=data.get('time', ''),
        description=data.get('description', ''),
        color=data.get('color', 'blue'),
    )
    db.session.add(schedule)
    db.session.commit()
    return jsonify(schedule.to_dict()), 201


@app.route('/api/schedules/<int:schedule_id>', methods=['PUT'])
def update_schedule(schedule_id):
    schedule = Schedule.query.get_or_404(schedule_id)
    data = request.get_json()

    if 'title' in data:
        schedule.title = data['title']
    if 'date' in data:
        schedule.date = datetime.fromisoformat(data['date']).date()
    if 'time' in data:
        schedule.time = data.get('time', '')
    if 'description' in data:
        schedule.description = data['description']
    if 'color' in data:
        schedule.color = data['color']

    db.session.commit()
    return jsonify(schedule.to_dict())


@app.route('/api/schedules/<int:schedule_id>', methods=['DELETE'])
def delete_schedule(schedule_id):
    schedule = Schedule.query.get_or_404(schedule_id)
    db.session.delete(schedule)
    db.session.commit()
    return jsonify({'message': '已删除'}), 200


# ==================== Natural Language Parser ====================

# Common exercise names in Chinese (used as hints, not required)
EXERCISE_KEYWORDS = {
    '卧推': '卧推', '深蹲': '深蹲', '硬拉': '硬拉',
    '引体向上': '引体向上', '划船': '划船', '推举': '推举',
    '弯举': '弯举', '臂屈伸': '臂屈伸', '卷腹': '卷腹',
    '举腿': '举腿', '高抬腿': '高抬腿', '跳绳': '跳绳',
    '跑步': '跑步', '游泳': '游泳', '椭圆机': '椭圆机',
    '自行车': '自行车', '单车': '单车',
    '俯卧撑': '俯卧撑', '平板支撑': '平板支撑',
    '哑铃飞鸟': '哑铃飞鸟', '飞鸟': '飞鸟',
    '侧平举': '侧平举', '前平举': '前平举',
    '拉力器': '拉力器', '绳索': '绳索',
    '坐姿划船': '坐姿划船', '高位下拉': '高位下拉',
    '腿举机': '腿举机', '腿弯举': '腿弯举', '腿屈伸': '腿屈伸',
    '提踵': '提踵', '站姿提踵': '站姿提踵',
    '上斜': '上斜', '下斜': '下斜',
    '肱三头肌': '肱三头肌', '肱二头肌': '肱二头肌',
    '三角肌': '三角肌', '肩': '肩',
    '胸': '胸', '背': '背', '腿': '腿', '腹': '腹',
    '网球': '网球', '羽毛球': '羽毛球', '篮球': '篮球',
    '有氧': '有氧', '力量': '力量',
    'HIIT': 'HIIT', 'hiit': 'HIIT',
    '瑜伽': '瑜伽', '拉伸': '拉伸',
    '前束': '前束', '中束': '中束', '后束': '后束',
    '阿诺德推肩': '阿诺德推肩', '阿诺德推举': '阿诺德推举',
    '面拉': '面拉', '反向飞鸟': '反向飞鸟',
    '锤式': '锤式', '交替前平举': '交替前平举',
    '俯身V字前举': '俯身V字前举', '俯身': '俯身',
}


def _normalize_weight_unit(unit):
    """Convert weight unit strings to standard kg/lb"""
    unit = unit.lower()
    if unit in ('斤',):
        return 'kg', 0.5  # 1斤 = 0.5kg
    if unit in ('磅', 'lb'):
        return 'lb', 1.0
    return 'kg', 1.0


def parse_exercise_line(text):
    """
    Parse a single line of exercise input.
    Supports multiple formats:
    "卧推 50kg 3x10"         -> {name:"卧推", weight:50, unit:"kg", sets:3, reps:10}
    "卧推 50kg 3组10次"      -> same
    "阿诺德推肩12*2.5kg"     -> {name:"阿诺德推肩", reps:12, weight:2.5, unit:"kg"}
    "前束3轮"                -> {name:"前束", sets:3}
    "引体向上 4x8"           -> {name:"引体向上", sets:4, reps:8}
    "跑步 30min"             -> {name:"跑步", duration:30}
    "跳绳 500个"             -> {name:"跳绳", reps:500}
    "游泳 1km"               -> {name:"游泳", distance:1.0}
    "飞鸟"                   -> {name:"飞鸟"}
    """
    text = text.strip()
    if not text:
        return None

    result = {'name': '', 'sets': 0, 'reps': 0, 'weight': 0.0,
              'weight_unit': 'kg', 'duration': 0, 'distance': 0.0, 'notes': ''}

    # ---- Step 1: Split name from parameters ----
    # Find first digit; everything before it is the exercise name
    num_match = re.search(r'\d', text)
    if num_match:
        name_part = text[:num_match.start()].strip()
        param_part = text[num_match.start():]
    else:
        name_part = text.strip()
        param_part = ''

    # Clean up name (remove separators and trailing noise)
    name = re.sub(r'[\s,，、。.·\-—:：()（）\[\]【】]+$', '', name_part).strip()
    result['name'] = name or '未知动作'

    if not param_part:
        return result

    # ---- Step 2: reps × weight (most common: "20*2.5kg", "12×7.5kg") ----
    reps_weight = re.search(
        r'(\d+)\s*[xX×*]\s*(\d+\.?\d*)\s*(kg|公斤|斤|磅|lb|KG|LB)',
        param_part
    )
    if reps_weight:
        result['reps'] = int(reps_weight.group(1))
        result['weight'] = float(reps_weight.group(2))
        unit, factor = _normalize_weight_unit(reps_weight.group(3))
        result['weight'] = round(result['weight'] * factor, 2)
        result['weight_unit'] = unit
        param_part = param_part.replace(reps_weight.group(0), '').strip()

    # ---- Step 3: sets × reps ("3x10", "5组5次", "4*8") ----
    if result['reps'] == 0:
        sets_reps = re.search(r'(\d+)\s*[xX×*组]\s*(\d+)\s*(?:次|个|下)?', param_part)
        if sets_reps:
            result['sets'] = int(sets_reps.group(1))
            result['reps'] = int(sets_reps.group(2))
            param_part = param_part.replace(sets_reps.group(0), '').strip()

    # ---- Step 4: sets only ("3轮", "4组") ----
    if result['sets'] == 0:
        sets_only = re.search(r'(\d+)\s*(?:轮|组|R|r|set|sets)', param_part)
        if sets_only:
            result['sets'] = int(sets_only.group(1))
            param_part = param_part.replace(sets_only.group(0), '').strip()

    # ---- Step 5: reps only ("500个", "20次", "15下") ----
    if result['reps'] == 0:
        reps_only = re.search(r'(\d+)\s*(?:次|个|下|rep|reps)', param_part)
        if reps_only:
            result['reps'] = int(reps_only.group(1))
            param_part = param_part.replace(reps_only.group(0), '').strip()

    # ---- Step 6: weight only ("50kg") ----
    if result['weight'] == 0:
        weight_only = re.search(r'(\d+\.?\d*)\s*(kg|公斤|斤|磅|lb|KG|LB)', param_part)
        if weight_only:
            result['weight'] = float(weight_only.group(1))
            unit, factor = _normalize_weight_unit(weight_only.group(2))
            result['weight'] = round(result['weight'] * factor, 2)
            result['weight_unit'] = unit
            param_part = param_part.replace(weight_only.group(0), '').strip()

    # ---- Step 7: duration ("30min", "45分钟") ----
    dur_match = re.search(r'(\d+)\s*(?:min|分钟|分|mins)', param_part)
    if dur_match:
        result['duration'] = int(dur_match.group(1))
        param_part = param_part.replace(dur_match.group(0), '').strip()

    # ---- Step 8: distance ("1km", "1.5km", "1000m") ----
    dist_match = re.search(r'(\d+\.?\d*)\s*(km|公里|m|米)', param_part)
    if dist_match:
        dist_val = float(dist_match.group(1))
        unit = dist_match.group(2)
        if unit in ('m', '米'):
            dist_val = dist_val / 1000
        result['distance'] = round(dist_val, 2)
        param_part = param_part.replace(dist_match.group(0), '').strip()

    # ---- Step 9: leftovers become notes ----
    notes = re.sub(r'^[\s,，、。.·\-—:：+]+|[\s,，、。.·\-—:：+]+$', '', param_part).strip()
    if notes and notes not in ('-', '—'):
        result['notes'] = notes

    return result


# ==================== Workouts API ====================

@app.route('/api/workouts', methods=['GET'])
def get_workouts():
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)

    query = Workout.query.order_by(Workout.date.desc(), Workout.created_at.desc())

    if year and month:
        query = query.filter(
            db.extract('year', Workout.date) == year,
            db.extract('month', Workout.date) == month,
        )

    workouts = query.all()
    return jsonify([w.to_dict() for w in workouts])


@app.route('/api/workouts', methods=['POST'])
def create_workout():
    data = request.get_json()
    workout = Workout(
        date=datetime.fromisoformat(data['date']).date() if data.get('date') else date.today(),
        workout_type=data.get('workout_type', 'strength'),
        duration=data.get('duration', 0),
        notes=data.get('notes', ''),
        mood=data.get('mood', 3),
    )
    db.session.add(workout)
    db.session.flush()

    # Parse and add exercises from raw text input
    raw_exercises = data.get('exercises_raw', '')
    if raw_exercises:
        lines = [l.strip() for l in raw_exercises.split('\n') if l.strip()]
        for i, line in enumerate(lines):
            parsed = parse_exercise_line(line)
            if parsed:
                exercise = WorkoutExercise(
                    workout_id=workout.id,
                    name=parsed['name'],
                    sets=parsed['sets'],
                    reps=parsed['reps'],
                    weight=parsed['weight'],
                    weight_unit=parsed['weight_unit'],
                    duration=parsed['duration'],
                    distance=parsed['distance'],
                    notes=parsed['notes'],
                    sort_order=i,
                )
                db.session.add(exercise)

    # Also add structured exercises if provided
    structured = data.get('exercises', [])
    if structured:
        for i, ex in enumerate(structured):
            exercise = WorkoutExercise(
                workout_id=workout.id,
                name=ex['name'],
                sets=ex.get('sets', 0),
                reps=ex.get('reps', 0),
                weight=ex.get('weight', 0.0),
                weight_unit=ex.get('weight_unit', 'kg'),
                duration=ex.get('duration', 0),
                distance=ex.get('distance', 0.0),
                notes=ex.get('notes', ''),
                sort_order=len(raw_exercises.split('\n')) + i if raw_exercises else i,
            )
            db.session.add(exercise)

    db.session.commit()
    return jsonify(workout.to_dict(include_exercises=True)), 201


@app.route('/api/workouts/<int:workout_id>', methods=['GET'])
def get_workout(workout_id):
    workout = Workout.query.get_or_404(workout_id)
    return jsonify(workout.to_dict(include_exercises=True))


@app.route('/api/workouts/<int:workout_id>', methods=['PUT'])
def update_workout(workout_id):
    workout = Workout.query.get_or_404(workout_id)
    data = request.get_json()

    if 'date' in data:
        workout.date = datetime.fromisoformat(data['date']).date()
    if 'workout_type' in data:
        workout.workout_type = data['workout_type']
    if 'duration' in data:
        workout.duration = data['duration']
    if 'notes' in data:
        workout.notes = data['notes']
    if 'mood' in data:
        workout.mood = data['mood']

    db.session.commit()
    return jsonify(workout.to_dict(include_exercises=True))


@app.route('/api/workouts/<int:workout_id>', methods=['DELETE'])
def delete_workout(workout_id):
    workout = Workout.query.get_or_404(workout_id)
    db.session.delete(workout)
    db.session.commit()
    return jsonify({'message': '已删除'}), 200


# ==================== Workout Exercises API ====================

@app.route('/api/workouts/<int:workout_id>/exercises', methods=['POST'])
def add_exercise(workout_id):
    workout = Workout.query.get_or_404(workout_id)
    data = request.get_json()

    # Try parsing as natural language
    if isinstance(data, str) or data.get('raw'):
        raw = data if isinstance(data, str) else data['raw']
        parsed = parse_exercise_line(raw)
        if not parsed:
            return jsonify({'error': '无法解析输入'}), 400
        exercise = WorkoutExercise(
            workout_id=workout_id,
            name=parsed['name'],
            sets=parsed['sets'],
            reps=parsed['reps'],
            weight=parsed['weight'],
            weight_unit=parsed['weight_unit'],
            duration=parsed['duration'],
            distance=parsed['distance'],
            notes=parsed['notes'],
            sort_order=workout.exercises.count(),
        )
    else:
        exercise = WorkoutExercise(
            workout_id=workout_id,
            name=data['name'],
            sets=data.get('sets', 0),
            reps=data.get('reps', 0),
            weight=data.get('weight', 0.0),
            weight_unit=data.get('weight_unit', 'kg'),
            duration=data.get('duration', 0),
            distance=data.get('distance', 0.0),
            notes=data.get('notes', ''),
            sort_order=data.get('sort_order', workout.exercises.count()),
        )

    db.session.add(exercise)
    db.session.commit()
    return jsonify(exercise.to_dict()), 201


@app.route('/api/workouts/<int:workout_id>/exercises/<int:exercise_id>', methods=['PUT'])
def update_exercise(workout_id, exercise_id):
    exercise = WorkoutExercise.query.filter_by(id=exercise_id, workout_id=workout_id).first_or_404()
    data = request.get_json()

    if 'name' in data: exercise.name = data['name']
    if 'sets' in data: exercise.sets = data['sets']
    if 'reps' in data: exercise.reps = data['reps']
    if 'weight' in data: exercise.weight = data['weight']
    if 'weight_unit' in data: exercise.weight_unit = data['weight_unit']
    if 'duration' in data: exercise.duration = data['duration']
    if 'distance' in data: exercise.distance = data['distance']
    if 'notes' in data: exercise.notes = data['notes']
    if 'sort_order' in data: exercise.sort_order = data['sort_order']

    db.session.commit()
    return jsonify(exercise.to_dict())


@app.route('/api/workouts/<int:workout_id>/exercises/<int:exercise_id>', methods=['DELETE'])
def delete_exercise(workout_id, exercise_id):
    exercise = WorkoutExercise.query.filter_by(id=exercise_id, workout_id=workout_id).first_or_404()
    db.session.delete(exercise)
    db.session.commit()
    return jsonify({'message': '已删除'}), 200


# ==================== Body Metrics API ====================

@app.route('/api/body-metrics', methods=['GET'])
def get_body_metrics():
    limit = request.args.get('limit', type=int, default=90)  # last 90 days by default

    metrics = BodyMetric.query.order_by(BodyMetric.date.desc()).limit(limit).all()
    return jsonify([m.to_dict() for m in reversed(metrics)])  # chronological order


@app.route('/api/body-metrics', methods=['POST'])
def create_body_metric():
    data = request.get_json()

    def _f(key):
        """Safe float conversion for JSON dict values."""
        val = data.get(key)
        if val is None or val == '':
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    # Auto-calculate BMI if weight provided
    bmi = _f('bmi')
    weight = _f('weight')
    if bmi is None and weight:
        bmi = round(weight / (1.65 ** 2), 1)  # default height estimate

    metric = BodyMetric(
        date=datetime.fromisoformat(data['date']).date() if data.get('date') else date.today(),
        weight=weight,
        body_fat=_f('body_fat'),
        bmi=bmi,
        waist=_f('waist'),
        chest=_f('chest'),
        hip=_f('hip'),
        arm=_f('arm'),
        thigh=_f('thigh'),
        notes=data.get('notes', ''),
    )
    db.session.add(metric)
    db.session.commit()
    return jsonify(metric.to_dict()), 201


@app.route('/api/body-metrics/<int:metric_id>', methods=['PUT'])
def update_body_metric(metric_id):
    metric = BodyMetric.query.get_or_404(metric_id)
    data = request.get_json()

    if 'date' in data: metric.date = datetime.fromisoformat(data['date']).date()
    if 'weight' in data: metric.weight = data['weight']
    if 'body_fat' in data: metric.body_fat = data['body_fat']
    if 'bmi' in data: metric.bmi = data['bmi']
    if 'waist' in data: metric.waist = data['waist']
    if 'chest' in data: metric.chest = data['chest']
    if 'hip' in data: metric.hip = data['hip']
    if 'arm' in data: metric.arm = data['arm']
    if 'thigh' in data: metric.thigh = data['thigh']
    if 'notes' in data: metric.notes = data['notes']

    db.session.commit()
    return jsonify(metric.to_dict())


@app.route('/api/body-metrics/<int:metric_id>', methods=['DELETE'])
def delete_body_metric(metric_id):
    metric = BodyMetric.query.get_or_404(metric_id)
    db.session.delete(metric)
    db.session.commit()
    return jsonify({'message': '已删除'}), 200


@app.route('/api/body-metrics/latest', methods=['GET'])
def get_latest_body_metric():
    metric = BodyMetric.query.order_by(BodyMetric.date.desc()).first()
    if metric:
        return jsonify(metric.to_dict())
    return jsonify(None)


# ==================== Fitness Goals API ====================

@app.route('/api/fitness-goals', methods=['GET'])
def get_fitness_goals():
    goals = FitnessGoal.query.order_by(FitnessGoal.achieved, FitnessGoal.created_at.desc()).all()
    return jsonify([g.to_dict() for g in goals])


@app.route('/api/fitness-goals', methods=['POST'])
def create_fitness_goal():
    data = request.get_json()

    def _f(key):
        """Safe float conversion for JSON dict values."""
        val = data.get(key)
        if val is None or val == '':
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    goal = FitnessGoal(
        title=data['title'],
        goal_type=data.get('goal_type', 'custom'),
        target_value=_f('target_value'),
        current_value=_f('current_value'),
        start_value=_f('start_value'),
        unit=data.get('unit', 'kg'),
        start_date=datetime.fromisoformat(data['start_date']).date() if data.get('start_date') else date.today(),
        target_date=datetime.fromisoformat(data['target_date']).date() if data.get('target_date') else None,
        notes=data.get('notes', ''),
    )
    db.session.add(goal)
    db.session.commit()
    return jsonify(goal.to_dict()), 201


@app.route('/api/fitness-goals/<int:goal_id>', methods=['PUT'])
def update_fitness_goal(goal_id):
    goal = FitnessGoal.query.get_or_404(goal_id)
    data = request.get_json()

    if 'title' in data: goal.title = data['title']
    if 'goal_type' in data: goal.goal_type = data['goal_type']
    if 'target_value' in data: goal.target_value = data['target_value']
    if 'current_value' in data: goal.current_value = data['current_value']
    if 'start_value' in data: goal.start_value = data['start_value']
    if 'unit' in data: goal.unit = data['unit']
    if 'start_date' in data: goal.start_date = datetime.fromisoformat(data['start_date']).date()
    if 'target_date' in data: goal.target_date = datetime.fromisoformat(data['target_date']).date()
    if 'achieved' in data: goal.achieved = data['achieved']
    if 'notes' in data: goal.notes = data['notes']

    db.session.commit()
    return jsonify(goal.to_dict())


@app.route('/api/fitness-goals/<int:goal_id>', methods=['DELETE'])
def delete_fitness_goal(goal_id):
    goal = FitnessGoal.query.get_or_404(goal_id)
    db.session.delete(goal)
    db.session.commit()
    return jsonify({'message': '已删除'}), 200


# ==================== Diary API ====================

@app.route('/api/diaries', methods=['GET'])
def get_diaries():
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    limit = request.args.get('limit', type=int, default=100)

    query = Diary.query.order_by(Diary.date.desc())
    if year and month:
        query = query.filter(
            db.extract('year', Diary.date) == year,
            db.extract('month', Diary.date) == month,
        )
    diaries = query.limit(limit).all()
    return jsonify([d.to_dict() for d in diaries])


@app.route('/api/diaries/<int:diary_id>', methods=['GET'])
def get_diary(diary_id):
    diary = Diary.query.get_or_404(diary_id)
    return jsonify(diary.to_dict())


@app.route('/api/diaries/date/<string:date_str>', methods=['GET'])
def get_diary_by_date(date_str):
    try:
        target = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': '日期格式错误'}), 400
    diary = Diary.query.filter_by(date=target).first()
    if diary:
        return jsonify(diary.to_dict())
    return jsonify(None)


@app.route('/api/diaries', methods=['POST'])
def create_diary():
    import json
    data = request.get_json()
    diary = Diary(
        date=datetime.fromisoformat(data['date']).date() if data.get('date') else date.today(),
        title=data.get('title', ''),
        content=data.get('content', ''),
        mood=data.get('mood', 3),
        weather=data.get('weather', ''),
        images=json.dumps(data.get('images', [])),
    )
    db.session.add(diary)
    db.session.commit()
    return jsonify(diary.to_dict()), 201


@app.route('/api/diaries/<int:diary_id>', methods=['PUT'])
def update_diary(diary_id):
    import json
    diary = Diary.query.get_or_404(diary_id)
    data = request.get_json()

    if 'date' in data:
        diary.date = datetime.fromisoformat(data['date']).date()
    if 'title' in data:
        diary.title = data['title']
    if 'content' in data:
        diary.content = data['content']
    if 'mood' in data:
        diary.mood = data['mood']
    if 'weather' in data:
        diary.weather = data['weather']
    if 'images' in data:
        diary.images = json.dumps(data['images'])

    diary.updated_at = datetime.now()
    db.session.commit()
    return jsonify(diary.to_dict())


@app.route('/api/diaries/<int:diary_id>', methods=['DELETE'])
def delete_diary(diary_id):
    diary = Diary.query.get_or_404(diary_id)
    db.session.delete(diary)
    db.session.commit()
    return jsonify({'message': '已删除'}), 200


# ==================== Transactions API (记账) ====================

@app.route('/api/transactions', methods=['GET'])
def get_transactions():
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    txn_type = request.args.get('type')
    limit = request.args.get('limit', type=int, default=200)

    query = Transaction.query.order_by(Transaction.date.desc(), Transaction.created_at.desc())
    if year and month:
        query = query.filter(
            db.extract('year', Transaction.date) == year,
            db.extract('month', Transaction.date) == month,
        )
    if txn_type in ('income', 'expense'):
        query = query.filter_by(txn_type=txn_type)

    txns = query.limit(limit).all()
    return jsonify([t.to_dict() for t in txns])


@app.route('/api/transactions', methods=['POST'])
def create_transaction():
    data = request.get_json()
    txn = Transaction(
        date=datetime.fromisoformat(data['date']).date() if data.get('date') else date.today(),
        txn_type=data.get('txn_type', 'expense'),
        amount=data.get('amount', 0.0),
        category=data.get('category', 'other'),
        note=data.get('note', ''),
    )
    db.session.add(txn)
    db.session.commit()
    return jsonify(txn.to_dict()), 201


@app.route('/api/transactions/<int:txn_id>', methods=['PUT'])
def update_transaction(txn_id):
    txn = Transaction.query.get_or_404(txn_id)
    data = request.get_json()

    if 'date' in data:
        txn.date = datetime.fromisoformat(data['date']).date()
    if 'txn_type' in data:
        txn.txn_type = data['txn_type']
    if 'amount' in data:
        txn.amount = data['amount']
    if 'category' in data:
        txn.category = data['category']
    if 'note' in data:
        txn.note = data['note']

    db.session.commit()
    return jsonify(txn.to_dict())


@app.route('/api/transactions/<int:txn_id>', methods=['DELETE'])
def delete_transaction(txn_id):
    txn = Transaction.query.get_or_404(txn_id)
    db.session.delete(txn)
    db.session.commit()
    return jsonify({'message': '已删除'}), 200


@app.route('/api/transactions/stats', methods=['GET'])
def get_transaction_stats():
    """Monthly / range stats: income, expense, balance, by-category breakdown."""
    year = request.args.get('year', type=int, default=date.today().year)
    month = request.args.get('month', type=int, default=date.today().month)

    txns = Transaction.query.filter(
        db.extract('year', Transaction.date) == year,
        db.extract('month', Transaction.date) == month,
    ).all()

    income = 0.0
    expense = 0.0
    expense_by_cat = {}
    income_by_cat = {}
    daily = {}

    for t in txns:
        d = t.date.isoformat()
        if d not in daily:
            daily[d] = {'income': 0.0, 'expense': 0.0}
        if t.txn_type == 'income':
            income += t.amount
            daily[d]['income'] += t.amount
            income_by_cat[t.category] = income_by_cat.get(t.category, 0.0) + t.amount
        else:
            expense += t.amount
            daily[d]['expense'] += t.amount
            expense_by_cat[t.category] = expense_by_cat.get(t.category, 0.0) + t.amount

    return jsonify({
        'year': year,
        'month': month,
        'income': round(income, 2),
        'expense': round(expense, 2),
        'balance': round(income - expense, 2),
        'count': len(txns),
        'expense_by_cat': expense_by_cat,
        'income_by_cat': income_by_cat,
        'daily': daily,
    })


# ==================== Image Upload API ====================

@app.route('/api/upload', methods=['POST'])
def upload_image():
    """Upload an image file, returns its public URL.
    Handles JPEG/PNG/WebP, plus HEIC/HEIF (iPhone/OPPO photos) by converting via pillow_heif.
    Server-side compresses too as a safety net.
    Stores in Supabase Storage (S3) when configured, otherwise local disk."""
    import uuid
    from werkzeug.utils import secure_filename
    from PIL import Image as PILImage
    import io

    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '空文件名'}), 400

    raw = file.read()
    # Detect HEIC/HEIF by magic bytes (file is saved with .jpg/.heic extension by client)
    magic = raw[4:12] if len(raw) >= 12 else b''
    is_heic = magic in (b'ftypheic', b'ftypmif1', b'ftypheix', b'ftypheim')

    # Decode + re-encode as JPEG. Handles HEIC, RGBA, oversized originals, etc.
    out_data, out_ext = raw, 'jpg'
    try:
        if is_heic:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        img = PILImage.open(io.BytesIO(raw))
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        # Always re-encode through JPEG to ensure browser compatibility + size reduction
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=82, optimize=True)
        out_data = buf.getvalue()
        out_ext = 'jpg'
    except Exception as e:
        # Couldn't decode (e.g. unsupported format) — fall back to saving the raw bytes
        # only if extension looks OK, otherwise reject
        ext = (file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else '')
        allowed = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'heic', 'heif'}
        if ext not in allowed:
            return jsonify({'error': f'不支持的格式: {ext}'}), 400
        out_data, out_ext = raw, ext

    fname = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}.{out_ext}"

    # Generate thumbnail (240px max side, low quality) for calendar cells
    tbuf = None
    try:
        if out_ext in ('jpg', 'jpeg', 'png', 'webp', 'bmp'):
            timg = PILImage.open(io.BytesIO(out_data))
            if timg.mode in ('RGBA', 'P'):
                timg = timg.convert('RGB')
            tmax = 240
            tw, th = timg.size
            if tw > tmax or th > tmax:
                ratio = min(tmax / tw, tmax / th)
                timg = timg.resize((int(tw * ratio), int(th * ratio)), PILImage.LANCZOS)
            tbuf = io.BytesIO()
            timg.save(tbuf, format='JPEG', quality=55, optimize=True)
    except Exception:
        tbuf = None

    tfname = fname.rsplit('.', 1)[0] + '_thumb.jpg'
    thumb_url = None
    if USE_SUPABASE_STORAGE:
        _supabase_put(fname, out_data)
        if tbuf is not None:
            _supabase_put(tfname, tbuf.getvalue())
            thumb_url = f'/uploads/{tfname}'
    else:
        upload_dir = os.path.join(basedir, '..', 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        with open(os.path.join(upload_dir, fname), 'wb') as f:
            f.write(out_data)
        if tbuf is not None:
            with open(os.path.join(upload_dir, tfname), 'wb') as f:
                f.write(tbuf.getvalue())
            thumb_url = f'/uploads/{tfname}'

    return jsonify({'url': f'/uploads/{fname}', 'thumb': thumb_url}), 201


def _supabase_put(key, data):
    """Upload bytes to Supabase Storage bucket via REST API."""
    import requests
    url = f'https://{SUPABASE_REF}.supabase.co/storage/v1/object/{STORAGE_BUCKET}/{key}'
    headers = {
        'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
        'apikey': SUPABASE_SERVICE_KEY,
        'Content-Type': 'image/jpeg',
        'x-upsert': 'true',
    }
    r = requests.post(url, data=data, headers=headers, timeout=60)
    r.raise_for_status()


def _supabase_get(key):
    """Fetch bytes from Supabase Storage bucket (public)."""
    import requests
    url = f'https://{SUPABASE_REF}.supabase.co/storage/v1/object/public/{STORAGE_BUCKET}/{key}'
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise FileNotFoundError(key)
    return r.content


# Serve uploaded files
@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    from flask import make_response, send_file
    import io
    if USE_SUPABASE_STORAGE:
        try:
            data = _supabase_get(filename)
        except Exception:
            return jsonify({'error': '文件不存在'}), 404
        resp = make_response(send_file(io.BytesIO(data), mimetype='image/jpeg'))
    else:
        resp = make_response(send_from_directory(os.path.join(basedir, '..', 'uploads'), filename))
    resp.headers['Cache-Control'] = 'no-store, must-revalidate'
    return resp


# ==================== Parse Exercise Text API (utility) ====================

@app.route('/api/parse-exercise', methods=['POST'])
def parse_exercise():
    """Utility endpoint: send raw text, get structured result back"""
    data = request.get_json()
    text = data.get('text', '')
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    results = []
    for line in lines:
        parsed = parse_exercise_line(line)
        if parsed:
            results.append(parsed)
    return jsonify(results)

@app.route('/api/weather')
def get_weather():
    """Fetch Guangzhou weather via public API"""
    import requests as http_req
    try:
        # Use wttr.in for simple weather data
        resp = http_req.get(
            'https://wttr.in/Guangzhou?format=%C|%t|%h|%w|%p',
            timeout=10,
            headers={'User-Agent': 'curl/8.0'}
        )
        text = resp.text.strip()
        parts = text.split('|')
        return jsonify({
            'condition': parts[0] if len(parts) > 0 else 'Unknown',
            'temperature': parts[1] if len(parts) > 1 else '--',
            'humidity': parts[2] if len(parts) > 2 else '--',
            'wind': parts[3] if len(parts) > 3 else '--',
            'precipitation': parts[4] if len(parts) > 4 else '--',
        })
    except Exception as e:
        return jsonify({'error': str(e), 'condition': '获取失败', 'temperature': '--'}), 200


# ==================== Stats API ====================

@app.route('/api/stats')
def get_stats():
    today = date.today()
    total_tasks = Task.query.count()
    pending_tasks = Task.query.filter_by(completed=False).count()
    today_tasks = Task.query.filter(
        Task.due_date == today,
        Task.completed == False
    ).count()
    overdue_tasks = Task.query.filter(
        Task.due_date < today,
        Task.completed == False
    ).count()
    notes_count = Note.query.count()
    today_schedules = Schedule.query.filter(Schedule.date == today).count()

    # Fitness stats
    today_workout = Workout.query.filter(Workout.date == today).first()
    workouts_this_week = Workout.query.filter(
        Workout.date >= date(today.year, today.month, today.day - today.weekday())
    ).count()
    latest_weight = BodyMetric.query.order_by(BodyMetric.date.desc()).first()
    active_goals = FitnessGoal.query.filter_by(achieved=False).count()

    # Money stats (this month)
    month_income = db.session.query(
        db.func.coalesce(db.func.sum(Transaction.amount), 0)
    ).filter(
        Transaction.txn_type == 'income',
        db.extract('year', Transaction.date) == today.year,
        db.extract('month', Transaction.date) == today.month,
    ).scalar()
    month_expense = db.session.query(
        db.func.coalesce(db.func.sum(Transaction.amount), 0)
    ).filter(
        Transaction.txn_type == 'expense',
        db.extract('year', Transaction.date) == today.year,
        db.extract('month', Transaction.date) == today.month,
    ).scalar()

    return jsonify({
        'total_tasks': total_tasks,
        'pending_tasks': pending_tasks,
        'today_tasks': today_tasks,
        'overdue_tasks': overdue_tasks,
        'notes_count': notes_count,
        'today_schedules': today_schedules,
        'has_today_workout': today_workout is not None,
        'today_workout_type': today_workout.workout_type if today_workout else None,
        'workouts_this_week': workouts_this_week,
        'latest_weight': latest_weight.weight if latest_weight else None,
        'active_goals': active_goals,
        'month_income': round(month_income, 2),
        'month_expense': round(month_expense, 2),
        'month_balance': round(month_income - month_expense, 2),
    })


# ==================== Storage Items API ====================

@app.route('/api/storage', methods=['GET'])
def get_storage_items():
    category = request.args.get('category')
    query = StorageItem.query.order_by(StorageItem.category, StorageItem.created_at.desc())
    if category and category != 'all':
        query = query.filter_by(category=category)
    items = query.all()
    return jsonify([i.to_dict() for i in items])


@app.route('/api/storage', methods=['POST'])
def create_storage_item():
    data = request.get_json()
    item = StorageItem(
        name=data.get('name', '').strip(),
        category=data.get('category', 'other'),
        quantity=data.get('quantity', 1),
        location=data.get('location', ''),
        brand=data.get('brand', ''),
        note=data.get('note', ''),
        expire_date=datetime.fromisoformat(data['expire_date']).date() if data.get('expire_date') else None,
    )
    if not item.name:
        return jsonify({'error': '请输入物品名称'}), 400
    db.session.add(item)
    db.session.commit()
    return jsonify(item.to_dict()), 201


@app.route('/api/storage/<int:item_id>', methods=['PUT'])
def update_storage_item(item_id):
    item = StorageItem.query.get_or_404(item_id)
    data = request.get_json()
    if 'name' in data: item.name = data['name'].strip()
    if 'category' in data: item.category = data['category']
    if 'quantity' in data:
        try:
            item.quantity = max(0, int(data['quantity']))
        except (TypeError, ValueError):
            pass
    if 'location' in data: item.location = data['location']
    if 'brand' in data: item.brand = data['brand']
    if 'note' in data: item.note = data['note']
    if 'expire_date' in data:
        item.expire_date = datetime.fromisoformat(data['expire_date']).date() if data.get('expire_date') else None
    db.session.commit()
    return jsonify(item.to_dict())


@app.route('/api/storage/<int:item_id>', methods=['DELETE'])
def delete_storage_item(item_id):
    item = StorageItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    return jsonify({'message': '已删除'}), 200


# ==================== Anniversaries API ====================

@app.route('/api/anniversaries', methods=['GET'])
def get_anniversaries():
    annivs = Anniversary.query.order_by(Anniversary.anniv_date).all()
    return jsonify([a.to_dict() for a in annivs])


@app.route('/api/anniversaries', methods=['POST'])
def create_anniversary():
    data = request.get_json()
    anniv = Anniversary(
        name=data.get('name', '').strip(),
        anniv_date=datetime.fromisoformat(data['anniv_date']).date(),
        yearly=data.get('yearly', True),
        note=data.get('note', ''),
    )
    if not anniv.name or not anniv.anniv_date:
        return jsonify({'error': '请输入名称和日期'}), 400
    db.session.add(anniv)
    db.session.commit()
    return jsonify(anniv.to_dict()), 201


@app.route('/api/anniversaries/<int:anniv_id>', methods=['PUT'])
def update_anniversary(anniv_id):
    anniv = Anniversary.query.get_or_404(anniv_id)
    data = request.get_json()
    if 'name' in data: anniv.name = data['name'].strip()
    if 'anniv_date' in data:
        anniv.anniv_date = datetime.fromisoformat(data['anniv_date']).date()
    if 'yearly' in data: anniv.yearly = data['yearly']
    if 'note' in data: anniv.note = data['note']
    db.session.commit()
    return jsonify(anniv.to_dict())


@app.route('/api/anniversaries/<int:anniv_id>', methods=['DELETE'])
def delete_anniversary(anniv_id):
    anniv = Anniversary.query.get_or_404(anniv_id)
    db.session.delete(anniv)
    db.session.commit()
    return jsonify({'message': '已删除'}), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)
