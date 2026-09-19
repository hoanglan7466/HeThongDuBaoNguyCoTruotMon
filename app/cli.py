import json
import os
from pathlib import Path
import click, joblib, numpy as np, pandas as pd
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import inspect
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, precision_score, recall_score, f1_score, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit
from .extensions import db
from .models import ModelVersion, User
from .services import FEATURES

def _upgrade_schema():
    """Apply the one additive migration needed by installations without Alembic."""
    columns = {column["name"] for column in inspect(db.engine).get_columns("prediction")}
    if "week_number" in columns:
        return False
    db.session.execute(db.text("ALTER TABLE prediction ADD COLUMN week_number INTEGER NOT NULL DEFAULT 5"))
    if db.engine.dialect.name == "mysql":
        db.session.execute(db.text("UPDATE prediction p JOIN enrollment e ON e.id = p.enrollment_id SET p.week_number = e.current_week"))
    else:
        db.session.execute(db.text("UPDATE prediction SET week_number = (SELECT current_week FROM enrollment WHERE enrollment.id = prediction.enrollment_id)"))
    db.session.commit()
    return True

def register_commands(app):
    app.cli.add_command(init_db); app.cli.add_command(train_model)

def _synthetic_training_data():
    """Create grouped synthetic snapshots without deriving labels from inputs.

    Each student contributes two week-5+ snapshots.  The final outcome is
    generated from latent student traits before observable features are made,
    so the model only sees correlated early indicators rather than its target.
    """
    rng=np.random.default_rng(42); students=500; snapshots=2
    student_id=np.repeat(np.arange(students),snapshots)
    academic=rng.normal(0,1,students); engagement=rng.normal(0,1,students); deadline=rng.normal(0,1,students)
    final_risk=1.0*academic+0.8*engagement+0.5*deadline+rng.normal(0,1.15,students)
    failed=(final_risk>0.25).astype(int)
    academic=np.repeat(academic,snapshots); engagement=np.repeat(engagement,snapshots); deadline=np.repeat(deadline,snapshots)
    week=np.tile(np.array([5,6]),students)
    score=np.clip(7.0-1.45*academic+rng.normal(0,.9,students*snapshots)-.08*(week-5),0,10)
    attendance=np.clip(84-10*engagement+rng.normal(0,6,students*snapshots)-.4*(week-5),45,100)
    late=np.clip(np.rint(1.5+1.2*deadline+rng.normal(0,1.1,students*snapshots)),0,7).astype(int)
    x=pd.DataFrame({"score":score,"attendance_rate":attendance,"late_submissions":late})
    return x, np.repeat(failed,snapshots), student_id

@click.command("init-db")
@click.option("--admin-password",envvar="DEMO_ADMIN_PASSWORD",default="Admin@123",show_default=False,hide_input=True)
@click.option("--advisor-password",envvar="DEMO_ADVISOR_PASSWORD",default="Covan@123",show_default=False,hide_input=True)
@with_appcontext
def init_db(admin_password, advisor_password):
    db.create_all()
    upgraded = _upgrade_schema()
    for username, legacy_username, full_name, email, role, password in (("admin","admin_demo","Quản trị viên DEMO","admin@example.invalid","ADMIN",admin_password),("covan","covan_demo","Cố vấn DEMO","covan@example.invalid","COVAN",advisor_password)):
        user=User.query.filter_by(username=username).first() or User.query.filter_by(username=legacy_username).first()
        if not user:
            user=User(username=username,full_name=full_name,email=email,role=role); db.session.add(user)
        user.username, user.full_name, user.email, user.role, user.active = username, full_name, email, role, True
        user.set_password(password)
    db.session.commit(); click.echo("Database initialized; DEMO accounts ready." + (" Prediction snapshots migrated." if upgraded else ""))

@click.command("train-model")
@with_appcontext
def train_model():
    x,y,groups=_synthetic_training_data()
    split=GroupShuffleSplit(n_splits=1,test_size=.25,random_state=42)
    train_index,test_index=next(split.split(x,y,groups=groups))
    x_train,x_test,y_train,y_test=x.iloc[train_index],x.iloc[test_index],y[train_index],y[test_index]
    model=RandomForestClassifier(n_estimators=250,max_depth=8,min_samples_leaf=3,class_weight="balanced",random_state=42,n_jobs=1); model.fit(x_train,y_train)
    pred=model.predict(x_test); version=pd.Timestamp.now(tz="UTC").strftime("rf-%Y%m%d-%H%M%S")
    metrics={"accuracy":accuracy_score(y_test,pred),"precision":precision_score(y_test,pred,zero_division=0),"recall":recall_score(y_test,pred,zero_division=0),"f1":f1_score(y_test,pred,zero_division=0),"confusion_matrix":confusion_matrix(y_test,pred).tolist(),"roc_auc":roc_auc_score(y_test,model.predict_proba(x_test)[:,1]),"test_samples":len(y_test),"demo_synthetic":True}
    metadata={"version":version,"features":FEATURES,"metrics":metrics,"feature_importance":dict(zip(FEATURES,map(float,model.feature_importances_))),"training_rows":len(x_train),"dataset_rows":len(x),"data_label":"Dữ liệu giả lập phục vụ kiểm thử","random_state":42,"evaluation_method":"GroupShuffleSplit theo sinh viên synthetic","train_students":len(set(groups[train_index])),"test_students":len(set(groups[test_index])),"prediction_weeks":[5,6]}
    path=Path(current_app.config["MODEL_PATH"]); path.parent.mkdir(parents=True,exist_ok=True); joblib.dump({"model":model,"metadata":metadata},path)
    ModelVersion.query.update({ModelVersion.is_active:False}); db.session.add(ModelVersion(version=version,metrics_json=json.dumps(metrics),features_json=json.dumps(FEATURES),artifact_path=os.path.relpath(path,Path(current_app.root_path).parent),is_active=True)); db.session.commit()
    (path.parent/"metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    click.echo(json.dumps(metadata,ensure_ascii=True,indent=2))
