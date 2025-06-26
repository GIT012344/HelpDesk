from flask import Flask, request, jsonify
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
from urllib.parse import parse_qs
import traceback
import re
import os
import psycopg2
from psycopg2.extras import RealDictCursor

CONTACT_STATE = "contact_conversation"

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = 'RF7HySsgh8pRmAW3UgwHu4fZ7WWyokBrrs1Ewx7tt8MJ47eFqlnZ4eOZnEg2UFZH++4ZW0gfRK/MLynU0kANOEq23M4Hqa6jdGGWeDO75TuPEEZJoHOw2yabnaSDOfhtXc9GzZdXW8qoVqFnROPhegdB04t89/1O/w1cDnyilFU='

LINE_HEADERS = {
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {CHANNEL_ACCESS_TOKEN}'
}

user_states = {}

# ฟังก์ชันช่วยเหลือใหม่
def info_row(label, value):
    return {
        "type": "box",
        "layout": "horizontal",
        "contents": [
            {
                "type": "text",
                "text": label,
                "size": "sm",
                "color": "#AAAAAA",
                "flex": 2
            },
            {
                "type": "text",
                "text": value if value else "-",
                "size": "sm",
                "wrap": True,
                "flex": 4
            }
        ]
    }

def status_row(label, value, color):
    return {
        "type": "box",
        "layout": "horizontal",
        "margin": "md",
        "contents": [
            {
                "type": "text",
                "text": label,
                "size": "sm",
                "color": "#AAAAAA",
                "flex": 2
            },
            {
                "type": "text",
                "text": value,
                "size": "sm",
                "color": color,
                "weight": "bold",
                "flex": 4
            }
        ]
    }

@app.route("/", methods=["GET"])
def home():
    return "✅ LINE Helpdesk is running.", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        # First check if there's any data
        if not request.data:
            return jsonify({"status": "error", "message": "No data received"}), 400

        try:
            payload = request.get_json()
        except Exception as e:
            print(f"❌ JSON decode error: {str(e)}")
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400

        if payload is None:
            return jsonify({"status": "error", "message": "Empty JSON"}), 400

        events = payload.get('events', [])
        
        for event in events:
            if event.get('type') == 'message' and event['message'].get('type') == 'text':
                handle_text_message(event)
            elif event.get('type') == 'postback':
                handle_postback(event)
                
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print("❌ ERROR in webhook():", e)
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

def handle_postback(event):
    data = event['postback']['data']
    params = event['postback'].get('params', {})
    reply_token = event['replyToken']
    user_id = event['source']['userId']
    
    # แยกข้อมูลจาก postback data
    from urllib.parse import parse_qs
    data_dict = parse_qs(data)
    
    if 'action' in data_dict:
        action = data_dict['action'][0]
        
        if action == "select_date":
            selected_date = params.get('date', '')
            if selected_date:
                selected_datetime = datetime.strptime(selected_date, "%Y-%m-%d")
                today = datetime.now().date()
                if selected_datetime.date() < today:
                    reply(reply_token, "⚠️ กรุณาเลือกวันใหม่ ไม่สามารถเลือกวันนี้ได้ โปรดดเลือกวันที่เป็นปัจจุบันหรืออนาคต")
                    return
                # แปลงรูปแบบวันที่เพื่อแสดงผล
                formatted_date = selected_datetime.strftime("%d/%m/%Y")
                user_states[user_id]["selected_date"] = selected_date
                send_time_picker(reply_token, formatted_date)
                
        if action == "view_history":
            selected_date = params.get('date', '')
            ticket_id = data_dict.get('ticket_id', [''])[0]
            if selected_date:
                show_monthly_history(reply_token, user_id, selected_date, ticket_id)


def show_monthly_history(reply_token, user_id, selected_date, ticket_id=None):
    """แสดงประวัติ Ticket รายเดือน"""
    try:
        # แปลงวันที่ที่เลือกเป็นรูปแบบเดือน-ปี
        selected_month = datetime.strptime(selected_date, "%Y-%m-%d").strftime("%Y-%m")
        
        # ดึงข้อมูล Ticket ทั้งหมดของผู้ใช้
        user_tickets = get_all_user_tickets(user_id)
        
        if not user_tickets:
            reply(reply_token, f"⚠️ ไม่พบ Ticket ในเดือน {selected_month}")
            return
        
        # กรอง Ticket เฉพาะเดือนที่เลือก
        monthly_tickets = [
            t for t in user_tickets 
            if t['date'].startswith(selected_month) and t['date'] != 'ไม่มีข้อมูล'
        ]
        
        if not monthly_tickets:
            reply(reply_token, f"⚠️ ไม่พบ Ticket ในเดือน {selected_month}")
            return
        
        # สร้าง Flex Message สำหรับแสดงผล
        bubbles = []
        for ticket in monthly_tickets[:10]:  # แสดงสูงสุด 10 Ticket ต่อเดือน
            status_color = "#1DB446" if ticket['status'] == "Completed" else "#FF0000" if ticket['status'] == "Rejected" else "#005BBB"
            
            try:
                ticket_date = datetime.strptime(ticket['date'], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M")
            except:
                ticket_date = ticket['date']
            
            bubble = {
                "type": "bubble",
                "size": "kilo",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"📅 {ticket_date}",
                            "weight": "bold",
                            "size": "sm"
                        }
                    ]
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        info_row("Ticket ID", ticket['ticket_id']),
                        info_row("ประเภท", ticket['type']),
                        status_row("สถานะ", ticket['status'], status_color)
                    ]
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "button",
                            "action": {
                                "type": "message",
                                "label": "ดูรายละเอียด",
                                "text": f"ดูรายละเอียด {ticket['ticket_id']}"
                            },
                            "style": "primary",
                            "color": "#005BBB"
                        }
                    ]
                }
            }
            bubbles.append(bubble)
        
        # สร้างข้อความสรุป
        summary_text = {
            "type": "text",
            "text": f"📊 พบ {len(monthly_tickets)} Ticket ในเดือน {selected_month}",
            "wrap": True
        }
        
        # สร้าง Flex Message แบบ Carousel
        if len(bubbles) > 1:
            flex_message = {
                "type": "flex",
                "altText": f"ประวัติ Ticket เดือน {selected_month}",
                "contents": {
                    "type": "carousel",
                    "contents": bubbles
                }
            }
        else:
            flex_message = {
                "type": "flex",
                "altText": f"ประวัติ Ticket เดือน {selected_month}",
                "contents": bubbles[0]
            }
        
        # ส่งข้อความสรุปและ Flex Message
        send_reply_message(reply_token, [summary_text, flex_message])
        
    except Exception as e:
        print("❌ Error in show_monthly_history:", str(e))
        traceback.print_exc()
        reply(reply_token, "⚠️ เกิดข้อผิดพลาดในการดึงข้อมูลประวัติ")

