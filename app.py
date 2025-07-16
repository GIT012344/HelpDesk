from flask import Flask, request, jsonify
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs
import traceback
import re
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from flask_socketio import SocketIO
from threading import Lock

CONTACT_STATE = "contact_conversation"
load_dotenv()
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

CHANNEL_ACCESS_TOKEN = 'O02yXH2dlIyu9da3bJPfhtHTZYkDJR/wy1TnWj5ZAgBUr0zfiNrY9mC3qm5nEWyILuI+rcVftmsvsQZp+AB8Hf6f5UmDosjtkQY0ufX+JrVwa3i+UwlAXa7UvBQ/JBef2pRD4wJ3QttJyLn1nfh1dQdB04t89/1O/w1cDnyilFU='

LINE_HEADERS = {
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {CHANNEL_ACCESS_TOKEN}'
}

user_states = {}
user_states_lock = Lock()

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

def safe_datetime_to_string(dt_value, default_format="%Y-%m-%d %H:%M:%S"):
    """แปลง datetime object เป็น string อย่างปลอดภัย"""
    if dt_value is None:
        return "ไม่มีข้อมูล"
    if hasattr(dt_value, 'strftime'):  # ถ้าเป็น datetime object
        return dt_value.strftime(default_format)
    return str(dt_value)

def safe_dict_value(value, default="ไม่มีข้อมูล"):
    """แปลงค่าใน dict เป็น string อย่างปลอดภัย"""
    if value is None:
        return default
    if hasattr(value, 'strftime'):  # ถ้าเป็น datetime object
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)

