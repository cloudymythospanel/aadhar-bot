import requests
import json
import base64
import uuid
import re
import sqlite3
from datetime import datetime
import os
import sys
import time
import logging
import signal
from io import BytesIO
import PyPDF2
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# ============== LOGGING SETUP ==============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============== BOT TOKEN ==============
TELEGRAM_BOT_TOKEN = "8941358080:AAGT9n_DBuOHpXb7JHHGcwih_Tgtd03dI3k"  # <-- PUT YOUR TOKEN HERE

# ============== ANTI-CAPTCHA CONFIG ==============
ANTI_CAPTCHA_KEY = "ca9d9ef71c4de20ce587e5cbeeb63d5d"  # <-- PUT YOUR anti-captcha.com API KEY HERE

# ============== PROXY / NETWORK CONFIG ==============
WARP_PROXY_PORT = 40000
WARP_PROXY_URL  = f"socks5h://127.0.0.1:{WARP_PROXY_PORT}"

def is_warp_proxy_available():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        res = s.connect_ex(('127.0.0.1', WARP_PROXY_PORT))
        s.close()
        return res == 0
    except Exception:
        return False

# ============== SESSION FACTORY ==============
def create_session(use_proxy=False):
    session = requests.Session()
    if use_proxy:
        session.proxies = {
            'http': WARP_PROXY_URL,
            'https': WARP_PROXY_URL,
        }
    session.mount('https://', requests.adapters.HTTPAdapter(
        pool_connections=100, pool_maxsize=300, max_retries=3, pool_block=False
    ))
    session.mount('http://', requests.adapters.HTTPAdapter(
        pool_connections=100, pool_maxsize=300, max_retries=3, pool_block=False
    ))
    return session

telegram_session = None
def get_telegram_session():
    global telegram_session
    if telegram_session is None:
        telegram_session = create_session(use_proxy=False)
    return telegram_session

def get_uidai_session():
    return create_session(use_proxy=is_warp_proxy_available())

# ============== PDF PASSWORD CRACKER ==============
class PDFPasswordCracker:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.found_password = None
        self.stop_flag = False
        self.progress = 0
        self.total_years = 0

    def try_password(self, pdf_path, password):
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                if pdf_reader.decrypt(password):
                    return True, password
                return False, None
        except Exception as e:
            logger.debug(f"Error with password {password}: {e}")
            return False, None

    def decrypt_pdf(self, pdf_path, password, output_path=None):
        try:
            if output_path is None:
                output_path = pdf_path.replace('.pdf', '_decrypted.pdf')
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                pdf_reader.decrypt(password)
                pdf_writer = PyPDF2.PdfWriter()
                for page in pdf_reader.pages:
                    pdf_writer.add_page(page)
                with open(output_path, 'wb') as output_file:
                    pdf_writer.write(output_file)
            logger.info(f"Decrypted PDF saved: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Error decrypting PDF: {e}")
            return None

    def crack_pdf(self, pdf_path, name, progress_callback=None):
        self.found_password = None
        self.stop_flag = False
        self.progress = 0
        name_upper = name.upper()
        patterns = []
        name_prefix = name_upper[:4] if len(name_upper) >= 4 else name_upper
        patterns.append(('first4', name_prefix))
        if len(name_upper) >= 6:
            patterns.append(('first6', name_upper[:6]))
        name_full = name_upper[:10] if len(name_upper) > 10 else name_upper
        patterns.append(('full', name_full))
        patterns.append(('lower_first4', name_prefix.lower()))
        if len(name_upper) >= 6:
            patterns.append(('lower_first6', name_upper[:6].lower()))
        patterns.append(('title_first4', name_prefix.title()))
        patterns.append(('first4_short', name_prefix[:4]))
        patterns.append(('with_at', f"{name_prefix}@"))
        patterns.append(('with_hash', f"{name_prefix}#"))
        patterns.append(('with_exclaim', f"{name_prefix}!"))
        patterns.append(('year_first', "@"))
        patterns.append(('only_name', name_prefix))
        current_year = datetime.now().year
        common_years = list(range(1940, 2010)) + list(range(1930, 1940)) + list(range(2010, current_year + 1))
        prioritized_passwords = []
        for year in common_years:
            for pattern_name, prefix in patterns:
                if pattern_name == 'year_first':
                    password = f"{year}{prefix}"
                elif pattern_name == 'only_name':
                    password = prefix
                elif pattern_name == 'first4_short':
                    password = f"{prefix[:4]}{year}"
                elif pattern_name == 'with_at':
                    password = f"{prefix}@{year}"
                elif pattern_name == 'with_hash':
                    password = f"{prefix}#{year}"
                elif pattern_name == 'with_exclaim':
                    password = f"{prefix}!{year}"
                else:
                    password = f"{prefix}{year}"
                prioritized_passwords.append(password)
        seen = set()
        unique_passwords = []
        for pwd in prioritized_passwords:
            if pwd not in seen:
                seen.add(pwd)
                unique_passwords.append(pwd)
        checked = 0
        batch_size = 20
        for i in range(0, len(unique_passwords), batch_size):
            if self.stop_flag:
                break
            batch = unique_passwords[i:i+batch_size]
            futures = [(self.executor.submit(self.try_password, pdf_path, p), p) for p in batch]
            for future, password in futures:
                if self.stop_flag:
                    break
                try:
                    success, found_pwd = future.result(timeout=2)
                    checked += 1
                    if success:
                        self.found_password = found_pwd
                        self.stop_flag = True
                        decrypted_path = self.decrypt_pdf(pdf_path, found_pwd)
                        return True, found_pwd, decrypted_path if decrypted_path else None
                except Exception as e:
                    logger.debug(f"Error checking password {password}: {e}")
                    continue
        no_year_passwords = [prefix for pattern_name, prefix in patterns if pattern_name not in ['only_name']]
        for password in no_year_passwords:
            if self.stop_flag:
                break
            success, found_pwd = self.try_password(pdf_path, password)
            if success:
                self.found_password = found_pwd
                self.stop_flag = True
                decrypted_path = self.decrypt_pdf(pdf_path, found_pwd)
                return True, found_pwd, decrypted_path if decrypted_path else None
        return False, None, None

# ============== AADHAAR BOT CLASS ==============
class AadhaarBot:
    def __init__(self):
        self.session = get_uidai_session()
        self.base_headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en_IN',
            'Connection': 'keep-alive',
            'Content-Type': 'application/json',
            'Origin': 'https://myaadhaar.uidai.gov.in',
            'Referer': 'https://myaadhaar.uidai.gov.in/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-site',
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
            'appid': 'MYAADHAAR',
            'sec-ch-ua': '"Not-A.Brand";v="99", "Chromium";v="124"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-platform': '"Android"',
        }
        self.session.headers.update(self.base_headers)
        logger.info("AadhaarBot initialized")
        self.cracker = PDFPasswordCracker()

    def generate_transaction_id(self):
        return str(uuid.uuid4())

    def is_base64(self, s):
        if not isinstance(s, str) or len(s) < 100:
            return False
        if s.startswith('data:'):
            s = s.split(',')[1] if ',' in s else s
        if len(s) % 4 != 0:
            return False
        try:
            base64.b64decode(s)
            return True
        except:
            return False

    def detect_file_type(self, file_bytes):
        if file_bytes[:4] == b'%PDF':
            return 'pdf'
        elif file_bytes[:8] == b'\x89PNG\r\n\x1a\n':
            return 'png'
        elif file_bytes[:2] == b'\xff\xd8':
            return 'jpg'
        return 'unknown'

    def detect_and_decode_base64(self, data, field_name="unknown", save=False):
        decoded_items = []
        if isinstance(data, dict):
            for key, value in list(data.items()):
                if isinstance(value, str) and len(value) > 100 and self.is_base64(value):
                    try:
                        clean_base64 = value.split(',')[1] if value.startswith('data:') and ',' in value else value
                        decoded_bytes = base64.b64decode(clean_base64)
                        file_type = self.detect_file_type(decoded_bytes)
                        if save and file_type in ['pdf', 'png', 'jpg']:
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            ext = {'pdf': 'pdf', 'png': 'png', 'jpg': 'jpg'}.get(file_type, 'bin')
                            filename = f"decoded_{field_name}_{key}_{timestamp}.{ext}"
                            with open(filename, 'wb') as f:
                                f.write(decoded_bytes)
                            decoded_items.append({'field': key, 'filename': filename, 'type': file_type, 'size': len(decoded_bytes), 'data': decoded_bytes})
                            logger.info(f"Saved: {filename}")
                        elif not save:
                            decoded_items.append({'field': key, 'type': file_type, 'size': len(decoded_bytes), 'data': decoded_bytes})
                    except Exception as e:
                        logger.error(f"Base64 decode error: {e}")
                if isinstance(value, (dict, list)):
                    decoded_items.extend(self.detect_and_decode_base64(value, f"{field_name}.{key}", save))
        elif isinstance(data, list):
            for idx, item in enumerate(data):
                if isinstance(item, (dict, list)):
                    decoded_items.extend(self.detect_and_decode_base64(item, f"{field_name}[{idx}]", save))
        return decoded_items

    def get_captcha(self, user_id):
        touch_session(user_id)
        transaction_id = self.generate_transaction_id()
        self.session.headers.update({'x-request-id': transaction_id, 'transactionId': transaction_id})
        captcha_data = {'captchaLength': '6', 'captchaType': '2', 'audioCaptchaRequired': True}
        try:
            response = self.session.post(
                'https://tathya.uidai.gov.in/audioCaptchaService/api/captcha/v3/generation',
                json=captcha_data, timeout=(6, 30)
            )
            if response.status_code != 200:
                return None, None, None
            resp_json = response.json()
            captcha_txn_id = resp_json.get('transactionId')
            captcha_base64 = resp_json.get('imageBase64')
            if not captcha_base64:
                for key, value in resp_json.items():
                    if isinstance(value, str) and len(value) > 100 and self.is_base64(value):
                        captcha_base64 = value
                        break
            if not captcha_base64:
                return None, None, None
            if captcha_base64.startswith('data:image'):
                captcha_base64 = captcha_base64.split(',')[1]
            image_bytes = base64.b64decode(captcha_base64)
            return image_bytes, captcha_txn_id, transaction_id
        except Exception as e:
            logger.error(f"Error getting captcha: {str(e)}")
            return None, None, None

    def send_aadhaar_otp(self, user_id, eid_number, captcha_value, captcha_txn_id, transaction_id):
        touch_session(user_id)
        self.session.headers.update({'x-request-id': transaction_id, 'transactionId': transaction_id})
        otp_request_data = {
            'eidNumber': eid_number, 'idType': 'eid',
            'captchaTxnId': captcha_txn_id, 'captchaValue': captcha_value,
            'transactionId': transaction_id, 'resendOTP': False
        }
        try:
            response = self.session.post(
                'https://tathya.uidai.gov.in/unifiedAppAuthService/api/v2/generate/aadhaar/otp',
                json=otp_request_data, timeout=(6, 15)
            )
            if response.status_code == 200:
                resp_json = response.json()
                otp_txn_id = resp_json.get('txnId')
                status = resp_json.get('status')
                message = resp_json.get('message')
                if otp_txn_id and status == "Success":
                    return True, otp_txn_id, message
                else:
                    return False, None, message
            else:
                return False, None, f"HTTP {response.status_code}"
        except Exception as e:
            return False, None, str(e)

    def download_aadhaar_pdf(self, user_id, eid_number, otp, otp_txn_id, transaction_id, mask=False):
        touch_session(user_id)
        self.session.headers.update({'x-request-id': transaction_id, 'transactionId': transaction_id})
        download_data = {'eid': eid_number, 'mask': mask, 'otp': otp, 'otpTxnId': otp_txn_id}
        try:
            response = self.session.post(
                'https://tathya.uidai.gov.in/downloadAadhaarService/api/aadhaar/download',
                json=download_data, timeout=(6, 20)
            )
            if response.status_code == 200:
                resp_json = response.json()
                decoded_files = self.detect_and_decode_base64(resp_json, "aadhaar_download", save=True)
                if decoded_files:
                    return True, decoded_files[0]['filename']
                else:
                    if resp_json.get('status') == 'Error' or resp_json.get('errorCode'):
                        error_msg = resp_json.get('message', resp_json.get('errorMessage', 'Unknown error'))
                        return False, error_msg
                    else:
                        return False, "No PDF data found"
            else:
                return False, f"HTTP {response.status_code}"
        except Exception as e:
            return False, str(e)

    def send_eid_otp(self, user_id, mobile, name, captcha_code, captcha_txn_id, transaction_id):
        touch_session(user_id)
        self.session.headers.update({'x-request-id': transaction_id, 'transactionId': transaction_id})
        request_data = {
            'mobileNumber': mobile, 'dob': None, 'email': None,
            'name': name.upper(), 'option': 'EID', 'otp': None,
            'otpTxnId': None, 'captchaTxnId': captcha_txn_id,
            'captcha': captcha_code, 'resendOtp': False
        }
        try:
            response = self.session.post(
                'https://tathya.uidai.gov.in/retrieveEidUid/ext/v1/generic/retrieveuideid',
                json=request_data, timeout=(6, 15)
            )
            if response.status_code == 200:
                resp_json = response.json()
                if 'responseData' in resp_json:
                    response_data = resp_json['responseData']
                    otp_txn_id = response_data.get('otpTxnId')
                    status = response_data.get('status')
                    if otp_txn_id and status == "Success":
                        return True, otp_txn_id
                    else:
                        return False, response_data.get('message', 'Unknown error')
                else:
                    return False, 'Invalid response'
            else:
                return False, f'HTTP {response.status_code}'
        except Exception as e:
            return False, str(e)

    def verify_eid_otp(self, user_id, mobile, name, otp_code, otp_txn_id, captcha_txn_id, captcha_code):
        touch_session(user_id)
        self.session.headers.update({'x-request-id': self.generate_transaction_id()})
        verify_data = {
            'mobileNumber': mobile, 'dob': None, 'name': name.upper(),
            'email': None, 'option': 'EID', 'otp': otp_code,
            'otpTxnId': otp_txn_id, 'captchaTxnId': captcha_txn_id,
            'captcha': captcha_code, 'resendOtp': False
        }
        try:
            response = self.session.post(
                'https://tathya.uidai.gov.in/retrieveEidUid/ext/v1/generic/retrieveuideid',
                json=verify_data, timeout=(6, 15)
            )
            if response.status_code == 200:
                resp_json = response.json()
                if resp_json.get('status') == 200 or resp_json.get('status') == "Success":
                    if 'responseData' in resp_json:
                        response_data = resp_json['responseData']
                        eid_number = response_data.get('eidNumber')
                        name_from_response = response_data.get('name', name)
                        if eid_number:
                            return True, eid_number, name_from_response
                        else:
                            return False, None, "No EID found"
                    else:
                        return False, None, "Invalid response"
                else:
                    error_msg = resp_json.get('errorDetails', {}).get('messageEnglish', 'Verification failed')
                    return False, None, error_msg
            else:
                return False, None, f'HTTP {response.status_code}'
        except Exception as e:
            return False, None, str(e)

    def crack_pdf_with_name(self, pdf_path, name, progress_callback=None):
        success, password, decrypted_path = self.cracker.crack_pdf(pdf_path, name, progress_callback)
        tips = None
        return success, password, decrypted_path, tips

