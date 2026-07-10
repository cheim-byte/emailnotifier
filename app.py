import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
import anthropic
import requests
import json
import os
import threading
import time
from datetime import datetime
from flask import Flask, render_template, jsonify, request
import sqlite3

app = Flask(__name__)

# Configuration
EMAIL = os.environ.get("EMAIL", "info@ironwood-solutions-llc.com")
PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
IMAP_SERVER = "mail.hostedemail.com"
IMAP_PORT = 993
SMTP_SERVER = "smtp.hostedemail.com"
SMTP_PORT = 465
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "ironwood-email-alerts1337")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"
NTFY_COMMAND_TOPIC = os.environ.get("NTFY_COMMAND_TOPIC", "ironwood-email-commands1337")
APP_URL = os.environ.get("APP_URL", "")  # e.g. https://web-production-8e399.up.railway.app
APP_TOKEN = os.environ.get("APP_TOKEN", "changeme")  # simple auth token for action buttons
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 60))  # seconds

# Anthropic client
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

IRONWOOD_CONTEXT = """
You are an AI assistant for Ironwood Solutions LLC, a federally registered small business prime contractor 
and pass-through/reseller in federal and state government procurement.

Company Details:
- President: Chris Heim (Christopher Heim Jr.)
- Email: info@ironwood-solutions-llc.com
- Phone: 862-254-3036
- Address: 106 Carol Ct, Dingmans Ferry, PA 18328
- UEI: CCYFG2PKGH43, CAGE: 21PM4
- Tagline: "Plan. Execute. Deliver. Repeat."
- Target margins: 10-18%

Active bids and focus areas:
- Federal government RFQs and RFPs
- Supplier quotes and procurement
- GovCon (Government Contracting) opportunities
- Current active bid: FA822826Q0009 (Air Force/Hill AFB, Eaton Sure Power C5D5.0 power supply, 20 units)
- Current active bid: 36C25526Q0486 (VA NCO 15, disposable cubicle curtains, 240 units)

Email style rules:
- Plain prose, first person
- No bold/bullets except line items
- Professional but direct tone
- Never expose supplier cost or margin
- Standard signature: Chris Heim | President | Ironwood Solutions LLC | 862-254-3036 | info@ironwood-solutions-llc.com
"""

