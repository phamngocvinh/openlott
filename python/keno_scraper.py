"""
Phiên bản lịch sử (Version History):
v1.0.0: Phiên bản đầu tiên. Cào kết quả xổ số Keno từ trang minhchinh.com
        và lưu kết quả vào file keno_results.txt với định dạng yêu cầu.
        Hỗ trợ cào tăng trưởng (incremental scraping) dựa trên dữ liệu cũ để tránh trùng lặp.
v1.1.0: Sửa lỗi không cào được dữ liệu sau khi minhchinh.com thiết kế lại trang Keno
        (giao diện mới dùng tiền tố CSS ".kn-"). Bộ phân tích cú pháp HTML được cập nhật
        sang cấu trúc mới: div#containerKQKeno > .kn-card > .kn-table > các .kn-row
        (kỳ quay .kn-ky, thời gian .kn-time / .kn-date, bộ 20 số .kn-ball).
        Định dạng file đầu ra (DD/MM/YYYY,HH:MM,n1..n20) và toàn bộ luồng
        gộp / loại trùng / sắp xếp được giữ nguyên để không ảnh hưởng các thành phần phía sau.
v1.2.0: Fallback theo KỲ (dự phòng khi kéo thiếu data). Thêm nguồn dự phòng
        ketquadientoan.com (cấu trúc .kq-*). parse_keno_rows giờ trả về ParseOutcome để phát
        hiện kỳ thiếu số thay vì âm thầm bỏ qua. run() chỉ tải nguồn dự phòng khi thiếu kỳ
        hoặc không cào được kỳ mới, rồi bù các kỳ thiếu. Định dạng file giữ nguyên.

Lưu ý múi giờ:
    Script dùng datetime.now() để lấy "ngày hôm nay". Trên CI (GitHub Actions),
    biến môi trường TZ=Asia/Saigon được set để now() trả về giờ VN, tránh request sai ngày.
"""

import os
import re
import requests
import time
from bs4 import BeautifulSoup
from datetime import datetime

from result_fallback import (
    GameId,
    ParseOutcome,
    detect_missing_fields,
)

class KenoConstants:
    """
    Lớp định nghĩa tất cả các hằng số dùng trong chương trình để tránh việc hardcode text.
    Đáp ứng nghiêm ngặt quy tắc số 1 của người dùng.
    """
    APP_NAME = "Deeplott"
    URL_BASE = "https://www.minhchinh.com/xo-so-dien-toan-keno.html"
    OUTPUT_FILENAME = "keno_results.txt"

    # Các tiêu đề HTTP headers để giả lập trình duyệt
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    # Định dạng ngày gửi lên máy chủ và ngày giờ lưu trữ
    DATE_FORMAT_REQUEST = "%d-%m-%Y"  # Định dạng gửi đi: dd-mm-yyyy
    DATETIME_FORMAT = "%d/%m/%Y %H:%M"

    # --- Cấu trúc HTML mới của trang Keno (giao diện thiết kế lại 09/2026) ---
    # Vùng chứa toàn bộ bảng kết quả Keno
    HTML_CONTAINER_ID = "containerKQKeno"
    HTML_CONTAINER_CLASS = "kn"
    # Mỗi kỳ quay là một dòng của bảng kết quả
    HTML_ROW_CLASS = "kn-row"
    HTML_ROW_HEAD_CLASS = "kn-row-head"
    # Các ô con trong mỗi dòng
    HTML_KY_CLASS = "kn-ky"
    HTML_TIME_CLASS = "kn-time"
    HTML_DATE_CLASS = "kn-date"
    HTML_BALLS_CLASS = "kn-balls"
    HTML_BALL_CLASS = "kn-ball"
    # Số lượng số trúng cần có cho một kỳ quay Keno hợp lệ
    EXPECTED_NUMBERS_COUNT = 20

    # Số lượng bản ghi tối đa để tải về đối chiếu trùng lặp ở bước đầu
    LOOKUP_LIMIT = 100

    # Số trang tối đa quét từ trang chủ mặc định để lấy dữ liệu mới nhất
    MAX_PAGES_TO_SCAN = 10

    # Cấu hình cơ chế tự động thử lại (retry) khi gặp lỗi kết nối/timeout
    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 2
    REQUEST_TIMEOUT_SECONDS = 15

    # Số lượng dòng kết quả tối đa được giữ lại trong file output (mới nhất được ưu tiên)
    MAX_RECORDS = 2000

    # --- Nguồn dự phòng (ketquadientoan.com) ---
    URL_BACKUP = "https://www.ketquadientoan.com/ket-qua-xo-so-dien-toan-keno.html"
    BACKUP_SOURCE_LABEL = "ketquadientoan.com"
    BACKUP_HTML_ITEM_CLASS = "kq-item"
    BACKUP_HTML_KY_CLASS = "kq-ky"
    BACKUP_HTML_TIME_CLASS = "kq-time"
    BACKUP_HTML_BALLS_CLASS = "kq-balls"
    BACKUP_HTML_BALL_CLASS = "kq-ball"
    # Số kỳ gần nhất lấy từ nguồn dự phòng khi nguồn chính hoàn toàn không có kỳ mới
    BACKUP_MERGE_LIMIT = 60

