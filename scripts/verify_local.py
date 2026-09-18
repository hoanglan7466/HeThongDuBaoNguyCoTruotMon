"""Verify the running local server using DEMO accounts; never print secrets."""
import http.cookiejar
import json
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, build_opener

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:5000"


def main():
    credentials = (("admin", "Admin@123"), ("covan", "Covan@123"))
    for username, password in credentials:
        client = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def request(path, data=None):
            try:
                response = client.open(BASE + path, None if data is None else urlencode(data).encode(), timeout=60)
            except HTTPError as error:
                response = error
            body = response.read().decode("utf-8-sig")
            assert "Traceback (most recent call last)" not in body
            return response.status, body, response.geturl()

        def token(body):
            return re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)

        assert request("/")[2].split("?", 1)[0].endswith("/login")
        for path in ("/students", "/academic-data", "/analysis", "/alerts", "/reports", "/model", "/admin/users", "/settings", "/api/dashboard"):
            assert request(path)[2].split("?", 1)[0].endswith("/login"), path
        health = json.loads(request("/health")[1])
        assert health["database"] == "ok" and health["model"] == "ready"
        login_page = request("/login")[1]
        assert request("/login", {"username": username, "password": password})[0] == 400
        status, body, url = request("/login", {"username": username, "password": password, "csrf_token": token(login_page)})
        assert status == 200 and url == BASE + "/", "Login failed"
        if username == "admin":
            assert request("/data/seed-demo", {"csrf_token": token(body)})[0] == 200
            status, body, _ = request("/predict/batch", {"csrf_token": token(body)})
            assert status == 200 and "bỏ qua 0" in body
        for path in ("/", "/students", "/students?q=DEMO001", "/students?page=2", "/academic-data", "/analysis", "/alerts", "/reports", "/reports/export.csv", "/model", "/data/template.csv", "/static/css/app.css"):
            assert request(path)[0] == 200, path
        for path in ("/data/import", "/admin/users", "/settings"):
            assert request(path)[0] == (200 if username == "admin" else 403), path
        if username == "covan":
            for path in ("/data/import", "/data/import/confirm", "/data/seed-demo"):
                assert request(path, {"csrf_token": token(body)})[0] == 403, path
        assert request("/missing-page")[0] == 404
        assert request("/students/99999999")[0] == 404
        body = request("/analysis")[1]
        prediction_path = re.search(r'action="(/predict/\d+)"', body).group(1)
        status, body, _ = request(prediction_path, {"csrf_token": token(body)})
        assert status == 200 and "Đã lưu dự báo" in body, "Prediction failed"
        stats = json.loads(request("/api/dashboard")[1])["data"]
        print(username, "pages/login/CSRF/prediction OK", json.dumps(stats))
        assert request("/logout", {"csrf_token": token(body)})[2].endswith("/login")
        assert request("/")[2].split("?", 1)[0].endswith("/login")
    print("Live HTTP verification passed")


if __name__ == "__main__":
    main()
