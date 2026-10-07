#!/usr/bin/env python3
"""
firestore_sync_scraper.py - Script đồng bộ dữ liệu xổ số lên Cloud Firestore.

Quy trình:
    1) Chạy 4 script cào dữ liệu (Keno, Lotto 535, Mega 645, Power 655)
    2) Push các file kết quả mới nhất lên Cloud Firestore

Ghi chú:
    - Repo public này KHÔNG chứa file kết quả .txt. Khi file chưa tồn tại,
      script lấy Firestore làm BASE: đọc content hiện tại trên Firestore và
      ghi xuống file local TRƯỚC khi chạy scraper, để scraper merge dữ liệu
      web + base giữ nguyên lịch sử.
    - Nếu file thiếu mà KHÔNG đọc được Firestore (mất mạng/sai credentials),
      script BỎ QUA game đó để tránh ghi đè làm mất dữ liệu lịch sử.
    - Không có tính năng Telegram.

Usage:
    python firestore_sync_scraper.py
    python firestore_sync_scraper.py --dry-run       # Chạy thử, không push
    python firestore_sync_scraper.py --force          # Push kể cả content không đổi

Yêu cầu:
    - Các script scraper phải tồn tại trong cùng thư mục
    - firebase-admin (pip install firebase-admin)
    - Biến môi trường FIREBASE_CREDENTIALS_PATH trỏ đến service account key JSON
"""

import os
import sys
import subprocess
import datetime
import argparse

# Đường dẫn gốc của thư mục python hiện tại
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))


class FirestoreConstants:
    """
    Lớp định nghĩa tất cả các hằng số dùng trong chương trình.
    Đáp ứng nghiêm ngặt quy tắc No Hardcoded Strings.
    """
    APP_NAME = "Deeplott"

    # Danh sách các scraper cần chạy
    SCRAPER_SCRIPTS = [
        "mega_645_scraper.py",
        "power_655_scraper.py",
        "lotto_535_scraper.py",
        "keno_scraper.py",
    ]

    # Danh sách các file kết quả tương ứng
    RESULT_FILES = [
        "mega_645_results.txt",
        "power_655_results.txt",
        "lotto_535_results.txt",
        "keno_results.txt",
    ]

    # Map file kết quả -> game_type dùng để lấy base từ Firestore
    RESULT_FILE_TO_GAME = {
        "mega_645_results.txt": "mega645",
        "power_655_results.txt": "power655",
        "lotto_535_results.txt": "lotto535",
        "keno_results.txt": "keno",
    }

    # Tên script push Firestore
    FIRESTORE_PUSH_SCRIPT = "firestore_push.py"

    # Dấu hiệu trong output của scraper cần đưa lên log tổng hợp, dùng để chẩn đoán
    # SỚM khi web nguồn đổi layout (scraper thoát mã 0 nhưng báo không tìm thấy kết quả).
    # So khớp theo kiểu "chứa chuỗi con".
    SCRAPER_OUTPUT_MARKERS = (
        "❌",
        "⚠️",
        "Lỗi",
        "Error",
        "Traceback",
        "Không tìm thấy kết quả",
        "Bỏ qua",
        "hết thời gian chờ",
    )

    # Số dòng output tối đa của mỗi scraper được in ra log tổng hợp
    SCRAPER_OUTPUT_MAX_LINES = 10