class KenoScraper:
    """
    Trình thu thập dữ liệu chuyên nghiệp để lấy kết quả xổ số Keno từ Minh Chính.
    Thiết kế hướng tới độ tin cậy cao, chạy mượt mà và tối ưu hóa hiệu năng.
    """

    def __init__(self):
        # Khởi tạo các đường dẫn và hằng số từ cấu hình chung
        self.base_url = KenoConstants.URL_BASE
        self.headers = KenoConstants.HEADERS
        self.output_file = KenoConstants.OUTPUT_FILENAME

    def get_existing_records(self, limit=KenoConstants.LOOKUP_LIMIT):
        """
        Đọc một số dòng đầu tiên từ file kết quả hiện tại để đối chiếu trùng lặp.
        Điều này giúp chương trình cào tăng trưởng cực kỳ nhanh và không bị trùng dữ liệu.
        """
        if not os.path.exists(self.output_file):
            return []

        try:
            with open(self.output_file, 'r', encoding='utf-8') as f:
                # Đọc tối đa 'limit' dòng đầu tiên để đối chiếu chéo
                lines = [f.readline().strip() for _ in range(limit)]
                return [line for line in lines if line]
        except Exception as e:
            # Ghi nhận lỗi nếu xảy ra sự cố trong quá trình đọc file
            print(f"Lỗi khi đọc danh sách bản ghi cũ: {e}")
            return []

    def post_request_with_retry(self, session, url, data):
        """
        Gửi yêu cầu POST thông qua Session với cơ chế tự động thử lại khi gặp lỗi kết nối hoặc timeout.
        Đảm bảo cookie tìm kiếm được giữ nguyên qua các trang phân trang.
        """
        for attempt in range(1, KenoConstants.MAX_RETRIES + 1):
            try:
                response = session.post(
                    url,
                    headers=self.headers,
                    data=data,
                    timeout=KenoConstants.REQUEST_TIMEOUT_SECONDS
                )
                response.raise_for_status()
                return response.text
            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                print(f"Lỗi khi gửi yêu cầu đến {url} (Lần thử {attempt}/{KenoConstants.MAX_RETRIES}): {e}")
                if attempt < KenoConstants.MAX_RETRIES:
                    time.sleep(KenoConstants.RETRY_DELAY_SECONDS)
                else:
                    raise e
        return None

    def get_request_with_retry(self, session, url):
        """
        Gửi yêu cầu GET thông qua Session với cơ chế tự động thử lại.
        Được dùng làm phương án dự phòng khi biểu mẫu POST không trả về dữ liệu mong đợi.
        """
        for attempt in range(1, KenoConstants.MAX_RETRIES + 1):
            try:
                response = session.get(
                    url,
                    headers=self.headers,
                    timeout=KenoConstants.REQUEST_TIMEOUT_SECONDS
                )
                response.raise_for_status()
                return response.text
            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                print(f"Lỗi khi tải {url} (Lần thử {attempt}/{KenoConstants.MAX_RETRIES}): {e}")
                if attempt < KenoConstants.MAX_RETRIES:
                    time.sleep(KenoConstants.RETRY_DELAY_SECONDS)
                else:
                    raise e
        return None

    def parse_keno_rows(self, html_content):
        """
        Phân tích cú pháp HTML trang Keno minhchinh (giao diện thiết kế lại).

        Trả về ParseOutcome:
            - records    : các kỳ đủ ngày + giờ + 20 số.
            - incomplete : các kỳ có ngày/giờ nhưng THIẾU số (không đủ 20) để run() thử vá từ
                           nguồn dự phòng ketquadientoan.

        Cấu trúc HTML mới:
            div#containerKQKeno > div.kn-card > div.kn-table > div.kn-row
        Mỗi dòng dữ liệu hợp lệ gồm:
            - .kn-ky    : mã kỳ quay (ví dụ "#296121")
            - .kn-time  : giờ quay   (ví dụ "09:44")
            - .kn-date  : ngày quay  (ví dụ "18/09/2026")
            - .kn-balls : vùng chứa 20 span.kn-ball (bộ số kết quả)
        """
        outcome = ParseOutcome()
        if not html_content:
            return outcome

        soup = BeautifulSoup(html_content, 'html.parser')

        # Ưu tiên đúng vùng chứa kết quả để tránh bắt nhầm các .kn-row khác (nếu có) trên trang
        container = soup.find('div', id=KenoConstants.HTML_CONTAINER_ID)
        if container is None:
            container = soup.find('div', class_=KenoConstants.HTML_CONTAINER_CLASS)
        if container is None:
            container = soup

        for row in container.find_all('div', class_=KenoConstants.HTML_ROW_CLASS):
            # Bỏ qua dòng tiêu đề của bảng kết quả
            if KenoConstants.HTML_ROW_HEAD_CLASS in (row.get('class') or []):
                continue

            # 1. Trích xuất mã kỳ quay (chỉ dùng để ghi log khi dữ liệu không hợp lệ)
            ky_div = row.find('div', class_=KenoConstants.HTML_KY_CLASS)
            draw_id = ky_div.get_text(strip=True) if ky_div else "N/A"

            # 2. Trích xuất ngày và giờ quay thưởng
            date_div = row.find('div', class_=KenoConstants.HTML_DATE_CLASS)
            time_div = row.find('div', class_=KenoConstants.HTML_TIME_CLASS)
            draw_date = date_div.get_text(strip=True) if date_div else ""
            draw_time = time_div.get_text(strip=True) if time_div else ""

            # 3. Trích xuất danh sách 20 số trúng giải Keno
            balls_div = row.find('div', class_=KenoConstants.HTML_BALLS_CLASS)
            numbers = []
            if balls_div:
                numbers = [
                    ball.get_text(strip=True)
                    for ball in balls_div.find_all('span', class_=KenoConstants.HTML_BALL_CLASS)
                    if ball.get_text(strip=True).isdigit()
                ]

            # Không có ngày/giờ thì không thể định danh kỳ -> bỏ qua hoàn toàn.
            if not draw_date or not draw_time:
                print(
                    f"Bỏ qua kỳ quay {draw_id} do thiếu ngày/giờ "
                    f"(ngày='{draw_date}', giờ='{draw_time}')."
                )
                continue

            record = f"{draw_date},{draw_time},{','.join(numbers)}"
            missing_fields = detect_missing_fields(GameId.KENO, record)
            outcome.add(GameId.KENO, record)
            if missing_fields:
                print(
                    f"⚠️  Kỳ quay {draw_id} thiếu dữ liệu "
                    f"(nhận được {len(numbers)}/{KenoConstants.EXPECTED_NUMBERS_COUNT} số) "
                    f"-> sẽ thử vá từ nguồn dự phòng."
                )

        return outcome

    @staticmethod
    def record_key(record):
        """Khóa định danh kỳ Keno = DD/MM/YYYY,HH:MM (2 field đầu)."""
        parts = record.split(",")
        if len(parts) >= 2:
            return f"{parts[0]},{parts[1]}"
        return record

    def fetch_page_content_backup(self):
        """Tải HTML từ nguồn dự phòng ketquadientoan (dùng lại cơ chế retry)."""
        session = requests.Session()
        try:
            return self.get_request_with_retry(session, KenoConstants.URL_BACKUP)
        except Exception as e:
            print(f"⚠️  Không tải được nguồn dự phòng {KenoConstants.BACKUP_SOURCE_LABEL}: {e}")
            return None

    def parse_keno_backup(self, html_content):
        """
        Phân tích HTML nguồn dự phòng ketquadientoan (cấu trúc .kq-*).

        Mỗi kỳ là một <article class="kq-item">:
            - .kq-ky    : mã kỳ (chỉ để log)
            - .kq-time  : "21:52 · 13/10/2026" -> giờ + ngày
            - .kq-balls : 20 span.kq-ball

        Trả về ParseOutcome (records đầy đủ + incomplete nếu thiếu số).
        """
        outcome = ParseOutcome()
        if not html_content:
            return outcome

        soup = BeautifulSoup(html_content, 'html.parser')
        for article in soup.find_all('article', class_=KenoConstants.BACKUP_HTML_ITEM_CLASS):
            time_div = article.find('div', class_=KenoConstants.BACKUP_HTML_TIME_CLASS)
            if not time_div:
                continue
            raw = time_div.get_text(" ", strip=True)
            time_match = re.search(r'(\d{2}:\d{2})', raw)
            date_match = re.search(r'(\d{2}/\d{2}/\d{4})', raw)
            if not time_match or not date_match:
                continue
            draw_time = time_match.group(1)
            draw_date = date_match.group(1)

            balls_div = article.find('div', class_=KenoConstants.BACKUP_HTML_BALLS_CLASS)
            numbers = []
            if balls_div:
                numbers = [
                    ball.get_text(strip=True)
                    for ball in balls_div.find_all('span', class_=KenoConstants.BACKUP_HTML_BALL_CLASS)
                    if ball.get_text(strip=True).isdigit()
                ]

            record = f"{draw_date},{draw_time},{','.join(numbers)}"
            outcome.add(GameId.KENO, record)

        return outcome

    def fill_from_backup(self, new_records, incomplete_records, existing_records):
        """
        Bù các kỳ thiếu từ nguồn dự phòng (lazy: chỉ gọi khi thực sự thiếu dữ liệu).

        - Với mỗi kỳ thiếu trong incomplete_records: nếu nguồn dự phòng có bản đầy đủ cùng
          khóa thì dùng bản đó.
        - Nếu nguồn chính hoàn toàn không có kỳ mới: bổ sung các kỳ gần nhất từ nguồn dự phòng.

        Returns:
            Danh sách new_records đã được bù.
        """
        # Chỉ tải nguồn dự phòng khi thiếu kỳ HOẶC không cào được kỳ mới nào.
        if new_records and not incomplete_records:
            return new_records

        print(
            f"⚠️  Keno thiếu dữ liệu (kỳ mới: {len(new_records)}, kỳ chưa đủ: "
            f"{len(incomplete_records)}), tải nguồn dự phòng {KenoConstants.BACKUP_SOURCE_LABEL}..."
        )
        html = self.fetch_page_content_backup()
        if not html:
            return new_records

        backup_outcome = self.parse_keno_backup(html)
        backup_map = {self.record_key(record): record for record in backup_outcome.records}
        known_keys = {self.record_key(record) for record in existing_records}
        known_keys |= {self.record_key(record) for record in new_records}

        added = 0
        for record in incomplete_records:
            key = self.record_key(record)
            backup_record = backup_map.get(key)
            if backup_record and key not in known_keys:
                new_records.append(backup_record)
                known_keys.add(key)
                added += 1
                print(f"   ↳ Vá kỳ {key} từ nguồn dự phòng.")

        # Nếu vẫn không có kỳ mới nào: bổ sung các kỳ gần nhất từ nguồn dự phòng.
        if not new_records:
            for record in backup_outcome.records[: KenoConstants.BACKUP_MERGE_LIMIT]:
                key = self.record_key(record)
                if key not in known_keys:
                    new_records.append(record)
                    known_keys.add(key)
                    added += 1

        print(f"   Vá dự phòng Keno: thêm {added} kỳ từ nguồn dự phòng.")
        return new_records

    def scrape_today(self, existing_records):
        """
        Quét kết quả Keno mới nhất bằng cách giả lập thao tác gửi biểu mẫu tìm kiếm
        cho ngày hôm nay (kết hợp phân trang). Dừng ngay lập tức khi phát hiện
        bản ghi đã có trong file kết quả để đảm bảo cào tăng trưởng.

        Returns:
            (new_records, incomplete_records)
        """
        today_str = datetime.now().strftime(KenoConstants.DATE_FORMAT_REQUEST)
        print(f"Bắt đầu cào Keno mới nhất hôm nay ({today_str}) từ Minh Chính...")

        # Khởi tạo session để lưu cookie tìm kiếm
        session = requests.Session()

        new_records = []
        incomplete_records = []
        seen_incomplete = set()
        stop_scraping = False
        # Tập hợp các bản ghi đã biết (dữ liệu cũ + dữ liệu vừa cào) để chống trùng lặp
        seen_records = set(existing_records)

        # Duyệt qua các trang phân trang kết quả của hôm nay
        for page in range(1, KenoConstants.MAX_PAGES_TO_SCAN + 1):
            if stop_scraping:
                break

            # Thiết lập dữ liệu form POST khớp với biểu mẫu #frmSearch của giao diện mới
            post_data = {
                "date": today_str,
                "ky": "",
                "number": "",
                "page": str(page)
            }

            print(f"Trang {page}: Đang tải dữ liệu từ URL: {self.base_url}")
            try:
                # Gửi yêu cầu POST mang tham số ngày hôm nay để lấy dữ liệu kết quả chính xác
                html_content = self.post_request_with_retry(session, self.base_url, data=post_data)
            except Exception as e:
                print(f"Bỏ qua trang {page} do lỗi: {e}")
                break

            # Sử dụng BeautifulSoup để phân tích cú pháp mã nguồn HTML theo giao diện mới
            page_outcome = self.parse_keno_rows(html_content)
            page_records = page_outcome.records
            self._collect_incomplete(page_outcome.incomplete, incomplete_records, seen_incomplete)

            # Phương án dự phòng: nếu POST không trả về dòng nào ở trang đầu tiên
            # (ví dụ máy chủ thay đổi cách xử lý biểu mẫu), thử tải trực tiếp bằng GET.
            if not page_records and page == 1:
                print("Không nhận được dữ liệu từ biểu mẫu POST, thử tải trực tiếp bằng GET...")
                try:
                    html_content = self.get_request_with_retry(session, self.base_url)
                    page_outcome = self.parse_keno_rows(html_content)
                    page_records = page_outcome.records
                    self._collect_incomplete(
                        page_outcome.incomplete, incomplete_records, seen_incomplete
                    )
                except Exception as e:
                    print(f"Phương án dự phòng GET cũng thất bại: {e}")

            # Nếu vẫn không có dòng nào thì dừng vòng lặp
            if not page_records:
                print(f"Không tìm thấy kết quả nào ở trang {page} cho ngày {today_str}.")
                break

            page_records_count = 0
            for record in page_records:
                # Nếu dòng kết quả này đã biết thì dừng cào ngay (mọi kỳ cũ hơn đều đã có)
                if record in seen_records:
                    print(f"Bản ghi đã tồn tại trong file: {record}. Dừng cào tại đây.")
                    stop_scraping = True
                    break

                new_records.append(record)
                seen_records.add(record)
                page_records_count += 1

            print(f"Trang {page}: Thu thập được {page_records_count} kỳ quay mới.")

        return new_records, incomplete_records

    def _collect_incomplete(self, records, incomplete_records, seen_incomplete):
        """Gom các kỳ thiếu dữ liệu (loại trùng theo khóa date,time)."""
        for record in records:
            key = self.record_key(record)
            if key not in seen_incomplete:
                seen_incomplete.add(key)
                incomplete_records.append(record)

    def parse_line_datetime(self, line):
        """
        Trích xuất đối tượng datetime từ một dòng kết quả để phục vụ việc sắp xếp thứ tự.
        """
        parts = line.strip().split(',')
        if len(parts) >= 2:
            date_str, time_str = parts[0], parts[1]
            try:
                return datetime.strptime(f"{date_str} {time_str}", KenoConstants.DATETIME_FORMAT)
            except ValueError:
                pass
        return datetime.min

    def save_and_sort_records(self, new_data):
        """
        Gộp dữ liệu mới cào và dữ liệu cũ, loại bỏ trùng lặp và sắp xếp theo thứ tự thời gian giảm dần
        (kỳ quay mới nhất luôn nằm ở đầu file), sau đó ghi lại vào file kết quả.
        """
        if not new_data and not os.path.exists(self.output_file):
            print("Không có dữ liệu mới và không tồn tại file kết quả cũ.")
            return

        all_records = list(new_data)

        # Đọc toàn bộ dữ liệu hiện có trong file để gộp chung nhằm sắp xếp tổng thể
        if os.path.exists(self.output_file):
            try:
                with open(self.output_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line_stripped = line.strip()
                        if line_stripped:
                            all_records.append(line_stripped)
            except Exception as e:
                print(f"Lỗi khi đọc file kết quả cũ để gộp: {e}")

        # Loại bỏ trùng lặp bằng cấu trúc set
        unique_records = list(set(all_records))

        # Sắp xếp các bản ghi theo thời gian giảm dần (mới nhất lên đầu)
        unique_records.sort(key=self.parse_line_datetime, reverse=True)

        # Giữ lại tối đa MAX_RECORDS dòng mới nhất (cắt bỏ các dòng cũ phía cuối)
        if len(unique_records) > KenoConstants.MAX_RECORDS:
            truncated_count = len(unique_records) - KenoConstants.MAX_RECORDS
            unique_records = unique_records[:KenoConstants.MAX_RECORDS]
            print(f"Đã cắt bỏ {truncated_count} dòng cũ, chỉ giữ lại {KenoConstants.MAX_RECORDS} dòng mới nhất.")

        try:
            with open(self.output_file, 'w', encoding='utf-8') as f:
                # Ghi lại toàn bộ danh sách đã được sắp xếp gọn gàng và loại trùng
                for line in unique_records:
                    f.write(line + '\n')
            print(f"Cập nhật thành công vào '{self.output_file}'. Đã gộp và sắp xếp thứ tự {len(unique_records)} kỳ quay.")
        except IOError as e:
            print(f"Lỗi ghi tập tin kết quả (IO Error): {e}")

    def run(self):
        """
        Luồng thực thi cốt lõi của tiến trình cào dữ liệu Keno.
        """
        print(f"=== Bắt đầu chương trình cào Keno cho {KenoConstants.APP_NAME} ===")

        # 1. Đọc dữ liệu cũ để đối chiếu
        existing_records = self.get_existing_records()
        if existing_records:
            print(f"Đã nạp {len(existing_records)} dòng kết quả cũ để đối chiếu chống trùng lặp.")
        else:
            print("Chưa có kết quả cũ hoặc file trống. Tiến hành cào mới hoàn toàn.")

        # 2. Quét dữ liệu kết quả hôm nay (nguồn chính minhchinh)
        all_new_records, incomplete_records = self.scrape_today(existing_records)

        # 2b. Dự phòng theo kỳ: bù kỳ thiếu từ ketquadientoan (chỉ khi thực sự cần).
        all_new_records = self.fill_from_backup(
            all_new_records, incomplete_records, existing_records
        )

        # 3. Gộp dữ liệu mới và cũ, loại bỏ trùng lặp và sắp xếp theo thứ tự giảm dần thời gian
        self.save_and_sort_records(all_new_records)

        print("=== Kết thúc tiến trình cào dữ liệu Keno ===")

if __name__ == "__main__":
    scraper = KenoScraper()
    scraper.run()
