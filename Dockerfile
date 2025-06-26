# ใช้ Python เป็น base image
FROM python:3.11

# ตั้ง working directory
WORKDIR /app

# คัดลอก requirements และติดตั้ง
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# คัดลอกไฟล์โปรเจกต์ทั้งหมด
COPY . .

# เปิด port สำหรับ Flask (ถ้าใช้ default คือ 5000)
EXPOSE 3000

# รันแอป
CMD ["python", "app.py"]