def handle_text_message(event):
    user_message = event['message']['text'].strip()
    reply_token = event['replyToken']
    user_id = event['source']['userId']
    
    reset_keywords = ["สมัครสมาชิก", "เช็กสถานะ", "ติดต่อเจ้าหน้าที่", "ยกเลิก"]
    if user_id in user_states and any(user_message.startswith(k) for k in reset_keywords):
        del user_states[user_id]
    
    
    if user_message.startswith(("confirm_", "cancel_")):
        handle_confirmation(event)
        return
    
    if user_id in user_states and user_states[user_id].get("step") == CONTACT_STATE:
        if not check_existing_user(user_id):
            del user_states[user_id]
            reply(reply_token, "⚠️ กรุณาสมัครสมาชิกหรือเข้าสู่ระบบใหม่")
            return
        
        if user_message.lower() in ["end", "จบ", "หยุด", "เสร็จสิ้น", "แจ้งปัญหา", "ยกเลิก","เช็กสถานะ"]:
            del user_states[user_id]
            reply(reply_token, "✅ การสนทนากับเจ้าหน้าที่ได้สิ้นสุดลง ขอบคุณที่ใช้บริการ")
            return
        else:
            # บันทึกข้อมูลชั่วคราวก่อน confirm
            user_states[user_id]["contact_message"] = user_message
            user_states[user_id]["step"] = "pre_contact"
            
            # ส่ง Confirm Message
            confirm_msg = create_confirm_message(
                "contact",
                f"ข้อความ: {user_message}"
            )
            send_reply_message(reply_token, [confirm_msg])
            return
        
    if user_message == "ติดต่อเจ้าหน้าที่":
        if not check_existing_user(user_id):
            reply(reply_token, "⚠️ กรุณาสมัครสมาชิกหรือเข้าสู่ระบบก่อนใช้งานบริการนี้")
            return
        
        user_states[user_id] = {
            "step": CONTACT_STATE,
            "service_type": "Contact",
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        quick_reply = {
            "type": "text",
            "text": "พิมพ์ข้อความที่ต้องการส่งถึงเจ้าหน้าที่ ผ่านช่อง chat",
            "quickReply": {
                "items": [{
                    "type": "action",
                    "action": {
                        "type": "message",
                        "label": "จบการสนทนา",
                        "text": "จบ"
                    }
                }]
            }
        }
        send_reply_message(reply_token, [quick_reply])
        return
    
    if is_valid_email(user_message):
        if check_existing_email(user_message):
            reply(reply_token, "เข้าสู่ระบบสำเร็จแล้วค่ะ")
            send_flex_choice(user_id)
            return

    if user_id in user_states:
        if user_states[user_id].get("step") == "ask_request":
            handle_user_request(reply_token, user_id, user_message)
            return
        if user_states[user_id].get("step") == "ask_helpdesk_issue":
            handle_helpdesk_issue(reply_token, user_id, user_message)
            return
        if user_states[user_id].get("step") == "ask_appointment" and "selected_date" in user_states[user_id]:
            if user_message == "กรอกเวลาเอง":
                reply(reply_token, "กรุณากรอกเวลาที่ต้องการในรูปแบบ HH:MM-HH:MM\nเช่น 11:30-12:45")
                return
            elif re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", user_message):
                start_time, end_time = user_message.split('-')
                if validate_time(start_time) and validate_time(end_time):
                    if is_time_before(start_time, end_time):
                        selected_date = user_states[user_id]["selected_date"]
                        appointment_datetime = f"{selected_date} {user_message}"
                        handle_save_appointment(reply_token, user_id, appointment_datetime)
                    else:
                        reply(reply_token, "⚠️ เวลาเริ่มต้นต้องน้อยกว่าเวลาสิ้นสุด")
                else:
                    reply(reply_token, "⚠️ รูปแบบเวลาไม่ถูกต้อง กรุณากรอกในรูปแบบ HH:MM-HH:MM\nเช่น 11:30-12:45")
                return
                
        handle_user_state(reply_token, user_id, user_message)
        return
    
    if user_message == "แจ้งปัญหา":
        handle_report_issue(reply_token, user_id)
    elif user_message == "ยกเลิก":
        handle_cancel(reply_token, user_id)
    elif user_message == "เช็กสถานะ" or user_message == "ดู Ticket ล่าสุด":
        check_latest_ticket(reply_token, user_id)
    elif user_message.startswith("สมัครสมาชิก"):
        handle_register(reply_token, user_id, user_message)
    elif user_message == "นัดหมายเวลา":
        handle_appointment(reply_token, user_id)
    elif user_message == "Helpdesk":
        handle_helpdesk(reply_token, user_id)
    elif user_message.startswith("นัดหมายเวลา ") or user_message.startswith("กรอกเวลานัดหมายเอง"):
        handle_appointment_time(reply_token, user_id, user_message)
    elif re.search(r"TICKET-\d{14}", user_message):
        match = re.search(r"(TICKET-\d{14})", user_message)
        ticket_id = match.group(1)
        show_ticket_details(reply_token, ticket_id, user_id)
    elif user_message.startswith("ดูรายละเอียด "):
        ticket_id = user_message.replace("ดูรายละเอียด ", "").strip()
        show_ticket_details(reply_token, ticket_id, user_id)
    else:
        reply(reply_token, "📌 ไปที่เมนูและเลือกบริการ")

def handle_confirmation(event):
    """จัดการการยืนยันจากผู้ใช้"""
    user_message = event['message']['text'].strip()
    reply_token = event['replyToken']
    user_id = event['source']['userId']
    
    if user_id not in user_states:
        reply(reply_token, "⚠️ เกิดข้อผิดพลาด กรุณาเริ่มกระบวนการใหม่")
        return
    
    if user_message.startswith("confirm_"):
        action_type = user_message.replace("confirm_", "")
        state = user_states[user_id]
        
        try:
            if action_type == "helpdesk" and state.get("step") == "pre_helpdesk":
                # ดึงข้อมูลผู้ใช้จาก Ticket ล่าสุดถ้าไม่มีใน state
                if "email" not in state:
                    latest_ticket = get_latest_ticket(user_id)
                    if latest_ticket:
                        state["email"] = latest_ticket.get('อีเมล', '')
                        state["name"] = latest_ticket.get('ชื่อ', '')
                        state["phone"] = latest_ticket.get('เบอร์ติดต่อ', '')
                        state["department"] = latest_ticket.get('แผนก', '')
                
                # สร้าง Ticket ตามระบบเดิม
                ticket_id = generate_ticket_id()
                success = save_helpdesk_to_sheet(
                    ticket_id,
                    user_id,
                    state.get("email", ""),
                    state.get("name", ""),
                    state.get("phone", ""),
                    state.get("department", ""),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    state.get("issue_text", "")
                )
                
                if success:
                    send_helpdesk_summary(user_id, ticket_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                                        state.get("issue_text", ""), state.get("email", ""), 
                                        state.get("name", ""), state.get("phone", ""), 
                                        state.get("department", ""))
                    reply(reply_token, f"✅ แจ้งปัญหาเรียบร้อย\nTicket ID ของคุณคือ: {ticket_id} \n โปรดรอการตอบกลับ ")
                else:
                    reply(reply_token, "❌ เกิดปัญหาในการบันทึกข้อมูล")
                
                del user_states[user_id]
                
            elif action_type == "service" and state.get("step") == "pre_service":
                # ดึงข้อมูลผู้ใช้จาก Ticket ล่าสุดถ้าไม่มีใน state
                if "email" not in state:
                    latest_ticket = get_latest_ticket(user_id)
                    if latest_ticket:
                        state["email"] = latest_ticket.get('อีเมล', '')
                        state["name"] = latest_ticket.get('ชื่อ', '')
                        state["phone"] = latest_ticket.get('เบอร์ติดต่อ', '')
                        state["department"] = latest_ticket.get('แผนก', '')
                
                # สร้าง Ticket ตามระบบเดิม
                ticket_id = generate_ticket_id()
                success = save_appointment_with_request(
                    ticket_id,
                    user_id,
                    state.get("email", ""),
                    state.get("name", ""),
                    state.get("phone", ""),
                    state.get("department", ""),
                    state.get("appointment_datetime", ""),
                    state.get("request_text", "")
                )
                
                if success:
                    send_ticket_summary_with_request(
                        user_id, ticket_id, state.get("appointment_datetime", ""), 
                        state.get("request_text", ""), state.get("email", ""), 
                        state.get("name", ""), state.get("phone", ""), 
                        state.get("department", "")
                    )
                    reply(reply_token, f"✅\nTicket ID ขอคุณคือ: {ticket_id} \n โปรดรอการตอบกลับ")
                else:
                    reply(reply_token, "❌ เกิดปัญหาในการบันทึกข้อมูล")
                
                del user_states[user_id]
                
            elif action_type == "contact" and state.get("step") == "pre_contact":
                # สำหรับการติดต่อเจ้าหน้าที่ ไม่จำเป็นต้องมี email ใน state
                save_contact_message(user_id, state.get("contact_message", ""), is_user=True)
                reply(reply_token, "📩 ข้อความของคุณถูกส่งถึงเจ้าหน้าที่แล้ว รอการตอบกลับ")
                del user_states[user_id]
                
        except Exception as e:
            print(f"❌ Error in handle_confirmation: {str(e)}")
            traceback.print_exc()
            reply(reply_token, "⚠️ เกิดข้อผิดพลาดในการดำเนินการ")
            if user_id in user_states:
                del user_states[user_id]
                
    elif user_message.startswith("cancel_"):
        if user_id in user_states:
            del user_states[user_id]
        reply(reply_token, "❌ การดำเนินการถูกยกเลิก")

def save_contact_message(user_id, message, is_user=False, is_system=False):
    """บันทึกข้อความใน Textbox พร้อมระบุประเภท"""
    try:
        scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        client = gspread.authorize(creds)
        sheet = client.open("Tickets").sheet1
        
        # หาแถวที่มี User ID นี้อยู่
        cell = sheet.find(str(user_id))
        if not cell:
            print(f"❌ ไม่พบผู้ใช้ {user_id} ในระบบ")
            return False
        
        # อ่านค่าเดิมและเพิ่มข้อความใหม่
        current_text = sheet.cell(cell.row, 13).value or ""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        new_text = f"{message}"
        
        # ตัดข้อความให้เหลือไม่เกิน 50000 ตัวอักษร
        if len(new_text) > 50000:
            new_text = new_text[-50000:]
        
        sheet.update_cell(cell.row, 13, new_text)
        
        # อัพเดทสถานะเป็น "กำลังดำเนินการ" หากเป็นข้อความจากผู้ใช้
        if is_user:
            sheet.update_cell(cell.row, 8, "None")
        
        return True
    except Exception as e:
        print(f"❌ Error saving contact message: {e}")
        traceback.print_exc()
        return False

def save_contact_request(user_id, message):
    """บันทึกคำขอติดต่อเจ้าหน้าที่ลง Google Sheet"""
    try:
        scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        client = gspread.authorize(creds)
        sheet = client.open("Tickets").sheet1
        
        # หาแถวที่มี User ID นี้อยู่
        cell = sheet.find(user_id)
        if not cell:
            return False
        
        # อัพเดทคอลัมน์ Textbox (คอลัมน์ที่ 13)
        current_text = sheet.cell(cell.row, 13).value or ""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_text = f"{current_text}[ผู้ใช้]{timestamp}: {message}"
        
        # ตัดข้อความให้เหลือไม่เกิน 50000 ตัวอักษร (Limit ของ Google Sheets)
        if len(new_text) > 50000:
            new_text = new_text[-50000:]
        
        sheet.update_cell(cell.row, 13, new_text)
        print(f"✅ บันทึกคำขอติดต่อเจ้าหน้าที่สำหรับ User ID: {user_id}")
        return True
    except Exception as e:
        print("❌ Error saving contact request:", e)
        traceback.print_exc()
        return False

def validate_time(time_str):
    """ตรวจสอบว่าเวลาในรูปแบบ HH:MM ถูกต้อง"""
    try:
        hours, minutes = map(int, time_str.split(':'))
        if 0 <= hours < 24 and 0 <= minutes < 60:
            return True
        return False
    except:
        return False

def handle_appointment_time(reply_token, user_id, user_message):
    # ดึงข้อมูลจาก state
    state = user_states[user_id]
    ticket_id = state["ticket_id"]
    
    # แยกเวลาจากข้อความ
    if user_message.startswith("นัดหมายเวลา "):
        appointment_time = user_message.replace("นัดหมายเวลา ", "").strip()
    elif user_message == "กรอกเวลานัดหมายเอง":
        reply(reply_token, "กรุณากรอกเวลานัดหมายในรูปแบบ HH:MM-HH:MM เช่น 13:00-14:00")
        return
    else:
        # ตรวจสอบรูปแบบเวลา
        if not re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", user_message):
            reply(reply_token, "⚠️ กรุณากรอกเวลาในรูปแบบ HH:MM-HH:MM เช่น 13:00-14:00")
            return
        appointment_time = user_message
    
    # บันทึกลง Google Sheet
    success = save_appointment_to_sheet(ticket_id, appointment_time)
    if success:
        reply(reply_token, f"✅ นัดหมายเวลา {appointment_time} สำเร็จสำหรับ Ticket {ticket_id}")
        
        # ส่งสรุปการนัดหมาย
        send_appointment_summary(user_id, ticket_id, appointment_time)
    else:
        reply(reply_token, "❌ เกิดปัญหาในการบันทึกเวลานัดหมาย")
    
    del user_states[user_id]

def send_appointment_summary(user_id, ticket_id, appointment_datetime):
    try:
        # แยกข้อมูลวันที่และเวลา
        date_part, time_range = appointment_datetime.split()
        start_time, end_time = time_range.split('-')
        
        # แปลงรูปแบบวันที่
        dt = datetime.strptime(date_part, "%Y-%m-%d")
        formatted_date = dt.strftime("%d/%m/%Y")
        
        flex_message = {
            "type": "flex",
            "altText": f"สรุปการนัดหมาย {ticket_id}",
            "contents": {
                "type": "bubble",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "text",
                            "text": "✅ นัดหมายบริการเรียบร้อย",
                            "weight": "bold",
                            "size": "lg",
                            "color": "#1DB446"
                        }
                    ]
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        {"type": "text", "text": f"Ticket ID: {ticket_id}", "wrap": True, "size": "sm"},
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "วันที่:",
                                    "size": "sm",
                                    "color": "#AAAAAA",
                                    "flex": 2
                                },
                                {
                                    "type": "text",
                                    "text": formatted_date,
                                    "size": "sm",
                                    "flex": 4
                                }
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "เวลา:",
                                    "size": "sm",
                                    "color": "#AAAAAA",
                                    "flex": 2
                                },
                                {
                                    "type": "text",
                                    "text": f"{start_time} - {end_time}",
                                    "size": "sm",
                                    "flex": 4
                                }
                            ]
                        },
                        {"type": "text", "text": "กรุณามาตรงเวลานะคะ", "wrap": True, "size": "sm", "margin": "md"}
                    ]
                }
            }
        }

        body = {
            "to": user_id,
            "messages": [flex_message]
        }

        res = requests.post('https://api.line.me/v2/bot/message/push', headers=LINE_HEADERS, json=body)
        print("📤 Sent Appointment Summary:", res.status_code, res.text)
    except Exception as e:
        print("❌ Error sending Appointment Summary:", e)
        traceback.print_exc()