# ============================================================
# Auto CAPTCHA solver
# ============================================================
try:
    import ddddocr as _ddddocr
    _ocr = _ddddocr.DdddOcr(show_ad=False)
    logger.info("ddddocr loaded — local CAPTCHA solving active")
except ImportError:
    _ocr = None
    logger.warning("ddddocr not installed — run: pip install ddddocr")

def _solve_local(image_bytes):
    if _ocr is None:
        return None
    try:
        result = _ocr.classification(image_bytes)
        result = re.sub(r'[^a-zA-Z0-9]', '', result)
        logger.info(f"ddddocr solved: {result}")
        return result if result else None
    except Exception as e:
        logger.error(f"ddddocr error: {e}")
        return None

def _solve_anticaptcha(image_bytes):
    try:
        img_b64 = base64.b64encode(image_bytes).decode()
        resp = requests.post(
            "https://api.anti-captcha.com/createTask",
            json={"clientKey": ANTI_CAPTCHA_KEY, "task": {"type": "ImageToTextTask", "body": img_b64}},
            timeout=10
        ).json()
        if resp.get("errorId") != 0:
            logger.error(f"Anti-Captcha error: {resp.get('errorDescription')}")
            return None
        task_id = resp["taskId"]
        for _ in range(20):
            time.sleep(3)
            result = requests.post(
                "https://api.anti-captcha.com/getTaskResult",
                json={"clientKey": ANTI_CAPTCHA_KEY, "taskId": task_id},
                timeout=10
            ).json()
            if result.get("status") == "ready":
                return result["solution"]["text"].strip()
        logger.error("Anti-Captcha timed out")
        return None
    except Exception as e:
        logger.error(f"Anti-Captcha error: {e}")
        return None

def solve_captcha_auto(image_bytes):
    # 1. Local OCR (ddddocr) is instant and free
    local_result = _solve_local(image_bytes)
    if local_result and len(local_result) >= 5:
        return local_result

    # 2. Try Anti-Captcha API if key is present
    if ANTI_CAPTCHA_KEY:
        result = _solve_anticaptcha(image_bytes)
        if result:
            return result

    return local_result

def _new_bot():
    return AadhaarBot()

def _get_captcha(chat_id, retries=3):
    for attempt in range(retries):
        image_bytes, captcha_txn_id, transaction_id = _new_bot().get_captcha(chat_id)
        if image_bytes:
            return image_bytes, captcha_txn_id, transaction_id
        logger.warning(f"Captcha attempt {attempt + 1} failed for {chat_id}")
    return None, None, None

def _get_and_solve_captcha(chat_id, max_retries=3, on_generated=None):
    """Try to get captcha and auto-solve it up to max_retries times.
    Returns (solved_text, image_bytes, captcha_txn_id, transaction_id).
    If auto-solve fails after all retries, returns (None, image_bytes, captcha_txn_id, transaction_id)
    so manual fallback can still show the last captcha image to the user.
    """
    last_image = None
    last_captcha_txn = None
    last_txn = None
    for attempt in range(max_retries):
        image_bytes, captcha_txn_id, transaction_id = _get_captcha(chat_id, retries=1)
        if not image_bytes:
            logger.warning(f"Auto-solve: captcha fetch failed, attempt {attempt+1}/{max_retries}")
            continue
        last_image = image_bytes
        last_captcha_txn = captcha_txn_id
        last_txn = transaction_id
        if on_generated:
            try:
                on_generated()
            except Exception:
                pass
        solved = solve_captcha_auto(image_bytes)
        if solved:
            logger.info(f"Auto-solve: solved on attempt {attempt+1}/{max_retries}: {solved}")
            return solved, image_bytes, captcha_txn_id, transaction_id
        logger.warning(f"Auto-solve: solve failed, attempt {attempt+1}/{max_retries}")
    return None, last_image, last_captcha_txn, last_txn

def _captcha_failed(chat_id, message_id=None):
    clear_session(chat_id)
    txt = (f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
           f"{em('alert', '⚠️')}  <b>〔 UIDAI Unavailable 〕</b>\n\n"
           f"◌  Could not reach UIDAI server.\n"
           f"◌  Please try again in a moment.\n\n"
           f"{DIVIDER}\n"
           f"<i>◌  Select a method below to start again.</i>")
    if message_id:
        edit_message(chat_id, message_id, txt)
    else:
        send_message(chat_id, txt, reply_markup=get_main_keyboard())

# ============== PREMIUM CUSTOM EMOJIS ==============
EMOJIS = {
    'sparkle': '5856936061334196743',
    'verified': '6003484885254149212',
    'shield': '5863998851584627334',
    'mobile': '5224260659641857816',
    'card': '6136246765369694911',
    'doc': '6136585445015821278',
    'coins': '5296742257146241213',
    'gift': '5246734896356936944',
    'users': '5240147597640876449',
    'cross': '5197252827247841976',
    'lock': '5893365724830765382',
    'unlock': '5902016123972358349',
    'crown': '5895266423952904371',
    'lightning': '6039641775377748623',
    'satellite': '6147565374289220368',
    'success': '6242011198322119983',
    'alert': '6244617272808183303',
    'arrow': '6244678063775289843',
    'gear': '6305329272647062762',
    'bot': '6276133579617473983',
    'download': '6273873791164618585',
    'search': '6273560898502138039',
    'fire': '6276012740712600711',
    'heart': '6276089912684976569',
    'folder': '6141157403342675803',
    'star': '5229027828527309057',
    'money': '5440670060093922400',
}

DEFAULT_EMOJI_MAP = {
    'sparkle': '✨',
    'verified': '✅',
    'shield': '🛡',
    'mobile': '📱',
    'card': '💳',
    'doc': '📄',
    'coins': '🪙',
    'gift': '🎁',
    'users': '👥',
    'cross': '❌',
    'lock': '🔒',
    'unlock': '🔓',
    'crown': '👑',
    'lightning': '⚡',
    'satellite': '📡',
    'success': '✅',
    'alert': '⚠️',
    'arrow': '👉',
    'gear': '⚙️',
    'bot': '🤖',
    'download': '📥',
    'search': '🔍',
    'fire': '🔥',
    'heart': '🖤',
    'folder': '📁',
    'star': '⭐',
    'money': '💰',
}

NON_EMOJIS = {'◈', '◌', '♾', '✓', '—', '-', '', 'MR'}

def em(name, fallback=""):
    emoji_id = EMOJIS.get(name)
    fb = fallback if (fallback and fallback not in NON_EMOJIS) else DEFAULT_EMOJI_MAP.get(name, '✨')
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fb}</tg-emoji>'
    return fb

# ============== CONFIG ==============
DIVIDER         = "━━━━━━━━━━━━━━━━━━━━━━━"
BOT_NAME        = f"{em('heart', '🖤')}𝐂𝐋𝐎𝐔𝐃𝐘 {em('heart', '🖤')}"
CONSOLE_BOT_NAME= "🖤 𝐂𝐋𝐎𝐔𝐃𝐘 🖤"
OWNER_ID        = 8094697912
OWNER_IDS       = {8094697912, 805388188}
OWNER_USERNAME  = "@jaat43210"

def is_owner(uid):
    try:
        return int(uid) in OWNER_IDS
    except Exception:
        return False

SESSION_TIMEOUT = 300

PLANS = {
    '10':  {'credits': 10,  'price': '$10',  'lifetime': False},
    '20':  {'credits': 20,  'price': '$20',  'lifetime': False},
    '50':  {'credits': 50,  'price': '$50',  'lifetime': False},
    '100': {'credits': 0,   'price': '$100', 'lifetime': False},
}

CHANNEL_USERNAME = "-1003582670016"
CHANNEL_NAME     = "DIᗩᗷᒪO"
CHANNEL_LINK     = "https://t.me/+tqO6x4lQ-bJiNjY1"
# ============== DATABASE ==============
DB_FILE      = "bot.db"
_db_conn     = None
_db_lock     = threading.Lock()
_db_init_lk  = threading.Lock()

def _get_db():
    global _db_conn
    if _db_conn is None:
        with _db_init_lk:
            if _db_conn is None:
                conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=30)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=10000")
                _db_conn = conn
    return _db_conn

def _init_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id          TEXT PRIMARY KEY,
            credits          INTEGER NOT NULL DEFAULT 50,
            lifetime         INTEGER NOT NULL DEFAULT 0,
            referred_by      TEXT,
            referral_count   INTEGER NOT NULL DEFAULT 0,
            joined           TEXT NOT NULL,
            banned           INTEGER NOT NULL DEFAULT 0,
            channel_verified INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT OR IGNORE INTO settings (key, value) VALUES ('free_credits', '50');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('free_mode', '0');
        UPDATE settings SET value = '50' WHERE key = 'free_credits';
    """)
    conn.commit()
    try:
        conn.execute("ALTER TABLE users ADD COLUMN channel_verified INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("UPDATE users SET credits = 50 WHERE credits < 50 AND lifetime = 0")
        conn.commit()
    except Exception:
        pass
    _migrate_from_json()
    _init_settings_cache()

def _migrate_from_json():
    if not os.path.exists('users.json'):
        return
    conn = _get_db()
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        return
    try:
        with open('users.json', 'r') as f:
            data = json.load(f)
        for uid, u in data.items():
            conn.execute("""
                INSERT OR IGNORE INTO users
                (user_id, credits, lifetime, referred_by, referral_count, joined, banned)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                uid,
                int(u.get('credits', 0)) if not u.get('lifetime') else 0,
                1 if u.get('lifetime') else 0,
                u.get('referred_by'),
                int(u.get('referral_count', 0)),
                u.get('joined', datetime.now().isoformat()),
                1 if u.get('banned') else 0,
            ))
        conn.commit()
        logger.info(f"Migrated {len(data)} users from users.json to bot.db")
        os.rename('users.json', 'users.json.migrated')
    except Exception as e:
        logger.error(f"Migration error: {e}")

