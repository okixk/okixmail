import os
import re
import base64
import imaplib
import smtplib
import email
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template, send_file
from io import BytesIO

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

def fetch_emails(imap, mailbox="INBOX"):
    """
    Fetch latest emails from the given IMAP mailbox.
    """
    imap.select(mailbox)
    status, messages = imap.search(None, "ALL")
    if status != "OK":
        return []

    email_ids = messages[0].split()
    emails = []
    for eid in reversed(email_ids[-20:]):
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

def decode_imap_utf7(name):
    """
    Decode IMAP modified UTF-7 (e.g. 'Gel&APY-scht' -> 'Gelöscht').
    """
    if isinstance(name, bytes):
        name = name.decode("ascii", errors="ignore")

    result = []
    i = 0
    while i < len(name):
        ch = name[i]
        if ch == "&":
            j = name.find("-", i)
            if j == -1:
                result.append("&")
                i += 1
                continue

            if j == i + 1:
                result.append("&")
                i = j + 1
                continue

            # IMAP uses modified base64 ("," instead of "/" and no padding)
            b64 = name[i + 1 : j].replace(",", "/")
            # add padding to multiple of 4
            b64 += "=" * ((4 - len(b64) % 4) % 4)

            try:
                decoded_bytes = base64.b64decode(b64)
                result.append(decoded_bytes.decode("utf-16-be", errors="replace"))
            except Exception:
                # if decoding fails, keep the original chunk
                result.append(name[i : j + 1])

            i = j + 1
        else:
            result.append(ch)
            i += 1

    return "".join(result)

def _parse_mailbox_name(line):
    """
    Extract mailbox name from an IMAP LIST response line.
    Example line: b'(\\HasNoChildren) "/" "INBOX"'
    """
    try:
        s = line.decode()
    except AttributeError:
        s = str(line)

    # RFC3501-ish: (<flags>) "<delimiter>" "<name>"
    m = re.match(r'\((?P<flags>.*?)\)\s+"(?P<delim>[^"]+)"\s+(?P<name>.+)', s)
    if m:
        name = m.group("name").strip()
    else:
        # Fallback: last piece after delimiter
        name = s.split(' "/" ')[-1].strip()

    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    return name


def list_folders_with_counts(imap):
    """
    Return folders with message + unread counts, queried directly from the server.
    Uses the raw IMAP name as 'key' and a decoded UTF-7 label for display.
    """
    folders = []
    status, data = imap.list()
    if status != "OK" or not data:
        return folders

    for raw in data:
        encoded_name = _parse_mailbox_name(raw)   # e.g. 'Gel&APY-scht'
        if not encoded_name:
            continue

        # Decode for human-readable label, e.g. 'Gelöscht'
        label = decode_imap_utf7(encoded_name)

        try:
            # IMPORTANT: still use the encoded IMAP name to talk to the server
            status, info = imap.select(encoded_name, readonly=True)
            if status != "OK" or not info:
                continue

            total = 0
            try:
                first = info[0].decode() if isinstance(info[0], (bytes, bytearray)) else str(info[0])
                total = int(first)
            except Exception:
                total = 0

            status, unseen_data = imap.search(None, "UNSEEN")
            if status == "OK" and unseen_data and unseen_data[0]:
                unread = len(unseen_data[0].split())
            else:
                unread = 0

            folders.append({
                "key": encoded_name,
                "label": label,
                "count": total,
                "unread": unread,
            })
        except Exception:
            continue

    return folders

@app.route("/api/messages", methods=["GET"])
def api_messages():
    try:
        folder = request.args.get("folder", "INBOX")
        imap = connect_imap()
        emails = fetch_emails(imap, mailbox=folder)
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

@app.route("/api/folders", methods=["GET"])
def api_folders():
    try:
        imap = connect_imap()
        folders = list_folders_with_counts(imap)
        imap.logout()
        return jsonify({"folders": folders})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/message/<account>/<id>", methods=["GET"])
def api_message(account, id):
    try:
        folder = request.args.get("folder", "INBOX")

        imap = connect_imap()
        imap.select(folder)
        status, msg_data = imap.fetch(id, "(RFC822)")
        if status != "OK" or not msg_data:
            imap.logout()
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

        plain_body = ""
        html_body = ""
        attachments = []

        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = part.get_content_disposition()
                filename = part.get_filename()

                # anything with a filename is an attachment (attachment or inline)
                if filename and disp in ("attachment", "inline"):
                    decoded_name = decode_str(filename)
                    payload = part.get_payload(decode=True) or b""
                    attachments.append({
                        "index": len(attachments),
                        "filename": decoded_name,
                        "content_type": ctype,
                        "size": len(payload),
                    })
                    continue

                if ctype == "text/plain" and not plain_body:
                    try:
                        plain_body = part.get_payload(decode=True).decode(errors="ignore")
                    except Exception:
                        plain_body = ""
                elif ctype == "text/html" and not html_body:
                    try:
                        html_body = part.get_payload(decode=True).decode(errors="ignore")
                    except Exception:
                        html_body = ""
        else:
            ctype = msg.get_content_type()
            disp = msg.get_content_disposition()
            filename = msg.get_filename()
            payload = msg.get_payload(decode=True) or b""

            if filename and disp in ("attachment", "inline"):
                decoded_name = decode_str(filename)
                attachments.append({
                    "index": 0,
                    "filename": decoded_name,
                    "content_type": ctype,
                    "size": len(payload),
                })
            else:
                try:
                    body_text = payload.decode(errors="ignore").strip()
                    if ctype == "text/plain":
                        plain_body = body_text
                    elif ctype == "text/html":
                        html_body = body_text
                except Exception:
                    pass

        # Show HTML body if available, else plain text
        body = html_body or plain_body or ""

        # mark as read in that folder
        imap.store(id, "+FLAGS", "\\Seen")
        imap.expunge()
        imap.logout()

        return jsonify({
            "subject": subject,
            "sender": sender,
            "to": receiver,
            "date_str": date_fmt,
            "body": body,
            "account_label": "GMX",
            "folder": folder,
            "attachments": attachments,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/message/<account>/<id>/attachment/<int:att_index>", methods=["GET"])
def api_attachment(account, id, att_index):
    folder = request.args.get("folder", "INBOX")

    imap = connect_imap()
    try:
        imap.select(folder)
        status, msg_data = imap.fetch(id, "(RFC822)")
        if status != "OK" or not msg_data:
            return jsonify({"error": "Message not found"}), 404

        msg = email.message_from_bytes(msg_data[0][1])

        current_index = 0
        target_part = None
        filename = None
        content_type = "application/octet-stream"

        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = part.get_content_disposition()
                fname = part.get_filename()
                if fname and disp in ("attachment", "inline"):
                    if current_index == att_index:
                        target_part = part
                        filename = decode_str(fname)
                        content_type = ctype or content_type
                        break
                    current_index += 1
        else:
            ctype = msg.get_content_type()
            disp = msg.get_content_disposition()
            fname = msg.get_filename()
            if fname and disp in ("attachment", "inline") and att_index == 0:
                target_part = msg
                filename = decode_str(fname)
                content_type = ctype or content_type

        if target_part is None:
            return jsonify({"error": "Attachment not found"}), 404

        payload = target_part.get_payload(decode=True) or b""
        bio = BytesIO(payload)

        return send_file(
            bio,
            mimetype=content_type,
            as_attachment=True,
            download_name=filename or "attachment"
        )
    finally:
        try:
            imap.logout()
        except Exception:
            pass

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