def handle_user_state(reply_token, user_id, user_message):
    state = user_states[user_id]
    step = state.get("step")

    if step == "ask_issue":
        handle_ask_issue(reply_token, user_id, user_message, state)
    elif step == "ask_category":
        handle_ask_category(reply_token, user_id, user_message, state)
    elif step == "ask_department":
        handle_ask_department(reply_token, user_id, user_message, state)
    elif step == "ask_phone":
        handle_ask_phone(reply_token, user_id, user_message, state)

def handle_ask_issue(reply_token, user_id, user_message, state):
    email = user_message
    if not is_valid_email(email):
        reply(reply_token, "⚠️ กรุณากรอกอีเมลให้ถูกต้อง เช่น example@domain.com")
        return
    if check_existing_email(email):
        reply(reply_token, "⚠️ อีเมลนี้มีการสมัครสมาชิกแล้ว")
        send_flex_choice(user_id)
        del user_states[user_id]
        return
    
    state["issue"] = email
    state["step"] = "ask_category"
    reply(reply_token, "📂 กรุณาบอกชื่อผู้ใช้")

def handle_ask_category(reply_token, user_id, user_message, state):
    state["category"] = user_message
    state["step"] = "ask_department"
    send_department_flex_message(reply_token)

def handle_ask_department(reply_token, user_id, user_message, state):
    if user_message in ["ผู้บริหาร/เลขานุการ", "ส่วนงานตรวจสอบภายใน", "ส่วนงานกฏหมาย", "งานสื่อสารองค์การ", "ฝ่ายนโยบายและแผน", "ฝ่ายเทคโนโลยีสารสนเทศ", "ฝ่ายบริหาร","ฝ่ายบริหารวิชาการและพัฒนาผู้ประกอบการ", "ฝ่ายตรวจสอบโลหะมีค่า", "ฝ่ายตรวจสอบอัญมณีและเครื่องประดับ", "ฝ่ายวิจัยและพัฒนามาตรฐาน", "ฝ่ายพัฒนาธุรกิจ"]:
        state["department"] = user_message
        state["step"] = "ask_phone"
        reply(reply_token, "📞 กรุณาบอกเบอร์ติดต่อกลับ")
    else:
        reply(reply_token, "กรอกแผนกที่ต้องการ เช่น ผู้บริหาร/เลขานุการ, ส่วนงานตรวจสอบภายใน, ส่วนงานกฏหมาย, งานสื่อสารองค์การ, ฝ่ายนโยบายและแผน, ฝ่ายเทคโนโลยีสารสนเทศ, ฝ่ายบริหาร,ฝ่ายบริหารวิชาการและพัฒนาผู้ประกอบการ, ฝ่ายตรวจสอบโลหะมีค่า, ฝ่ายตรวจสอบอัญมณีและเครื่องประดับ, ฝ่ายวิจัยและพัฒนามาตรฐาน, ฝ่ายพัฒนาธุรกิจ")
        send_department_quick_reply(reply_token)

def handle_ask_phone(reply_token, user_id, user_message, state):
    phone = user_message
    if not re.fullmatch(r"0\d{9}", phone):
        reply(reply_token, "⚠️ กรุณาระบุเบอร์ติดต่อ 10 หลักให้ถูกต้อง เช่น 0812345678")
        return

    state["phone"] = phone
    ticket_id = generate_ticket_id()
    success = save_ticket_to_sheet(user_id, state, ticket_id)
    if success:
        send_flex_ticket_summary(user_id, state, ticket_id)
        send_flex_choice(user_id)
    else:
        reply(reply_token, "❌ เกิดปัญหาในการบันทึกข้อมูลลง Google Sheet")
    del user_states[user_id]

def handle_report_issue(reply_token, user_id):
    if check_existing_user(user_id):
        reply(reply_token, "ยินดีให้บริการค่ะ/ครับ")
        send_flex_choice(user_id)
    else:
        user_states[user_id] = {"step": "ask_issue"}
        reply(reply_token, "📝 กรุณากรอกอีเมล")

def handle_cancel(reply_token, user_id):
    if user_id in user_states:
        del user_states[user_id]
    reply(reply_token, "❎ ยกเลิกการสมัครสมาชิกเรียบร้อยแล้ว")

def handle_register(line_bot_api, reply_token, user_id, user_message):
    parsed = parse_issue_message(user_message)
    if parsed:
        ticket_id = generate_ticket_id()
        
        # ใช้ Excel Online แทน Google Sheets
        success = save_ticket_to_excel_online(
            user_id,
            {
                'email': parsed.get('issue', ''),
                'name': parsed.get('category', ''),
                'phone': parsed.get('phone', ''),
                'department': parsed.get('department', '-'),
                'type': 'Information'
            },
            ticket_id
        )
        
        if success:
            reply_message(line_bot_api, reply_token, f"✅ สมัครสมาชิกเรียบร้อยแล้วค่ะ : {ticket_id}")
            send_flex_ticket_summary(line_bot_api, user_id, parsed, ticket_id)
        else:
            reply_message(line_bot_api, reply_token, "❌ เกิดปัญหาในการบันทึกข้อมูลลง Excel Online")
    else:
        reply_message(line_bot_api, reply_token, "⚠️ กรุณาระบุข้อมูลให้ครบถ้วน")