def ensure_user(user_id, referrer_id=None):
    uid = str(user_id)
    with _db_lock:
        conn = _get_db()
        conn.execute("""
            INSERT OR IGNORE INTO users
            (user_id, credits, lifetime, referred_by, referral_count, joined, banned)
            VALUES (?, ?, 0, ?, 0, ?, 0)
        """, (uid, get_free_credits(), str(referrer_id) if referrer_id else None, datetime.now().isoformat()))
        if conn.execute("SELECT changes()").fetchone()[0] > 0:
            if referrer_id:
                rid = str(referrer_id)
                conn.execute("""
                    UPDATE users SET credits = credits + 1, referral_count = referral_count + 1
                    WHERE user_id = ? AND user_id != ?
                """, (rid, uid))
            conn.commit()
            return True
        return False

def get_user(user_id):
    row = _get_db().execute("SELECT * FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
    return dict(row) if row else None

def get_credits(user_id):
    row = _get_db().execute("SELECT credits, lifetime FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
    if row is None:
        return 0
    return float('inf') if row['lifetime'] else row['credits']

def is_lifetime(user_id):
    row = _get_db().execute("SELECT lifetime FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
    return bool(row['lifetime']) if row else False

def has_credits(user_id):
    if is_free_mode():
        return True
    return get_credits(user_id) > 0

def add_credits(user_id, amount, make_lifetime=False):
    uid = str(user_id)
    with _db_lock:
        conn = _get_db()
        if make_lifetime:
            conn.execute("UPDATE users SET lifetime = 1 WHERE user_id = ?", (uid,))
        else:
            conn.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?", (amount, uid))
        conn.commit()

def set_ban(user_id, banned: bool):
    with _db_lock:
        conn = _get_db()
        conn.execute("UPDATE users SET banned = ? WHERE user_id = ?", (1 if banned else 0, str(user_id)))
        conn.commit()

def is_banned(user_id):
    if is_owner(user_id):
        return False
    try:
        row = _get_db().execute("SELECT banned FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
        return bool(row['banned']) if row else False
    except Exception:
        return False

def all_users():
    rows = _get_db().execute("SELECT * FROM users").fetchall()
    return {r['user_id']: dict(r) for r in rows}

def deduct_credit(user_id):
    if is_free_mode():
        return
    with _db_lock:
        conn = _get_db()
        conn.execute("UPDATE users SET credits = MAX(0, credits - 1) WHERE user_id = ? AND lifetime = 0", (str(user_id),))
        conn.commit()

def remove_credits_from(user_id, amount):
    with _db_lock:
        conn = _get_db()
        conn.execute("UPDATE users SET credits = MAX(0, credits - ?) WHERE user_id = ? AND lifetime = 0", (amount, str(user_id)))
        conn.commit()

# ============== BOT SETTINGS ==============
_settings_lock  = threading.Lock()
_settings_cache = {'free_credits': 1, 'free_mode': True}

def _init_settings_cache():
    global _settings_cache
    try:
        rows = _get_db().execute("SELECT key, value FROM settings").fetchall()
        for row in rows:
            if row['key'] == 'free_credits':
                _settings_cache['free_credits'] = int(row['value'])
            elif row['key'] == 'free_mode':
                _settings_cache['free_mode'] = (row['value'] == '1')
    except Exception as e:
        logger.error(f"Settings load error: {e}")

def get_free_credits():
    with _settings_lock:
        return _settings_cache.get('free_credits', 50)

def set_free_credits(amount):
    with _settings_lock:
        _settings_cache['free_credits'] = amount
    with _db_lock:
        conn = _get_db()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('free_credits', ?)", (str(amount),))
        conn.commit()

def is_free_mode():
    with _settings_lock:
        return _settings_cache.get('free_mode', False)

def toggle_free_mode():
    with _settings_lock:
        new_val = not _settings_cache.get('free_mode', False)
        _settings_cache['free_mode'] = new_val
    with _db_lock:
        conn = _get_db()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('free_mode', ?)", ('1' if new_val else '0',))
        conn.commit()
    return new_val

# ============== SESSION MANAGEMENT ==============
user_sessions   = {}
_sessions_lock  = threading.Lock()

def get_session(chat_id):
    with _sessions_lock:
        return user_sessions.get(chat_id, {'step': 'main', 'data': {}, 'last_activity': time.time()})

def set_session(chat_id, step, data=None):
    with _sessions_lock:
        existing = user_sessions.get(chat_id, {})
        d = data if data is not None else existing.get('data', {})
        user_sessions[chat_id] = {'step': step, 'data': d, 'last_activity': time.time()}

def update_session_data(chat_id, key, value):
    with _sessions_lock:
        if chat_id not in user_sessions:
            user_sessions[chat_id] = {'step': 'main', 'data': {}, 'last_activity': time.time()}
        user_sessions[chat_id]['data'][key] = value
        user_sessions[chat_id]['last_activity'] = time.time()

def clear_session(chat_id):
    with _sessions_lock:
        user_sessions[chat_id] = {'step': 'main', 'data': {}, 'last_activity': time.time()}

def touch_session(chat_id):
    with _sessions_lock:
        if chat_id in user_sessions:
            user_sessions[chat_id]['last_activity'] = time.time()

def _cleanup_sessions():
    while True:
        time.sleep(20)
        try:
            expired = []
            with _sessions_lock:
                for cid, s in list(user_sessions.items()):
                    if s.get('step', 'main') != 'main':
                        idle = time.time() - s.get('last_activity', time.time())
                        if idle > SESSION_TIMEOUT:
                            user_sessions[cid] = {'step': 'main', 'data': {}, 'last_activity': time.time()}
                            expired.append(cid)
            for cid in expired:
                try:
                    send_message(cid,
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"⏰  <b>〔 Session Expired 〕</b>\n\n"
                        f"⚠️  Reason   ·  Idle for 60 seconds\n"
                        f"💳  Credits  ·  Not deducted\n\n"
                        f"{DIVIDER}\n"
                        f"<i>🔄  Select a method below to start again.</i>",
                        reply_markup=get_main_keyboard()
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Session cleanup error: {e}")

# ============== CHANNEL MEMBERSHIP ==============
VERIFIED_USERS = set()

def mark_channel_verified(user_id):
    VERIFIED_USERS.add(int(user_id))
    try:
        with _db_lock:
            conn = _get_db()
            conn.execute("UPDATE users SET channel_verified = 1 WHERE user_id = ?", (str(user_id),))
            conn.commit()
    except Exception as e:
        logger.error(f"Error marking channel verified: {e}")

def is_channel_verified_db(user_id):
    try:
        row = _get_db().execute("SELECT channel_verified FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
        if row and row['channel_verified']:
            return True
    except Exception:
        pass
    return False

def is_channel_member(user_id):
    uid = int(user_id)
    if is_owner(uid) or uid in VERIFIED_USERS or is_channel_verified_db(uid):
        return True
    try:
        target_chat = int(CHANNEL_USERNAME) if CHANNEL_USERNAME.startswith("-100") or CHANNEL_USERNAME.isdigit() else CHANNEL_USERNAME
        response = get_telegram_session().get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChatMember",
            params={'chat_id': target_chat, 'user_id': uid},
            timeout=6
        )
        if response.status_code == 200:
            r = response.json()
            if r.get('ok'):
                status = r['result']['status']
                if status in ('member', 'administrator', 'creator'):
                    mark_channel_verified(uid)
                    return True
    except Exception as e:
        logger.error(f"Channel check error: {e}")
    return False

# ============== BOT USERNAME ==============
_bot_username = None
def get_bot_username():
    global _bot_username
    if _bot_username:
        return _bot_username
    try:
        r = get_telegram_session().get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=5
        ).json()
        if r.get('ok'):
            _bot_username = r['result']['username']
    except Exception:
        pass
    return _bot_username or "UIDAIGrambot"

# ============== TELEGRAM HELPERS ==============
def send_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    if reply_markup:
        data['reply_markup'] = json.dumps(reply_markup)
    try:
        response = get_telegram_session().post(url, json=data, timeout=10)
        result = response.json()
        if not result.get('ok'):
            logger.error(f"Telegram send error: {result}")
        return result
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

def answer_callback_query(callback_query_id, text=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    data = {'callback_query_id': callback_query_id}
    if text:
        data['text'] = text
    try:
        get_telegram_session().post(url, json=data, timeout=5)
    except Exception as e:
        logger.error(f"Error answering callback: {e}")

def send_photo(chat_id, photo_bytes, caption=None, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {'photo': ('photo.png', photo_bytes, 'image/png')}
    data = {'chat_id': chat_id, 'parse_mode': 'HTML'}
    if caption:
        data['caption'] = caption
    if reply_markup:
        data['reply_markup'] = json.dumps(reply_markup)
    try:
        response = get_telegram_session().post(url, data=data, files=files, timeout=20)
        result = response.json()
        if not result.get('ok'):
            logger.error(f"Telegram send photo error: {result}")
        return result
    except Exception as e:
        logger.error(f"Error sending photo: {e}")
        return None

def send_photo_url(chat_id, url, caption=None, reply_markup=None):
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    data   = {'chat_id': chat_id, 'photo': url, 'parse_mode': 'HTML'}
    if caption:
        data['caption'] = caption
    if reply_markup:
        data['reply_markup'] = json.dumps(reply_markup)
    try:
        response = get_telegram_session().post(tg_url, json=data, timeout=15)
        result = response.json()
        if not result.get('ok'):
            logger.error(f"Telegram send photo URL error: {result}")
        return result
    except Exception as e:
        logger.error(f"Error sending photo URL: {e}")
        return None

def edit_message(chat_id, message_id, text):
    url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    data = {'chat_id': chat_id, 'message_id': message_id, 'text': text, 'parse_mode': 'HTML'}
    try:
        get_telegram_session().post(url, json=data, timeout=10)
    except Exception as e:
        logger.error(f"Error editing message: {e}")

def send_document(chat_id, file_path, caption=None, filename="Aadhaar.pdf"):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        with open(file_path, 'rb') as f:
            files = {'document': (filename, f, 'application/pdf')}
            data = {'chat_id': chat_id, 'parse_mode': 'HTML'}
            if caption:
                data['caption'] = caption
            response = get_telegram_session().post(url, data=data, files=files, timeout=30).json()
        try:
            os.remove(file_path)
        except Exception:
            pass
        return response
    except Exception as e:
        logger.error(f"Error sending document: {e}")
        return None

# ============== KEYBOARDS ==============
def get_main_keyboard():
    return {
        'keyboard': [
            [
                {'text': '📱  Mobile Number', 'style': 'primary'},
                {'text': '🆔  Aadhaar Number', 'style': 'primary'}
            ],
            [
                {'text': '📋  EID', 'style': 'success'}
            ],
            [
                {'text': '💳  Credits', 'style': 'primary'},
                {'text': '💰  Buy Credits', 'style': 'success'},
                {'text': '🎁  Referral', 'style': 'danger'}
            ],
        ],
        'resize_keyboard': True,
        'one_time_keyboard': False
    }

def get_cancel_keyboard():
    return {'inline_keyboard': [[{'text': '❌  Cancel', 'callback_data': 'cancel', 'style': 'danger'}]]}

def get_buy_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '💳  10 Credits  —  $10',  'callback_data': 'buy_10', 'style': 'primary'}],
            [{'text': '💳  20 Credits  —  $20',  'callback_data': 'buy_20', 'style': 'primary'}],
            [{'text': '💳  50 Credits  —  $50',  'callback_data': 'buy_50', 'style': 'primary'}],
            [{'text': '👑  Lifetime     —  $100', 'callback_data': 'buy_100', 'style': 'success'}],
        ]
    }

def get_join_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '🌐  Join Channel',    'url': CHANNEL_LINK, 'style': 'primary'}],
            [{'text': '✅  I have joined',   'callback_data': 'check_join', 'style': 'success'}],
        ]
    }

