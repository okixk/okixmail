import os
import re
import base64
import imaplib
import smtplib
import time
import email
from email import policy, encoders
from email.header import decode_header
from email.mime.base import MIMEBase
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
TRASH_MAILBOX = "Gel&APY-scht"

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

def extract_data_uri_attachments_from_html(html):
    """
    Find <img src="data:..."> in HTML, strip them out, and return
    (attachments, cleaned_html).

    Attachments are dicts with: filename, content_type, data (base64 string).
    """
    if not html:
        return [], html

    attachments = []

    def repl(match):
        src = match.group(1)
        if not src.lower().startswith("data:"):
            return ""

        try:
            header, b64data = src.split(",", 1)
        except ValueError:
            return ""

        # header looks like: data:image/png;base64
        header = header[5:]  # drop "data:"
        mime_type = "application/octet-stream"
        if ";base64" in header:
            mime_type = header.split(";")[0] or mime_type
        elif header:
            mime_type = header

        # map mime -> extension
        ext_map = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/svg+xml": "svg",
        }
        idx = len(attachments) + 1
        ext = ext_map.get(mime_type, "bin")
        filename = f"image-{idx}.{ext}"

        attachments.append({
            "filename": filename,
            "content_type": mime_type,
            "data": b64data,
        })

        return f"[image {idx}]"

    pattern = r'<img\b[^>]*\bsrc=["\'](data:[^"\']+)["\'][^>]*>'
    cleaned_html = re.sub(pattern, repl, html, flags=re.IGNORECASE)
    return attachments, cleaned_html