class FirestoreSyncScraper:
    """
    Trình đồng bộ dữ liệu xổ số lên Cloud Firestore.
    Thực hiện chạy scraper, phát hiện thay đổi và push lên Firestore.
    """

    def __init__(self):
        self.updated_files: list[str] = []
        self.dry_run: bool = False
        self.force: bool = False
        # Thống kê bootstrap từ Firestore (dùng cho log tổng kết)
        self.seeded_count: int = 0
        self.skipped_count: int = 0

    def log(self, message: str) -> None:
        """In log kèm timestamp."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {message}")

    def run_command(
        self,
        cmd: list,
        cwd: str | None = None,
        check: bool = True,
        capture: bool = False,
    ) -> subprocess.CompletedProcess:
        """
        Chạy một lệnh shell và trả về kết quả.

        Args:
            cmd: Danh sách lệnh (vd: ['python', 'script.py'])
            cwd: Thư mục làm việc (None = thư mục hiện tại)
            check: True = raise exception nếu lệnh thất bại
            capture: True = capture stdout/stderr

        Returns:
            subprocess.CompletedProcess chứa kết quả

        Raises:
            subprocess.CalledProcessError: nếu lệnh thất bại và check=True
            subprocess.TimeoutExpired: nếu lệnh chạy quá 120 giây
        """
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                check=check,
                capture_output=capture,
                text=True,
                timeout=120,
            )
            return result
        except subprocess.CalledProcessError as e:
            self.log(f"❌ LỖI khi chạy lệnh: {' '.join(cmd)}")
            if e.stderr:
                self.log(f"   stderr: {e.stderr}")
            raise
        except subprocess.TimeoutExpired:
            self.log(f"❌ LỖI: Lệnh đã hết thời gian chờ: {' '.join(cmd)}")
            raise

    def run_scrapers(self) -> list[str]:
        """
        Chạy lần lượt 4 script scraper ngay trong thư mục hiện tại.

        Mỗi scraper sẽ đọc dữ liệu cũ từ file .txt tương ứng (nếu có),
        cào thêm dữ liệu mới từ web, và ghi đè vào file.

        Returns:
            Danh sách các file kết quả đã được cập nhật (đường dẫn tương đối)
        """
        # Reset thống kê bootstrap
        self.seeded_count = 0
        self.skipped_count = 0
        updated_files: list[str] = []

        for scraper_file, result_file in zip(
            FirestoreConstants.SCRAPER_SCRIPTS,
            FirestoreConstants.RESULT_FILES,
        ):
            scraper_path = os.path.join(CURRENT_DIR, scraper_file)
            result_path = os.path.join(CURRENT_DIR, result_file)

            self.log(f"🔍 Đang chạy scraper: {scraper_file}")

            # Kiểm tra scraper có tồn tại không
            if not os.path.isfile(scraper_path):
                self.log(f"⚠️  Không tìm thấy {scraper_file}, bỏ qua.")
                continue

            # Bootstrap base từ Firestore nếu file local chưa tồn tại,
            # tránh trường hợp chạy fresh chỉ cào ~100 records làm mất data cũ.
            was_missing = not os.path.isfile(result_path)
            if not self._bootstrap_from_firestore(result_file, result_path):
                self.skipped_count += 1
                self.log(f"⏭️  Bỏ qua {scraper_file} ({result_file}) do không có base an toàn.")
                continue
            if was_missing and os.path.isfile(result_path):
                self.seeded_count += 1

            # Ghi lại mtime cũ của file kết quả để so sánh sau khi chạy
            old_mtime: float | None = None
            if os.path.isfile(result_path):
                old_mtime = os.path.getmtime(result_path)
                old_size = os.path.getsize(result_path)
                self.log(f"   File cũ: {result_file} ({old_size} bytes, {self._format_mtime(old_mtime)})")

            try:
                # Chạy scraper với CWD = CURRENT_DIR để scraper ghi file vào đúng chỗ.
                # capture=True để lấy stdout/stderr phục vụ chẩn đoán khi nghi ngờ web
                # nguồn đổi layout (scraper in ra "Không tìm thấy kết quả nào...").
                result = self.run_command(
                    [sys.executable, scraper_path], cwd=CURRENT_DIR, capture=True
                )
                self._log_scraper_output(scraper_file, result)
                self.log(f"✅ Hoàn tất scraper: {scraper_file}")

                # Kiểm tra xem file đã được cập nhật chưa (so sánh mtime)
                if os.path.isfile(result_path):
                    new_mtime = os.path.getmtime(result_path)
                    new_size = os.path.getsize(result_path)
                    is_updated = old_mtime is None or new_mtime > old_mtime

                    if is_updated:
                        updated_files.append(result_file)
                        size_diff = new_size - (old_size if old_mtime is not None else 0)
                        self.log(
                            f"📝 File đã được cập nhật: {result_file} "
                            f"({'+' if size_diff > 0 else ''}{size_diff} bytes)"
                        )
                    else:
                        self.log(f"⏭️  File không thay đổi: {result_file} ({new_size} bytes)")
                else:
                    self.log(f"📝 File mới được tạo: {result_file}")
                    updated_files.append(result_file)

            except subprocess.TimeoutExpired:
                self.log(f"⏰ Scraper {scraper_file} chạy quá lâu, bỏ qua.")
                continue
            except Exception as e:
                self.log(f"❌ Lỗi khi chạy {scraper_file}: {e}")
                continue

        return updated_files

    def _log_scraper_output(self, scraper_file: str, result) -> None:
        """
        Ghi lại các dòng output đáng chú ý của scraper vào log tổng hợp.

        Mục đích: khi web nguồn đổi layout, scraper vẫn thoát mã 0 nhưng in ra thông
        báo không tìm thấy kết quả. Đưa các dòng này lên log giúp đối chiếu ngay.

        Args:
            scraper_file: tên file scraper vừa chạy.
            result: subprocess.CompletedProcess chứa stdout/stderr.
        """
        combined = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
        if not combined:
            self.log(f"   (scraper {scraper_file} không in ra thông tin gì)")
            return

        all_lines = [line.strip() for line in combined.splitlines() if line.strip()]
        notable = [
            line
            for line in all_lines
            if any(marker in line for marker in FirestoreConstants.SCRAPER_OUTPUT_MARKERS)
        ]

        if not notable:
            self.log(f"   (scraper {scraper_file}: {len(all_lines)} dòng log, không có cảnh báo)")
            return

        self.log(
            f"   ⚠️  scraper {scraper_file}: "
            f"{len(notable)}/{len(all_lines)} dòng output cần chú ý:"
        )
        for line in notable[: FirestoreConstants.SCRAPER_OUTPUT_MAX_LINES]:
            self.log(f"      │ {line}")

        remaining = len(notable) - FirestoreConstants.SCRAPER_OUTPUT_MAX_LINES
        if remaining > 0:
            self.log(f"      │ ... (còn {remaining} dòng, xem log chi tiết của scraper)")

    def _bootstrap_from_firestore(self, result_file: str, result_path: str) -> bool:
        """
        Seed file kết quả local từ Firestore khi file chưa tồn tại.

        Khi chạy fresh (không có file .txt local), các scraper chỉ cào được
        ~100 records mới nhất từ web. Nếu push trực tiếp sẽ ghi đè và mất
        toàn bộ dữ liệu lịch sử trên Firestore. Method này dùng Firestore
        làm base: đọc content hiện tại và ghi xuống file local TRƯỚC khi
        scraper chạy, để scraper merge web + base giữ nguyên lịch sử.

        Args:
            result_file: Tên file kết quả (vd: 'mega_645_results.txt').
            result_path: Đường dẫn tuyệt đối đến file kết quả.

        Returns:
            True = có thể chạy scraper cho game này.
            False = phải BỎ QUA game (không thể xác định base an toàn,
            tránh push dữ liệu thiếu làm mất data trên Firestore).
        """
        # Đã có file local → dùng base local như trước đây
        if os.path.isfile(result_path):
            return True

        game_type = FirestoreConstants.RESULT_FILE_TO_GAME.get(result_file)
        if not game_type:
            self.log(f"⚠️  Không xác định được game_type cho {result_file}, bỏ qua.")
            return False

        cred_path = os.environ.get('FIREBASE_CREDENTIALS_PATH')
        if not cred_path or not os.path.exists(cred_path):
            self.log(
                f"❌ {result_file} chưa tồn tại và không đọc được Firestore "
                f"(FIREBASE_CREDENTIALS_PATH thiếu hoặc trỏ đến file không tồn tại). "
                f"Bỏ qua game để tránh mất dữ liệu lịch sử."
            )
            return False

        try:
            # Import muộn sau khi kiểm tra env để tránh lỗi khó hiểu
            from firestore_helper import get_existing_content
        except Exception as e:
            self.log(f"❌ Không import được firestore_helper: {e}. Bỏ qua {result_file}.")
            return False

        self.log(f"🔄 {result_file} chưa tồn tại, đang lấy base từ Firestore ({game_type})...")
        try:
            existing = get_existing_content(game_type)
        except Exception as e:
            self.log(
                f"❌ Đọc Firestore ({game_type}) thất bại: {e}. "
                f"Bỏ qua game để tránh mất dữ liệu lịch sử."
            )
            return False

        if existing is None:
            self.log(
                f"   Firestore chưa có dữ liệu cho {game_type}. "
                f"Đây là lần chạy đầu tiên, scraper sẽ cào dữ liệu mới nhất từ web."
            )
            # Không có gì để mất → cho phép scraper cào mới
            return True

        try:
            with open(result_path, 'w', encoding='utf-8') as f:
                f.write(existing)
                if existing and not existing.endswith('\n'):
                    f.write('\n')
        except IOError as e:
            self.log(f"❌ Ghi file base {result_file} thất bại: {e}. Bỏ qua game.")
            return False

        seeded_count = len([l for l in existing.split('\n') if l.strip()])
        self.log(f"✅ Đã seed {seeded_count} records từ Firestore vào {result_file} làm base.")
        return True

    def push_to_firestore(self) -> bool:
        """
        Gọi firestore_push.py để đẩy dữ liệu lên Cloud Firestore.

        Returns:
            True nếu push thành công, False nếu thất bại
        """
        push_script = os.path.join(CURRENT_DIR, FirestoreConstants.FIRESTORE_PUSH_SCRIPT)

        if not os.path.isfile(push_script):
            self.log(f"❌ Không tìm thấy {FirestoreConstants.FIRESTORE_PUSH_SCRIPT}")
            return False

        # Xây dựng lệnh push
        cmd = [sys.executable, push_script]
        if self.dry_run:
            cmd.append('--dry-run')
        if self.force:
            cmd.append('--force')

        self.log(f"🚀 Đang push dữ liệu lên Cloud Firestore...")
        if self.dry_run:
            self.log(f"   (dry-run mode: sẽ không ghi thật)")

        try:
            self.run_command(cmd, cwd=CURRENT_DIR)
            if not self.dry_run:
                self.log(f"✅ Push lên Firestore thành công!")
            else:
                self.log(f"✅ Dry-run hoàn tất (không có dữ liệu nào được ghi)")
            return True
        except Exception as e:
            self.log(f"❌ Push lên Firestore thất bại: {e}")
            return False

    def _format_mtime(self, mtime: float) -> str:
        """Format timestamp thành chuỗi dễ đọc."""
        dt = datetime.datetime.fromtimestamp(mtime)
        return dt.strftime("%d/%m/%Y %H:%M:%S")

    def run(self) -> tuple[bool, str, int]:
        """
        Luồng thực thi chính của chương trình.

        Returns:
            Tuple (success: bool, message: str, updated_files_count: int)
        """
        mode_label = " (DRY RUN)" if self.dry_run else ""
        self.log(f"{'='*50}")
        self.log(f"=== Bắt đầu đồng bộ dữ liệu xổ số lên Firestore{mode_label} ===")
        self.log(f"{'='*50}")
        self.log(f"📋 Danh sách scraper sẽ chạy: {', '.join(FirestoreConstants.SCRAPER_SCRIPTS)}")

        try:
            # Bước 1: Chạy tất cả scraper trong thư mục hiện tại
            self.log(f"\n📌 Bước 1: Chạy các script cào dữ liệu...")
            updated_files = self.run_scrapers()

            if updated_files:
                self.log(f"\n📌 Các file được cập nhật: {', '.join(updated_files)}")
            else:
                self.log(f"\n📌 Không có file nào được cập nhật bởi scraper.")
            if self.seeded_count:
                self.log(f"📌 Đã seed {self.seeded_count} file base từ Firestore (do thiếu file local).")
            if self.skipped_count:
                self.log(f"⏸️  Đã bỏ qua {self.skipped_count} game do không có base an toàn (tránh mất data).")

            # Bước 2: Push lên Firestore
            self.log(f"\n📌 Bước 2: Đồng bộ lên Cloud Firestore...")
            push_success = self.push_to_firestore()

            if not push_success:
                error_msg = "Đồng bộ thất bại ở bước push Firestore."
                self.log(f"❌ {error_msg}")
                return False, error_msg, len(updated_files)

            # Tổng kết
            if updated_files:
                message = (
                    f"Đồng bộ thành công! "
                    f"Đã cập nhật {len(updated_files)} file: {', '.join(updated_files)}"
                )
            else:
                message = "Không có dữ liệu mới. Firestore đã được đồng bộ."
            if self.skipped_count:
                message += f" | Bỏ qua {self.skipped_count} game do thiếu base an toàn."

            self.log(f"\n{'='*50}")
            self.log(f"✅ {message}")
            self.log(f"{'='*50}")
            return True, message, len(updated_files)

        except Exception as e:
            error_msg = f"Lỗi hệ thống trong quá trình đồng bộ: {str(e)}"
            self.log(f"❌ {error_msg}")
            return False, error_msg, 0


def main():
    """
    Hàm chính: Khởi tạo và chạy FirestoreSyncScraper.
    """
    parser = argparse.ArgumentParser(
        description='Đồng bộ dữ liệu xổ số: chạy scraper → push lên Cloud Firestore',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
  python firestore_sync_scraper.py              # Chạy scraper + push Firestore
  python firestore_sync_scraper.py --dry-run     # Chạy thử, không push
  python firestore_sync_scraper.py --force       # Push kể cả content không đổi

Chạy fresh (thiếu file .txt local): script lấy base từ Firestore, nếu
không đọc được Firestore sẽ BỎ QUA game để tránh mất dữ liệu lịch sử.
        """,
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Chạy thử: chạy scraper nhưng không push lên Firestore',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Push lên Firestore ngay cả khi nội dung không thay đổi so với lần trước',
    )
    args = parser.parse_args()

    scraper = FirestoreSyncScraper()
    scraper.dry_run = args.dry_run
    scraper.force = args.force

    success, message, count = scraper.run()

    # Trả về exit code phù hợp cho CI/CD
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