# ============== ADMIN KEYBOARDS ==============
def get_admin_home_keyboard():
    fm   = is_free_mode()
    fc   = get_free_credits()
    fm_label = f"🔓  Free Mode: {'ON ✅' if fm else 'OFF ❌'}"
    fc_label = f"🎁  Free Credits: {fc}"
    return {
        'inline_keyboard': [
            [{'text': '📊  Statistics',      'callback_data': 'adm_stats', 'style': 'primary'},
             {'text': '👥  User List',        'callback_data': 'adm_users_0', 'style': 'primary'}],
            [{'text': '🔍  Lookup User',      'callback_data': 'adm_lookup', 'style': 'primary'},
             {'text': '📢  Broadcast',        'callback_data': 'adm_broadcast', 'style': 'primary'}],
            [{'text': fm_label,               'callback_data': 'adm_freemode', 'style': 'success' if fm else 'danger'}],
            [{'text': fc_label,               'callback_data': 'adm_freecredits', 'style': 'success'}],
            [{'text': '💳  Anti-Captcha Bal', 'callback_data': 'adm_acbal', 'style': 'primary'}],
        ]
    }

def get_admin_back_keyboard():
    return {'inline_keyboard': [[{'text': '◀  Back to Panel', 'callback_data': 'adm_home', 'style': 'primary'}]]}

def get_user_profile_keyboard(uid, banned, lifetime):
    rows = [
        [{'text': '➕  Add Credits',    'callback_data': f'adm_addcr_{uid}', 'style': 'success'},
         {'text': '➖  Remove Credits', 'callback_data': f'adm_remcr_{uid}', 'style': 'danger'}],
        [{'text': '♾  Grant Lifetime',  'callback_data': f'adm_lifetime_{uid}', 'style': 'primary'}],
        [{'text': '🚫  Ban' if not banned else '✅  Unban',
          'callback_data': f'adm_ban_{uid}' if not banned else f'adm_unban_{uid}',
          'style': 'danger' if not banned else 'success'}],
        [{'text': '◀  Back',            'callback_data': 'adm_home', 'style': 'primary'}],
    ]
    return {'inline_keyboard': rows}

def get_users_page_keyboard(page, total_pages):
    nav = []
    if page > 0:
        nav.append({'text': '◀  Prev', 'callback_data': f'adm_users_{page-1}', 'style': 'primary'})
    if page < total_pages - 1:
        nav.append({'text': 'Next  ▶', 'callback_data': f'adm_users_{page+1}', 'style': 'primary'})
    rows = []
    if nav:
        rows.append(nav)
    rows.append([{'text': '◀  Back to Panel', 'callback_data': 'adm_home', 'style': 'primary'}])
    return {'inline_keyboard': rows}

# ============== SHARED DISPLAY HELPERS ==============
def show_credits_info(chat_id):
    u  = get_user(chat_id)
    cr = get_credits(chat_id)
    free_mode = is_free_mode()
    if free_mode:
        cr_display = f"{em('unlock', '🔓')}  Unlimited (Free Mode)"
    elif cr == float('inf'):
        cr_display = f"{em('crown', '♾')}  Lifetime"
    else:
        cr_display = f"<b>{int(cr)}</b>"
    ref_count  = u.get('referral_count', 0) if u else 0
    joined     = u.get('joined', '')[:10] if u else '—'
    note = (f"{em('unlock', '🔓')}  Free Mode is active — unlimited downloads\n"
            f"{em('sparkle', '🔹')}  Earn free credits via your referral link") if free_mode else (
            f"{em('coins', '🔹')}  1 credit = 1 Aadhaar download\n"
            f"{em('gift', '🔹')}  Earn free credits via your referral link")
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"{em('card', '💳')}  <b>〔 My Credits 〕</b>\n\n"
        f"{em('lightning', '⚡')}  Balance      ·  {cr_display}\n"
        f"{em('users', '👥')}  Referrals    ·  {ref_count}\n"
        f"{em('sparkle', '📅')}  Member since ·  {joined}\n\n"
        f"{DIVIDER}\n"
        f"<i>{note}</i>"
    )

def show_buy_menu(chat_id):
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"{em('money', '💰')}  <b>〔 Buy Credits 〕</b>\n\n"
        f"{em('card', '💳')}  10 credits    ·  <b>$10</b>\n"
        f"{em('card', '💳')}  20 credits    ·  <b>$20</b>\n"
        f"{em('card', '💳')}  50 credits    ·  <b>$50</b>\n"
        f"{em('crown', '👑')}  Lifetime      ·  <b>$100</b>\n\n"
        f"{DIVIDER}\n"
        f"<i>{em('arrow', '👇')}  Tap a plan below to see payment details</i>",
        reply_markup=get_buy_keyboard()
    )

def show_referral_info(chat_id):
    username  = get_bot_username()
    link      = f"https://t.me/{username}?start=ref_{chat_id}"
    u         = get_user(chat_id)
    ref_count = u.get('referral_count', 0) if u else 0
    earned    = ref_count
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"{em('gift', '🎁')}  <b>〔 Referral Program 〕</b>\n\n"
        f"{em('sparkle', '🔗')}  Your Link:\n<code>{link}</code>\n\n"
        f"{em('users', '👥')}  Friends joined  ·  <b>{ref_count}</b>\n"
        f"{em('lightning', '⚡')}  Credits earned  ·  <b>{earned}</b>\n\n"
        f"{DIVIDER}\n"
        f"<i>{em('fire', '💡')}  Share your link — earn +1 credit per friend who joins!</i>"
    )

# ============== ADMIN DISPLAY ==============
def show_admin_home(chat_id):
    data = all_users()
    total          = len(data)
    lifetime_count = sum(1 for u in data.values() if u.get('lifetime'))
    total_credits  = sum(u.get('credits', 0) for u in data.values() if not u.get('lifetime'))
    banned_count   = sum(1 for u in data.values() if u.get('banned'))
    today          = datetime.now().date().isoformat()
    new_today      = sum(1 for u in data.values() if u.get('joined', '')[:10] == today)
    fm             = is_free_mode()
    fc             = get_free_credits()
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"{em('gear', '⚙️')} <b>〔 Admin Panel 〕</b>\n\n"
        f"{em('users', '👥')}  Total Users   ·  <b>{total}</b>\n"
        f"{em('sparkle', '✨')}  New Today     ·  <b>{new_today}</b>\n"
        f"{em('crown', '👑')}  Lifetime      ·  <b>{lifetime_count}</b>\n"
        f"{em('coins', '🪙')}  Credits Pool  ·  <b>{total_credits}</b>\n"
        f"{em('lock', '🔒')}  Banned        ·  <b>{banned_count}</b>\n\n"
        f"{DIVIDER}\n"
        f"{em('unlock', '🔓')}  Free Mode     ·  <b>{'ON ✅' if fm else 'OFF ❌'}</b>\n"
        f"{em('gift', '🎁')}  Free Credits  ·  <b>{fc}</b> per new user\n\n"
        f"{DIVIDER}\n"
        f"<i>{em('arrow', '👉')}  Select an action below</i>",
        reply_markup=get_admin_home_keyboard()
    )

def show_admin_stats(chat_id):
    data = all_users()
    total          = len(data)
    lifetime_count = sum(1 for u in data.values() if u.get('lifetime'))
    total_credits  = sum(u.get('credits', 0) for u in data.values() if not u.get('lifetime'))
    banned_count   = sum(1 for u in data.values() if u.get('banned'))
    total_refs     = sum(u.get('referral_count', 0) for u in data.values())
    today          = datetime.now().date().isoformat()
    new_today      = sum(1 for u in data.values() if u.get('joined', '')[:10] == today)
    rich_users     = [(uid, u.get('credits', 0)) for uid, u in data.items() if not u.get('lifetime') and u.get('credits', 0) > 0]
    rich_users.sort(key=lambda x: x[1], reverse=True)
    top_str = "\n".join(f"   <code>{uid}</code>  ·  {cr}" for uid, cr in rich_users[:5]) or "   —"
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"{em('satellite', '📊')} <b>〔 Statistics 〕</b>\n\n"
        f"{em('users', '👥')}  Total Users     ·  <b>{total}</b>\n"
        f"{em('sparkle', '✨')}  New Today        ·  <b>{new_today}</b>\n"
        f"{em('crown', '👑')}  Lifetime Users  ·  <b>{lifetime_count}</b>\n"
        f"{em('lock', '🔒')}  Banned Users     ·  <b>{banned_count}</b>\n"
        f"{em('coins', '🪙')}  Credits in Pool ·  <b>{total_credits}</b>\n"
        f"{em('gift', '🎁')}  Total Referrals ·  <b>{total_refs}</b>\n\n"
        f"<b>Top 5 by credits:</b>\n{top_str}\n\n"
        f"{DIVIDER}",
        reply_markup=get_admin_back_keyboard()
    )

def show_admin_user_profile(chat_id, target_uid):
    u = get_user(target_uid)
    if not u:
        send_message(chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"{em('cross', '❌')}  User <code>{target_uid}</code> not found.\n\n{DIVIDER}",
            reply_markup=get_admin_back_keyboard()
        )
        return
    cr         = get_credits(target_uid)
    cr_display = f"{em('crown', '👑')} Lifetime" if cr == float('inf') else str(int(cr))
    banned    = u.get('banned', False)
    joined    = u.get('joined', '—')[:10]
    refs      = u.get('referral_count', 0)
    referred  = u.get('referred_by', '—') or '—'
    status    = f"{em('lock', '🚫')} Banned" if banned else (f"{em('crown', '👑')} Lifetime" if u.get('lifetime') else f"{em('verified', '✅')} Active")
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"{em('folder', '📁')} <b>〔 User Profile 〕</b>\n\n"
        f"{em('card', '🪪')}  ID           ·  <code>{target_uid}</code>\n"
        f"{em('shield', '🛡️')}  Status       ·  {status}\n"
        f"{em('lightning', '⚡')}  Credits      ·  <b>{cr_display}</b>\n"
        f"{em('users', '👥')}  Referrals   ·  {refs}\n"
        f"{em('gift', '🎁')}  Referred By ·  {referred}\n"
        f"{em('sparkle', '✨')}  Joined       ·  {joined}\n\n"
        f"{DIVIDER}",
        reply_markup=get_user_profile_keyboard(target_uid, banned, cr == float('inf'))
    )

def show_admin_users_page(chat_id, page):
    data   = all_users()
    items  = sorted(data.items(), key=lambda x: x[1].get('joined', ''), reverse=True)
    per_page = 10
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * per_page:(page + 1) * per_page]
    lines = []
    for uid, u in chunk:
        cr  = '♾' if u.get('lifetime') else str(u.get('credits', 0))
        ban = '🚫' if u.get('banned') else '✅'
        lines.append(f"{ban}  <code>{uid}</code>  ·  {cr} cr  ·  {u.get('joined','')[:10]}")
    body = "\n".join(lines) or "No users."
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"{em('users', '👥')} <b>〔 Users  —  Page {page+1}/{total_pages} 〕</b>\n\n"
        f"{body}\n\n"
        f"{DIVIDER}\n"
        f"<i>{em('arrow', '👉')}  Use /lookup &lt;id&gt; to view a profile</i>",
        reply_markup=get_users_page_keyboard(page, total_pages)
    )

def get_anticaptcha_balance():
    if not ANTI_CAPTCHA_KEY:
        return None
    try:
        r = requests.post(
            "https://api.anti-captcha.com/getBalance",
            json={"clientKey": ANTI_CAPTCHA_KEY}, timeout=8
        ).json()
        if r.get("errorId") == 0:
            return r.get("balance", 0)
    except Exception:
        pass
    return None

def broadcast_message(sender_id, text):
    data   = all_users()
    sent   = 0
    failed = 0
    for uid in data:
        if uid == str(sender_id):
            continue
        try:
            result = send_message(int(uid), text)
            if result and result.get('ok'):
                sent += 1
            else:
                failed += 1
        except Exception:
            failed += 1
        time.sleep(0.05)
    return sent, failed