def check_latest_ticket(reply_token, user_id):
    """แสดงรายการ Ticket ทั้งหมดของผู้ใช้"""
    try:
        user_tickets = get_all_user_tickets(user_id)
        
        if not user_tickets:
            reply(reply_token, "⚠️ ไม่พบ Ticket ของคุณในระบบ")
            return
        
        # สร้าง Flex Message สำหรับแสดงรายการ Ticket
        bubbles = []
        for ticket in user_tickets:
            # กำหนดสีตามสถานะ
            status_color = "#1DB446" if ticket['status'] == "Completed" else "#FF0000" if ticket['status'] == "Rejected" else "#005BBB"
            
            # แสดงวันที่ในรูปแบบที่อ่านง่าย
            try:
                ticket_date = datetime.strptime(ticket['date'], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M")
            except:
                ticket_date = ticket['date']
            
            bubble = {
                "type": "bubble",
                "size": "kilo",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"📄 Ticket {ticket['ticket_id']}",
                            "weight": "bold",
                            "size": "md",
                            "color": "#005BBB"
                        }
                    ]
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        info_row("ประเภท", ticket['type']),
                        info_row("วันที่แจ้ง", ticket_date),
                        status_row("สถานะ", ticket['status'], status_color)
                    ]
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "button",
                            "action": {
                                "type": "message",
                                "label": "ดูรายละเอียด",
                                "text": f"ดูรายละเอียด {ticket['ticket_id']}"
                            },
                            "style": "primary",
                            "color": "#005BBB"
                        },
                        {
                            "type": "button",
                            "action": {
                                "type": "datetimepicker",
                                "label": "ดูประวัติย้อนหลัง",
                                "data": f"action=view_history&ticket_id={ticket['ticket_id']}",
                                "mode": "date",
                                "initial": datetime.now().strftime("%Y-%m-01"),
                                "max": datetime.now().strftime("%Y-%m-%d")
                            },
                            "style": "secondary",
                            "color": "#5DADE2",
                            "margin": "sm"
                        }
                    ]
                }
            }
            bubbles.append(bubble)
        
        # สร้างข้อความแนะนำการใช้งาน
        guide_message = {
            "type": "text",
            "text": "📌 คุณสามารถดูประวัติ Ticket ย้อนหลังได้โดยกดปุ่ม 'ดูประวัติย้อนหลัง' และเลือกเดือนที่ต้องการ",
            "wrap": True
        }
        
        # สร้าง Flex Message แบบ Carousel ถ้ามีหลาย Ticket
        if len(bubbles) > 1:
            flex_message = {
                "type": "flex",
                "altText": "รายการ Ticket ของคุณ",
                "contents": {
                    "type": "carousel",
                    "contents": bubbles[:10]  # แสดงสูงสุด 10 Ticket
                }
            }
        else:
            flex_message = {
                "type": "flex",
                "altText": "รายการ Ticket ของคุณ",
                "contents": bubbles[0]
            }
        
        # ส่งทั้งข้อความแนะนำและ Flex Message
        send_reply_message(reply_token, [guide_message, flex_message])
        
    except Exception as e:
        print("❌ Error in check_latest_ticket:", str(e))
        traceback.print_exc()
        reply(reply_token, "⚠️ เกิดข้อผิดพลาดในการดึงข้อมูล Ticket")

def show_ticket_details(reply_token, ticket_id, user_id=None):
    """แสดงรายละเอียดของ Ticket ที่เลือก"""
    try:
        scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        client = gspread.authorize(creds)
        sheet = client.open("Tickets").sheet1
        data = sheet.get_all_records()
        
        # ค้นหา Ticket ที่ตรงกับ ticket_id
        found_ticket = None
        for row in data:
            if row.get('Ticket ID') == ticket_id or row.get('หมายเลข Ticket') == ticket_id:
                # ตรวจสอบว่าเป็น Ticket ของผู้ใช้ที่ขอรายละเอียด (ถ้ามี user_id)
                if not user_id or str(row.get('User ID', '')).strip() == str(user_id).strip():
                    # จัดรูปแบบเบอร์โทรศัพท์
                    phone = str(row.get('เบอร์ติดต่อ', ''))
                    phone = phone.replace("'", "")
                    if phone and not phone.startswith('0'):
                        phone = '0' + phone[-9:]
                    
                    found_ticket = {
                        'ticket_id': row.get('Ticket ID', 'TICKET-UNKNOWN'),
                        'email': row.get('อีเมล', 'ไม่มีข้อมูล'),
                        'name': row.get('ชื่อ', 'ไม่มีข้อมูล'),
                        'phone': phone,
                        'department': row.get('แผนก', 'ไม่มีข้อมูล'),
                        'date': row.get('วันที่แจ้ง', 'ไม่มีข้อมูล'),
                        'status': row.get('สถานะ', 'Pending'),
                        'appointment': row.get('Appointment', 'None'),
                        'requeste': row.get('Requeste', 'None'),
                        'report': row.get('Report', 'None'),
                        'type': row.get('Type', 'ไม่ระบุ')
                    }
                    break
        
        if not found_ticket:
            reply(reply_token, f"⚠️ ไม่พบ Ticket {ticket_id} ในระบบ")
            return
        
        # สร้าง Flex Message สำหรับแสดงรายละเอียด Ticket
        flex_message = create_ticket_flex_message(found_ticket)
        if not flex_message:
            reply(reply_token, "⚠️ เกิดข้อผิดพลาดในการสร้าง Ticket Summary")
            return
            
        send_reply_message(reply_token, [flex_message])
        
    except Exception as e:
        print("❌ Error in show_ticket_details:", str(e))
        traceback.print_exc()
        reply(reply_token, "⚠️ เกิดข้อผิดพลาดในการดึงข้อมูล Ticket")

def show_ticket_details(reply_token, ticket_id, user_id=None):
    """แสดงรายละเอียดของ Ticket ที่เลือก"""
    try:
        scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        client = gspread.authorize(creds)
        sheet = client.open("Tickets").sheet1
        data = sheet.get_all_records()
        
        # ค้นหา Ticket ที่ตรงกับ ticket_id
        found_ticket = None
        for row in data:
            if row.get('Ticket ID') == ticket_id or row.get('หมายเลข Ticket') == ticket_id:
                # ตรวจสอบว่าเป็น Ticket ของผู้ใช้ที่ขอรายละเอียด (ถ้ามี user_id)
                if not user_id or str(row.get('User ID', '')).strip() == str(user_id).strip():
                    # จัดรูปแบบเบอร์โทรศัพท์
                    phone = str(row.get('เบอร์ติดต่อ', ''))
                    phone = phone.replace("'", "")
                    if phone and not phone.startswith('0'):
                        phone = '0' + phone[-9:]
                    
                    found_ticket = {
                        'ticket_id': row.get('Ticket ID', 'TICKET-UNKNOWN'),
                        'email': row.get('อีเมล', 'ไม่มีข้อมูล'),
                        'name': row.get('ชื่อ', 'ไม่มีข้อมูล'),
                        'phone': phone,
                        'department': row.get('แผนก', 'ไม่มีข้อมูล'),
                        'date': row.get('วันที่แจ้ง', 'ไม่มีข้อมูล'),
                        'status': row.get('สถานะ', 'Pending'),
                        'appointment': row.get('Appointment', 'None'),
                        'requeste': row.get('Requeste', 'None'),
                        'report': row.get('Report', 'None'),
                        'type': row.get('Type', 'ไม่ระบุ')
                    }
                    break
        
        if not found_ticket:
            reply(reply_token, f"⚠️ ไม่พบ Ticket {ticket_id} ในระบบ")
            return
        
        # สร้าง Flex Message สำหรับแสดงรายละเอียด Ticket
        flex_message = create_ticket_flex_message(found_ticket)
        if not flex_message:
            reply(reply_token, "⚠️ เกิดข้อผิดพลาดในการสร้าง Ticket Summary")
            return
            
        send_reply_message(reply_token, [flex_message])
        
    except Exception as e:
        print("❌ Error in show_ticket_details:", str(e))
        traceback.print_exc()
        reply(reply_token, "⚠️ เกิดข้อผิดพลาดในการดึงข้อมูล Ticket")

