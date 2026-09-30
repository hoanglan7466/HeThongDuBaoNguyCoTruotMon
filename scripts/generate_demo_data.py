"""Generate the deterministic DEMO CSV used by the local application."""
import csv
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "demo_data" / "du_lieu_sinh_vien_demo.csv"
LARGE_OUTPUT = ROOT / "demo_data" / "du_lieu_sinh_vien_mo_rong_480.csv"
MAJORS = [("CNTT", "Công nghệ thông tin"), ("QTKD", "Quản trị kinh doanh"), ("NNH", "Ngôn ngữ học")]
FAMILY_NAMES = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Vũ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương"]
MIDDLE_NAMES = ["Minh", "Thị", "Quang", "Khánh", "Thanh", "Ngọc", "Đức", "Gia", "Thu", "Hải", "Bảo", "Phương"]
GIVEN_NAMES = ["Anh", "Huy", "Vy", "Linh", "Long", "Trang", "Nam", "Mai", "Khoa", "Nhi", "Sơn", "Hà"]
COURSES = [("CS101", "Cơ sở dữ liệu"), ("ML201", "Học máy ứng dụng"), ("SK301", "Kỹ năng chuyên ngành")]

def main():
    rows = []
    index = 1
    for cohort in range(17, 21):
        for major_index, (major, major_name) in enumerate(MAJORS):
            class_name = f"{major} {cohort}-01"
            students = 3 if index <= 18 else 2  # 30 students total across every cohort and major.
            for student_offset in range(students):
                course_code, course_name = COURSES[major_index]
                week = index if index <= 4 else 5 + ((index - 5) % 6)
                score = round(4.2 + ((index * 7) % 52) / 10, 1)
                attendance = 1 + ((index * 9) % 10)
                late = (index * 3) % 6
                rows.append([
                    f"DEMO{index:03d}", student_name(index),
                    class_name, f"demo{index:03d}@example.invalid", course_code, course_name,
                    "HK1-2026", "Học kỳ 1 năm học 2026-2027", week, score, attendance, late,
                ])
                index += 1
    OUTPUT.parent.mkdir(exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["student_code", "full_name", "class_name", "email", "course_code", "course_name", "semester_code", "semester_name", "current_week", "score", "attendance_rate", "late_submissions"])
        writer.writerows(rows)
    print(f"Generated {len(rows)} demo records: {OUTPUT}")

def student_name(index):
    return f"{FAMILY_NAMES[(index-1)%len(FAMILY_NAMES)]} {MIDDLE_NAMES[((index-1)//len(FAMILY_NAMES))%len(MIDDLE_NAMES)]} {GIVEN_NAMES[((index-1)//(len(FAMILY_NAMES)*len(MIDDLE_NAMES)))%len(GIVEN_NAMES)]}"

def generate_large():
    rows=[]; index=1; rng=random.Random(20260930); used_codes=set()
    for cohort in range(17,21):
        for major_index,(major,major_name) in enumerate(MAJORS):
            for class_number in range(1,5):
                for student_number in range(1,11):
                    course_code,course_name=COURSES[(major_index+class_number)%len(COURSES)]
                    week=5+((index-1)%6); score=round(3.5+((index*13)%62)/10,1)
                    student_code=f"{cohort}{rng.randrange(10_000_000,100_000_000):08d}"
                    while student_code in used_codes:
                        student_code=f"{cohort}{rng.randrange(10_000_000,100_000_000):08d}"
                    used_codes.add(student_code)
                    rows.append([student_code,student_name(index),f"{major} {cohort}-{class_number:02d}",f"sv{student_code}@example.invalid",course_code,course_name,"HK1-2026","Học kỳ 1 năm học 2026-2027",week,score,1+((index*7)%10),(index*5)%7]); index+=1
    with LARGE_OUTPUT.open("w",newline="",encoding="utf-8-sig") as handle:
        writer=csv.writer(handle); writer.writerow(["student_code","full_name","class_name","email","course_code","course_name","semester_code","semester_name","current_week","score","attendance_rate","late_submissions"]); writer.writerows(rows)
    print(f"Generated {len(rows)} extended records: {LARGE_OUTPUT}")

if __name__ == "__main__":
    main()