# ============== GATES ==============
def channel_gate(chat_id):
    if is_channel_member(chat_id):
        return True
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"{em('satellite', '🌐')}  <b>〔 Channel Required 〕</b>\n\n"
        f"{em('alert', '⚠️')}  Join <b>{CHANNEL_NAME}</b> to use this bot.\n\n"
        f"{DIVIDER}\n"
        f"<i>{em('arrow', '👇')}  Tap Join below, then confirm with the button.</i>",
        reply_markup=get_join_keyboard()
    )
    return False

def credit_gate(chat_id):
    if has_credits(chat_id):
        return True
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"{em('card', '💳')}  <b>〔 No Credits 〕</b>\n\n"
        f"{em('cross', '❌')}  Balance  ·  <b>0</b>\n\n"
        f"{em('money', '💰')}  Tap <b>Buy Credits</b> to purchase a plan.\n"
        f"{em('gift', '🎁')}  Tap <b>Referral</b> to earn credits free.\n\n"
        f"{DIVIDER}"
    )
    return False

# ============== PDF DELIVERY ==============
def deliver_pdf(chat_id, pdf_path, verified_name):
    name_display = verified_name if verified_name and verified_name.strip() else "Mr."
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"{em('gear', '⚙️')}  <b>〔 Processing 〕</b>\n\n"
        f"<i>{em('unlock', '🔓')}  Decrypting your document…</i>"
    )
    try:
        crack_success, password, decrypted_path, _ = _new_bot().crack_pdf_with_name(pdf_path, name_display, None)
        if crack_success and decrypted_path:
            caption = (
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('doc', '📄')}  <b>〔 Document Ready {em('success', '✅')} 〕</b>\n\n"
                f"👤  Name    ·  <b>{name_display}</b>\n"
                f"📋  Format  ·  e-Aadhaar PDF\n"
                f"{em('unlock', '🔓')}  Status  ·  <b>Unlocked</b>\n"
                f"{DIVIDER}"
            )
            send_document(chat_id, decrypted_path, caption=caption, filename="Aadhaar.pdf")
            try:
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
            except Exception:
                pass
        else:
            caption = (
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('doc', '📄')}  <b>〔 Document Ready 〕</b>\n\n"
                f"👤  Name    ·  <b>{name_display}</b>\n"
                f"📋  Format  ·  e-Aadhaar PDF\n"
                f"{em('lock', '🔒')}  Status  ·  Password Protected\n"
                f"{DIVIDER}\n\n"
                f"<i>{em('fire', '💡')}  Password: first 4 letters of name + birth year\n"
                f"   Example: <code>RAJE1995</code></i>"
            )
            send_document(chat_id, pdf_path, caption=caption, filename="Aadhaar.pdf")
    except Exception as e:
        logger.error(f"PDF delivery error: {e}")
        send_document(
            chat_id, pdf_path,
            caption=f"<b>{BOT_NAME}</b>\n{DIVIDER}\n{em('doc', '📄')}  <b>〔 Document Ready 〕</b>",
            filename="Aadhaar.pdf"
        )

    deduct_credit(chat_id)
    cr = get_credits(chat_id)
    cr_display = "♾  Lifetime" if cr == float('inf') else str(int(cr))
    clear_session(chat_id)
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"{em('success', '✅')}  <b>〔 Download Complete 〕</b>\n\n"
        f"{em('card', '💳')}  Credits remaining  ·  {cr_display}\n\n"
        f"{DIVIDER}\n"
        f"<i>{em('arrow', '🔄')}  Select a method below for another download.</i>",
        reply_markup=get_main_keyboard()
    )

def send_welcome_banner(chat_id):
    cr = get_credits(chat_id)
    cr_display = f"{em('crown', '👑')}  Lifetime" if cr == float('inf') else str(int(cr))
    start_caption = (
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n\n"
        f"<b>{em('doc', '📄')} e-Aadhaar PDF  —  straight to Telegram</b>\n\n"
        f"{em('shield', '🛡')}  Source     ·  Official UIDAI portal\n"
        f"{em('unlock', '🔓')}  Delivery  ·  Auto-unlocked, no password\n"
        f"{em('sparkle', '✨')}  Methods   ·  Mobile  ·  Aadhaar  ·  EID\n\n"
        f"{DIVIDER}\n"
        f"{em('card', '💳')}  Credits   ·  <b>{cr_display}</b>\n\n"
        f"<i>{em('arrow', '👉')}  Select a method below to begin.</i>"
    )
    start_img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'start_logo.jpg')
    if os.path.exists(start_img_path):
        with open(start_img_path, 'rb') as f:
            img_bytes = f.read()
        send_photo(chat_id, img_bytes, caption=start_caption, reply_markup=get_main_keyboard())
    else:
        send_photo_url(chat_id, "https://xyzpayz.fun/logo.png", caption=start_caption, reply_markup=get_main_keyboard())