def save_helpdesk_to_sheet(ticket_id, user_id, email, name, phone, department, report_time, appointment_time, issue_text):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        formatted_phone = format_phone_number(phone)
        cur.execute('''
            INSERT INTO tickets (
                ticket_id, user_id, email, name, phone, department, created_at, status, appointment, requeste, report, type
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            ticket_id,
            user_id,
            email,
            name,
            formatted_phone,
            department,
            report_time,
            "Pending",
            appointment_time,
            "None",
            issue_text if issue_text else "None",
            "Helpdesk"
        ))
        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ Saved Helpdesk ticket: {ticket_id} (PostgreSQL)")
        return True
    except Exception as e:
        print("❌ Error saving Helpdesk ticket (PostgreSQL):", e)
        traceback.print_exc()
        return False

def create_ticket_flex_message(ticket_data):
    try:
        status_color = "#1DB446" if ticket_data['status'] == "Completed" else "#FF0000" if ticket_data['status'] == "Rejected" else "#005BBB"
        
        # สร้างเนื้อหาหลักของ Flex Message
        contents = [
            info_row("ประเภท", ticket_data['type']),
            info_row("อีเมล", ticket_data['email']),
            info_row("ชื่อ", ticket_data['name']),
            info_row("เบอร์ติดต่อ", display_phone_number(ticket_data['phone'])),
            info_row("แผนก", ticket_data['department']),
            info_row("วันที่แจ้ง", ticket_data['date']),
            {
                "type": "separator",
                "margin": "md"
            }
        ]
        
        # เพิ่มข้อมูลตามประเภท Ticket
        if ticket_data['type'] == "Service":
            # สำหรับ Service Type - แสดง Requeste
            if ticket_data['requeste'] != "None":
                contents.append({
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {
                            "type": "text",
                            "text": "ความประสงค์:",
                            "size": "sm",
                            "color": "#AAAAAA",
                            "flex": 2
                        },
                        {
                            "type": "text",
                            "text": ticket_data['requeste'],
                            "size": "sm",
                            "wrap": True,
                            "flex": 4
                        }
                    ]
                })
            
            # แสดงเวลานัดหมายถ้ามี
            if ticket_data['appointment'] != "None":
                try:
                    date_part, time_range = ticket_data['appointment'].split()
                    dt = datetime.strptime(date_part, "%Y-%m-%d")
                    formatted_date = dt.strftime("%d/%m/%Y")
                    contents.append(info_row("วันนัดหมาย", formatted_date))
                    contents.append(info_row("ช่วงเวลา", time_range))
                except:
                    contents.append(info_row("วันและเวลานัดหมาย", ticket_data['appointment']))
        
        elif ticket_data['type'] == "Helpdesk":
            # สำหรับ Helpdesk Type - แสดง Report
            if ticket_data['report'] != "None":
                contents.append({
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {
                            "type": "text",
                            "text": "ปัญหาที่แจ้ง:",
                            "size": "sm",
                            "color": "#AAAAAA",
                            "flex": 2
                        },
                        {
                            "type": "text",
                            "text": ticket_data['report'],
                            "size": "sm",
                            "wrap": True,
                            "flex": 4
                        }
                    ]
                })
        
        # เพิ่มสถานะ
        contents.append(status_row("สถานะ", ticket_data['status'], status_color))
        
        return {
            "type": "flex",
            "altText": f"รายละเอียด Ticket {ticket_data['ticket_id']}",
            "contents": {
                "type": "bubble",
                "size": "kilo",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"📄 Ticket {ticket_data['ticket_id']}",
                            "weight": "bold",
                            "size": "lg",
                            "color": "#005BBB",
                            "align": "center"
                        }
                    ]
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "md",
                    "contents": contents
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "button",
                            "action": {
                                "type": "message",
                                "label": "กลับไปที่รายการ Ticket",
                                "text": "เช็กสถานะ"
                            },
                            "style": "secondary",
                            "color": "#AAAAAA"
                        }
                    ]
                }
            }
        }
    except Exception as e:
        print("❌ Error creating flex message:", e)
        return None

def send_reply_message(reply_token, messages):
    try:
        body = {
            "replyToken": reply_token,
            "messages": messages
        }
        res = requests.post('https://api.line.me/v2/bot/message/reply', headers=LINE_HEADERS, json=body)
        print("📤 Reply response:", res.status_code, res.text)
    except Exception as e:
        print("❌ Failed to reply:", e)
        traceback.print_exc()

def reply(reply_token, text):
    send_reply_message(reply_token, [{"type": "text", "text": text}])

def send_department_flex_message(reply_token):
    """ส่ง Flex Message สำหรับเลือกแผนกแบบสวยงามและใช้งานได้จริง"""
    flex_message = {
        "type": "flex",
        "altText": "กรุณาเลือกแผนกที่ต้องการ",
        "contents": {
            "type": "bubble",
            "size": "kilo",
            "header": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "📌 เลือกแผนก",
                        "weight": "bold",
                        "size": "lg",
                        "color": "#2E4053",
                        "align": "center"
                    },
                    {
                        "type": "text",
                        "text": "กรุณาเลือกแผนกของท่าน",
                        "size": "sm",
                        "color": "#7F8C8D",
                        "align": "center",
                        "margin": "sm"
                    }
                ],
                "paddingBottom": "md",
                "backgroundColor": "#F8F9F9"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "separator",
                        "color": "#EAEDED"
                    },
                    # ผู้บริหาร/เลขานุการ
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "👔",
                                "size": "sm",
                                "flex": 1,
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": "ผู้บริหาร/เลขานุการ",
                                "size": "sm",
                                "flex": 4,
                                "weight": "bold",
                                "color": "#2E4053"
                            },
                            {
                                "type": "text",
                                "text": "›",
                                "size": "sm",
                                "flex": 1,
                                "align": "end",
                                "color": "#7F8C8D"
                            }
                        ],
                        "action": {
                            "type": "message",
                            "label": "ผู้บริหาร/เลขานุการ",
                            "text": "ผู้บริหาร/เลขานุการ"
                        },
                        "backgroundColor": "#EBF5FB",
                        "paddingAll": "sm",
                        "cornerRadius": "md",
                        "margin": "sm"
                    },
                    # ส่วนงานตรวจสอบภายใน
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "🔍",
                                "size": "sm",
                                "flex": 1,
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": "ส่วนงานตรวจสอบภายใน",
                                "size": "sm",
                                "flex": 4,
                                "weight": "bold",
                                "color": "#2E4053"
                            },
                            {
                                "type": "text",
                                "text": "›",
                                "size": "sm",
                                "flex": 1,
                                "align": "end",
                                "color": "#7F8C8D"
                            }
                        ],
                        "action": {
                            "type": "message",
                            "label": "ส่วนงานตรวจสอบภายใน",
                            "text": "ส่วนงานตรวจสอบภายใน"
                        },
                        "backgroundColor": "#EAFAF1",
                        "paddingAll": "sm",
                        "cornerRadius": "md",
                        "margin": "sm"
                    },
                    # ส่วนงานกฏหมาย
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "⚖️",
                                "size": "sm",
                                "flex": 1,
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": "ส่วนงานกฏหมาย",
                                "size": "sm",
                                "flex": 4,
                                "weight": "bold",
                                "color": "#2E4053"
                            },
                            {
                                "type": "text",
                                "text": "›",
                                "size": "sm",
                                "flex": 1,
                                "align": "end",
                                "color": "#7F8C8D"
                            }
                        ],
                        "action": {
                            "type": "message",
                            "label": "ส่วนงานกฏหมาย",
                            "text": "ส่วนงานกฏหมาย"
                        },
                        "backgroundColor": "#FEF9E7",
                        "paddingAll": "sm",
                        "cornerRadius": "md",
                        "margin": "sm"
                    },
                    # งานสื่อสารองค์การ
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "📢",
                                "size": "sm",
                                "flex": 1,
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": "งานสื่อสารองค์การ",
                                "size": "sm",
                                "flex": 4,
                                "weight": "bold",
                                "color": "#2E4053"
                            },
                            {
                                "type": "text",
                                "text": "›",
                                "size": "sm",
                                "flex": 1,
                                "align": "end",
                                "color": "#7F8C8D"
                            }
                        ],
                        "action": {
                            "type": "message",
                            "label": "งานสื่อสารองค์การ",
                            "text": "งานสื่อสารองค์การ"
                        },
                        "backgroundColor": "#FDEDEC",
                        "paddingAll": "sm",
                        "cornerRadius": "md",
                        "margin": "sm"
                    },
                    # ฝ่ายนโยบายและแผน
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "📊",
                                "size": "sm",
                                "flex": 1,
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": "ฝ่ายนโยบายและแผน",
                                "size": "sm",
                                "flex": 4,
                                "weight": "bold",
                                "color": "#2E4053"
                            },
                            {
                                "type": "text",
                                "text": "›",
                                "size": "sm",
                                "flex": 1,
                                "align": "end",
                                "color": "#7F8C8D"
                            }
                        ],
                        "action": {
                            "type": "message",
                            "label": "ฝ่ายนโยบายและแผน",
                            "text": "ฝ่ายนโยบายและแผน"
                        },
                        "backgroundColor": "#F5EEF8",
                        "paddingAll": "sm",
                        "cornerRadius": "md",
                        "margin": "sm"
                    },
                    # ฝ่ายเทคโนโลยีสารสนเทศ
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "💻",
                                "size": "sm",
                                "flex": 1,
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": "ฝ่ายเทคโนโลยีสารสนเทศ",
                                "size": "sm",
                                "flex": 4,
                                "weight": "bold",
                                "color": "#2E4053"
                            },
                            {
                                "type": "text",
                                "text": "›",
                                "size": "sm",
                                "flex": 1,
                                "align": "end",
                                "color": "#7F8C8D"
                            }
                        ],
                        "action": {
                            "type": "message",
                            "label": "ฝ่ายเทคโนโลยีสารสนเทศ",
                            "text": "ฝ่ายเทคโนโลยีสารสนเทศ"
                        },
                        "backgroundColor": "#E8F8F5",
                        "paddingAll": "sm",
                        "cornerRadius": "md",
                        "margin": "sm"
                    },
                    # ฝ่ายบริหาร
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "🏢",
                                "size": "sm",
                                "flex": 1,
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": "ฝ่ายบริหาร",
                                "size": "sm",
                                "flex": 4,
                                "weight": "bold",
                                "color": "#2E4053"
                            },
                            {
                                "type": "text",
                                "text": "›",
                                "size": "sm",
                                "flex": 1,
                                "align": "end",
                                "color": "#7F8C8D"
                            }
                        ],
                        "action": {
                            "type": "message",
                            "label": "ฝ่ายบริหาร",
                            "text": "ฝ่ายบริหาร"
                        },
                        "backgroundColor": "#F9EBEA",
                        "paddingAll": "sm",
                        "cornerRadius": "md",
                        "margin": "sm"
                    },
                    # ฝ่ายบริหารวิชาการและพัฒนาผู้ประกอบการ
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "🎓",
                                "size": "sm",
                                "flex": 1,
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": "ฝ่ายบริหารวิชาการและพัฒนาผู้ประกอบการ",
                                "size": "sm",
                                "flex": 4,
                                "weight": "bold",
                                "color": "#2E4053"
                            },
                            {
                                "type": "text",
                                "text": "›",
                                "size": "sm",
                                "flex": 1,
                                "align": "end",
                                "color": "#7F8C8D"
                            }
                        ],
                        "action": {
                            "type": "message",
                            "label": "ฝ่ายบริหารวิชาการและพัฒนาผู้ประกอบการ",
                            "text": "ฝ่ายบริหารวิชาการและพัฒนาผู้ประกอบการ"
                        },
                        "backgroundColor": "#EAF2F8",
                        "paddingAll": "sm",
                        "cornerRadius": "md",
                        "margin": "sm"
                    },
                    # ฝ่ายตรวจสอบโลหะมีค่า
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "💰",
                                "size": "sm",
                                "flex": 1,
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": "ฝ่ายตรวจสอบโลหะมีค่า",
                                "size": "sm",
                                "flex": 4,
                                "weight": "bold",
                                "color": "#2E4053"
                            },
                            {
                                "type": "text",
                                "text": "›",
                                "size": "sm",
                                "flex": 1,
                                "align": "end",
                                "color": "#7F8C8D"
                            }
                        ],
                        "action": {
                            "type": "message",
                            "label": "ฝ่ายตรวจสอบโลหะมีค่า",
                            "text": "ฝ่ายตรวจสอบโลหะมีค่า"
                        },
                        "backgroundColor": "#F5EEF8",
                        "paddingAll": "sm",
                        "cornerRadius": "md",
                        "margin": "sm"
                    },
                    # ฝ่ายตรวจสอบอัญมณีและเครื่องประดับ
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "💎",
                                "size": "sm",
                                "flex": 1,
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": "ฝ่ายตรวจสอบอัญมณีและเครื่องประดับ",
                                "size": "sm",
                                "flex": 4,
                                "weight": "bold",
                                "color": "#2E4053"
                            },
                            {
                                "type": "text",
                                "text": "›",
                                "size": "sm",
                                "flex": 1,
                                "align": "end",
                                "color": "#7F8C8D"
                            }
                        ],
                        "action": {
                            "type": "message",
                            "label": "ฝ่ายตรวจสอบอัญมณีและเครื่องประดับ",
                            "text": "ฝ่ายตรวจสอบอัญมณีและเครื่องประดับ"
                        },
                        "backgroundColor": "#FEF9E7",
                        "paddingAll": "sm",
                        "cornerRadius": "md",
                        "margin": "sm"
                    },
                    # ฝ่ายวิจัยและพัฒนามาตรฐาน
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "🔬",
                                "size": "sm",
                                "flex": 1,
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": "ฝ่ายวิจัยและพัฒนามาตรฐาน",
                                "size": "sm",
                                "flex": 4,
                                "weight": "bold",
                                "color": "#2E4053"
                            },
                            {
                                "type": "text",
                                "text": "›",
                                "size": "sm",
                                "flex": 1,
                                "align": "end",
                                "color": "#7F8C8D"
                            }
                        ],
                        "action": {
                            "type": "message",
                            "label": "ฝ่ายวิจัยและพัฒนามาตรฐาน",
                            "text": "ฝ่ายวิจัยและพัฒนามาตรฐาน"
                        },
                        "backgroundColor": "#EAFAF1",
                        "paddingAll": "sm",
                        "cornerRadius": "md",
                        "margin": "sm"
                    },
                    # ฝ่ายพัฒนาธุรกิจ
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "📈",
                                "size": "sm",
                                "flex": 1,
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": "ฝ่ายพัฒนาธุรกิจ",
                                "size": "sm",
                                "flex": 4,
                                "weight": "bold",
                                "color": "#2E4053"
                            },
                            {
                                "type": "text",
                                "text": "›",
                                "size": "sm",
                                "flex": 1,
                                "align": "end",
                                "color": "#7F8C8D"
                            }
                        ],
                        "action": {
                            "type": "message",
                            "label": "ฝ่ายพัฒนาธุรกิจ",
                            "text": "ฝ่ายพัฒนาธุรกิจ"
                        },
                        "backgroundColor": "#EBF5FB",
                        "paddingAll": "sm",
                        "cornerRadius": "md",
                        "margin": "sm"
                    },
                    {
                        "type": "separator",
                        "color": "#EAEDED",
                        "margin": "md"
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "เลือกแผนกที่ต้องการติดต่อ",
                        "size": "xxs",
                        "color": "#7F8C8D",
                        "align": "center",
                        "margin": "sm"
                    }
                ]
            },
            "styles": {
                "footer": {
                    "separator": True
                }
            }
        }
    }
    
    send_reply_message(reply_token, [flex_message])

def parse_issue_message(message):
    try:
        issue = re.search(r"แจ้งปัญหา[:：]\s*(.*)", message)
        category = re.search(r"ประเภท[:：]\s*(.*)", message)
        phone = re.search(r"เบอร์ติดต่อ[:：]\s*(.*)", message)
        department = re.search(r"แผนก[:：]\s*(.*)", message)
        if issue and category and phone:
            return {
                "issue": issue.group(1).strip(),
                "category": category.group(1).strip(),
                "phone": phone.group(1).strip(),
                "department": department.group(1).strip() if department else "-"
            }
        return None
    except:
        return None

def generate_ticket_id():
    now = datetime.now()
    return f"TICKET-{now.strftime('%Y%m%d%H%M%S')}"

def save_ticket_to_sheet(user_id, data, ticket_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        phone_number = format_phone_number(data['phone'])
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute('''
            INSERT INTO tickets (
                ticket_id, user_id, email, name, phone, department, created_at, status, appointment, requeste, report, type
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            ticket_id,
            user_id,
            data['issue'],
            data['category'],
            phone_number,
            data.get('department', '-'),
            now,
            "None",
            now,  # Appointment
            "None",  # Requeste
            "None",  # Report
            "Information"
        ))
        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ Ticket {ticket_id} saved as Information type (PostgreSQL)")
        return True
    except Exception as e:
        print("❌ Error saving ticket (PostgreSQL):", e)
        traceback.print_exc()
        return False
    
