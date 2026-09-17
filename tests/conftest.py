import pytest
from app import create_app
from app.extensions import db
from app.models import User

@pytest.fixture
def app(tmp_path):
    app=create_app({"TESTING":True,"WTF_CSRF_ENABLED":False,"SQLALCHEMY_DATABASE_URI":"sqlite:///:memory:","MODEL_PATH":str(tmp_path/"model.joblib"),"SECRET_KEY":"test"})
    with app.app_context():
        db.create_all(); u=User(username="admin",full_name="Admin",role="ADMIN"); u.set_password("StrongPass123!"); db.session.add(u); db.session.commit()
    yield app

@pytest.fixture
def client(app): return app.test_client()

@pytest.fixture
def auth(client):
    client.post("/login",data={"username":"admin","password":"StrongPass123!"})
    return client
