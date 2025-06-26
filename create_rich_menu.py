import requests
import json


# 🔐 Channel Access Token จาก LINE Developer Console
CHANNEL_ACCESS_TOKEN = '0wrW85zf5NXhGWrHRjwxitrZ33JPegxtB749lq9TWRlrlCvfl0CKN9ceTw+kzPqBc6yjEOlV3EJOqUsBNhiFGQu3asN1y6CbHIAkJINhHNWi5gY9+O3+SnvrPaZzI7xbsBuBwe8XdIx33wdAN+79bgdB04t89/1O/w1cDnyilFU='

headers = {
    'Authorization': f'Bearer {CHANNEL_ACCESS_TOKEN}',
    'Content-Type': 'application/json'
}

# 📐 ข้อมูล Rich Menu (4 ปุ่ม)
rich_menu_data = {
    "size": {"width": 2500, "height": 1686},
    "selected": True,
    "name": "MainMenu",
    "chatBarText": "เมนูหลัก",
    "areas": [
        # ปุ่ม 1: เข้าเว็บไซต์ (แถวบน เต็มความกว้าง)
        {
            "bounds": {"x": 0, "y": 0, "width": 2500, "height": 843},
            "action": {
                "type": "uri",
                "uri": "https://git.or.th/th/home"
            }
        },
        # ปุ่ม 2: เช็กสถานะ (แถวล่าง ซ้าย)
        {
            "bounds": {"x": 0, "y": 843, "width": 833, "height": 843},
            "action": {"type": "message", "text": "เช็กสถานะ"}
        },
        # ปุ่ม 3: ติดต่อเจ้าหน้าที่ (แถวล่าง กลาง)
        {
            "bounds": {"x": 834, "y": 843, "width": 833, "height": 843},
            "action": {"type": "message", "text": "ติดต่อเจ้าหน้าที่"}
        },
        # ปุ่ม 4: แจ้งปัญหา (แถวล่าง ขวา)
        {
            "bounds": {"x": 1667, "y": 843, "width": 833, "height": 843},
            "action": {"type": "message", "text": "แจ้งปัญหา"}
        }
    ]
}

# ▶️ สร้าง Rich Menu
print("📌 กำลังสร้าง Rich Menu...")
res = requests.post(
    'https://api.line.me/v2/bot/richmenu',
    headers=headers,
    data=json.dumps(rich_menu_data)
)

if res.status_code == 200:
    rich_menu_id = res.json()['richMenuId']
    print(f"✅ Rich Menu ID: {rich_menu_id}")
else:
    print("❌ สร้าง Rich Menu ไม่สำเร็จ:", res.status_code, res.text)
    exit()

# 🖼️ อัปโหลดภาพเมนู
IMAGE_PATH = 'menu.png'
print("🖼️ กำลังอัปโหลดภาพเมนู...")

with open(IMAGE_PATH, 'rb') as image_file:
    image_headers = {
        'Authorization': f'Bearer {CHANNEL_ACCESS_TOKEN}',
        'Content-Type': 'image/png'
    }
    image_res = requests.post(
        f'https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content',
        headers=image_headers,
        data=image_file
    )

if image_res.status_code == 200:
    print("✅ อัปโหลดภาพเมนูสำเร็จ")
else:
    print("❌ อัปโหลดภาพเมนูไม่สำเร็จ:", image_res.status_code, image_res.text)
    exit()

# 🔗 ผูก Rich Menu กับผู้ใช้ทุกคน
print("📎 กำลังผูก Rich Menu กับผู้ใช้ทุกคน...")
link_res = requests.post(
    f"https://api.line.me/v2/bot/user/all/richmenu/{rich_menu_id}",
    headers={'Authorization': f'Bearer {CHANNEL_ACCESS_TOKEN}'}
)

if link_res.status_code == 200:
    print("✅ ผูกเมนูให้ผู้ใช้ทุกคนเรียบร้อยแล้ว")
else:
    print("❌ ผูกเมนูไม่สำเร็จ:", link_res.status_code, link_res.text)