def send_flex_choice(user_id):
    flex_message = {
        "type": "flex",
        "altText": "เลือกประเภทบริการ",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": "กรุณาเลือกประเภทบริการ",
                        "size": "md",
                        "weight": "bold"
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#1DB446",
                        "action": {
                            "type": "message",
                            "label": "Service",
                            "text": "นัดหมายเวลา"
                        }
                    },
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#FF0000",
                        "action": {
                            "type": "message",
                            "label": "Helpdesk",
                            "text": "Helpdesk"
                        }
                    }
                ]
            }
        }
    }

    body = {
        "to": user_id,
        "messages": [flex_message]
    }

    try:
        res = requests.post('https://api.line.me/v2/bot/message/push', headers=LINE_HEADERS, json=body)
        print("📤 Sent Flex Choice:", res.status_code, res.text)
    except Exception as e:
        print("❌ Error sending Flex Choice:", e)
        traceback.print_exc()

def send_flex_ticket_summary(user_id, data, ticket_id,type_vaul="Information"):
    flex_message = {
        "type": "flex",
        "altText": f"📄 สรุปรายการสมัครสมาชิก {ticket_id}",
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "📄 สมัครสมาชิกสำเร็จ",
                        "weight": "bold",
                        "size": "lg",
                        "color": "#1DB446"
                    }
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {"type": "text", "text": f"Ticket ID: {ticket_id}", "wrap": True, "size": "sm"},
                    {"type": "text", "text": f"อีเมล: {data.get('issue')}", "wrap": True, "size": "sm"},
                    {"type": "text", "text": f"ชื่อ: {data.get('category')}", "wrap": True, "size": "sm"},
                    {"type": "text", "text": f"เบอร์ติดต่อ: {data.get('phone')}", "wrap": True, "size": "sm"},
                    {"type": "text", "text": f"แผนก: {data.get('department', '-')}", "wrap": True, "size": "sm"},
                    {"type": "text", "text": f"ณ เวลา: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "wrap": True, "size": "sm"},
                    {"type": "text", "text": f"ประเภท: {type_vaul}", "wrap": True, "size": "sm"},
                ]
            }
        }
    }

    body = {
        "to": user_id,
        "messages": [flex_message]
    }

    try:
        res = requests.post('https://api.line.me/v2/bot/message/push', headers=LINE_HEADERS, json=body)
        print("📤 Sent Flex Message:", res.status_code, res.text)
    except Exception as e:
        print("❌ Error sending Flex Message:", e)
        traceback.print_exc()

def handle_appointment(reply_token, user_id):
    """เริ่มกระบวนการนัดหมาย"""
    latest_ticket = get_latest_ticket(user_id)
    if not latest_ticket:
        reply(reply_token, "⚠️ ไม่พบ Ticket ของคุณในระบบ กรุณาสร้าง Ticket ก่อน")
        return
    
    # เตรียมข้อมูลผู้ใช้ใน state
    user_states[user_id] = {
        "step": "ask_appointment",
        "service_type": "Service",
        "email": latest_ticket.get('อีเมล', ''),
        "name": latest_ticket.get('ชื่อ', ''),
        "phone": str(latest_ticket.get('เบอร์ติดต่อ', '')),
        "department": latest_ticket.get('แผนก', ''),
        "ticket_id": generate_ticket_id()
    }
    
    send_date_picker(reply_token)

def send_date_picker(reply_token):
    # ไม่กำหนด min_date และ max_date เพื่อให้เลือกวันใดก็ได้
    flex_message = {
        "type": "flex",
        "altText": "เลือกวันนัดหมาย",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "📅 กรุณาเลือกวันนัดหมาย",
                        "weight": "bold",
                        "size": "lg",
                        "color": "#005BBB"
                    },
                    {
                        "type": "text",
                        "text": "สามารถเลือกวันใดก็ได้",
                        "margin": "sm",
                        "size": "sm",
                        "color": "#AAAAAA"
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "action": {
                            "type": "datetimepicker",
                            "label": "เลือกวันที่",
                            "data": "action=select_date",
                            "mode": "date"
                            # ไม่กำหนด initial, min, max เพื่อให้เลือกวันใดก็ได้
                        },
                        "style": "primary",
                        "color": "#1DB446"
                    }
                ]
            }
        }
    }
    
    send_reply_message(reply_token, [flex_message])