# ============== CALLBACK HANDLER ==============
def handle_callback(chat_id, callback_query_id, data):
    answer_callback_query(callback_query_id)
    ensure_user(chat_id)

    if data == 'check_join':
        mark_channel_verified(chat_id)
        ensure_user(chat_id)
        clear_session(chat_id)
        send_welcome_banner(chat_id)
        return

    if data == 'cancel':
        clear_session(chat_id)
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<i>{em('cross', '❌')}  Session cancelled.</i>"
        )
        return

    if data == 'auto_name':
        s = get_session(chat_id)
        if s.get('step') == 'awaiting_name':
            handle_message(chat_id, 'MR')
        return

    if data == 'credits':
        show_credits_info(chat_id)
        return

    if data == 'buy':
        show_buy_menu(chat_id)
        return

    if data == 'referral':
        show_referral_info(chat_id)
        return

    if data.startswith('buy_'):
        plan_key = data.split('_')[1]
        plan = PLANS.get(plan_key)
        if not plan:
            return
        label = f"{em('crown', '👑')} Lifetime" if plan['lifetime'] else f"{plan['credits']} credits"
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"{em('money', '💰')}  <b>〔 Payment — {plan['price']} 〕</b>\n\n"
            f"{em('gift', '📦')}  Plan    ·  {label}\n"
            f"{em('card', '💵')}  Amount  ·  <b>{plan['price']}</b>\n\n"
            f"{DIVIDER}\n"
            f"📩  Message <b>{OWNER_USERNAME}</b> on Telegram to pay\n\n"
            f"🪪  Your ID  ·  <code>{chat_id}</code>\n\n"
            f"{DIVIDER}\n"
            f"<i>{em('lightning', '⏳')}  Credits will be added after payment is verified.</i>"
        )
        return

    # ── ADMIN CALLBACKS ──────────────────────────────────────
    if is_owner(chat_id) and data.startswith('adm_'):
        if data == 'adm_home':
            show_admin_home(chat_id)
            return

        if data == 'adm_stats':
            show_admin_stats(chat_id)
            return

        if data == 'adm_freemode':
            new_state = toggle_free_mode()
            state_str = 'ON ✅  — Users can download without credits' if new_state else 'OFF ❌  — Credits required as normal'
            send_message(chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Free Mode 〕</b>\n\n"
                f"◈  Status  ·  <b>{state_str}</b>\n\n{DIVIDER}",
                reply_markup=get_admin_home_keyboard()
            )
            return

        if data == 'adm_freecredits':
            set_session(chat_id, 'admin_awaiting_freecredits', {})
            send_message(chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Free Credits 〕</b>\n\n"
                f"◈  Current  ·  <b>{get_free_credits()}</b> credits per new user\n\n"
                f"▸  Enter the new amount\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            return

        if data == 'adm_acbal':
            bal = get_anticaptcha_balance()
            if bal is None:
                msg = "✗  Could not fetch balance (key missing or API error)."
            else:
                msg = f"◈  Anti-Captcha Balance  ·  <b>${bal:.4f}</b>"
            send_message(chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 Anti-Captcha 〕</b>\n\n{msg}\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            return

        if data == 'adm_lookup':
            set_session(chat_id, 'admin_awaiting_lookup', {})
            send_message(chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Lookup User 〕</b>\n\n"
                f"▸  Send the Telegram User ID\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            return

        if data == 'adm_broadcast':
            set_session(chat_id, 'admin_awaiting_broadcast', {})
            send_message(chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Broadcast 〕</b>\n\n"
                f"▸  Send the message text to broadcast to all users\n\n"
                f"<i>◌  HTML formatting supported</i>\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            return

        if data.startswith('adm_users_'):
            try:
                page = int(data.split('_')[-1])
            except ValueError:
                page = 0
            show_admin_users_page(chat_id, page)
            return

        if data.startswith('adm_addcr_'):
            uid = data[len('adm_addcr_'):]
            set_session(chat_id, 'admin_awaiting_addcr', {'target_uid': uid})
            send_message(chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Add Credits 〕</b>\n\n"
                f"◈  Target  ·  <code>{uid}</code>\n\n"
                f"▸  Enter amount to add (or <b>-1</b> for Lifetime)\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            return

        if data.startswith('adm_remcr_'):
            uid = data[len('adm_remcr_'):]
            set_session(chat_id, 'admin_awaiting_remcr', {'target_uid': uid})
            send_message(chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Remove Credits 〕</b>\n\n"
                f"◈  Target  ·  <code>{uid}</code>\n\n"
                f"▸  Enter amount to remove\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            return

        if data.startswith('adm_lifetime_'):
            uid = data[len('adm_lifetime_'):]
            add_credits(uid, 0, make_lifetime=True)
            send_message(int(uid),
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Credits Received 〕</b>\n\n"
                f"◈  Plan    ·  Lifetime\n"
                f"◈  Status  ·  Active\n\n{DIVIDER}",
                reply_markup=get_main_keyboard()
            )
            send_message(chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"✅  Granted <b>Lifetime</b> to <code>{uid}</code>\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            return

        if data.startswith('adm_ban_'):
            uid = data[len('adm_ban_'):]
            set_ban(uid, True)
            send_message(chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"🚫  User <code>{uid}</code> has been <b>banned</b>.\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            return

        if data.startswith('adm_unban_'):
            uid = data[len('adm_unban_'):]
            set_ban(uid, False)
            send_message(chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"✅  User <code>{uid}</code> has been <b>unbanned</b>.\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            return

    # ── USER CALLBACKS ────────────────────────────────────────
    if data in ('search_mobile', 'search_aadhaar', 'search_eid'):
        if not channel_gate(chat_id):
            return
        if not credit_gate(chat_id):
            return

    if data == 'search_mobile':
        set_session(chat_id, 'awaiting_mobile', {'mode': 'mobile'})
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 Mobile Search 〕</b>\n\n"
            f"▸  Enter your mobile number\n\n"
            f"<i>◌  OTP will be sent to this number</i>",
            reply_markup=get_cancel_keyboard()
        )
    elif data == 'search_aadhaar':
        set_session(chat_id, 'awaiting_aadhaar', {'mode': 'aadhaar'})
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 Aadhaar Search 〕</b>\n\n"
            f"▸  Enter your 12-digit Aadhaar number\n\n"
            f"<i>◌  Spaces are removed automatically</i>",
            reply_markup=get_cancel_keyboard()
        )
    elif data == 'search_eid':
        set_session(chat_id, 'awaiting_eid_input', {'mode': 'eid'})
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 EID Search 〕</b>\n\n"
            f"▸  Enter your Enrollment ID (EID)\n\n"
            f"<i>◌  Format: 1234/56789/12345</i>",
            reply_markup=get_cancel_keyboard()
        )

# ============== ADMIN COMMAND HANDLER ==============
def handle_admin_command(chat_id, text, msg=None):
    parts = text.strip().split()
    cmd = parts[0].lower()

    if cmd == '/admin':
        show_admin_home(chat_id)
        return True

    if cmd == '/lookup' and len(parts) == 2:
        show_admin_user_profile(chat_id, parts[1])
        return True

    if cmd == '/add':
        target_uid = None
        if len(parts) >= 2:
            target_uid = parts[1].strip().strip('<>@ ')
        elif msg and 'reply_to_message' in msg:
            reply = msg['reply_to_message']
            if 'forward_from' in reply:
                target_uid = str(reply['forward_from']['id'])
            elif 'from' in reply:
                target_uid = str(reply['from']['id'])

        if target_uid:
            ensure_user(target_uid)
            add_credits(target_uid, 0, make_lifetime=True)
            mark_channel_verified(target_uid)
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"✅  <b>Lifetime Free Access Granted!</b>\n\n"
                f"👤  User ID: <code>{target_uid}</code>\n"
                f"♾  Access: <b>Lifetime (Unlimited Free)</b>\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            try:
                send_message(
                    int(target_uid),
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"🎉  <b>Congratulations!</b>\n\n"
                    f"Aapko Owner dwara <b>Lifetime Free Access</b> grant kar diya gaya hai!\n"
                    f"♾  Credits: <b>Unlimited</b>\n\n{DIVIDER}",
                    reply_markup=get_main_keyboard()
                )
            except Exception:
                pass
            return True
    if cmd == '/ban':
        target_uid = None
        if len(parts) >= 2:
            target_uid = parts[1].strip().strip('<>@ ')
        elif msg and 'reply_to_message' in msg:
            reply = msg['reply_to_message']
            if 'forward_from' in reply:
                target_uid = str(reply['forward_from']['id'])
            elif 'from' in reply:
                target_uid = str(reply['from']['id'])

        if target_uid:
            if is_owner(target_uid):
                send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n⚠️  Cannot ban the owner!\n\n{DIVIDER}", reply_markup=get_admin_back_keyboard())
                return True
            ensure_user(target_uid)
            set_ban(target_uid, True)
            clear_session(target_uid)
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('lock', '🚫')}  <b>User Banned Successfully!</b>\n\n"
                f"👤  User ID: <code>{target_uid}</code>\n"
                f"🔒  Status: <b>Banned (Access Blocked)</b>\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            try:
                send_message(
                    int(target_uid),
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"{em('lock', '🚫')}  <b>Aapko bot se ban kar diya gaya hai.</b>\n\n"
                    f"Aap ab is bot ko use nahi kar sakte.\n{DIVIDER}"
                )
            except Exception:
                pass
            return True
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"⚠️  <b>Usage:</b> <code>/ban &lt;user_id&gt;</code>\n"
                f"<i>Example: <code>/ban 1234567890</code>\nYa kisi user ke message ko reply karke /ban likhein.</i>\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            return True

    if cmd == '/unban':
        target_uid = None
        if len(parts) >= 2:
            target_uid = parts[1].strip().strip('<>@ ')
        elif msg and 'reply_to_message' in msg:
            reply = msg['reply_to_message']
            if 'forward_from' in reply:
                target_uid = str(reply['forward_from']['id'])
            elif 'from' in reply:
                target_uid = str(reply['from']['id'])

        if target_uid:
            ensure_user(target_uid)
            set_ban(target_uid, False)
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('verified', '✅')}  <b>User Unbanned Successfully!</b>\n\n"
                f"👤  User ID: <code>{target_uid}</code>\n"
                f"🔓  Status: <b>Active (Access Restored)</b>\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            try:
                send_message(
                    int(target_uid),
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"{em('verified', '✅')}  <b>Aapka ban hata diya gaya hai!</b>\n\n"
                    f"Aap ab bot ko wapas use kar sakte hain.\n{DIVIDER}",
                    reply_markup=get_main_keyboard()
                )
            except Exception:
                pass
            return True
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"⚠️  <b>Usage:</b> <code>/unban &lt;user_id&gt;</code>\n"
                f"<i>Example: <code>/unban 1234567890</code>\nYa kisi user ke message ko reply karke /unban likhein.</i>\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            return True

    return False

# ============== MESSAGE HANDLER ==============
_KB_ACTIONS = {
    '📱  mobile number':  'search_mobile',
    '🆔  aadhaar number': 'search_aadhaar',
    '📋  eid':            'search_eid',
    '💳  credits':        'credits',
    '💰  buy credits':    'buy',
    '🎁  referral':       'referral',
}

def handle_message(chat_id, message_text):
    logger.info(f"Msg [{chat_id}]: {message_text[:60]}")
    ensure_user(chat_id)

    if is_banned(chat_id):
        send_message(chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"🚫  <b>You have been banned.</b>\n\n"
            f"<i>◌  Contact support if you believe this is a mistake.</i>"
        )
        return

    if not is_owner(chat_id) and not is_channel_member(chat_id):
        send_message(chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 Channel Required 〕</b>\n\n"
            f"▸  Join <b>{CHANNEL_NAME}</b> to use this bot.\n\n"
            f"{DIVIDER}\n"
            f"<i>◌  Tap Join below, then confirm with the button.</i>",
            reply_markup=get_join_keyboard()
        )
        return

    if is_owner(chat_id) and message_text.startswith('/'):
        if handle_admin_command(chat_id, message_text):
            return

    # ── ADMIN SESSION INPUT ───────────────────────────────────
    if is_owner(chat_id):
        s    = get_session(chat_id)
        step = s.get('step', 'main')
        sd   = s.get('data', {})

        if step == 'admin_awaiting_lookup':
            clear_session(chat_id)
            show_admin_user_profile(chat_id, message_text.strip())
            return

        if step == 'admin_awaiting_addcr':
            clear_session(chat_id)
            uid = sd.get('target_uid', '')
            try:
                amount = int(message_text.strip())
                if amount == -1:
                    add_credits(uid, 0, make_lifetime=True)
                    send_message(int(uid),
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"<b>〔 Credits Received 〕</b>\n\n"
                        f"◈  Plan    ·  Lifetime\n◈  Status  ·  Active\n\n{DIVIDER}",
                        reply_markup=get_main_keyboard()
                    )
                    send_message(chat_id,
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"✅  Granted <b>Lifetime</b> to <code>{uid}</code>\n\n{DIVIDER}",
                        reply_markup=get_admin_back_keyboard()
                    )
                else:
                    add_credits(uid, amount)
                    send_message(int(uid),
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"<b>〔 Credits Received 〕</b>\n\n"
                        f"◈  Credits  ·  +{amount}\n"
                        f"◈  Balance  ·  {int(get_credits(uid))}\n\n{DIVIDER}",
                        reply_markup=get_main_keyboard()
                    )
                    send_message(chat_id,
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"✅  Added <b>{amount}</b> credits to <code>{uid}</code>\n\n{DIVIDER}",
                        reply_markup=get_admin_back_keyboard()
                    )
            except ValueError:
                send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗  Invalid amount.\n\n{DIVIDER}", reply_markup=get_admin_back_keyboard())
            return

        if step == 'admin_awaiting_remcr':
            clear_session(chat_id)
            uid = sd.get('target_uid', '')
            try:
                amount = int(message_text.strip())
                remove_credits_from(uid, amount)
                send_message(chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"✅  Removed <b>{amount}</b> credits from <code>{uid}</code>\n"
                    f"◈  New Balance  ·  {int(get_credits(uid))}\n\n{DIVIDER}",
                    reply_markup=get_admin_back_keyboard()
                )
            except ValueError:
                send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗  Invalid amount.\n\n{DIVIDER}", reply_markup=get_admin_back_keyboard())
            return

        if step == 'admin_awaiting_freecredits':
            clear_session(chat_id)
            try:
                amount = int(message_text.strip())
                if amount < 0:
                    raise ValueError
                set_free_credits(amount)
                send_message(chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"✅  Free credits set to <b>{amount}</b> per new user.\n\n{DIVIDER}",
                    reply_markup=get_admin_home_keyboard()
                )
            except ValueError:
                send_message(chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗  Invalid amount. Enter a number ≥ 0.\n\n{DIVIDER}",
                    reply_markup=get_admin_back_keyboard()
                )
            return

        if step == 'admin_awaiting_broadcast':
            clear_session(chat_id)
            send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 Broadcasting… 〕</b>\n\n<i>◌  Please wait…</i>")
            sent, failed = broadcast_message(chat_id, message_text)
            send_message(chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Broadcast Complete 〕</b>\n\n"
                f"◈  Sent    ·  <b>{sent}</b>\n"
                f"◈  Failed  ·  <b>{failed}</b>\n\n{DIVIDER}",
                reply_markup=get_admin_back_keyboard()
            )
            return

    action = _KB_ACTIONS.get(message_text.strip().lower())
    if action:
        if action in ('search_mobile', 'search_aadhaar', 'search_eid'):
            if not channel_gate(chat_id):
                return
            if not credit_gate(chat_id):
                return
            clear_session(chat_id)
            if action == 'search_mobile':
                set_session(chat_id, 'awaiting_mobile', {'mode': 'mobile'})
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n{em('mobile', '📱')}  <b>〔 Mobile Search 〕</b>\n\n▸  Enter your mobile number\n\n<i>{em('sparkle', '📩')}  OTP will be sent to this number</i>",
                    reply_markup=get_cancel_keyboard()
                )
            elif action == 'search_aadhaar':
                set_session(chat_id, 'awaiting_aadhaar', {'mode': 'aadhaar'})
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n{em('card', '🆔')}  <b>〔 Aadhaar Search 〕</b>\n\n▸  Enter your 12-digit Aadhaar number\n\n<i>{em('sparkle', '🔹')}  Spaces are removed automatically</i>",
                    reply_markup=get_cancel_keyboard()
                )
            elif action == 'search_eid':
                set_session(chat_id, 'awaiting_eid_input', {'mode': 'eid'})
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n{em('doc', '📋')}  <b>〔 EID Search 〕</b>\n\n▸  Enter your Enrollment ID (EID)\n\n<i>{em('sparkle', '🔹')}  Format: 1234/56789/12345</i>",
                    reply_markup=get_cancel_keyboard()
                )
        elif action == 'credits':
            show_credits_info(chat_id)
        elif action == 'buy':
            show_buy_menu(chat_id)
        elif action == 'referral':
            show_referral_info(chat_id)
        return

    s = get_session(chat_id)
    current_step = s.get('step', 'main')
    d = s.get('data', {})

    if current_step != 'main':
        idle = time.time() - s.get('last_activity', time.time())
        if idle > SESSION_TIMEOUT:
            clear_session(chat_id)
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('alert', '⏰')}  <b>〔 Session Expired 〕</b>\n\n"
                f"⚠️  Reason   ·  Idle for 5 minutes\n"
                f"{em('card', '💳')}  Credits  ·  Not deducted\n\n"
                f"{DIVIDER}\n"
                f"<i>{em('arrow', '🔄')}  Select a method below to start again.</i>"
            )
            return

    touch_session(chat_id)

    if message_text.lower() in ['/cancel', 'cancel']:
        clear_session(chat_id)
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<i>{em('cross', '❌')}  Session cancelled.</i>"
        )
        return

    if current_step == 'main':
        return

    # MOBILE FLOW
    if current_step == 'awaiting_mobile':
        clean_num = re.sub(r'[\s\-\(\)\+]', '', message_text.strip())
        if clean_num.startswith('91') and len(clean_num) == 12:
            clean_num = clean_num[2:]
        elif clean_num.startswith('0') and len(clean_num) == 11:
            clean_num = clean_num[1:]

        if re.match(r'^\d{10}$', clean_num):
            set_session(chat_id, 'awaiting_name', {**d, 'mobile': clean_num})
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('users', '👤')}  <b>〔 Step 2 of 4 — Name 〕</b>\n\n"
                f"▸  Enter your full name as on Aadhaar\n\n"
                f"<i>{em('fire', '💡')}  Don't know the name? Press the button below.</i>",
                reply_markup={
                    'inline_keyboard': [
                        [{'text': '🔍  Find Auto', 'callback_data': 'auto_name', 'style': 'success'}],
                        [{'text': '❌  Cancel',    'callback_data': 'cancel', 'style': 'danger'}],
                    ]
                }
            )
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('cross', '❌')}  Invalid number.\n\n"
                f"<i>{em('sparkle', '🔹')}  Enter a valid mobile number (e.g. 9876543210 or +919876543210).</i>"
            )

    elif current_step == 'awaiting_name':
        name = message_text.strip().upper() if len(message_text.strip()) >= 2 else "MR"
        _r = send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
            f"◌  Generating…\n"
            f"◌  Auto-solving…"
        )
        _mid = _r.get('result', {}).get('message_id') if _r else None

        def _on_gen():
            if _mid:
                edit_message(chat_id, _mid,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                    f"{em('verified', '✅')}  Generating…\n"
                    f"◌  Auto-solving…"
                )

        solved, image_bytes, captcha_txn_id, transaction_id = _get_and_solve_captcha(chat_id, max_retries=3, on_generated=_on_gen)
        if solved:
            _txt = (f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                    f"{em('verified', '✅')}  Generating…\n"
                    f"{em('verified', '✅')}  Auto-solved\n"
                    f"◌  Sending OTP…")
            if _mid: edit_message(chat_id, _mid, _txt)
            else: send_message(chat_id, _txt)
            session_data = {**d, 'name': name, 'captcha1_txn_id': captcha_txn_id, 'transaction_id': transaction_id, 'captcha_code': solved}
            set_session(chat_id, 'sending_otp', session_data)
            success, result = _new_bot().send_eid_otp(chat_id, session_data['mobile'], session_data['name'], solved, captcha_txn_id, transaction_id)
            if success:
                if _mid:
                    edit_message(chat_id, _mid,
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                        f"{em('verified', '✅')}  Generating…\n"
                        f"{em('verified', '✅')}  Auto-solved\n"
                        f"{em('verified', '✅')}  OTP Sent"
                    )
                set_session(chat_id, 'awaiting_otp', {**session_data, 'eid_otp_txn_id': result})
                send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n{em('sparkle', '📩')}  <b>〔 OTP Sent {em('success', '✅')} 〕</b>\n\n▸  Enter the 6-digit OTP sent to your mobile\n\n<i>{em('lightning', '⏳')}  Valid for 10 minutes</i>", reply_markup=get_cancel_keyboard())
            else:
                clear_session(chat_id)
                send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n{em('cross', '❌')}  OTP failed — {result}\n\n<i>{em('arrow', '🔄')}  Select a method below to retry.</i>")
        elif image_bytes:
            _txt = (f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                    f"{em('verified', '✅')}  Generating…\n"
                    f"{em('cross', '❌')}  Auto-solve failed\n"
                    f"◌  Please solve manually")
            if _mid: edit_message(chat_id, _mid, _txt)
            set_session(chat_id, 'awaiting_captcha1', {**d, 'name': name,
                                        'captcha1_txn_id': captcha_txn_id, 'transaction_id': transaction_id})
            send_photo(chat_id, image_bytes, caption=f"<i>{em('arrow', '👆')}  Type the characters shown above</i>")
        else:
            _captcha_failed(chat_id, _mid)

    elif current_step == 'awaiting_captcha1':
        set_session(chat_id, 'sending_otp', {**d, 'captcha_code': message_text.strip()})
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"{em('satellite', '📡')}  <b>〔 Sending OTP 〕</b>\n\n"
            f"<i>{em('lightning', '⏳')}  Please wait…</i>"
        )
        sd = get_session(chat_id)['data']
        success, result = _new_bot().send_eid_otp(
            chat_id, sd['mobile'], sd['name'],
            sd['captcha_code'], sd['captcha1_txn_id'], sd['transaction_id']
        )
        if success:
            set_session(chat_id, 'awaiting_otp', {**sd, 'eid_otp_txn_id': result})
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('sparkle', '📩')}  <b>〔 OTP Sent {em('success', '✅')} 〕</b>\n\n"
                f"▸  Enter the 6-digit OTP sent to your mobile\n\n"
                f"<i>{em('lightning', '⏳')}  Valid for 10 minutes</i>",
                reply_markup=get_cancel_keyboard()
            )
        else:
            clear_session(chat_id)
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('cross', '❌')}  OTP failed — {result}\n\n"
                f"<i>{em('arrow', '🔄')}  Select a method below to retry.</i>"
            )

    elif current_step == 'awaiting_otp':
        if re.match(r'^\d{6}$', message_text):
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('shield', '🛡')}  <b>〔 Verifying 〕</b>\n\n"
                f"<i>{em('lightning', '⏳')}  Checking OTP…</i>"
            )
            success, eid, name = _new_bot().verify_eid_otp(
                chat_id, d['mobile'], d['name'], message_text,
                d['eid_otp_txn_id'], d['captcha1_txn_id'], d['captcha_code']
            )
            if success:
                verified_name = name if name and name.strip() else "Mr."
                set_session(chat_id, 'awaiting_captcha2', {**d, 'eid': eid, 'verified_name': verified_name})
                _r2 = send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"{em('verified', '✅')}  <b>〔 Identity Verified 〕</b>\n\n"
                    f"{em('users', '👤')}  Name  ·  <b>{verified_name}</b>\n"
                    f"{em('card', '🪪')}  EID   ·  <code>{eid}</code>\n\n"
                    f"{DIVIDER}\n"
                    f"{em('bot', '🤖')}  <b>〔 Captcha for PDF 〕</b>\n\n"
                    f"◌  Generating…\n"
                    f"◌  Auto-solving…"
                )
                def _on_gen2():
                    if _mid2:
                        edit_message(chat_id, _mid2,
                            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                            f"{em('verified', '✅')}  <b>〔 Identity Verified 〕</b>\n\n"
                            f"{em('users', '👤')}  Name  ·  <b>{verified_name}</b>\n"
                            f"{em('card', '🪪')}  EID   ·  <code>{eid}</code>\n\n"
                            f"{DIVIDER}\n"
                            f"{em('bot', '🤖')}  <b>〔 Captcha for PDF 〕</b>\n\n"
                            f"{em('verified', '✅')}  Generating…\n"
                            f"◌  Auto-solving…"
                        )

                solved2, image_bytes, captcha_txn_id, transaction_id = _get_and_solve_captcha(chat_id, max_retries=3, on_generated=_on_gen2)
                if solved2:
                    sd = get_session(chat_id)['data']
                    _txt2 = (f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                             f"{em('verified', '✅')}  <b>〔 Identity Verified 〕</b>\n\n"
                             f"{em('users', '👤')}  Name  ·  <b>{verified_name}</b>\n"
                             f"{em('card', '🪪')}  EID   ·  <code>{eid}</code>\n\n"
                             f"{DIVIDER}\n"
                             f"{em('bot', '🤖')}  <b>〔 Captcha for PDF 〕</b>\n\n"
                             f"{em('verified', '✅')}  Generating…\n"
                             f"{em('verified', '✅')}  Auto-solved\n"
                             f"◌  Sending OTP…")
                    if _mid2: edit_message(chat_id, _mid2, _txt2)
                    else: send_message(chat_id, _txt2)
                    sd2 = {**sd, 'captcha2_txn_id': captcha_txn_id, 'transaction_id2': transaction_id, 'captcha2_code': solved2}
                    set_session(chat_id, 'sending_pdf_otp', sd2)
                    success2, otp_txn_id, msg2 = _new_bot().send_aadhaar_otp(chat_id, sd2['eid'], solved2, captcha_txn_id, transaction_id)
                    if success2:
                        if _mid2:
                            edit_message(chat_id, _mid2,
                                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                                f"{em('verified', '✅')}  <b>〔 Identity Verified 〕</b>\n\n"
                                f"{em('users', '👤')}  Name  ·  <b>{verified_name}</b>\n"
                                f"{em('card', '🪪')}  EID   ·  <code>{eid}</code>\n\n"
                                f"{DIVIDER}\n"
                                f"{em('bot', '🤖')}  <b>〔 Captcha for PDF 〕</b>\n\n"
                                f"{em('verified', '✅')}  Generating…\n"
                                f"{em('verified', '✅')}  Auto-solved\n"
                                f"{em('verified', '✅')}  OTP Sent"
                            )
                        set_session(chat_id, 'awaiting_pdf_otp', {**sd2, 'pdf_otp_txn_id': otp_txn_id})
                        send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n{em('sparkle', '📩')}  <b>〔 OTP Sent {em('success', '✅')} 〕</b>\n\n▸  Enter the 6-digit OTP to download your PDF\n\n<i>{em('lightning', '⏳')}  Valid for 10 minutes</i>", reply_markup=get_cancel_keyboard())
                    else:
                        clear_session(chat_id)
                        send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n{em('cross', '❌')}  OTP failed — {msg2}\n\n<i>{em('arrow', '🔄')}  Select a method below to retry.</i>")
                elif image_bytes:
                    sd = get_session(chat_id)['data']
                    _txt2 = (f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                             f"{em('verified', '✅')}  <b>〔 Identity Verified 〕</b>\n\n"
                             f"{em('users', '👤')}  Name  ·  <b>{verified_name}</b>\n"
                             f"{em('card', '🪪')}  EID   ·  <code>{eid}</code>\n\n"
                             f"{DIVIDER}\n"
                             f"{em('bot', '🤖')}  <b>〔 Captcha for PDF 〕</b>\n\n"
                             f"{em('verified', '✅')}  Generating…\n"
                             f"{em('cross', '❌')}  Auto-solve failed\n"
                             f"◌  Please solve manually")
                    if _mid2: edit_message(chat_id, _mid2, _txt2)
                    set_session(chat_id, 'awaiting_captcha2', {**sd, 'captcha2_txn_id': captcha_txn_id, 'transaction_id2': transaction_id})
                    send_photo(chat_id, image_bytes, caption=f"<i>{em('arrow', '👆')}  Type the characters shown above</i>")
                else:
                    _captcha_failed(chat_id, _mid2)
            else:
                clear_session(chat_id)
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"{em('cross', '❌')}  Verification failed — {eid}\n\n"
                    f"<i>{em('arrow', '🔄')}  Select a method below to retry.</i>"
                )
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('cross', '❌')}  Invalid OTP.\n\n"
                f"<i>{em('sparkle', '🔹')}  Enter the 6-digit number (digits only).</i>"
            )

    elif current_step == 'awaiting_captcha2':
        sd = {**d, 'captcha2_code': message_text.strip()}
        set_session(chat_id, 'sending_pdf_otp', sd)
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"{em('satellite', '📡')}  <b>〔 Sending OTP for PDF 〕</b>\n\n"
            f"<i>{em('lightning', '⏳')}  Please wait…</i>"
        )
        success, otp_txn_id, msg = _new_bot().send_aadhaar_otp(
            chat_id, sd['eid'], sd['captcha2_code'], sd['captcha2_txn_id'], sd['transaction_id2']
        )
        if success:
            set_session(chat_id, 'awaiting_pdf_otp', {**sd, 'pdf_otp_txn_id': otp_txn_id})
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('sparkle', '📩')}  <b>〔 OTP Sent {em('success', '✅')} 〕</b>\n\n"
                f"▸  Enter the 6-digit OTP to download your PDF\n\n"
                f"<i>{em('lightning', '⏳')}  Valid for 10 minutes</i>",
                reply_markup=get_cancel_keyboard()
            )
        else:
            clear_session(chat_id)
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('cross', '❌')}  OTP failed — {msg}\n\n"
                f"<i>{em('arrow', '🔄')}  Select a method below to retry.</i>"
            )

    elif current_step == 'awaiting_pdf_otp':
        if re.match(r'^\d{6}$', message_text):
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('verified', '✅')}  <b>〔 Downloading 〕</b>\n\n"
                f"<i>{em('lightning', '⚡')}  Fetching your Aadhaar PDF…</i>"
            )
            success, pdf_path = _new_bot().download_aadhaar_pdf(
                chat_id, d['eid'], message_text, d['pdf_otp_txn_id'], d['transaction_id2'], False
            )
            if success and pdf_path and '.pdf' in pdf_path:
                deliver_pdf(chat_id, pdf_path, d.get('verified_name', 'Mr.'))
            else:
                clear_session(chat_id)
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"{em('cross', '❌')}  Download failed — {pdf_path}\n\n"
                    f"<i>{em('arrow', '🔄')}  Select a method below to retry.</i>"
                )
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('cross', '❌')}  Invalid OTP.\n\n"
                f"<i>{em('sparkle', '🔹')}  Enter the 6-digit number (digits only).</i>"
            )

    # AADHAAR / EID DIRECT FLOW
    elif current_step == 'awaiting_aadhaar':
        uid = message_text.strip().replace(' ', '')
        if re.match(r'^\d{12}$', uid):
            set_session(chat_id, 'awaiting_captcha_direct', {**d, 'eid': uid, 'verified_name': 'Mr.'})
            _r3 = send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                f"◌  Generating…\n"
                f"◌  Auto-solving…"
            )
            _mid3 = _r3.get('result', {}).get('message_id') if _r3 else None

            def _on_gen3():
                if _mid3:
                    edit_message(chat_id, _mid3,
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                        f"{em('verified', '✅')}  Generating…\n"
                        f"◌  Auto-solving…"
                    )

            solved, image_bytes, captcha_txn_id, transaction_id = _get_and_solve_captcha(chat_id, max_retries=3, on_generated=_on_gen3)
            if solved:
                sd = get_session(chat_id)['data']
                _txt3 = (f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                         f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                         f"{em('verified', '✅')}  Generating…\n"
                         f"{em('verified', '✅')}  Auto-solved\n"
                         f"◌  Sending OTP…")
                if _mid3: edit_message(chat_id, _mid3, _txt3)
                else: send_message(chat_id, _txt3)
                sd2 = {**sd, 'captcha2_txn_id': captcha_txn_id, 'transaction_id2': transaction_id, 'captcha2_code': solved}
                set_session(chat_id, 'sending_pdf_otp_direct', sd2)
                success, otp_txn_id, msg = _new_bot().send_aadhaar_otp(chat_id, sd2['eid'], solved, captcha_txn_id, transaction_id)
                if success:
                    if _mid3:
                        edit_message(chat_id, _mid3,
                            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                            f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                            f"{em('verified', '✅')}  Generating…\n"
                            f"{em('verified', '✅')}  Auto-solved\n"
                            f"{em('verified', '✅')}  OTP Sent"
                        )
                    set_session(chat_id, 'awaiting_pdf_otp_direct', {**sd2, 'pdf_otp_txn_id': otp_txn_id})
                    send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n{em('sparkle', '📩')}  <b>〔 OTP Sent {em('success', '✅')} 〕</b>\n\n▸  Enter the 6-digit OTP to download your PDF\n\n<i>{em('lightning', '⏳')}  Valid for 10 minutes</i>", reply_markup=get_cancel_keyboard())
                else:
                    clear_session(chat_id)
                    send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n{em('cross', '❌')}  OTP failed — {msg}\n\n<i>{em('arrow', '🔄')}  Select a method below to retry.</i>")
            elif image_bytes:
                sd = get_session(chat_id)['data']
                _txt3 = (f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                         f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                         f"{em('verified', '✅')}  Generating…\n"
                         f"{em('cross', '❌')}  Auto-solve failed\n"
                         f"◌  Please solve manually")
                if _mid3: edit_message(chat_id, _mid3, _txt3)
                set_session(chat_id, 'awaiting_captcha_direct', {**sd, 'captcha2_txn_id': captcha_txn_id, 'transaction_id2': transaction_id})
                send_photo(chat_id, image_bytes, caption=f"<i>{em('arrow', '👆')}  Type the characters shown above</i>")
            else:
                _captcha_failed(chat_id, _mid3)
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('cross', '❌')}  Invalid Aadhaar.\n\n"
                f"<i>{em('sparkle', '🔹')}  Enter the 12-digit Aadhaar number (digits only).</i>"
            )

    elif current_step == 'awaiting_eid_input':
        eid = message_text.strip()
        if len(eid) >= 10:
            set_session(chat_id, 'awaiting_captcha_direct', {**d, 'eid': eid, 'verified_name': 'Mr.'})
            _r4 = send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                f"◌  Generating…\n"
                f"◌  Auto-solving…"
            )
            _mid4 = _r4.get('result', {}).get('message_id') if _r4 else None

            def _on_gen4():
                if _mid4:
                    edit_message(chat_id, _mid4,
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                        f"{em('verified', '✅')}  Generating…\n"
                        f"◌  Auto-solving…"
                    )

            solved, image_bytes, captcha_txn_id, transaction_id = _get_and_solve_captcha(chat_id, max_retries=3, on_generated=_on_gen4)
            if solved:
                sd = get_session(chat_id)['data']
                _txt4 = (f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                         f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                         f"{em('verified', '✅')}  Generating…\n"
                         f"{em('verified', '✅')}  Auto-solved\n"
                         f"◌  Sending OTP…")
                if _mid4: edit_message(chat_id, _mid4, _txt4)
                else: send_message(chat_id, _txt4)
                sd2 = {**sd, 'captcha2_txn_id': captcha_txn_id, 'transaction_id2': transaction_id, 'captcha2_code': solved}
                set_session(chat_id, 'sending_pdf_otp_direct', sd2)
                success, otp_txn_id, msg = _new_bot().send_aadhaar_otp(chat_id, sd2['eid'], solved, captcha_txn_id, transaction_id)
                if success:
                    if _mid4:
                        edit_message(chat_id, _mid4,
                            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                            f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                            f"{em('verified', '✅')}  Generating…\n"
                            f"{em('verified', '✅')}  Auto-solved\n"
                            f"{em('verified', '✅')}  OTP Sent"
                        )
                    set_session(chat_id, 'awaiting_pdf_otp_direct', {**sd2, 'pdf_otp_txn_id': otp_txn_id})
                    send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n{em('sparkle', '📩')}  <b>〔 OTP Sent {em('success', '✅')} 〕</b>\n\n▸  Enter the 6-digit OTP to download your PDF\n\n<i>{em('lightning', '⏳')}  Valid for 10 minutes</i>", reply_markup=get_cancel_keyboard())
                else:
                    clear_session(chat_id)
                    send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n{em('cross', '❌')}  OTP failed — {msg}\n\n<i>{em('arrow', '🔄')}  Select a method below to retry.</i>")
            elif image_bytes:
                sd = get_session(chat_id)['data']
                _txt4 = (f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                         f"{em('bot', '🤖')}  <b>〔 Captcha 〕</b>\n\n"
                         f"{em('verified', '✅')}  Generating…\n"
                         f"{em('cross', '❌')}  Auto-solve failed\n"
                         f"◌  Please solve manually")
                if _mid4: edit_message(chat_id, _mid4, _txt4)
                set_session(chat_id, 'awaiting_captcha_direct', {**sd, 'captcha2_txn_id': captcha_txn_id, 'transaction_id2': transaction_id})
                send_photo(chat_id, image_bytes, caption=f"<i>{em('arrow', '👆')}  Type the characters shown above</i>")
            else:
                _captcha_failed(chat_id, _mid4)
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('cross', '❌')}  Invalid EID.\n\n"
                f"<i>{em('sparkle', '🔹')}  Please check and re-enter your Enrollment ID.</i>"
            )

    elif current_step == 'awaiting_captcha_direct':
        sd = {**d, 'captcha2_code': message_text.strip()}
        set_session(chat_id, 'sending_pdf_otp_direct', sd)
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"{em('satellite', '📡')}  <b>〔 Sending OTP 〕</b>\n\n"
            f"<i>{em('lightning', '⏳')}  Please wait…</i>"
        )
        success, otp_txn_id, msg = _new_bot().send_aadhaar_otp(
            chat_id, sd['eid'], sd['captcha2_code'], sd['captcha2_txn_id'], sd['transaction_id2']
        )
        if success:
            set_session(chat_id, 'awaiting_pdf_otp_direct', {**sd, 'pdf_otp_txn_id': otp_txn_id})
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('sparkle', '📩')}  <b>〔 OTP Sent {em('success', '✅')} 〕</b>\n\n"
                f"▸  Enter the 6-digit OTP to download your PDF\n\n"
                f"<i>{em('lightning', '⏳')}  Valid for 10 minutes</i>",
                reply_markup=get_cancel_keyboard()
            )
        else:
            clear_session(chat_id)
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('cross', '❌')}  OTP failed — {msg}\n\n"
                f"<i>{em('arrow', '🔄')}  Select a method below to retry.</i>"
            )

    elif current_step == 'awaiting_pdf_otp_direct':
        if re.match(r'^\d{6}$', message_text):
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('verified', '✅')}  <b>〔 Downloading 〕</b>\n\n"
                f"<i>{em('lightning', '⚡')}  Fetching your Aadhaar PDF…</i>"
            )
            success, pdf_path = _new_bot().download_aadhaar_pdf(
                chat_id, d['eid'], message_text, d['pdf_otp_txn_id'], d['transaction_id2'], False
            )
            if success and pdf_path and '.pdf' in pdf_path:
                deliver_pdf(chat_id, pdf_path, d.get('verified_name', 'Mr.'))
            else:
                clear_session(chat_id)
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"{em('cross', '❌')}  Download failed — {pdf_path}\n\n"
                    f"<i>{em('arrow', '🔄')}  Select a method below to retry.</i>"
                )
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"{em('cross', '❌')}  Invalid OTP.\n\n"
                f"<i>{em('sparkle', '🔹')}  Enter the 6-digit number (digits only).</i>"
            )

