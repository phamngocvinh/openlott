"""
firestore_helper.py - Module dùng chung cho Firestore operations.

Module này khởi tạo Firebase Admin SDK một lần (singleton pattern)
và cung cấp các hàm tiện ích để đọc/ghi dữ liệu kết quả xổ số lên Cloud Firestore.

Cách dùng:
    from firestore_helper import push_results, get_existing_content

    # Ghi dữ liệu lên Firestore
    push_results('mega645', 'nội dung file text...')

    # Đọc dữ liệu hiện tại để so sánh
    old_content = get_existing_content('mega645')
"""

import os
import firebase_admin
from firebase_admin import credentials, firestore

# Biến global giữ Firestore client (singleton pattern)
_db = None


def get_db():
    """
    Lấy Firestore client (khởi tạo Firebase app nếu chưa có).

    Thứ tự ưu tiên credentials:
    1. Biến môi trường FIREBASE_CREDENTIALS_PATH (trỏ đến file JSON key)
    2. Application Default Credentials (hữu ích khi chạy trên Google Cloud)
    """
    global _db
    if _db is None:
        cred_path = os.environ.get('FIREBASE_CREDENTIALS_PATH')
        if cred_path and os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
        else:
            # Fallback: dùng ADC (Application Default Credentials)
            # hữu ích khi chạy trên Cloud Run/Cloud Functions
            cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)
        _db = firestore.client()
    return _db


def push_results(game_type: str, content: str):
    """
    Đẩy nội dung file text kết quả xổ số lên Firestore.

    Args:
        game_type: Tên loại vé ('mega645', 'power655', 'lotto535', 'keno').
        content: Nội dung file text (nhiều dòng, mỗi dòng là một kỳ quay).

    Mỗi document trong Firestore có cấu trúc:
    {
        'content': string,    // Nội dung file text
        'updatedAt': Timestamp // Thời điểm cập nhật (tự động từ server)
    }
    """
    db = get_db()
    doc_ref = db.collection('results').document(game_type)
    doc_ref.set({
        'content': content,
        'updatedAt': firestore.SERVER_TIMESTAMP,
    })
    line_count = len([l for l in content.split('\n') if l.strip()])
    print(f"✅ Pushed {game_type} to Firestore ({line_count} draws)")


def get_existing_content(game_type: str) -> str | None:
    """
    Đọc nội dung hiện tại từ Firestore để so sánh.

    Args:
        game_type: Tên loại vé.

    Returns:
        Nội dung text hiện tại, hoặc None nếu chưa có document.
    """
    db = get_db()
    doc = db.collection('results').document(game_type).get()
    if doc.exists:
        data = doc.to_dict()
        if data:
            return data.get('content')
    return None
