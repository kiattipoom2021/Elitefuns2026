"""Fernet AES-256 encryption สำหรับ MT5 password (และ secret อื่นในอนาคต)"""
import os
import base64
from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    key = os.getenv("ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY ไม่ได้ตั้งค่าใน .env")
    # รองรับทั้ง raw 32-byte key และ Fernet key (44-char base64)
    if len(key) == 32:
        key = base64.urlsafe_b64encode(key.encode())
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _get_fernet().decrypt(token.encode()).decode()


def generate_key() -> str:
    """รันครั้งเดียวเพื่อสร้าง key ใหม่ — เก็บใน .env"""
    return Fernet.generate_key().decode()