# ============== CONCURRENT UPDATE DISPATCHER ==============
_msg_executor = ThreadPoolExecutor(max_workers=300)

def _process_update(update):
    try:
        if 'callback_query' in update:
            cq   = update['callback_query']
            cid  = cq['message']['chat']['id']
            cqid = cq['id']
            data = cq.get('data', '')
            logger.info(f"Button click [{cid}]: {data}")
            if is_banned(cid):
                answer_callback_query(cqid, "You are banned from using this bot.")
                return
            handle_callback(cid, cqid, data)

        elif 'message' in update:
            msg  = update['message']
            cid  = msg['chat']['id']
            text = msg.get('text', '').strip()
            if not text:
                return

            logger.info(f"Message [{cid}]: {text}")

            if is_banned(cid):
                send_message(
                    cid,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"{em('lock', '🚫')}  <b>You are banned.</b>\n\n"
                    f"<i>◌  Aapko is bot se ban kar diya gaya hai. Access blocked.</i>\n\n{DIVIDER}"
                )
                return

            if text.startswith('/start'):
                parts = text.split()
                referrer_id = None
                if len(parts) > 1 and parts[1].startswith('ref_'):
                    try:
                        referrer_id = int(parts[1][4:])
                    except ValueError:
                        pass

                if not is_channel_member(cid):
                    send_message(
                        cid,
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"<b>〔 Channel Required 〕</b>\n\n"
                        f"▸  Join <b>{CHANNEL_NAME}</b> to use this bot.\n\n"
                        f"{DIVIDER}\n"
                        f"<i>◌  Tap the button below after joining.</i>",
                        reply_markup=get_join_keyboard()
                    )
                    return

                ensure_user(cid, referrer_id)
                clear_session(cid)
                send_welcome_banner(cid)
            elif is_owner(cid) and text.startswith('/'):
                if not handle_admin_command(cid, text, msg):
                    handle_message(cid, text)
            else:
                handle_message(cid, text)

    except Exception as e:
        logger.error(f"Update processing error: {e}")