def send_time_picker(reply_token, selected_date):
    # กำหนดช่วงเวลาที่สามารถนัดหมายได้
    time_slots = [
        {"label": "05:00 - 06:00", "value": "05:00-06:00"},
        {"label": "06:00 - 07:00", "value": "06:00-07:00"},
        {"label": "07:00 - 08:00", "value": "07:00-08:00"},
        {"label": "08:00 - 09:00", "value": "08:00-09:00"},
        {"label": "09:00 - 10:00", "value": "09:00-10:00"},
        {"label": "10:00 - 11:00", "value": "10:00-11:00"},
        {"label": "11:00 - 12:00", "value": "11:00-12:00"},
        {"label": "13:00 - 14:00", "value": "13:00-14:00"},
        {"label": "14:00 - 15:00", "value": "14:00-15:00"},
        {"label": "15:00 - 16:00", "value": "15:00-16:00"}
    ]
    
    # สร้าง Quick Reply buttons
    quick_reply_items = []
    for slot in time_slots:
        quick_reply_items.append({
            "type": "action",
            "action": {
                "type": "message",
                "label": slot["label"],
                "text": slot["value"]
            }
        })
    
    # เพิ่มตัวเลือกกรอกเวลาเอง
    quick_reply_items.append({
        "type": "action",
        "action": {
            "type": "message",
            "label": "กรอกเวลาเอง",
            "text": "กรอกเวลาเอง"
        }
    })
    
    message = {
        "type": "text",
        "text": f"📅 วันที่คุณเลือก: {selected_date}\n\n⏰ กรุณาเลือกช่วงเวลาที่ต้องการ หรือกด 'กรอกเวลาเอง' เพื่อระบุเวลาแบบกำหนดเอง:",
        "quickReply": {
            "items": quick_reply_items
        }
    }
    
    send_reply_message(reply_token, [message])

def send_appointment_quick_reply(reply_token):
    # สร้างรายการเวลาที่สามารถนัดหมายได้
    time_slots = [
        "05:00-06:00", "06:00-07:00", "07:00-08:00",
        "09:00-10:00", "10:00-11:00", "11:00-12:00",
        "13:00-14:00", "14:00-15:00", "15:00-16:00"
    ]
    
    quick_reply_items = []
    for slot in time_slots:
        quick_reply_items.append({
            "type": "action",
            "action": {
                "type": "message",
                "label": slot,
                "text": f"นัดหมายเวลา {slot}"
            }
        })
    
    # เพิ่มตัวเลือกกรอกเวลาเอง
    quick_reply_items.append({
        "type": "action",
        "action": {
            "type": "message",
            "label": "กรอกเวลาเอง",
            "text": "กรอกเวลานัดหมายเอง"
        }
    })
    
    message = {
        "type": "text",
        "text": "กรุณาเลือกเวลานัดหมายหรือกรอกเวลาเองในรูปแบบ HH:MM-HH:MM",
        "quickReply": {
            "items": quick_reply_items
        }
    }
    send_reply_message(reply_token, [message])

def handle_save_appointment(reply_token, user_id, appointment_datetime):
    """บันทึกการนัดหมายลงระบบ"""
    if user_id not in user_states or user_states[user_id].get("step") != "ask_appointment":
        reply(reply_token, "⚠️ เกิดข้อผิดพลาด กรุณาเริ่มกระบวนการใหม่")
        return
    
    # เปลี่ยนสถานะเป็นรอรับความประสงค์
    user_states[user_id]["step"] = "ask_request"
    user_states[user_id]["appointment_datetime"] = appointment_datetime
    
    reply(reply_token, "📝 กรุณากรอกความประสงค์หรือรายละเอียดเพิ่มเติมของบริการที่ต้องการ")

def handle_user_request(reply_token, user_id, request_text):
    """จัดการกับความประสงค์ที่ผู้ใช้กรอก"""
    if user_id not in user_states or user_states[user_id].get("step") != "ask_request":
        reply(reply_token, "⚠️ เกิดข้อผิดพลาด กรุณาเริ่มกระบวนการใหม่")
        return
    
    # บันทึกข้อมูลชั่วคราวก่อน confirm
    user_states[user_id]["request_text"] = request_text
    user_states[user_id]["step"] = "pre_service"
    
    # ส่ง Confirm Message
    confirm_msg = create_confirm_message(
        "service",
        f"นัดหมาย: {user_states[user_id]['appointment_datetime']}\nความประสงค์: {request_text}"
    )
    send_reply_message(reply_token, [confirm_msg])

