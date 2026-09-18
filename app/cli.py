import json
import os
from pathlib import Path
import click, joblib, numpy as np, pandas as pd
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import inspect
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, precision_score, recall_score, f1_score, roc_auc_score
from sklearn.model_selection import train_test_split
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
    rng=np.random.default_rng(42); n=1000
    score=rng.uniform(0,10,n); attendance=rng.uniform(45,100,n); late=rng.integers(0,7,n)
    latent=1.15*(5-score)+0.055*(78-attendance)+0.42*late+rng.normal(0,1.0,n)
    failed=(latent>1.15).astype(int)
    x=pd.DataFrame({"score":score,"attendance_rate":attendance,"late_submissions":late}); y=failed
    x_train,x_test,y_train,y_test=train_test_split(x,y,test_size=.25,random_state=42,stratify=y)
    model=RandomForestClassifier(n_estimators=250,max_depth=8,min_samples_leaf=3,class_weight="balanced",random_state=42,n_jobs=1); model.fit(x_train,y_train)
    pred=model.predict(x_test); version=pd.Timestamp.now(tz="UTC").strftime("rf-%Y%m%d-%H%M%S")
    metrics={"accuracy":accuracy_score(y_test,pred),"precision":precision_score(y_test,pred,zero_division=0),"recall":recall_score(y_test,pred,zero_division=0),"f1":f1_score(y_test,pred,zero_division=0),"confusion_matrix":confusion_matrix(y_test,pred).tolist(),"roc_auc":roc_auc_score(y_test,model.predict_proba(x_test)[:,1]),"test_samples":len(y_test),"demo_synthetic":True}
    metadata={"version":version,"features":FEATURES,"metrics":metrics,"feature_importance":dict(zip(FEATURES,map(float,model.feature_importances_))),"training_rows":len(x_train),"dataset_rows":n,"data_label":"Dữ liệu giả lập phục vụ kiểm thử","random_state":42}
    path=Path(current_app.config["MODEL_PATH"]); path.parent.mkdir(parents=True,exist_ok=True); joblib.dump({"model":model,"metadata":metadata},path)
    ModelVersion.query.update({ModelVersion.is_active:False}); db.session.add(ModelVersion(version=version,metrics_json=json.dumps(metrics),features_json=json.dumps(FEATURES),artifact_path=os.path.relpath(path,Path(current_app.root_path).parent),is_active=True)); db.session.commit()
    (path.parent/"metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    click.echo(json.dumps(metadata,ensure_ascii=True,indent=2))