def parse_priority_header(msg):
    parts = [
        msg.get("X-Priority") or "",
        msg.get("Priority") or "",
        msg.get("Importance") or "",
        msg.get("X-MSMail-Priority") or "",
    ]
    raw = " ".join(parts).lower()
    if not raw.strip():
        return "normal"

    if "high" in raw or raw.strip().startswith(("1", "2")) or "urgent" in raw:
        return "high"
    if "low" in raw or raw.strip().startswith(("4", "5")) or "non-urgent" in raw:
        return "low"
    return "normal"

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

        # Priority
        priority = parse_priority_header(msg)

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
                body = ""

        body = plain_body or html_body or ""
        preview = (html_to_text(body).replace("\n", " ").strip()[:90] + "...") if body else ""

        emails.append({
            "id": eid.decode(),
            "sender": sender,
            "subject": subject,
            "preview": preview,
            "unread": is_unread,
            "date_str": date_fmt,
            "account": "gmx",
            "priority": priority,
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

def find_sent_mailbox(imap):
    """
    Try to find a 'Sent' / 'Gesendet' folder by name.
    Returns the encoded IMAP name (key) to use with APPEND.
    Falls back to INBOX if nothing obvious is found.
    """
    status, data = imap.list()
    if status != "OK" or not data:
        return "INBOX"

    for raw in data:
        encoded_name = _parse_mailbox_name(raw)
        if not encoded_name:
            continue

        label = decode_imap_utf7(encoded_name).lower()
        if "gesendet" in label or "sent" in label:
            return encoded_name
    return "INBOX"

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
    """
    Return a single message (with HTML/plain body + attachment metadata + priority).
    """
    imap = None
    try:
        folder = request.args.get("folder", "INBOX")
        mark_read = request.args.get("mark_read") == "1"

        imap = connect_imap()
        typ, _ = imap.select(folder)
        if typ != "OK":
            imap.logout()
            return jsonify({"error": f"Could not select folder {folder}"}), 500

        status, msg_data = imap.fetch(id, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
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

        # Priority
        priority = parse_priority_header(msg)

        plain_body = ""
        html_body = ""
        attachments = []

        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = part.get_content_disposition()
                filename = part.get_filename()

                # Attachments (attachment or inline with filename)
                if filename and disp in ("attachment", "inline"):
                    try:
                        payload = part.get_payload(decode=True) or b""
                    except Exception:
                        payload = b""
                    attachments.append({
                        "index": len(attachments),
                        "filename": decode_str(filename),
                        "content_type": ctype,
                        "size": len(payload),
                    })
                    continue

                # Body (no filename)
                if ctype == "text/plain" and not plain_body:
                    try:
                        plain_body = (part.get_payload(decode=True) or b"").decode(errors="ignore")
                    except Exception:
                        plain_body = ""
                elif ctype == "text/html" and not html_body:
                    try:
                        html_body = (part.get_payload(decode=True) or b"").decode(errors="ignore")
                    except Exception:
                        html_body = ""
        else:
            ctype = msg.get_content_type()
            disp = msg.get_content_disposition()
            filename = msg.get_filename()
            body_bytes = msg.get_payload(decode=True) or b""

            if filename and disp in ("attachment", "inline"):
                attachments.append({
                    "index": 0,
                    "filename": decode_str(filename),
                    "content_type": ctype,
                    "size": len(body_bytes),
                })
            else:
                try:
                    text = body_bytes.decode(errors="ignore").strip()
                except Exception:
                    text = ""
                if ctype == "text/plain":
                    plain_body = text
                elif ctype == "text/html":
                    html_body = text

        body = html_body or plain_body or ""

        if mark_read:
            try:
                imap.store(id, "+FLAGS", "\\Seen")
                imap.expunge()
            except Exception:
                pass

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
            "priority": priority,
        })
    except Exception as e:
        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass
        return jsonify({"error": str(e)}), 500

@app.route("/api/message/<account>/<id>/delete", methods=["POST"])
def api_delete_message(account, id):
    """
    'Delete' a message by moving it to the GMX trash folder (Gelöscht)
    and then removing it from the current folder.

    Returns metadata (original folder + Message-ID) so the client
    can later restore it.
    """
    imap = None
    try:
        folder = request.args.get("folder", "INBOX")

        imap = connect_imap()
        # Select the current folder (where the message lives now)
        typ, _ = imap.select(folder)
        if typ != "OK":
            imap.logout()
            return jsonify({"error": f"Could not select folder {folder}"}), 500

        trash_folder = TRASH_MAILBOX

        # Grab Message-ID from header so we can find the copy in trash later
        message_id = None
        try:
            typ, data = imap.fetch(id, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            if typ == "OK" and data and data[0]:
                header_bytes = data[0][1]
                header_msg = email.message_from_bytes(header_bytes)
                message_id = header_msg.get("Message-ID")
        except Exception:
            message_id = None

        # Are we deleting from a "normal" folder or directly from trash?
        restorable = folder != trash_folder

        # If we're not already in the trash, copy the message there first
        if restorable:
            copy_typ, _ = imap.copy(id, trash_folder)
            if copy_typ != "OK":
                imap.logout()
                return jsonify({"error": "Could not move message to trash"}), 500

        # Now mark the message as deleted in the current folder
        imap.store(id, "+FLAGS", r"(\Deleted)")
        imap.expunge()
        imap.logout()

        return jsonify({
            "status": "moved_to_trash" if restorable else "deleted_from_trash",
            "id": id,
            "from_folder": folder,
            "trash_folder": trash_folder,
            "message_id": message_id,
            "restorable": restorable,
        })
    except Exception as e:
        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass
        return jsonify({"error": str(e)}), 500


@app.route("/api/message/<account>/<id>/restore", methods=["POST"])
def api_restore_message(account, id):
    """
    Restore a message from the trash folder back to its original folder.

    Expects JSON body:
      { "from_folder": "...", "trash_folder": "...", "message_id": "..." }
    """
    imap = None
    try:
        data = request.json or {}
        from_folder = data.get("from_folder") or "INBOX"
        trash_folder = data.get("trash_folder") or TRASH_MAILBOX
        message_id = data.get("message_id")

        if not message_id:
            return jsonify({"error": "Missing message_id"}), 400

        imap = connect_imap()

        # Look for the message in trash by Message-ID header
        typ, _ = imap.select(trash_folder)
        if typ != "OK":
            imap.logout()
            return jsonify({"error": f"Could not select trash folder {trash_folder}"}), 500

        mid = message_id.replace('"', "").strip()
        search_crit = f'"{mid}"'
        typ, search_data = imap.search(None, "HEADER", "Message-ID", search_crit)
        if typ != "OK" or not search_data or not search_data[0]:
            imap.logout()
            return jsonify({"error": "Message not found in trash"}), 404

        # If multiple hits, use the last one
        candidates = search_data[0].split()
        msg_seq = candidates[-1].decode() if isinstance(candidates[-1], (bytes, bytearray)) else str(candidates[-1])

        # Copy back to the original folder
        typ, _ = imap.copy(msg_seq, from_folder)
        if typ != "OK":
            imap.logout()
            return jsonify({"error": "Could not copy message back to folder"}), 500

        # Remove it from trash
        imap.store(msg_seq, "+FLAGS", r"(\Deleted)")
        imap.expunge()
        imap.logout()

        return jsonify({
            "status": "restored",
            "from_folder": from_folder,
            "trash_folder": trash_folder,
        })
    except Exception as e:
        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass
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

def extract_data_uri_attachments_from_html(html):
    """
    Find <img src="data:..."> tags, turn them into attachments, and
    replace them with a small placeholder so GMX doesn't choke on
    ultra-long lines.
    Returns: (clean_html, attachments_list)
    """
    if not html:
        return html, []

    pattern = re.compile(
        r'<img\b[^>]*\bsrc=["\'](data:(?P<mime>[^;]+);base64,(?P<data>[A-Za-z0-9+/=\s]+))["\'][^>]*>',
        re.IGNORECASE,
    )

    attachments = []
    idx = 1

    def repl(match):
        nonlocal idx
        mime = match.group("mime") or "application/octet-stream"
        # remove whitespace in the base64 part
        data_b64 = re.sub(r"\s+", "", match.group("data") or "")
        attachments.append(
            {
                "filename": f"inline-image-{idx}.bin",
                "content_type": mime,
                "data": data_b64,
            }
        )
        placeholder = f'<p>[image {idx}]</p>'
        idx += 1
        return placeholder

    cleaned_html = pattern.sub(repl, html)
    return cleaned_html, attachments

@app.route("/api/send", methods=["POST"])
def api_send():
    data = request.json or {}

    def parse_addr_list(raw):
        """Accept comma/semicolon-separated strings or lists."""
        if not raw:
            return []
        if isinstance(raw, (list, tuple)):
            parts = []
            for item in raw:
                if not item:
                    continue
                parts.extend(str(item).replace(";", ",").split(","))
        else:
            parts = str(raw).replace(";", ",").split(",")

        return [p.strip() for p in parts if p.strip()]

    # --- Recipients / meta ---
    to_list = parse_addr_list(data.get("to"))
    cc_list = parse_addr_list(data.get("cc"))
    bcc_list = parse_addr_list(data.get("bcc"))

    subject = data.get("subject", "")
    priority = (data.get("priority") or "normal").lower()

    body_html = data.get("body_html")
    body_text = data.get("body_text")
    legacy_body = data.get("body", "")

    # --- turn inline data: images into attachments ---
    inline_attachments = []
    if body_html:
        body_html, inline_attachments = extract_data_uri_attachments_from_html(body_html)

    attachments_json = (data.get("attachments") or []) + inline_attachments

    if not (to_list or cc_list or bcc_list):
        return jsonify(
            {"error": "At least one recipient (To, Cc or Bcc) is required"}
        ), 400

    has_html = bool(body_html)
    has_attachments = bool(attachments_json)

    # ---- Build message structure ----
    if has_html:
        if not body_text:
            body_text = html_to_text(body_html)

        alt = MIMEMultipart("alternative")
        text_part = MIMEText(body_text or "", "plain", "utf-8")
        encoders.encode_quopri(text_part)
        alt.attach(text_part)

        html_part = MIMEText(body_html or "", "html", "utf-8")
        encoders.encode_quopri(html_part)
        alt.attach(html_part)

        if has_attachments:
            msg = MIMEMultipart("mixed")
            msg.attach(alt)
        else:
            msg = alt
    else:
        body_text = body_text or legacy_body or ""
        if has_attachments:
            msg = MIMEMultipart("mixed")
            text_part = MIMEText(body_text or "", "plain", "utf-8")
            encoders.encode_quopri(text_part)
            msg.attach(text_part)
        else:
            msg = MIMEText(body_text or "", "plain", "utf-8")
            encoders.encode_quopri(msg)

    msg["From"] = EMAIL_ACCOUNT
    if to_list:
        msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject

    # ---- Priority headers ----
    if priority == "high":
        msg["X-Priority"] = "1 (High)"
        msg["Importance"] = "High"
    elif priority == "low":
        msg["X-Priority"] = "5 (Low)"
        msg["Importance"] = "Low"
    else:
        msg["X-Priority"] = "3 (Normal)"
        msg["Importance"] = "Normal"

    # ---- Attach files (JSON + extracted data: URIs) ----
    for att in attachments_json:
        try:
            filename = att.get("filename") or "attachment"
            content_type = att.get("content_type") or "application/octet-stream"
            data_b64 = att.get("data") or ""
            if not data_b64:
                continue

            payload = base64.b64decode(data_b64)
        except Exception:
            continue

        main_type, _, sub_type = content_type.partition("/")
        if not main_type:
            main_type = "application"
        if not sub_type:
            sub_type = "octet-stream"

        part = MIMEBase(main_type, sub_type)
        part.set_payload(payload)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    # All recipients, including Bcc
    recipients = to_list + cc_list + bcc_list
    if not recipients:
        recipients = [EMAIL_ACCOUNT]

    raw_msg_bytes = msg.as_bytes(policy=policy.SMTP)

    try:
        # --- Send via SMTP ---
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ACCOUNT, recipients, raw_msg_bytes)
        server.quit()

        # --- Save copy to Sent (IMAP) ---
        try:
            imap = connect_imap()
            sent_mailbox = find_sent_mailbox(imap)
            imap.append(
                sent_mailbox,
                "\\Seen",
                imaplib.Time2Internaldate(time.time()),
                raw_msg_bytes,
            )
            imap.logout()
        except Exception:
            pass

        return jsonify({"status": "sent"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