# ============== GET UPDATES ==============
def get_updates(offset=None):
    url    = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {'timeout': 10, 'allowed_updates': ['message', 'callback_query']}
    if offset:
        params['offset'] = offset
    try:
        response = get_telegram_session().get(url, params=params, timeout=13)
        result   = response.json()
        if result.get('ok'):
            return result.get('result', [])
        else:
            logger.error(f"Telegram API error: {result}")
            return []
    except Exception as e:
        logger.error(f"Error getting updates: {e}")
        return []

# ============== MAIN ==============
def main():
    _init_db()

    print("━" * 50)
    print(f"  {CONSOLE_BOT_NAME}  —  starting up")
    print("━" * 50)

    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[ error ] TELEGRAM_BOT_TOKEN not set.")
        return

    try:
        r = get_telegram_session().get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=10
        )
        bot_info = r.json()
        if bot_info.get('ok'):
            _bot_username_val = bot_info['result']['username']
            global _bot_username
            _bot_username = _bot_username_val
            print(f"[ online ]  @{_bot_username_val}")
            if is_warp_proxy_available():
                print(f"[ network]  WARP SOCKS5 proxy active (127.0.0.1:{WARP_PROXY_PORT})")
            else:
                print(f"[ network]  direct connection")
            print(f"[ cracker]  PyPDF2 / 4 threads")
            print(f"[ credits]  system active")
            print(f"[ owner  ]  {OWNER_ID}")
        else:
            print(f"[ error ] Bot auth failed: {bot_info}")
            return
    except Exception as e:
        print(f"[ error ] {e}")
        return

    def _sigint(sig, frame):
        print("\n[ stopped ]")
        os._exit(0)
    signal.signal(signal.SIGINT, _sigint)

    t = threading.Thread(target=_cleanup_sessions, daemon=True)
    t.start()

    print("━" * 50)
    print("  running  —  Ctrl+C to stop")
    print("━" * 50)

    last_update_id = 0

    while True:
        try:
            updates = get_updates(last_update_id + 1)
            for update in updates:
                last_update_id = update.get('update_id')
                _msg_executor.submit(_process_update, update)

        except KeyboardInterrupt:
            print("\n[ stopped ]")
            os._exit(0)
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[ stopped ]")
        os._exit(0)