def save_appointment_with_request(ticket_id, user_id, email, name, phone, department, appointment_datetime, request_text):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        formatted_phone = format_phone_number(phone)
        cur.execute('''
            INSERT INTO tickets (
                ticket_id, user_id, email, name, phone, department, created_at, status, appointment, requeste, report, type
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            ticket_id,
            user_id,
            email,
            name,
            formatted_phone,
            department,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Pending",
            appointment_datetime,
            request_text if request_text else "None",
            "None",
            "Service"
        ))
        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ Saved Service ticket: {ticket_id} (PostgreSQL)")
        return True
    except Exception as e:
        print("❌ Error saving Service ticket (PostgreSQL):", e)
        traceback.print_exc()
        return False

def send_ticket_summary_with_request(user_id, ticket_id, appointment_datetime, request_text, email, name, phone, department, type_value="Service"):
    try:
        # แยกข้อมูลวันที่และเวลา
        date_part, time_range = appointment_datetime.split()
        dt = datetime.strptime(date_part, "%Y-%m-%d")
        formatted_date = dt.strftime("%d/%m/%Y")
        start_time, end_time = time_range.split('-')
        
        flex_message = {
            "type": "flex",
            "altText": f"สรุป Ticket {ticket_id}",
            "contents": {
                "type": "bubble",
                "size": "kilo",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"📄 Ticket {ticket_id}",
                            "weight": "bold",
                            "size": "lg",
                            "color": "#005BBB"
                        }
                    ]
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        info_row("อีเมล", email),
                        info_row("ชื่อ", name),
                        info_row("เบอร์ติดต่อ", display_phone_number(phone)),
                        info_row("แผนก", department),
                        info_row("วันที่นัดหมาย", formatted_date),
                        info_row("ช่วงเวลา", f"{start_time} - {end_time}"),
                        {
                            "type": "separator",
                            "margin": "md"
                        },
                        info_row("ประเภท", type_value),
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "ความประสงค์:",
                                    "size": "sm",
                                    "color": "#AAAAAA",
                                    "flex": 2
                                },
                                {
                                    "type": "text",
                                    "text": request_text,
                                    "size": "sm",
                                    "wrap": True,
                                    "flex": 4
                                }
                            ]
                        },
                        status_row("สถานะ", "Pending", "#005BBB")
                    ]
                }
            }
        }

        body = {
            "to": user_id,
            "messages": [flex_message]
        }

        res = requests.post('https://api.line.me/v2/bot/message/push', headers=LINE_HEADERS, json=body)
        print("📤 Sent Ticket Summary with Request:", res.status_code, res.text)
    except Exception as e:
        print("❌ Error sending Ticket Summary with Request:", e)
        traceback.print_exc()

def is_time_before(start_time, end_time):
    """ตรวจสอบว่าเวลาเริ่มต้นน้อยกว่าเวลาสิ้นสุด"""
    try:
        start_h, start_m = map(int, start_time.split(':'))
        end_h, end_m = map(int, end_time.split(':'))
        
        if start_h < end_h:
            return True
        elif start_h == end_h and start_m < end_m:
            return True
        return False
    except:
        return False

def save_appointment_to_sheet(ticket_id, appointment_datetime):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE tickets SET appointment = %s WHERE ticket_id = %s", (appointment_datetime, ticket_id))
        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ Updated appointment for {ticket_id}: {appointment_datetime} (PostgreSQL)")
        return True
    except Exception as e:
        print("❌ Error saving appointment (PostgreSQL):", e)
        traceback.print_exc()
        return False

def get_latest_ticket(user_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM tickets WHERE user_id = %s ORDER BY created_at DESC LIMIT 1", (user_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        phone = str(row['phone']) if row['phone'] else ''
        phone = phone.replace("'", "")
        if phone and not phone.startswith('0'):
            phone = '0' + phone[-9:]
        latest_ticket = {
            'อีเมล': row.get('email', ''),
            'ชื่อ': row.get('name', ''),
            'เบอร์ติดต่อ': phone,
            'แผนก': row.get('department', ''),
            'วันที่แจ้ง': row.get('created_at', ''),
        }
        return latest_ticket
    except Exception as e:
        print("❌ Error getting latest ticket (PostgreSQL):", e)
        traceback.print_exc()
        return None

def is_valid_email(email):
    pattern = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    return re.fullmatch(pattern, email) is not None

def check_existing_email(email):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM tickets WHERE LOWER(email) = LOWER(%s) OR LOWER(issue) = LOWER(%s) LIMIT 1", (email, email))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return True
        return False
    except Exception as e:
        print("❌ Error checking email (PostgreSQL):", e)
        traceback.print_exc()
        return False
    
def check_existing_user(user_id):
    """ตรวจสอบว่าผู้ใช้มีข้อมูลในระบบและมีการเข้าสู่ระบบแล้ว (PostgreSQL)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM tickets WHERE user_id = %s LIMIT 1", (user_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and (row.get('email') or row.get('issue')):
            return True
        return False
    except Exception as e:
        print("❌ Error checking user ID (PostgreSQL):", e)
        traceback.print_exc()
        return False

def check_ticket_status(ticket_id):
    try:
        scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        client = gspread.authorize(creds)
        sheet = client.open("Tickets").sheet1
        data = sheet.get_all_records()

        for row in data:
            if row.get('หมายเลข Ticket') == ticket_id or row.get('Ticket ID') == ticket_id:
                return (
                    f"\nอีเมล: {row.get('อีเมล') or row.get('issue') or '-'}\n"
                    f"ชื่อ: {row.get('ชื่อ') or row.get('category') or '-'}\n"
                    f"เบอร์ติดต่อ: {display_phone_number(row.get('เบอร์ติดต่อ') or row.get('phone'))}\n"
                    f"แผนก: {row.get('แผนก') or row.get('department') or '-'}\n"
                    f"สถานะ: {row.get('สถานะ') or row.get('status') or '-'}"
                )

        return None
    except Exception as e:
        print("❌ Error checking status:", e)
        traceback.print_exc()
        return None

def handle_helpdesk(reply_token, user_id):
    """เริ่มกระบวนการแจ้งปัญหา Helpdesk"""
    latest_ticket = get_latest_ticket(user_id)
    if not latest_ticket:
        reply(reply_token, "⚠️ ไม่พบข้อมูลผู้ใช้ในระบบ กรุณาสมัครสมาชิกก่อน")
        return
    
    # เตรียมข้อมูลผู้ใช้ใน state
    user_states[user_id] = {
        "step": "ask_helpdesk_issue",
        "service_type": "Helpdesk",
        "email": latest_ticket.get('อีเมล', ''),
        "name": latest_ticket.get('ชื่อ', ''),
        "phone": str(latest_ticket.get('เบอร์ติดต่อ', '')),
        "department": latest_ticket.get('แผนก', '')
    }
    
    send_helpdesk_quick_reply(reply_token)
    
    # แปลงเบอร์โทรศัพท์เป็น string
    phone = str(latest_ticket.get('เบอร์ติดต่อ', '')) if latest_ticket.get('เบอร์ติดต่อ') else ""
    
    # บันทึกว่า user กำลังแจ้งปัญหา Helpdesk
    user_states[user_id] = {
        "step": "ask_helpdesk_issue",
        "ticket_id": generate_ticket_id(),
        "email": latest_ticket.get('อีเมล', ''),
        "name": latest_ticket.get('ชื่อ', ''),
        "phone": phone,
        "department": latest_ticket.get('แผนก', '')
    }
    
    # ส่ง Quick Reply สำหรับปัญหาทั่วไป
    send_helpdesk_quick_reply(reply_token)

def send_helpdesk_quick_reply(reply_token):
    """ส่ง Quick Reply สำหรับปัญหาทั่วไป"""
    quick_reply_items = [
        {
            "type": "action",
            "action": {
                "type": "message",
                "label": "คอมพิวเตอร์เสีย",
                "text": "คอมพิวเตอร์เสีย"
            }
        },
        {
            "type": "action",
            "action": {
                "type": "message",
                "label": "เน็ตเวิร์คล่ม",
                "text": "เน็ตเวิร์คล่ม"
            }
        },
        {
            "type": "action",
            "action": {
                "type": "message",
                "label": "เครื่องปริ้นเตอร์ไม่ทำงาน",
                "text": "เครื่องปริ้นเตอร์ไม่ทำงาน"
            }
        },
        {
            "type": "action",
            "action": {
                "type": "message",
                "label": "อุปกรณ์เสียหาย",
                "text": "อุปกรณ์เสียหาย"
            }
        },
        {
            "type": "action",
            "action": {
                "type": "message",
                "label": "อื่นๆ ไม่สามารถระบุได้",
                "text": "แจ้งปัญหาอื่นๆ"
            }
        },
    ]
    
    message = {
        "type": "text",
        "text": "กรุณาแจ้งปัญหา:",
        "quickReply": {
            "items": quick_reply_items
        }
    }
    send_reply_message(reply_token, [message])

def handle_helpdesk_issue(reply_token, user_id, issue_text):
    """จัดการกับปัญหาที่ผู้ใช้แจ้ง"""
    if user_id not in user_states or user_states[user_id].get("step") != "ask_helpdesk_issue":
        reply(reply_token, "⚠️ เกิดข้อผิดพลาด กรุณาเริ่มกระบวนการใหม่")
        return
    
    # บันทึกข้อมูลชั่วคราวก่อน confirm
    user_states[user_id]["issue_text"] = issue_text
    user_states[user_id]["step"] = "pre_helpdesk"
    
    # ส่ง Confirm Message
    confirm_msg = create_confirm_message(
        "helpdesk",
        f"แจ้งปัญหา: {issue_text}"
    )
    send_reply_message(reply_token, [confirm_msg])

def send_helpdesk_summary(user_id, ticket_id, report_time, issue_text, email, name, phone, department, type_value="Helpdesk"):
    try:
        flex_message = {
            "type": "flex",
            "altText": f"สรุป Ticket {ticket_id}",
            "contents": {
                "type": "bubble",
                "size": "kilo",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"📄 Ticket {ticket_id}",
                            "weight": "bold",
                            "size": "lg",
                            "color": "#005BBB"
                        }
                    ]
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        info_row("อีเมล", email),
                        info_row("ชื่อ", name),
                        info_row("เบอร์ติดต่อ", display_phone_number(phone)),
                        info_row("แผนก", department),
                        info_row("วันที่แจ้ง", report_time),
                        {
                            "type": "separator",
                            "margin": "md"
                        },
                        info_row("ประเภท", type_value),
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "วันที่แจ้งปัญหา:",
                                    "size": "sm",
                                    "color": "#AAAAAA",
                                    "flex": 2
                                },
                                {
                                    "type": "text",
                                    "text": report_time,
                                    "size": "sm",
                                    "wrap": True,
                                    "flex": 4
                                }
                            ]
                        },
                        {
                            "type": "separator",
                            "margin": "md"
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "ปัญหาที่แจ้ง:",
                                    "size": "sm",
                                    "color": "#AAAAAA",
                                    "flex": 2
                                },
                                {
                                    "type": "text",
                                    "text": issue_text,
                                    "size": "sm",
                                    "wrap": True,
                                    "flex": 4
                                }
                            ]
                        },
                        status_row("สถานะ", "Pending", "#005BBB")
                    ]
                }
            }
        }

        body = {
            "to": user_id,
            "messages": [flex_message]
        }

        res = requests.post('https://api.line.me/v2/bot/message/push', headers=LINE_HEADERS, json=body)
        print("📤 Sent Helpdesk Summary:", res.status_code, res.text)
    except Exception as e:
        print("❌ Error sending Helpdesk Summary:", e)
        traceback.print_exc()

def get_all_user_tickets(user_id):
    """ดึง Ticket ทั้งหมดของผู้ใช้จาก PostgreSQL"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM tickets WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
        rows = cur.fetchall()
        user_tickets = []
        for row in rows:
            # row เป็น dict เพราะใช้ RealDictCursor
            phone = str(row['phone']) if row['phone'] else ''
            phone = phone.replace("'", "")
            if phone and not phone.startswith('0'):
                phone = '0' + phone[-9:]
            ticket_data = {
                'ticket_id': row.get('ticket_id', 'TICKET-UNKNOWN'),
                'email': row.get('email', 'ไม่มีข้อมูล'),
                'name': row.get('name', 'ไม่มีข้อมูล'),
                'phone': phone,
                'department': row.get('department', 'ไม่มีข้อมูล'),
                'date': row.get('created_at', 'ไม่มีข้อมูล'),
                'status': row.get('status', 'Pending'),
                'appointment': row.get('appointment', 'None'),
                'requeste': row.get('requeste', 'None'),
                'report': row.get('report', 'None'),
                'type': row.get('type', 'ไม่ระบุ')
            }
            user_tickets.append(ticket_data)
        cur.close()
        conn.close()
        return user_tickets
    except Exception as e:
        print("❌ Error getting user tickets (PostgreSQL):", e)
        traceback.print_exc()
        return None
    
def create_confirm_message(action_type, details):
    """สร้าง Confirm Message ด้วย Flex"""
    return {
        "type": "flex",
        "altText": "กรุณายืนยันการดำเนินการ",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "ยืนยันการดำเนินการ",
                        "weight": "bold",
                        "size": "lg",
                        "color": "#005BBB"
                    },
                    {
                        "type": "text",
                        "text": f"คุณต้องการ{action_type}ใช่หรือไม่?",
                        "margin": "md",
                        "size": "md"
                    },
                    {
                        "type": "separator",
                        "margin": "lg"
                    },
                    {
                        "type": "text",
                        "text": details[:100] + "..." if len(details) > 100 else details,
                        "margin": "lg",
                        "wrap": True,
                        "size": "sm",
                        "color": "#666666"
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#1DB446",
                        "action": {
                            "type": "message",
                            "label": "ยืนยัน",
                            "text": f"confirm_{action_type}"
                        }
                    },
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#FF0000",
                        "action": {
                            "type": "message",
                            "label": "ยกเลิก",
                            "text": f"cancel_{action_type}"
                        }
                    }
                ]
            }
        }
    }

def display_phone_number(phone):
    try:
        phone_str = str(phone).strip().replace("'", "").replace('"', "")
        if phone_str.startswith("66") and len(phone_str) == 11:
            return "0" + phone_str[2:]
        if len(phone_str) == 9 and not phone_str.startswith("0"):
            return "0" + phone_str
        return phone_str
    except:
        return "-"
def format_phone_number(phone):
    """
    จัดรูปแบบเบอร์โทรศัพท์ให้เก็บเลข 0 ได้แน่นอน
    โดยการแปลงเป็นข้อความและเติมเครื่องหมาย ' นำหน้า
    """
    if phone is None:
        return "''"  # ส่งคืนสตริงว่างที่มีเครื่องหมาย '
    
    phone_str = str(phone).strip()
    
    # กรณีเบอร์ว่างหรือไม่ใช่ตัวเลข
    if not phone_str.isdigit():
        return "''"
    
    # เติม ' นำหน้าเสมอ ไม่ว่าเบอร์จะขึ้นต้นด้วยอะไร
    return f"'{phone_str}"

def get_db_connection():
    import os
    conn = psycopg2.connect(
        os.environ.get('DATABASE_URL'),
        cursor_factory=RealDictCursor
    )
    return conn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    GOOGLE_CREDS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    app.run(host="0.0.0.0", port=port, debug=True)