@app.route("/", methods=["GET"])
def home():
    return "✅ LINE Helpdesk is running.", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
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
            # --- เพิ่ม welcome quick reply เมื่อเริ่มแชท ---
            if event.get('type') == 'follow':
                user_id = event['source']['userId']
                welcome_message = {
                    "type": "text",
                    "text": "ยินดีต้อนรับสู่ระบบ Helpdesk\nกรุณาเลือกบริการที่ต้องการ:",
                    "quickReply": get_welcome_quick_reply()
                }
                send_reply_message(event['replyToken'], [welcome_message])
                continue
            if event.get('type') == 'message' and event['message'].get('type') == 'text':
                with user_states_lock:
                    handle_text_message(event)
                    user_id = event['source'].get('userId')
                    message_text = event['message'].get('text')
                    ticket_id = None
                    try:
                        conn = get_db_connection()
                        cur = conn.cursor()
                        cur.execute("SELECT ticket_id FROM tickets WHERE user_id = %s ORDER BY created_at DESC LIMIT 1", (user_id,))
                        row = cur.fetchone()
                        if row:
                            if isinstance(row, dict):
                                ticket_id = row.get('ticket_id')
                            elif isinstance(row, tuple):
                                ticket_id = row[0]
                            else:
                                ticket_id = None
                        cur.close()
                        conn.close()
                    except Exception:
                        ticket_id = None
                    if ticket_id:
                        socketio.emit('new_message', {
                            'ticket_id': ticket_id,
                            'admin_id': None,
                            'sender_name': 'LINE User',
                            'message': message_text,
                            'is_admin_message': False
                        })
            elif event.get('type') == 'postback':
                with user_states_lock:
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
                # ตรวจสอบว่าวันที่เลือกไม่ใช่วันในอดีต
                if selected_datetime.date() < today:
                    reply(reply_token, "⚠️ ไม่สามารถเลือกวันที่ผ่านมาแล้ว กรุณาเลือกวันที่เป็นปัจจุบันหรืออนาคต")
                    return
                # ถ้าเลือกวันปัจจุบัน ให้บันทึกเวลาปัจจุบันไว้ใน state
                if selected_datetime.date() == today:
                    current_time = datetime.now().time()
                    user_states[user_id]["current_time"] = current_time.strftime("%H:%M")
                else:
                    user_states[user_id].pop("current_time", None)
                user_states[user_id]["selected_date"] = selected_date
                formatted_date = selected_datetime.strftime("%d/%m/%Y")
                send_time_picker(reply_token, formatted_date, user_id)
        
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
                ticket_date = str(ticket['date'])
            
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
    
    # รายการคำสั่งที่ใช้ยกเลิกการทำงานปัจจุบัน
    cancel_keywords = ["จบ", "ยกเลิก", "cancel", "ออก", "end", "stop"]
    
    # --- ตรวจสอบการเรียกใช้เมนูซ้อนกัน ---
    if user_id in user_states and user_states[user_id].get("step") not in [None, ""]:
        # ถ้าผู้ใช้พยายามยกเลิกการทำงานปัจจุบัน
        if any(user_message.startswith(kw) for kw in cancel_keywords):
            del user_states[user_id]
            reply(reply_token, "✅ การดำเนินการปัจจุบันถูกยกเลิกแล้ว คุณสามารถเลือกบริการใหม่ได้")
            return
        
        # ตรวจสอบว่าผู้ใช้พยายามเรียกเมนูใหม่ขณะที่ยังมีเมนูที่ทำงานอยู่
        menu_keywords = ["เช็กสถานะ", "ติดต่อเจ้าหน้าที่", "แจ้งปัญหา", 
                        "นัดหมายเวลา", "Helpdesk", ]
        
        if any(kw in user_message for kw in menu_keywords):
            current_service = user_states[user_id].get("service_type", "บริการปัจจุบัน")
            reply(reply_token, 
                f"⚠️ คุณกำลังใช้งาน '{current_service}' อยู่\n\n"
                "กรุณาดำเนินการให้เสร็จสิ้นก่อน หรือพิมพ์ 'จบ' เพื่อออกจากการบริการนี้\n"
                "ก่อนเลือกบริการอื่น")
            return
    
    # --- ตรวจสอบคำสั่งยกเลิก ---
    if any(user_message.startswith(kw) for kw in cancel_keywords):
        if user_id in user_states:
            del user_states[user_id]
        reply(reply_token, "✅ สิ้นสุดการสนทนา")
        return
    
    # --- ส่วนเดิมของฟังก์ชัน handle_text_message ---
    reset_keywords = ["สมัครสมาชิก", "เช็กสถานะ", "ติดต่อเจ้าหน้าที่", "ยกเลิก"]
    
    # --- เพิ่มการตรวจสอบ state สำหรับปัญหาอื่นๆ ---
    if user_id in user_states and user_states[user_id].get("step") == "ask_custom_issue":
        user_states[user_id]["issue_text"] = user_message
        user_states[user_id]["step"] = "ask_custom_issue_details"
        reply(reply_token, "📝 กรุณากรอกรายละเอียดย่อยของปัญหาที่แจ้ง (เช่น อุปกรณ์ที่เกี่ยวข้อง, สถานที่เกิดปัญหา, อาการที่สังเกตเห็น)")
        return
    if user_id in user_states and user_states[user_id].get("step") == "ask_custom_issue_details":
        user_states[user_id]["subgroup"] = user_message
        user_states[user_id]["step"] = "pre_helpdesk"
        confirm_msg = create_confirm_message(
            "helpdesk",
            f"แจ้งปัญหา: {user_states[user_id]['issue_text']}\n"
            f"รายละเอียดย่อย: {user_message}"
        )
        send_reply_message(reply_token, [confirm_msg])
        return
    # เพิ่มการเช็ค state 'ask_custom_request' สำหรับ Service
    if user_id in user_states and user_states[user_id].get("step") == "ask_custom_request":
        handle_custom_request(reply_token, user_id, user_message)
        return
    
    if user_id in user_states and any(user_message.startswith(k) for k in reset_keywords):
        del user_states[user_id]
    
    if user_message.startswith(("confirm_", "cancel_")):
        handle_confirmation(event)
        return
    
    if user_id in user_states and user_states[user_id].get("step") == CONTACT_STATE:
        if not check_existing_user(user_id):
            del user_states[user_id]
            reply(reply_token, "⚠️ กรุณาสมัครสมาชิกหรือเข้าสู่ระบบใหม่ โดยเลือกไปที่เมนูและเลือกแจ้งปัญหาเพื่อลงทะเบียน")
            return
        if user_message.strip().lower() in ["จบ", "end", "หยุด", "เสร็จสิ้น"]:
            del user_states[user_id]
            reply(reply_token, "✅ การสนทนากับเจ้าหน้าที่ได้สิ้นสุดลง ขอบคุณที่ใช้บริการ")
            return
        # บันทึกข้อความทันที ไม่ต้อง confirm
        save_contact_message(user_id, user_message, is_user=True)
        reply(reply_token, "📩 ข้อความของคุณถูกส่งถึงเจ้าหน้าที่แล้ว รอการตอบกลับ หรือพิมพ์ 'จบ' เพื่อออกจากโหมดสนทนา")
        return
    
    if user_message == "ติดต่อเจ้าหน้าที่":
        if not check_existing_user(user_id):
            reply(reply_token, "⚠️ กรุณาสมัครสมาชิกโดยเลือกเมนูแจ้งปัญหา เพื่อเริ่มการใช้งานบริการอื่นๆ")
            return
        user_states[user_id] = {
            "step": CONTACT_STATE,
            "service_type": "Contact",
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        quick_reply = {
            "type": "text",
            "text": "พิมพ์ข้อความที่ต้องการส่งถึงเจ้าหน้าที่ ผ่านช่อง chat",
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
        if user_states[user_id].get("step") == "ask_subgroup":
            handle_service_subgroup(reply_token, user_id, user_message)
            return
        if user_states[user_id].get("step") == "ask_custom_subgroup":
            handle_custom_subgroup(reply_token, user_id, user_message)
            return
        if user_states[user_id].get("step") == "ask_helpdesk_issue":
            handle_helpdesk_issue(reply_token, user_id, user_message)
            return
        if user_states[user_id].get("step") == "ask_helpdesk_subgroup":
            handle_helpdesk_subgroup(reply_token, user_id, user_message)
            return
        if user_states[user_id].get("step") == "ask_custom_helpdesk_subgroup":
            handle_custom_helpdesk_subgroup(reply_token, user_id, user_message)
            return
        if user_states[user_id].get("step") == "ask_appointment" and "selected_date" in user_states[user_id]:
            if user_message == "กรอกเวลาเอง":
                reply(reply_token, "กรุณากรอกเวลาที่ต้องการในรูปแบบ HH:MM-HH:MM\nเช่น 11:30-12:45")
                return
            elif re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", user_message):
                start_time, end_time = user_message.split('-')
                if validate_time(start_time) and validate_time(end_time):
                    if is_time_before(start_time, end_time):
                        # ตรวจสอบว่าวันที่เลือกเป็นวันนี้หรือไม่
                        if "selected_date" in user_states[user_id]:
                            selected_date = datetime.strptime(user_states[user_id]["selected_date"], "%Y-%m-%d").date()
                            today = datetime.now().date()
                            if selected_date == today:
                                current_time = datetime.now().time()
                                start_time_obj = datetime.strptime(start_time, "%H:%M").time()
                                if start_time_obj < current_time:
                                    reply(reply_token, f"⚠️ ไม่สามารถเลือกเวลาที่ผ่านมาแล้ว (เวลาปัจจุบัน: {current_time.strftime('%H:%M')})")
                                    return
                    appointment_datetime = f"{user_states[user_id]['selected_date']} {user_message}"
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
        handle_report_issue(reply_token, user_id)
    elif user_message.startswith("สมัคร"):
        handle_report_issue(reply_token, user_id)
    elif user_message.startswith("reg"):
        handle_report_issue(reply_token, user_id)
    elif user_message.startswith("register"):
        handle_report_issue(reply_token, user_id)
    elif user_message.startswith("ลงทะเบียน"):
        handle_report_issue(reply_token, user_id)
    elif user_message.startswith("Reg"):
        handle_report_issue(reply_token, user_id)
    elif user_message.startswith("Register"):
        handle_report_issue(reply_token, user_id)
    elif user_message.startswith("ล็อคอิน"):
        handle_report_issue(reply_token, user_id)
    elif user_message.startswith("Login"):
        handle_report_issue(reply_token, user_id)
    elif user_message.startswith("login"):
        handle_report_issue(reply_token, user_id)
    elif user_message == "นัดหมายเวลา":
        handle_appointment(reply_token, user_id)
    elif user_message == "Helpdesk":
        handle_helpdesk(reply_token, user_id)
    elif user_message.startswith("นัดหมายเวลา ") or user_message.startswith("กรอกเวลานัดหมายเอง"):
        handle_appointment_time(reply_token, user_id, user_message)
    elif re.search(r"TICKET-\d{14}", user_message):
        match = re.search(r"(TICKET-\d{14})", user_message)
        if match:
            ticket_id = match.group(1)
            show_ticket_details(reply_token, ticket_id, user_id)
        else:
            reply(reply_token, "ไม่พบ Ticket ID ที่ระบุ")
        return
    elif user_message.startswith("ดูรายละเอียด "):
        ticket_id = user_message.replace("ดูรายละเอียด ", "").strip()
        show_ticket_details(reply_token, ticket_id, user_id)
    elif user_id in user_states and user_states[user_id].get("step") == "ask_custom_request":
        handle_custom_request(reply_token, user_id, user_message)
        return
    else:
        # เมื่อผู้ใช้พิมพ์ข้อความที่ไม่รู้จัก ให้แสดงเมนูหลัก
        reply_message = {
            "type": "text",
            "text": "กรุณาเลือกบริการที่ต้องการจากเมนูด้านล่าง:",
            "quickReply": get_main_menu_quick_reply()
        }
        send_reply_message(reply_token, [reply_message])

def handle_confirmation(event):
    """จัดการการยืนยันจากผู้ใช้"""
    user_message = event['message']['text'].strip()
    reply_token = event['replyToken']
    user_id = event['source']['userId']
    
    if user_id not in user_states:
        reply(reply_token, "⚠️โปรดเลือกเมนูบริการ เพื่อเริ่มกระบวนการใหม่")
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
                    state.get("issue_text", ""),
                    state.get("subgroup", "")  # เพิ่ม subgroup
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
                    state.get("request_text", ""),
                    state.get("subgroup", "")  # เพิ่ม subgroup
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
    """บันทึกข้อความใน Textbox พร้อมระบุประเภท และ insert ลง messages"""
    try:
        from datetime import datetime, timezone
        conn = get_db_connection()
        cur = conn.cursor()
        # หา Ticket ล่าสุดของผู้ใช้
        cur.execute("SELECT * FROM tickets WHERE user_id = %s ORDER BY created_at DESC LIMIT 1", (user_id,))
        row = cur.fetchone()
        if not row:
            print(f"❌ ไม่พบผู้ใช้ {user_id} ในระบบ")
            return False
        # รองรับ row เป็น dict หรือ tuple
        def get_row_value(row, key, default=None):
            if row is None:
                return default
            if isinstance(row, dict):
                return row.get(key, default)
            elif isinstance(row, tuple) and hasattr(cur, 'description') and cur.description is not None:
                columns = [desc[0] for desc in cur.description]
                if key in columns:
                    return row[columns.index(key)]
                return default
            return default
        current_text = get_row_value(row, 'textbox', "") or ""
        # --- ปรับ timestamp เป็น UTC string ไม่มี microseconds เฉพาะข้อความจาก LINE ---
        timestamp = datetime.utcnow().replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        new_text = f"{message}"
        if len(new_text) > 50000:
            new_text = new_text[-50000:]
        cur.execute("UPDATE tickets SET textbox = %s WHERE ticket_id = %s", (new_text, get_row_value(row, 'ticket_id')))
        ticket_id = get_row_value(row, 'ticket_id')
        sender_name = get_row_value(row, 'name') or "User"
        cur.execute(
            """
            INSERT INTO messages (ticket_id, sender_name, message, is_admin_message, user_id, line_id, platform, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                ticket_id,
                sender_name,
                message,
                False,
                user_id,
                user_id,
                "LINE",
                timestamp
            )
        )
        conn.commit()
        socketio.emit('new_message', {
            'ticket_id': ticket_id,
            'admin_id': None,
            'sender_name': sender_name,
            'message': message,
            'is_admin_message': False
        })
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Error saving contact message: {e}")
        traceback.print_exc()
        return False

def save_contact_request(user_id, message):
    """บันทึกคำขอติดต่อเจ้าหน้าที่ลง Google Sheet และ insert ลง messages ด้วย"""
    try:
        scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        client = gspread.authorize(creds)
        sheet = client.open("Tickets").sheet1
        cell = sheet.find(user_id)
        if not cell:
            return False
        current_text = sheet.cell(cell.row, 13).value or ""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_text = f"{current_text}[ผู้ใช้]{timestamp}: {message}"
        if len(new_text) > 50000:
            new_text = new_text[-50000:]
        sheet.update_cell(cell.row, 13, new_text)
        print(f"✅ บันทึกคำขอติดต่อเจ้าหน้าที่สำหรับ User ID: {user_id}")
        # --- เพิ่ม insert ลง messages ---
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM tickets WHERE user_id = %s ORDER BY created_at DESC LIMIT 1", (user_id,))
            row = cur.fetchone()
            def get_row_value(row, key, default=None):
                if row is None:
                    return default
                if isinstance(row, dict):
                    return row.get(key, default)
                elif isinstance(row, tuple) and hasattr(cur, 'description') and cur.description is not None:
                    columns = [desc[0] for desc in cur.description]
                    if key in columns:
                        return row[columns.index(key)]
                    return default
                return default
            ticket_id = get_row_value(row, 'ticket_id') if row else None
            sender_name = get_row_value(row, 'name') if row and get_row_value(row, 'name') else "User"
            if ticket_id:
                cur.execute(
                    """
                    INSERT INTO messages (ticket_id, sender_name, message, is_admin_message, user_id, line_id, platform, timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        ticket_id,
                        sender_name,
                        message,
                        False,
                        user_id,
                        user_id,
                        "LINE",
                        timestamp
                    )
                )
                conn.commit()
                # --- emit socket event ---
                socketio.emit('new_message', {
                    'ticket_id': ticket_id,
                    'admin_id': None,
                    'sender_name': sender_name,
                    'message': message,
                    'is_admin_message': False
                })
            cur.close()
            conn.close()
        except Exception as e:
            print(f"❌ Error saving message to messages table: {e}")
            traceback.print_exc()
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
        start_time, end_time = user_message.split('-')
        if validate_time(start_time) and validate_time(end_time):
            if is_time_before(start_time, end_time):
                # ตรวจสอบว่าวันที่เลือกเป็นวันนี้หรือไม่
                if "selected_date" in state:
                    selected_date = datetime.strptime(state["selected_date"], "%Y-%m-%d").date()
                    today = datetime.now().date()
                    if selected_date == today:
                        current_time = datetime.now().time()
                        start_time_obj = datetime.strptime(start_time, "%H:%M").time()
                        if start_time_obj < current_time:
                            reply(reply_token, f"⚠️ ไม่สามารถเลือกเวลาที่ผ่านมาแล้ว (เวลาปัจจุบัน: {current_time.strftime('%H:%M')})")
                            return
                appointment_time = user_message
            else:
                reply(reply_token, "⚠️ เวลาเริ่มต้นต้องน้อยกว่าเวลาสิ้นสุด")
                return
        else:
            reply(reply_token, "⚠️ รูปแบบเวลาไม่ถูกต้อง กรุณากรอกในรูปแบบ HH:MM-HH:MM เช่น 13:00-14:00")
            return
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
    reply(reply_token, "📂 กรุณากรอกชื่อ-นามสกุล")

def handle_ask_category(reply_token, user_id, user_message, state):
    state["category"] = user_message
    state["step"] = "ask_department"
    send_department_flex_message(reply_token)

def handle_ask_department(reply_token, user_id, user_message, state):
    if user_message in ["ผู้บริหาร/เลขานุการ", "ส่วนงานตรวจสอบภายใน", "ส่วนงานกฏหมาย", "งานสื่อสารองค์การ", "ฝ่ายนโยบายและแผน", "ฝ่ายเทคโนโลยีสารสนเทศ", "ฝ่ายบริหาร","ฝ่ายบริหารวิชาการและพัฒนาผู้ประกอบการ", "ฝ่ายตรวจสอบโลหะมีค่า", "ฝ่ายตรวจสอบอัญมณีและเครื่องประดับ", "ฝ่ายวิจัยและพัฒนามาตรฐาน", "ฝ่ายพัฒนาธุรกิจ"]:
        state["department"] = user_message
        state["step"] = "ask_phone"
        reply(reply_token, "📞 กรุณากรอกเบอร์ติดต่อกลับ")
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
    """เริ่มกระบวนการสมัครสมาชิกหรือแจ้งปัญหา"""
    # ตรวจสอบว่ามีเมนูอื่นทำงานอยู่หรือไม่
    if user_id in user_states and user_states[user_id].get("step") not in [None, ""]:
        current_service = user_states[user_id].get("service_type", "บริการปัจจุบัน")
        reply(reply_token, 
            f"⚠️ คุณกำลังใช้งาน '{current_service}' อยู่\n\n"
            "กรุณาดำเนินการให้เสร็จสิ้นก่อน หรือพิมพ์ 'ยกเลิก' เพื่อออกจากการบริการนี้\n"
            "ก่อนเลือกบริการอื่น")
        return
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
    """แสดงรายการ Ticket ทั้งหมดของผู้ใช้เฉพาะประเภท Service และ Helpdesk"""
    try:
        user_tickets = get_all_user_tickets(user_id)
        if not user_tickets:
            reply(reply_token, "⚠️ ไม่พบ Ticket บริการหรือปัญหาในระบบ")
            return
        bubbles = []
        for ticket in user_tickets:
            status_color = "#1DB446" if ticket['status'] == "Completed" else "#FF0000" if ticket['status'] == "Rejected" else "#005BBB"
            try:
                ticket_date = datetime.strptime(ticket['date'], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M")
            except:
                ticket_date = str(ticket['date'])
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
                            "color": "#005BBB",
                            "wrap": True
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
        guide_message = {
            "type": "text",
            "text": "📌 คุณสามารถดูประวัติ Ticket บริการและปัญหาย้อนหลังได้โดยกดปุ่ม 'ดูประวัติย้อนหลัง' และเลือกเดือนที่ต้องการ",
            "wrap": True
        }
        if len(bubbles) > 1:
            flex_message = {
                "type": "flex",
                "altText": "รายการ Ticket บริการและปัญหาของคุณ",
                "contents": {
                    "type": "carousel",
                    "contents": bubbles[:10]
                }
            }
        else:
            flex_message = {
                "type": "flex",
                "altText": "รายการ Ticket บริการและปัญหาของคุณ",
                "contents": bubbles[0]
            }
        send_reply_message(reply_token, [guide_message, flex_message])
    except Exception as e:
        print("❌ Error in check_latest_ticket:", str(e))
        traceback.print_exc()
        reply(reply_token, "⚠️ เกิดข้อผิดพลาดในการดึงข้อมูล Ticket")

def show_ticket_details(reply_token, ticket_id, user_id=None):
    """แสดงรายละเอียดของ Ticket ที่เลือก (เฉพาะประเภท Service และ Helpdesk)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tickets WHERE ticket_id = %s AND type IN ('Service', 'Helpdesk')",
            (ticket_id,)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            reply(reply_token, f"⚠️ ไม่พบ Ticket บริการหรือปัญหา {ticket_id} ในระบบ")
            return
        # --- ใช้ get_row_value ---
        def get_row_value(row, key, default=None):
            if row is None:
                return default
            if isinstance(row, dict):
                return row.get(key, default)
            elif isinstance(row, tuple) and hasattr(cur, 'description') and cur.description is not None:
                columns = [desc[0] for desc in cur.description]
                if key in columns:
                    return row[columns.index(key)]
                return default
            return default
        if user_id and str(get_row_value(row, 'user_id', '')).strip() != str(user_id).strip():
            reply(reply_token, f"⚠️ ไม่พบ Ticket {ticket_id} ของคุณในระบบ")
            return
        phone = str(get_row_value(row, 'phone', ''))
        phone = phone.replace("'", "")
        if phone and not phone.startswith('0'):
            phone = '0' + phone[-9:]
        found_ticket = {
            'ticket_id': get_row_value(row, 'ticket_id', 'TICKET-UNKNOWN'),
            'email': get_row_value(row, 'email', 'ไม่มีข้อมูล'),
            'name': get_row_value(row, 'name', 'ไม่มีข้อมูล'),
            'phone': phone,
            'department': get_row_value(row, 'department', 'ไม่มีข้อมูล'),
            'date': safe_datetime_to_string(get_row_value(row, 'created_at', ''), 'ไม่มีข้อมูล'),
            'status': get_row_value(row, 'status', 'New'),
            'appointment': get_row_value(row, 'appointment', 'None'),
            'requested': get_row_value(row, 'requested', 'None'),
            'report': get_row_value(row, 'report', 'None'),
            'type': get_row_value(row, 'type', 'ไม่ระบุ')
        }
        flex_message = create_ticket_flex_message(found_ticket)
        if not flex_message:
            reply(reply_token, "⚠️ เกิดข้อผิดพลาดในการสร้าง Ticket Summary")
            return
        send_reply_message(reply_token, [flex_message])
    except Exception as e:
        print("❌ Error in show_ticket_details:", str(e))
        traceback.print_exc()
        reply(reply_token, "⚠️ เกิดข้อผิดพลาดในการดึงข้อมูล Ticket")

def save_helpdesk_to_sheet(ticket_id, user_id, email, name, phone, department, report_time, appointment_time, issue_text, subgroup=None):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        formatted_phone = format_phone_number(phone)
        cur.execute('''
            INSERT INTO tickets (
                ticket_id, user_id, email, name, phone, department, created_at, 
                status, appointment, requested, report, type, subgroup
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            ticket_id,
            user_id,
            email,
            name,
            formatted_phone,
            department,
            report_time,
            "New",
            appointment_time,
            "None",
            issue_text if issue_text else "None",
            "Helpdesk",
            subgroup if subgroup else "None"
        ))
        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ Saved Helpdesk ticket with subgroup: {ticket_id} (PostgreSQL)")
        return True
    except Exception as e:
        print("❌ Error saving Helpdesk ticket with subgroup (PostgreSQL):", e)
        traceback.print_exc()
        return False

def create_ticket_flex_message(ticket_data):
    try:
        status_color = "#1DB446" if ticket_data['status'] == "Completed" else "#FF0000" if ticket_data['status'] == "Rejected" else "#005BBB"
        
        # แปลง datetime เป็น string ถ้าจำเป็น
        date_str = ticket_data['date']
        if hasattr(date_str, 'strftime'):  # ถ้าเป็น datetime object
            date_str = date_str.strftime("%Y-%m-%d %H:%M:%S")
        else:
            date_str = str(date_str)
        
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
        
        if ticket_data['type'] == "Service":
            if ticket_data['requested'] != "None":
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
                            "text": ticket_data['requested'],
                            "size": "sm",
                            "wrap": True,
                            "flex": 4
                        }
                    ]
                })
            
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
                            "align": "center",
                            "wrap": True
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
    """ส่งข้อความพร้อม Quick Reply เมนูหลัก (ยกเว้นในกรณีที่อยู่ในขั้นตอนการทำงานอื่น)"""
    # ไม่สามารถใช้ reply_token เป็น user_id ได้ ต้องแยกกรณี
    message = {
        "type": "text",
        "text": text,
        "quickReply": get_main_menu_quick_reply()
    }
    send_reply_message(reply_token, [message])

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
                        "margin": "sm",
                        "wrap": True
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
        # ตรวจสอบว่ามี subgroup หรือไม่
        if 'subgroup' in data:
            cur.execute('''
                INSERT INTO tickets (
                    ticket_id, user_id, email, name, phone, department, created_at, status, appointment, requested, report, type, subgroup
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                "None",  # Requested
                "None",  # Report
                "Information",
                data['subgroup']
            ))
        else:
            cur.execute('''
                INSERT INTO tickets (
                    ticket_id, user_id, email, name, phone, department, created_at, status, appointment, requested, report, type
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
                "None",  # Requested
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
                            "label": "แจ้งบริการ",
                            "text": "นัดหมายเวลา"
                        }
                    },
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#FF0000",
                        "action": {
                            "type": "message",
                            "label": "แจ้งปัญหา",
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
                        "color": "#1DB446",
                        "wrap": True
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
    # ตรวจสอบว่ามีเมนูอื่นทำงานอยู่หรือไม่
    if user_id in user_states and user_states[user_id].get("step") not in [None, ""]:
        current_service = user_states[user_id].get("service_type", "บริการปัจจุบัน")
        reply(reply_token, 
            f"⚠️ คุณกำลังใช้งาน '{current_service}' อยู่\n\n"
            "กรุณาดำเนินการให้เสร็จสิ้นก่อน หรือพิมพ์ 'ยกเลิก' เพื่อออกจากการบริการนี้\n"
            "ก่อนเลือกบริการอื่น")
        return
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
                        "text": "กรุณาเลือกวันเดือนและปี",
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

def send_time_picker(reply_token, selected_date, user_id=None):
    # ดึงข้อมูลเวลาปัจจุบันถ้าวันที่เลือกเป็นวันนี้
    current_time = None
    now = datetime.now()
    now_str = now.strftime('%d/%m/%Y %H:%M')
    if user_id and user_id in user_states and "current_time" in user_states[user_id]:
        current_time = datetime.strptime(user_states[user_id]["current_time"], "%H:%M").time()
    # กำหนดช่วงเวลาที่สามารถนัดหมายได้
    all_time_slots = [
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
    # กรองเวลาเฉพาะวันนี้
    time_slots = []
    if current_time:
        for slot in all_time_slots:
            start_time_str = slot["value"].split('-')[0]
            start_time = datetime.strptime(start_time_str, "%H:%M").time()
            if start_time > current_time:
                time_slots.append(slot)
    else:
        time_slots = all_time_slots

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
        "text": f"📅 วันที่คุณเลือก: {selected_date}\n⏰ เวลาปัจจุบัน: {now_str}\n\nกรุณาเลือกช่วงเวลาที่ต้องการนัดหมาย หรือพิมพ์ 'กรอกเวลาเอง' 10:00-11:00: HH:MM-HH:MM",
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
    user_states[user_id]["step"] = "ask_request"
    user_states[user_id]["appointment_datetime"] = appointment_datetime
    quick_reply_items = [
        {"type": "action", "action": {"type": "message", "label": "Hardware", "text": "Hardware"}},
        {"type": "action", "action": {"type": "message", "label": "Meeting", "text": "Meeting"}},
        {"type": "action", "action": {"type": "message", "label": "Service", "text": "Service"}},
        {"type": "action", "action": {"type": "message", "label": "Software", "text": "Software"}},
        {"type": "action", "action": {"type": "message", "label": "บริการอื่นๆ", "text": "บริการอื่นๆ"}},
        {"type": "action", "action": {"type": "message", "label": "กรอกข้อมูลเอง", "text": "กรอกข้อมูลเอง"}}
    ]
    message = {
        "type": "text",
        "text": "กรุณาเลือกประเภทบริการที่ต้องการ:",
        "quickReply": {"items": quick_reply_items}
    }
    send_reply_message(reply_token, [message])

def handle_user_request(reply_token, user_id, request_text):
    """จัดการกับความประสงค์ที่ผู้ใช้กรอก"""
    if user_id not in user_states or user_states[user_id].get("step") != "ask_request":
        reply(reply_token, "⚠️ เกิดข้อผิดพลาด กรุณาเริ่มกระบวนการใหม่")
        return
    if request_text == "กรอกข้อมูลเอง":
        reply(reply_token, "📝 กรุณากรอกรายละเอียดของบริการที่ต้องการ เช่น ตั้งค่าคอมพิวเตอร์, ขอ Link ประชุม Zoom เป็นต้น")
        user_states[user_id]["step"] = "ask_custom_request"
        return
    user_states[user_id]["request_text"] = request_text
    user_states[user_id]["step"] = "ask_subgroup"
    send_service_subgroup_quick_reply(reply_token, request_text)

def save_appointment_with_request(ticket_id, user_id, email, name, phone, department, appointment_datetime, request_text, subgroup=None):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        formatted_phone = format_phone_number(phone)
        cur.execute('''
            INSERT INTO tickets (
                ticket_id, user_id, email, name, phone, department, created_at, 
                status, appointment, requested, report, type, subgroup
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            ticket_id,
            user_id,
            email,
            name,
            formatted_phone,
            department,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "New",
            appointment_datetime,
            request_text if request_text else "None",
            "None",
            "Service",
            subgroup if subgroup else "None"
        ))
        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ Saved Service ticket with subgroup: {ticket_id} (PostgreSQL)")
        return True
    except Exception as e:
        print("❌ Error saving Service ticket with subgroup (PostgreSQL):", e)
        traceback.print_exc()
        return False

def send_ticket_summary_with_request(user_id, ticket_id, appointment_datetime, request_text, email, name, phone, department, type_value="Service"):
    try:
        # แปลง datetime เป็น string ถ้าจำเป็น
        appointment_str = appointment_datetime
        if hasattr(appointment_str, 'strftime'):  # ถ้าเป็น datetime object
            appointment_str = appointment_str.strftime("%Y-%m-%d %H:%M:%S")
        else:
            appointment_str = str(appointment_str)
        
        # แยกข้อมูลวันที่และเวลา
        try:
            date_part, time_range = appointment_str.split()
            dt = datetime.strptime(date_part, "%Y-%m-%d")
            formatted_date = dt.strftime("%d/%m/%Y")
            start_time, end_time = time_range.split('-')
        except:
            formatted_date = appointment_str
            start_time = "N/A"
            end_time = "N/A"
        
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
                            "text": f"📄 Ticket  {ticket_id}",
                            "weight": "bold",
                            "size": "lg",
                            "color": "#005BBB",
                            "wrap": True
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
                        status_row("สถานะ", "New", "#005BBB")
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
        def get_row_value(row, key, default=None):
            if row is None:
                return default
            if isinstance(row, dict):
                return row.get(key, default)
            elif isinstance(row, tuple) and hasattr(cur, 'description') and cur.description is not None:
                columns = [desc[0] for desc in cur.description]
                if key in columns:
                    return row[columns.index(key)]
                return default
            return default
        phone = str(get_row_value(row, 'phone')) if get_row_value(row, 'phone') else ''
        phone = phone.replace("'", "")
        if phone and not phone.startswith('0'):
            phone = '0' + phone[-9:]
        # แปลง datetime เป็น string
        created_at = get_row_value(row, 'created_at', '')
        if hasattr(created_at, 'strftime'):  # ถ้าเป็น datetime object
            created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")
        else:
            created_at = str(created_at)
        latest_ticket = {
            'อีเมล': get_row_value(row, 'email', ''),
            'ชื่อ': get_row_value(row, 'name', ''),
            'เบอร์ติดต่อ': phone,
            'แผนก': get_row_value(row, 'department', ''),
            'วันที่แจ้ง': created_at,
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
        if row:
            def get_row_value(row, key, default=None):
                if row is None:
                    return default
                if isinstance(row, dict):
                    return row.get(key, default)
                elif isinstance(row, tuple) and hasattr(cur, 'description') and cur.description is not None:
                    columns = [desc[0] for desc in cur.description]
                    if key in columns:
                        return row[columns.index(key)]
                    return default
                return default
            if get_row_value(row, 'email') or get_row_value(row, 'issue'):
                return True
        return False
    except Exception as e:
        print("❌ Error checking user ID (PostgreSQL):", e)
        traceback.print_exc()
        return False

def check_ticket_status(ticket_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM tickets WHERE ticket_id = %s", (ticket_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        
        if row:
            def get_row_value(row, key, default=None):
                if row is None:
                    return default
                if isinstance(row, dict):
                    return row.get(key, default)
                elif isinstance(row, tuple) and hasattr(cur, 'description') and cur.description is not None:
                    columns = [desc[0] for desc in cur.description]
                    if key in columns:
                        return row[columns.index(key)]
                    return default
                return default
            phone = str(get_row_value(row, 'phone', ''))
            phone = phone.replace("'", "")
            if phone and not phone.startswith('0'):
                phone = '0' + phone[-9:]
            
            return (
                f"\nอีเมล: {safe_dict_value(get_row_value(row, 'email'))}\n"
                f"ชื่อ: {safe_dict_value(get_row_value(row, 'name'))}\n"
                f"เบอร์ติดต่อ: {display_phone_number(phone)}\n"
                f"แผนก: {safe_dict_value(get_row_value(row, 'department'))}\n"
                f"สถานะ: {safe_dict_value(get_row_value(row, 'status'))}"
            )
        
        return None
    except Exception as e:
        print("❌ Error checking status:", e)
        traceback.print_exc()
        return None

def handle_helpdesk(reply_token, user_id):
    """เริ่มกระบวนการแจ้งปัญหา Helpdesk"""
    # ตรวจสอบว่ามีเมนูอื่นทำงานอยู่หรือไม่
    if user_id in user_states and user_states[user_id].get("step") not in [None, ""]:
        current_service = user_states[user_id].get("service_type", "บริการปัจจุบัน")
        reply(reply_token, 
            f"⚠️ คุณกำลังใช้งาน '{current_service}' อยู่\n\n"
            "กรุณาดำเนินการให้เสร็จสิ้นก่อน หรือพิมพ์ 'ยกเลิก' เพื่อออกจากการบริการนี้\n"
            "ก่อนเลือกบริการอื่น")
        return
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
        {"type": "action", "action": {"type": "message", "label": "คอมพิวเตอร์", "text": "คอมพิวเตอร์ / Hardware"}},
        {"type": "action", "action": {"type": "message", "label": "โปรแกรม", "text": "โปรแกรม / Software"}},
        {"type": "action", "action": {"type": "message", "label": "ปริ้นเตอร์", "text": "ปริ้นเตอร์ / Printer"}},
        {"type": "action", "action": {"type": "message", "label": "อุปกรณ์อื่นๆ", "text": "อุปกรณ์อื่นๆ / Devices"}},
        {"type": "action", "action": {"type": "message", "label": "การใช้งานทั่วไป", "text": "การใช้งานทั่วไป"}},
        {"type": "action", "action": {"type": "message", "label": "ข้อมูล", "text": "ข้อมูล / Data"}},
        {"type": "action", "action": {"type": "message", "label": "เน็ตเวิร์ค", "text": "เน็ตเวิร์ค / Network"}},
        {"type": "action", "action": {"type": "message", "label": "ปัญหาอื่นๆ", "text": "ปัญหาอื่นๆ"}},
    ]
    message = {
        "type": "text",
        "text": "กรุณาเลือกประเภทปัญหาที่ต้องการแจ้ง:",
        "quickReply": {"items": quick_reply_items}
    }
    send_reply_message(reply_token, [message])

def handle_helpdesk_issue(reply_token, user_id, issue_text):
    """จัดการกับปัญหาที่ผู้ใช้แจ้ง"""
    if user_id not in user_states or user_states[user_id].get("step") != "ask_helpdesk_issue":
        reply(reply_token, "⚠️ เกิดข้อผิดพลาด กรุณาเริ่มกระบวนการใหม่")
        return
    user_states[user_id]["issue_text"] = issue_text
    if issue_text == "ปัญหาอื่นๆ":
        user_states[user_id]["step"] = "ask_custom_issue"
        reply(reply_token, "📝 กรุณาระบุปัญหาที่ต้องการแจ้ง")
        return
    user_states[user_id]["step"] = "ask_helpdesk_subgroup"
    send_helpdesk_subgroup_quick_reply(reply_token, issue_text)

def send_helpdesk_subgroup_quick_reply(reply_token, issue_text):
    """ส่ง Quick Reply สำหรับรายละเอียดย่อยของปัญหา Helpdesk"""
    subgroup_options = {
        "คอมพิวเตอร์ / Hardware": [
            "เครื่องไม่เปิด", "เครื่องค้าง", "เครื่องช้า", 
            "หน้าจอเสีย", "ฮาร์ดดิสก์มีปัญหา", "อื่นๆ"
        ],
        "โปรแกรม / Software": [
            "ติดตั้งโปรแกรม", "อัปเดตโปรแกรม", "โปรแกรมค้าง",
            "โปรแกรมทำงานผิดปกติ", "ลบโปรแกรม", "อื่นๆ"
        ],
        "ปริ้นเตอร์ / Printer": [
            "พิมพ์ไม่ออก", "กระดาษติด", "สีเพี้ยน",
            "เชื่อมต่อไม่ได้", "กระดาษหมด", "อื่นๆ"
        ],
        "อุปกรณ์อื่นๆ / Devices": [
            "เมาส์", "คีย์บอร์ด", "ลำโพง",
            "ไมโครโฟน", "เว็บแคม", "อื่นๆ"
        ],
        "เน็ตเวิร์ค / Network": [
            "เชื่อมต่อ Wi-Fi ไม่ได้", "อินเทอร์เน็ตช้า",
            "เชื่อมต่อเครือข่ายไม่ได้", "VPN มีปัญหา", "อื่นๆ"
        ],
        "การใช้งานทั่วไป": [
            "ลืมรหัสผ่าน", "ล็อกอินไม่ได้",
            "อีเมลมีปัญหา", "อื่นๆ"
        ],
        "ข้อมูล / Data": [
            "ข้อมูลหาย", "กู้ข้อมูล", "แบ็กอัปข้อมูล",
            "โอนย้ายข้อมูล", "อื่นๆ"
        ]
    }
    options = subgroup_options.get(issue_text, ["อื่นๆ"])
    quick_reply_items = [
        {
            "type": "action",
            "action": {
                "type": "message",
                "label": opt,
                "text": opt
            }
        } for opt in options
    ]
    quick_reply_items.append({
        "type": "action",
        "action": {
            "type": "message",
            "label": "กรอกรายละเอียดเอง",
            "text": "กรอกรายละเอียดเอง"
        }
    })
    message = {
        "type": "text",
        "text": f"กรุณาเลือกรายละเอียดย่อยสำหรับ {issue_text} หรือกรอกเอง:",
        "quickReply": {
            "items": quick_reply_items
        }
    }
    send_reply_message(reply_token, [message])

def handle_helpdesk_subgroup(reply_token, user_id, subgroup_text):
    """จัดการกับรายละเอียดย่อยของปัญหา Helpdesk"""
    if user_id not in user_states or user_states[user_id].get("step") != "ask_helpdesk_subgroup":
        reply(reply_token, "⚠️ เกิดข้อผิดพลาด กรุณาเริ่มกระบวนการใหม่")
        return
    if subgroup_text == "กรอกรายละเอียดเอง":
        user_states[user_id]["step"] = "ask_custom_helpdesk_subgroup"
        reply(reply_token, "📝 กรุณากรอกรายละเอียดย่อยด้วยตัวเอง")
        return
    user_states[user_id]["subgroup"] = subgroup_text
    user_states[user_id]["step"] = "pre_helpdesk"
    confirm_msg = create_confirm_message(
        "helpdesk",
        f"แจ้งปัญหา: {user_states[user_id]['issue_text']}\n"
        f"รายละเอียดย่อย: {subgroup_text}"
    )
    send_reply_message(reply_token, [confirm_msg])

def handle_custom_helpdesk_subgroup(reply_token, user_id, custom_text):
    """จัดการกับรายละเอียดย่อยที่ผู้ใช้กรอกเอง"""
    if user_id not in user_states or user_states[user_id].get("step") != "ask_custom_helpdesk_subgroup":
        reply(reply_token, "⚠️ เกิดข้อผิดพลาด กรุณาเริ่มกระบวนการใหม่")
        return
    user_states[user_id]["subgroup"] = custom_text
    user_states[user_id]["step"] = "pre_helpdesk"
    confirm_msg = create_confirm_message(
        "helpdesk",
        f"แจ้งปัญหา: {user_states[user_id]['issue_text']}\n"
        f"รายละเอียดย่อย: {custom_text}"
    )
    send_reply_message(reply_token, [confirm_msg])

def send_helpdesk_summary(user_id, ticket_id, report_time, issue_text, email, name, phone, department, subgroup=None):
    try:
        report_time_str = report_time
        if hasattr(report_time_str, 'strftime'):
            report_time_str = report_time_str.strftime("%Y-%m-%d %H:%M:%S")
        else:
            report_time_str = str(report_time_str)
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
                        info_row("วันที่แจ้ง", report_time_str),
                        {"type": "separator", "margin": "md"},
                        info_row("ประเภท", "Helpdesk"),
                        info_row("ปัญหาหลัก", issue_text),
                        info_row("รายละเอียดย่อย", subgroup if subgroup else "ไม่มี"),
                        status_row("สถานะ", "New", "#005BBB")
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
    """ดึง Ticket ทั้งหมดของผู้ใช้จาก PostgreSQL เฉพาะประเภท Service และ Helpdesk"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tickets WHERE user_id = %s AND type IN ('Service', 'Helpdesk') ORDER BY created_at DESC",
            (user_id,)
        )
        rows = cur.fetchall()
        user_tickets = []
        columns = [desc[0] for desc in cur.description] if hasattr(cur, 'description') and cur.description is not None else []
        for row in rows:
            def get_row_value(row, key, default=None):
                if row is None:
                    return default
                if isinstance(row, dict):
                    return row.get(key, default)
                elif isinstance(row, tuple) and columns:
                    if key in columns:
                        return row[columns.index(key)]
                    return default
                return default
            phone = str(get_row_value(row, 'phone')) if get_row_value(row, 'phone') else ''
            phone = phone.replace("'", "")
            if phone and not phone.startswith('0'):
                phone = '0' + phone[-9:]
            created_at = get_row_value(row, 'created_at', 'ไม่มีข้อมูล')
            # ตรวจสอบชนิดข้อมูลก่อนเรียก strftime
            if isinstance(created_at, datetime):
                created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")
            else:
                created_at = str(created_at)
            ticket_data = {
                'ticket_id': get_row_value(row, 'ticket_id', 'TICKET-UNKNOWN'),
                'email': get_row_value(row, 'email', 'ไม่มีข้อมูล'),
                'name': get_row_value(row, 'name', 'ไม่มีข้อมูล'),
                'phone': phone,
                'department': get_row_value(row, 'department', 'ไม่มีข้อมูล'),
                'date': created_at,
                'status': get_row_value(row, 'status', 'New'),
                'appointment': get_row_value(row, 'appointment', 'None'),
                'requested': get_row_value(row, 'requested', 'None'),
                'report': get_row_value(row, 'report', 'None'),
                'type': get_row_value(row, 'type', 'ไม่ระบุ')
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
                        "text": f"คุณต้องการเรียกใช้ {action_type} ยืนยันหรือไม่?",
                        "margin": "md",
                        "size": "md"
                    },
                    {
                        "type": "separator",
                        "margin": "lg"
                    },
                    {
                        "type": "text",
                        "text": details,  # แสดงข้อความทั้งหมด ไม่ตัด ...
                        "margin": "lg",
                        "wrap": True,
                        "size": "sm",
                        "color": "#666666"
                    },
                    {
                        "type": "text",
                        "text": "พิมพ์ 'ยกเลิก' เพื่อออกจากการดำเนินการนี้",
                        "margin": "lg",
                        "size": "xs",
                        "color": "#AAAAAA"
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

# --- เพิ่มฟังก์ชัน Service Subgroup ---
def send_service_subgroup_quick_reply(reply_token, request_text):
    """ส่ง Quick Reply สำหรับรายละเอียดย่อยของบริการ"""
    subgroup_options = {
        "Hardware": ["ลงทะเบียน USB", "ตรวจสอบอุปกรณ์", "ติดตั้งอุปกรณ์", "อื่นๆ"],
        "Meeting": ["ขอ Link ประชุม / Zoom", "เชื่อมต่อ TV", "ตั้งค่าห้องประชุม", "อื่นๆ"],
        "Service": ["ขอยืมอุปกรณ์", "เชื่อมต่ออุปกรณ์", "ซ่อมบำรุง", "อื่นๆ"],
        "Software": ["ติดตั้งโปรแกรม", "อัปเดตโปรแกรม", "ลบโปรแกรม", "อื่นๆ"],
        "บริการอื่นๆ": ["ขอคำปรึกษา", "ฝึกอบรม", "อื่นๆ"]
    }
    options = subgroup_options.get(request_text, ["อื่นๆ"])
    quick_reply_items = [
        {"type": "action", "action": {"type": "message", "label": opt, "text": opt}} for opt in options
    ]
    quick_reply_items.append({
        "type": "action",
        "action": {"type": "message", "label": "กรอกรายละเอียดเอง", "text": "กรอกรายละเอียดเอง"}
    })
    message = {
        "type": "text",
        "text": f"กรุณาเลือกรายละเอียดย่อยสำหรับ {request_text} หรือกรอกเอง:",
        "quickReply": {"items": quick_reply_items}
    }
    send_reply_message(reply_token, [message])

def handle_service_subgroup(reply_token, user_id, subgroup_text):
    if user_id not in user_states or user_states[user_id].get("step") != "ask_subgroup":
        reply(reply_token, "⚠️ เกิดข้อผิดพลาด กรุณาเริ่มกระบวนการใหม่")
        return
    if subgroup_text == "กรอกรายละเอียดเอง":
        user_states[user_id]["step"] = "ask_custom_subgroup"
        reply(reply_token, "📝 กรุณากรอกรายละเอียดย่อยด้วยตัวเอง")
        return
    user_states[user_id]["subgroup"] = subgroup_text
    user_states[user_id]["step"] = "pre_service"
    confirm_msg = create_confirm_message("service", f"นัดหมาย: {user_states[user_id]['appointment_datetime']}\nความประสงค์: {user_states[user_id]['request_text']}\nรายละเอียดย่อย: {subgroup_text}")
    send_reply_message(reply_token, [confirm_msg])

def handle_custom_subgroup(reply_token, user_id, custom_text):
    if user_id not in user_states or user_states[user_id].get("step") != "ask_custom_subgroup":
        reply(reply_token, "⚠️ เกิดข้อผิดพลาด กรุณาเริ่มกระบวนการใหม่")
        return
    user_states[user_id]["subgroup"] = custom_text
    user_states[user_id]["step"] = "pre_service"
    confirm_msg = create_confirm_message("service", f"นัดหมาย: {user_states[user_id]['appointment_datetime']}\nความประสงค์: {user_states[user_id]['request_text']}\nรายละเอียดย่อย: {custom_text}")
    send_reply_message(reply_token, [confirm_msg])

def handle_custom_request(reply_token, user_id, custom_text):
    """จัดการกับความประสงค์ที่ผู้ใช้กรอกเอง"""
    if user_id not in user_states or user_states[user_id].get("step") != "ask_custom_request":
        reply(reply_token, "⚠️ เกิดข้อผิดพลาด กรุณาเริ่มกระบวนการใหม่")
        return
    user_states[user_id]["request_text"] = custom_text
    user_states[user_id]["step"] = "ask_subgroup"
    quick_reply_items = [
        {"type": "action", "action": {"type": "message", "label": "ลงทะเบียน USB", "text": "ลงทะเบียน USB"}},
        {"type": "action", "action": {"type": "message", "label": "ตรวจสอบอุปกรณ์", "text": "ตรวจสอบอุปกรณ์"}},
        {"type": "action", "action": {"type": "message", "label": "ขอ Link ประชุม / Zoom", "text": "ขอ Link ประชุม / Zoom"}},
        {"type": "action", "action": {"type": "message", "label": "เชื่อมต่อ TV", "text": "เชื่อมต่อ TV"}},
        {"type": "action", "action": {"type": "message", "label": "เชื่อมต่ออุปกรณ์", "text": "เชื่อมต่ออุปกรณ์"}},
        {"type": "action", "action": {"type": "message", "label": "ติดตั้งโปรแกรม", "text": "ติดตั้งโปรแกรม"}},
        {"type": "action", "action": {"type": "message", "label": "อื่นๆ", "text": "อื่นๆ"}},
        {"type": "action", "action": {"type": "message", "label": "กรอกรายละเอียดเอง", "text": "กรอกรายละเอียดเอง"}}
    ]
    message = {
        "type": "text",
        "text": f"กรุณาเลือกรายละเอียดย่อยสำหรับ '{custom_text}' หรือกรอกเอง:",
        "quickReply": {"items": quick_reply_items}
    }
    send_reply_message(reply_token, [message])

# --- ฟังก์ชัน Quick Reply ---
def get_welcome_quick_reply():
    """สร้าง Quick Reply สำหรับการเริ่มแชท"""
    return {
        "items": [
            {
                "type": "action",
                "action": {
                    "type": "message",
                    "label": "แจ้งปัญหา",
                    "text": "แจ้งปัญหา"
                }
            },
            {
                "type": "action",
                "action": {
                    "type": "message",
                    "label": "สมัครสมาชิก",
                    "text": "สมัครสมาชิก"
                }
            },
            {
                "type": "action",
                "action": {
                    "type": "message",
                    "label": "จบ",
                    "text": "จบ"
                }
            }
        ]
    }

def get_main_menu_quick_reply():
    """สร้าง Quick Reply สำหรับเมนูหลัก"""
    return {
        "items": [
            {
                "type": "action",
                "action": {
                    "type": "message",
                    "label": "แจ้งปัญหา",
                    "text": "แจ้งปัญหา"
                }
            },
            {
                "type": "action",
                "action": {
                    "type": "message",
                    "label": "แชทกับเจ้าหน้าที่",
                    "text": "ติดต่อเจ้าหน้าที่"
                }
            },
            {
                "type": "action",
                "action": {
                    "type": "message",
                    "label": "เช็คสถานะ",
                    "text": "เช็กสถานะ"
                }
            },
            {
                "type": "action",
                "action": {
                    "type": "message",
                    "label": "จบ",
                    "text": "จบ"
                }
            }
        ]
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    GOOGLE_CREDS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    socketio.run(app, host="0.0.0.0", port=port, debug=True)