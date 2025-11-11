import os
import re
import imaplib
import smtplib
import email
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template

load_dotenv()

IMAP_SERVER = 'imap.gmx.com'
IMAP_PORT = 993
SMTP_SERVER = 'mail.gmx.com'
SMTP_PORT = 587
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

app = Flask(__name__)

def connect_imap():
    imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    imap.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    return imap

def decode_str(s):
    parts = decode_header(s or "")
    text, enc = parts[0]
    if isinstance(text, bytes):
        return text.decode(enc or "utf-8", errors="ignore")
    return text

def html_to_text(html):
    if not html:
        return ""
    # Simple HTML tag remover:
    text = re.sub('<[^<]+?>', '', html)
    # Replace HTML entities:
    text = re.sub('&nbsp;', ' ', text)
    text = re.sub('&amp;', '&', text)
    text = re.sub('&lt;', '<', text)
    text = re.sub('&gt;', '>', text)
    text = re.sub('&#39;', "'", text)
    text = re.sub('&quot;', '"', text)
    return text.strip()

def fetch_emails(imap):
    imap.select("INBOX")
    status, messages = imap.search(None, "ALL")
    if status != "OK":
        return []

    email_ids = messages[0].split()
    emails = []
    for eid in reversed(email_ids[-20:]):  # Latest 20 emails
        status, msg_data = imap.fetch(eid, "(RFC822 FLAGS)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        raw_flags = msg_data[0][0].decode() if msg_data and msg_data[0] else ""
        is_unread = "\\Seen" not in raw_flags
        msg = email.message_from_bytes(msg_data[0][1])
        subject = decode_str(msg.get("Subject"))
        sender = decode_str(msg.get("From"))
        date_str = msg.get("Date")
        try:
            date_fmt = parsedate_to_datetime(date_str).strftime("%Y-%m-%d %H:%M") if date_str else ""
        except Exception:
            date_fmt = ""

        # Extract the body (prefer plain text, otherwise html)
        plain_body = ""
        html_body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain" and not plain_body:
                    try:
                        plain_body = part.get_payload(decode=True).decode(errors="ignore").strip()
                    except Exception:
                        plain_body = ""
                if ctype == "text/html" and not html_body:
                    try:
                        html_body = part.get_payload(decode=True).decode(errors="ignore").strip()
                    except Exception:
                        html_body = ""
        else:
            ctype = msg.get_content_type()
            try:
                body = msg.get_payload(decode=True)
                if body:
                    body = body.decode(errors="ignore").strip()
                    if ctype == "text/plain":
                        plain_body = body
                    elif ctype == "text/html":
                        html_body = body
            except Exception:
                pass

        # Use plain_body if available, else html_body for main content
        body = plain_body or html_body or ""

        # For preview, always use text-only version
        preview = (html_to_text(body).replace("\n", " ").strip()[:90] + "...") if body else ""

        emails.append({
            "id": eid.decode(),
            "sender": sender,
            "subject": subject,
            "preview": preview,
            "unread": is_unread,
            "date_str": date_fmt,
            "account": "gmx"
        })
    return emails

@app.route("/api/messages", methods=["GET"])
def api_messages():
    try:
        imap = connect_imap()
        emails = fetch_emails(imap)
        imap.logout()
        return jsonify(emails)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/inbox", methods=["GET"])
def api_inbox():
    try:
        imap = connect_imap()
        emails = fetch_emails(imap)
        imap.logout()
        total_count = len(emails)
        total_unread = sum(1 for email in emails if email["unread"])
        return jsonify({
            "all": {"count": total_count, "unread": total_unread},
            "accounts": [{"key": "gmx", "label": "GMX", "count": total_count, "unread": total_unread}]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/message/<account>/<id>", methods=["GET"])
def api_message(account, id):
    try:
        imap = connect_imap()
        imap.select("INBOX")
        status, msg_data = imap.fetch(id, "(RFC822)")
        if status != "OK":
            return jsonify({"error": "Message not found"}), 404
        msg = email.message_from_bytes(msg_data[0][1])
        subject = decode_str(msg.get("Subject"))
        sender = decode_str(msg.get("From"))
        receiver = decode_str(msg.get("To", EMAIL_ACCOUNT))
        date_str = msg.get("Date", "")
        try:
            date_fmt = parsedate_to_datetime(date_str).strftime("%Y-%m-%d %H:%M")
        except Exception:
            date_fmt = date_str

        # Extract the body (prefer html)
        plain_body = ""
        html_body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain" and not plain_body:
                    try:
                        plain_body = part.get_payload(decode=True).decode(errors="ignore")
                    except Exception:
                        plain_body = ""
                if ctype == "text/html" and not html_body:
                    try:
                        html_body = part.get_payload(decode=True).decode(errors="ignore")
                    except Exception:
                        html_body = ""
        else:
            ctype = msg.get_content_type()
            try:
                body = msg.get_payload(decode=True)
                if body:
                    body = body.decode(errors="ignore").strip()
                    if ctype == "text/plain":
                        plain_body = body
                    elif ctype == "text/html":
                        html_body = body
            except Exception:
                pass

        # Show HTML body if available, else plain text
        body = html_body or plain_body or ""

        imap.store(id, "+FLAGS", "\\Seen")
        imap.expunge()
        imap.logout()
        return jsonify({
            "subject": subject,
            "sender": sender,
            "to": receiver,
            "date_str": date_fmt,
            "body": body,
            "account_label": "GMX"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/send", methods=["POST"])
def api_send():
    data = request.json or {}
    to = data.get("to")
    subject = data.get("subject", "")
    body = data.get("body", "")
    if not to:
        return jsonify({"error": "Missing 'to' field"}), 400

    msg = MIMEMultipart()
    msg["From"] = EMAIL_ACCOUNT
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ACCOUNT, to, msg.as_string())
        server.quit()
        return jsonify({"status": "sent"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
