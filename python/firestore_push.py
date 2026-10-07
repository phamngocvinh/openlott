#!/usr/bin/env python3
"""
firestore_push.py - Script push kết quả xổ số từ file txt lên Cloud Firestore.

Script này ĐỘC LẬP với các scraper hiện tại. Nó chỉ đọc các file kết quả
đã được các scraper ghi ra, rồi đẩy nội dung lên Firestore.

Dùng SAU KHI các scraper chạy xong, có thể tích hợp vào CI/CD pipeline.

Chống mất dữ liệu (guard):
    - Guard mặc định TỪ CHỐI push nếu file mới có ít records hơn content
      hiện tại trên Firestore (phòng trường hợp file local bị thiếu/truncated
      khiến push sẽ ghi đè và mất dữ liệu lịch sử).
    - Dùng --force để ghi đè có chủ đích và bypass guard này.

Usage:
    python firestore_push.py                          # Push tất cả 4 loại
    python firestore_push.py --game mega645           # Chỉ push 1 loại
    python firestore_push.py --dry-run                # Chạy thử, không ghi thật
    python firestore_push.py --force                  # Ghi đè dù content không đổi

Yêu cầu:
    - firebase-admin (pip install firebase-admin)
    - Biến môi trường FIREBASE_CREDENTIALS_PATH trỏ đến service account key JSON
    - File kết quả .txt đã tồn tại trong thư mục python/
"""

import os
import sys
import argparse

# Đường dẫn thư mục chứa script hiện tại
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# Map loại vé -> tên file kết quả
RESULT_FILES = {
    'mega645': 'mega_645_results.txt',
    'power655': 'power_655_results.txt',
    'lotto535': 'lotto_535_results.txt',
    'keno': 'keno_results.txt',
}

VALID_GAMES = list(RESULT_FILES.keys())


def read_file(filepath: str) -> str | None:
    """
    Đọc toàn bộ nội dung file text.

    Args:
        filepath: Đường dẫn tuyệt đối đến file.

    Returns:
        Nội dung file (string) hoặc None nếu file không tồn tại/lỗi đọc.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        print(f"❌ File not found: {filepath}")
        return None
    except IOError as e:
        print(f"❌ Error reading {filepath}: {e}")
        return None


def main():
    """
    Hàm chính: đọc file kết quả và push lên Firestore.
    """
    parser = argparse.ArgumentParser(
        description='Push lottery result files to Cloud Firestore',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
  python firestore_push.py
  python firestore_push.py --game mega645
  python firestore_push.py --dry-run
  python firestore_push.py --force    # Ghi đè dù content không đổi / bypass guard
        """,
    )
    parser.add_argument(
        '--game',
        choices=VALID_GAMES,
        help=f'Chỉ push 1 loại vé cụ thể. Các lựa chọn: {", ".join(VALID_GAMES)}',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Chạy thử: hiển thị những gì sẽ push, không ghi thật vào Firestore',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Ghi đè lên Firestore ngay cả khi nội dung không thay đổi so với lần trước',
    )
    args = parser.parse_args()

    # Kiểm tra biến môi trường
    cred_path = os.environ.get('FIREBASE_CREDENTIALS_PATH')
    if not cred_path:
        print("⚠️  Warning: FIREBASE_CREDENTIALS_PATH not set. Using Application Default Credentials.")
    elif not os.path.exists(cred_path):
        print(f"❌ Error: FIREBASE_CREDENTIALS_PATH points to non-existent file: {cred_path}")
        print("   Please check the path or download the service account key again.")
        sys.exit(1)

    # Import helper (phải import sau khi kiểm tra env để tránh lỗi khó hiểu)
    from firestore_helper import push_results, get_existing_content

    # Xác định danh sách loại vé cần push
    games_to_process = [args.game] if args.game else VALID_GAMES

    print(f"{'='*50}")
    print(f"Firestore Push - Lottery Results Sync")
    print(f"{'='*50}")
    if args.dry_run:
        print("🔍 DRY RUN MODE - No data will be written")
    print(f"Games to process: {', '.join(games_to_process)}")
    print()

    success_count = 0
    skip_count = 0
    error_count = 0

    for game_type in games_to_process:
        filename = RESULT_FILES[game_type]
        filepath = os.path.join(CURRENT_DIR, filename)

        print(f"📄 Processing {game_type} ({filename})...", end=' ')

        # Đọc nội dung file
        content = read_file(filepath)
        if content is None:
            print('❌ FAILED (cannot read file)')
            error_count += 1
            continue

        line_count = len([l for l in content.split('\n') if l.strip()])
        print(f'{line_count} draws found')

        # Dry run: không ghi thật
        if args.dry_run:
            print(f'   🔍 Would push {game_type}: {line_count} draws')
            # Cảnh báo nếu file ít records hơn Firestore (guard sẽ chặn khi push thật)
            try:
                existing = get_existing_content(game_type)
                if existing is not None:
                    existing_line_count = len([l for l in existing.split('\n') if l.strip()])
                    if line_count < existing_line_count:
                        print(
                            f'   ⚠️  Guard warning: file có {line_count} records '
                            f'< Firestore ({existing_line_count}). '
                            f'Sẽ bị chặn khi push thật (trừ --force).'
                        )
            except Exception:
                pass
            success_count += 1
            continue

        # Lấy content hiện tại trên Firestore một lần, dùng chung cho
        # check "không đổi" và guard chống mất data (trừ khi --force)
        existing = None
        if not args.force:
            try:
                existing = get_existing_content(game_type)
            except Exception as e:
                # Nếu lỗi khi đọc Firestore (vd: mất mạng), vẫn tiếp tục push
                print(f'   ⚠️  Could not check existing content ({e}), will push anyway')

        if not args.force:
            # Guard chống mất data: từ chối push nếu file mới ít records hơn Firestore
            if existing is not None:
                existing_line_count = len([l for l in existing.split('\n') if l.strip()])
                if line_count < existing_line_count:
                    print(
                        f'   🛑 BLOCKED: file chỉ có {line_count} records nhưng '
                        f'Firestore đang có {existing_line_count}. Từ chối push để '
                        f'tránh mất dữ liệu lịch sử (dùng --force để ghi đè).'
                    )
                    error_count += 1
                    continue

            # Kiểm tra nếu nội dung không đổi thì skip
            if existing == content:
                print(f'   ⏭️  Skip: content unchanged ({line_count} draws)')
                skip_count += 1
                continue

        # Push lên Firestore
        try:
            push_results(game_type, content)
            print(f'   ✅ Pushed successfully')
            success_count += 1
        except Exception as e:
            print(f'   ❌ FAILED: {e}')
            error_count += 1

    # Tổng kết
    print()
    print(f"{'='*50}")
    print(f"Summary:")
    print(f"  ✅ Successful: {success_count}")
    print(f"  ⏭️  Skipped (unchanged): {skip_count}")
    print(f"  ❌ Errors: {error_count}")
    print(f"{'='*50}")

    # Exit code: 0 nếu không có lỗi, 1 nếu có lỗi
    if error_count > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
