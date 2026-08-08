from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Task(db.Model):
    __tablename__ = 'tasks'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    priority = db.Column(db.String(10), default='medium')  # high, medium, low
    category = db.Column(db.String(50), default='general')  # general, work, life, health, study
    completed = db.Column(db.Boolean, default=False)
    due_date = db.Column(db.Date, nullable=True)
    due_time = db.Column(db.String(10), default='')  # HH:MM
    repeat = db.Column(db.String(20), default='none')  # none, daily, weekdays, weekly, monthly, custom
    repeat_days = db.Column(db.String(20), default='')  # e.g. "1,3,5" for Monday/Wednesday/Friday (0=Monday)
    last_completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'priority': self.priority,
            'category': self.category,
            'completed': self.completed,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'due_time': self.due_time,
            'repeat': self.repeat,
            'repeat_days': self.repeat_days,
            'last_completed_at': self.last_completed_at.isoformat() if self.last_completed_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Note(db.Model):
    __tablename__ = 'notes'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), default='')
    content = db.Column(db.Text, default='')
    color = db.Column(db.String(20), default='default')  # for UI theming
    pinned = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'content': self.content,
            'color': self.color,
            'pinned': self.pinned,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Schedule(db.Model):
    __tablename__ = 'schedules'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.String(10), default='')  # HH:MM format
    description = db.Column(db.Text, default='')
    color = db.Column(db.String(20), default='blue')
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'date': self.date.isoformat() if self.date else None,
            'time': self.time,
            'description': self.description,
            'color': self.color,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Workout(db.Model):
    """一次训练记录"""
    __tablename__ = 'workouts'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=datetime.now)
    workout_type = db.Column(db.String(20), default='strength')  # strength, cardio, mixed
    duration = db.Column(db.Integer, default=0)  # minutes
    notes = db.Column(db.Text, default='')
    mood = db.Column(db.Integer, default=3)  # 1-5
    created_at = db.Column(db.DateTime, default=datetime.now)

    exercises = db.relationship('WorkoutExercise', backref='workout', lazy='dynamic',
                                 cascade='all, delete-orphan')

    def to_dict(self, include_exercises=False):
        result = {
            'id': self.id,
            'date': self.date.isoformat() if self.date else None,
            'workout_type': self.workout_type,
            'duration': self.duration,
            'notes': self.notes,
            'mood': self.mood,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
        if include_exercises:
            result['exercises'] = [e.to_dict() for e in self.exercises.all()]
        return result


class WorkoutExercise(db.Model):
    """训练中的单个动作"""
    __tablename__ = 'workout_exercises'

    id = db.Column(db.Integer, primary_key=True)
    workout_id = db.Column(db.Integer, db.ForeignKey('workouts.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    sets = db.Column(db.Integer, default=0)
    reps = db.Column(db.Integer, default=0)
    weight = db.Column(db.Float, default=0.0)
    weight_unit = db.Column(db.String(10), default='kg')
    duration = db.Column(db.Integer, default=0)  # for cardio: minutes
    distance = db.Column(db.Float, default=0.0)  # for cardio: km/m
    notes = db.Column(db.String(200), default='')
    sort_order = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            'id': self.id,
            'workout_id': self.workout_id,
            'name': self.name,
            'sets': self.sets,
            'reps': self.reps,
            'weight': self.weight,
            'weight_unit': self.weight_unit,
            'duration': self.duration,
            'distance': self.distance,
            'notes': self.notes,
            'sort_order': self.sort_order,
        }


class BodyMetric(db.Model):
    """体质指标记录"""
    __tablename__ = 'body_metrics'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=datetime.now)
    weight = db.Column(db.Float, nullable=True)  # kg
    body_fat = db.Column(db.Float, nullable=True)  # percentage
    bmi = db.Column(db.Float, nullable=True)
    waist = db.Column(db.Float, nullable=True)  # cm
    chest = db.Column(db.Float, nullable=True)  # cm
    hip = db.Column(db.Float, nullable=True)  # cm
    arm = db.Column(db.Float, nullable=True)  # cm
    thigh = db.Column(db.Float, nullable=True)  # cm
    notes = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'date': self.date.isoformat() if self.date else None,
            'weight': self.weight,
            'body_fat': self.body_fat,
            'bmi': self.bmi,
            'waist': self.waist,
            'chest': self.chest,
            'hip': self.hip,
            'arm': self.arm,
            'thigh': self.thigh,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class FitnessGoal(db.Model):
    """健身目标"""
    __tablename__ = 'fitness_goals'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    goal_type = db.Column(db.String(20), default='weight')  # weight, body_fat, workout_freq, custom
    target_value = db.Column(db.Float, nullable=True)
    current_value = db.Column(db.Float, nullable=True)
    start_value = db.Column(db.Float, nullable=True)
    unit = db.Column(db.String(20), default='kg')
    start_date = db.Column(db.Date, nullable=True)
    target_date = db.Column(db.Date, nullable=True)
    achieved = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'goal_type': self.goal_type,
            'target_value': self.target_value,
            'current_value': self.current_value,
            'start_value': self.start_value,
            'unit': self.unit,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'target_date': self.target_date.isoformat() if self.target_date else None,
            'achieved': self.achieved,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Diary(db.Model):
    """日记"""
    __tablename__ = 'diaries'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=datetime.now)
    title = db.Column(db.String(200), default='')
    content = db.Column(db.Text, default='')
    mood = db.Column(db.Integer, default=3)  # 1-5
    weather = db.Column(db.String(50), default='')
    images = db.Column(db.Text, default='[]')  # JSON array of image URLs
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        import json
        try:
            images = json.loads(self.images) if self.images else []
        except Exception:
            images = []
        # Build {url, thumb} pairs for each image
        image_list = []
        for u in images:
            if isinstance(u, dict):
                image_list.append(u)
            else:
                # Derive thumb URL: /uploads/xxx.jpg -> /uploads/xxx_thumb.jpg
                thumb = None
                if isinstance(u, str) and '/uploads/' in u:
                    thumb = u.rsplit('.', 1)[0] + '_thumb.jpg'
                image_list.append({'url': u, 'thumb': thumb})
        return {
            'id': self.id,
            'date': self.date.isoformat() if self.date else None,
            'title': self.title,
            'content': self.content,
            'mood': self.mood,
            'weather': self.weather,
            'images': image_list,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Transaction(db.Model):
    """记账记录"""
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=datetime.now)
    txn_type = db.Column(db.String(10), nullable=False, default='expense')  # income, expense
    amount = db.Column(db.Float, nullable=False, default=0.0)
    category = db.Column(db.String(50), default='other')
    note = db.Column(db.String(200), default='')
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'date': self.date.isoformat() if self.date else None,
            'txn_type': self.txn_type,
            'amount': self.amount,
            'category': self.category,
            'note': self.note,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class StorageItem(db.Model):
    """收纳物品"""
    __tablename__ = 'storage_items'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)          # 物品名称
    category = db.Column(db.String(50), default='other')      # clothes/food/daily/electronics/books/other
    quantity = db.Column(db.Integer, default=1)               # 数量
    location = db.Column(db.String(100), default='')          # 存放位置
    brand = db.Column(db.String(100), default='')             # 品牌/规格
    note = db.Column(db.String(300), default='')              # 备注
    expire_date = db.Column(db.Date)                           # 保质期/到期日（可选）
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'category': self.category,
            'quantity': self.quantity,
            'location': self.location,
            'brand': self.brand,
            'note': self.note,
            'expire_date': self.expire_date.isoformat() if self.expire_date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Anniversary(db.Model):
    """纪念日（生日/节日等）"""
    __tablename__ = 'anniversaries'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)       # 名称：爸妈生日、结婚纪念日...
    anniv_date = db.Column(db.Date, nullable=False)        # 日期（年份取最近一次；每年重复时看月日）
    yearly = db.Column(db.Boolean, default=True)           # 是否每年重复（生日=True，一次性纪念日=False）
    note = db.Column(db.String(300), default='')
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'anniv_date': self.anniv_date.isoformat() if self.anniv_date else None,
            'yearly': self.yearly,
            'note': self.note,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