def init_db():
    conn = sqlite3.connect('emails.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS emails
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  message_id TEXT UNIQUE,
                  sender TEXT,
                  subject TEXT,
                  body TEXT,
                  received_at TEXT,
                  importance TEXT,
                  importance_reason TEXT,
                  draft_reply TEXT,
                  status TEXT DEFAULT 'pending',
                  sent_at TEXT)''')
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect('emails.db')
    conn.row_factory = sqlite3.Row
    return conn

def decode_str(s):
    if s is None:
        return ""
    decoded = decode_header(s)
    result = ""
    for part, enc in decoded:
        if isinstance(part, bytes):
            result += part.decode(enc or 'utf-8', errors='replace')
        else:
            result += part
    return result

def get_email_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                    break
                except:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode('utf-8', errors='replace')
        except:
            body = str(msg.get_payload())
    return body[:3000]  # Limit body size

def analyze_email_with_claude(sender, subject, body):
    prompt = f"""
Analyze this email received by Ironwood Solutions LLC and:
1. Determine importance (HIGH/MEDIUM/LOW) based on:
   - HIGH: Active bids, supplier quotes, government contracting officers, urgent deadlines, payment/contract issues
   - MEDIUM: General business inquiries, potential opportunities, follow-ups
   - LOW: Marketing, newsletters, spam, irrelevant

2. Write a professional draft reply appropriate for the situation.

Email Details:
From: {sender}
Subject: {subject}
Body: {body}

Respond in JSON format only:
{{
  "importance": "HIGH/MEDIUM/LOW",
  "importance_reason": "brief reason why",
  "draft_reply": "full draft reply text including signature"
}}
"""
    
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=IRONWOOD_CONTEXT,
        messages=[{"role": "user", "content": prompt}]
    )
    
    text = response.content[0].text.strip()
    # Strip markdown if present
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)

def send_ntfy_notification(title, message, priority="default", tags="email", actions=None):
    try:
        headers = {
            "Title": title,
            "Priority": priority,
            "Tags": tags
        }
        if actions:
            headers["Actions"] = actions
        requests.post(
            NTFY_URL,
            data=message.encode('utf-8'),
            headers=headers,
            timeout=10
        )
    except Exception as e:
        print(f"Ntfy error: {e}")

def check_emails():
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL, PASSWORD)
        mail.select("inbox")
        
        # Search for unseen emails
        _, data = mail.search(None, "UNSEEN")
        email_ids = data[0].split()
        
        conn = get_db()
        
        for eid in email_ids:
            _, msg_data = mail.fetch(eid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            
            message_id = msg.get("Message-ID", str(eid))
            
            # Check if already processed
            existing = conn.execute("SELECT id FROM emails WHERE message_id = ?", (message_id,)).fetchone()
            if existing:
                continue
            
            sender = decode_str(msg.get("From", ""))
            subject = decode_str(msg.get("Subject", "(No Subject)"))
            body = get_email_body(msg)
            received_at = datetime.now().isoformat()
            
            print(f"Processing email: {subject} from {sender}")
            
            # Analyze with Claude
            try:
                analysis = analyze_email_with_claude(sender, subject, body)
                importance = analysis.get("importance", "MEDIUM")
                importance_reason = analysis.get("importance_reason", "")
                draft_reply = analysis.get("draft_reply", "")
            except Exception as e:
                print(f"Claude analysis error: {e}")
                importance = "MEDIUM"
                importance_reason = "Analysis failed"
                draft_reply = ""
            
            # Save to DB
            cur = conn.execute("""INSERT OR IGNORE INTO emails 
                           (message_id, sender, subject, body, received_at, importance, importance_reason, draft_reply, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                        (message_id, sender, subject, body, received_at, importance, importance_reason, draft_reply))
            conn.commit()
            email_db_id = cur.lastrowid
            
            # Send ntfy notification
            priority_map = {"HIGH": "urgent", "MEDIUM": "default", "LOW": "low"}
            tags_map = {"HIGH": "rotating_light,email", "MEDIUM": "email", "LOW": "email"}
            
            # Full draft in notification (ntfy supports up to ~4000 chars)
            draft_preview = draft_reply if len(draft_reply) <= 3500 else (draft_reply[:3500] + "\n...[truncated - see dashboard]")
            
            notif_title = f"[{importance}] #{email_db_id} {subject[:40]}"
            notif_body = f"From: {sender[:60]}\n{importance_reason}\n\n--- DRAFT REPLY ---\n{draft_preview}\n\nReply via command topic: send {email_db_id} | redo {email_db_id} <notes> | dismiss {email_db_id}"
            
            actions = None
            if APP_URL:
                actions = (
                    f"http, Approve & Send, {APP_URL}/api/emails/{email_db_id}/send-approved?token={APP_TOKEN}, method=POST, clear=true; "
                    f"view, Open Dashboard, {APP_URL}"
                )
            
            send_ntfy_notification(
                notif_title, 
                notif_body,
                priority=priority_map.get(importance, "default"),
                tags=tags_map.get(importance, "email"),
                actions=actions
            )
        
        conn.close()
        mail.logout()
        
    except Exception as e:
        print(f"Email check error: {e}")

def send_email_reply(to, subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL
        msg['To'] = to
        msg['Subject'] = f"Re: {subject}" if not subject.startswith("Re:") else subject
        msg.attach(MIMEText(body, 'plain'))
        
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(EMAIL, PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Send email error: {e}")
        return False

def revise_draft_with_claude(sender, subject, body, current_draft, instructions):
    prompt = f"""
Here is an email received by Ironwood Solutions LLC and the current draft reply.
Revise the draft according to Chris's instructions.

Original Email:
From: {sender}
Subject: {subject}
Body: {body}

Current Draft:
{current_draft}

Chris's Instructions: {instructions}

Respond in JSON format only:
{{
  "draft_reply": "full revised draft reply text including signature"
}}
"""
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=IRONWOOD_CONTEXT,
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)["draft_reply"]

def approve_and_send(email_id):
    """Send the stored draft for an email. Returns (success, info)."""
    conn = get_db()
    email_row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    if not email_row:
        conn.close()
        return False, "Email not found"
    if email_row['status'] == 'sent':
        conn.close()
        return False, "Already sent"
    if not email_row['draft_reply']:
        conn.close()
        return False, "No draft available"
    
    success = send_email_reply(email_row['sender'], email_row['subject'], email_row['draft_reply'])
    if success:
        conn.execute("UPDATE emails SET status = 'sent', sent_at = ? WHERE id = ?",
                    (datetime.now().isoformat(), email_id))
        conn.commit()
        send_ntfy_notification(
            f"Reply Sent: #{email_id} {email_row['subject'][:40]}",
            f"Reply sent to {email_row['sender'][:60]}",
            tags="white_check_mark,email"
        )
    conn.close()
    return success, "Sent" if success else "SMTP send failed"

def command_listener_loop():
    """Long-poll the ntfy command topic for commands: send N, dismiss N, redo N <instructions>"""
    print("Command listener started...")
    since = "now"
    while True:
        try:
            url = f"https://ntfy.sh/{NTFY_COMMAND_TOPIC}/json?poll=1&since={since}"
            resp = requests.get(url, timeout=30)
            for line in resp.text.strip().split("\n"):
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except:
                    continue
                if msg.get("event") != "message":
                    continue
                since = str(msg.get("time", "now"))
                text = msg.get("message", "").strip()
                handle_command(text)
        except Exception as e:
            print(f"Command listener error: {e}")
        time.sleep(15)

def handle_command(text):
    parts = text.split(None, 2)
    if len(parts) < 2:
        return
    cmd = parts[0].lower()
    try:
        email_id = int(parts[1].lstrip("#"))
    except ValueError:
        send_ntfy_notification("Command Error", f"Couldn't parse email number in: {text}", tags="warning")
        return
    
    if cmd == "send":
        success, info = approve_and_send(email_id)
        if not success:
            send_ntfy_notification(f"Send Failed: #{email_id}", info, tags="warning")
    
    elif cmd == "dismiss":
        conn = get_db()
        conn.execute("UPDATE emails SET status = 'dismissed' WHERE id = ?", (email_id,))
        conn.commit()
        conn.close()
        send_ntfy_notification(f"Dismissed: #{email_id}", "Email dismissed.", tags="wastebasket")
    
    elif cmd == "redo":
        instructions = parts[2] if len(parts) > 2 else "Improve the draft."
        conn = get_db()
        email_row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
        if not email_row:
            conn.close()
            send_ntfy_notification(f"Redo Failed: #{email_id}", "Email not found", tags="warning")
            return
        try:
            new_draft = revise_draft_with_claude(
                email_row['sender'], email_row['subject'], email_row['body'],
                email_row['draft_reply'] or "", instructions
            )
            conn.execute("UPDATE emails SET draft_reply = ? WHERE id = ?", (new_draft, email_id))
            conn.commit()
            preview = new_draft if len(new_draft) <= 3500 else (new_draft[:3500] + "\n...[truncated - see dashboard]")
            actions = None
            if APP_URL:
                actions = f"http, Approve & Send, {APP_URL}/api/emails/{email_id}/send-approved?token={APP_TOKEN}, method=POST, clear=true"
            send_ntfy_notification(
                f"Revised Draft: #{email_id} {email_row['subject'][:35]}",
                f"--- NEW DRAFT ---\n{preview}\n\nsend {email_id} | redo {email_id} <notes> | dismiss {email_id}",
                tags="pencil,email",
                actions=actions
            )
        except Exception as e:
            send_ntfy_notification(f"Redo Failed: #{email_id}", str(e)[:200], tags="warning")
        conn.close()



def email_monitor_loop():
    print("Email monitor started...")
    while True:
        check_emails()
        time.sleep(CHECK_INTERVAL)

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/emails/<int:email_id>/send-approved', methods=['POST', 'GET'])
def send_approved(email_id):
    token = request.args.get('token', '')
    if token != APP_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401
    success, info = approve_and_send(email_id)
    return jsonify({"success": success, "info": info})

@app.route('/api/emails')
def get_emails():
    conn = get_db()
    emails = conn.execute("""SELECT * FROM emails ORDER BY received_at DESC LIMIT 50""").fetchall()
    conn.close()
    return jsonify([dict(e) for e in emails])

@app.route('/api/emails/<int:email_id>')
def get_email(email_id):
    conn = get_db()
    email_row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    conn.close()
    if email_row:
        return jsonify(dict(email_row))
    return jsonify({"error": "Not found"}), 404

@app.route('/api/emails/<int:email_id>/send', methods=['POST'])
def send_reply(email_id):
    data = request.json
    draft = data.get('draft', '')
    
    conn = get_db()
    email_row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    
    if not email_row:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    
    success = send_email_reply(email_row['sender'], email_row['subject'], draft)
    
    if success:
        conn.execute("UPDATE emails SET status = 'sent', sent_at = ?, draft_reply = ? WHERE id = ?",
                    (datetime.now().isoformat(), draft, email_id))
        conn.commit()
        
        send_ntfy_notification(
            f"Reply Sent: {email_row['subject'][:40]}",
            f"Reply sent to {email_row['sender'][:50]}",
            tags="white_check_mark,email"
        )
    
    conn.close()
    return jsonify({"success": success})

@app.route('/api/emails/<int:email_id>/dismiss', methods=['POST'])
def dismiss_email(email_id):
    conn = get_db()
    conn.execute("UPDATE emails SET status = 'dismissed' WHERE id = ?", (email_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/emails/<int:email_id>/regenerate', methods=['POST'])
def regenerate_draft(email_id):
    conn = get_db()
    email_row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    
    if not email_row:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    
    try:
        analysis = analyze_email_with_claude(email_row['sender'], email_row['subject'], email_row['body'])
        draft = analysis.get("draft_reply", "")
        conn.execute("UPDATE emails SET draft_reply = ? WHERE id = ?", (draft, email_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "draft": draft})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/api/check-now', methods=['POST'])
def check_now():
    threading.Thread(target=check_emails, daemon=True).start()
    return jsonify({"success": True, "message": "Checking emails now..."})

@app.route('/api/stats')
def get_stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM emails WHERE status = 'pending'").fetchone()[0]
    sent = conn.execute("SELECT COUNT(*) FROM emails WHERE status = 'sent'").fetchone()[0]
    high = conn.execute("SELECT COUNT(*) FROM emails WHERE importance = 'HIGH' AND status = 'pending'").fetchone()[0]
    conn.close()
    return jsonify({"total": total, "pending": pending, "sent": sent, "high_priority": high})

# Initialize DB and start the monitor thread on import (works under gunicorn)
init_db()
monitor_thread = threading.Thread(target=email_monitor_loop, daemon=True)
monitor_thread.start()
command_thread = threading.Thread(target=command_listener_loop, daemon=True)
command_thread.start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
