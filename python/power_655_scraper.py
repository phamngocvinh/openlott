"""
Version History:
v1.0.0: Initial version. Scraping the latest 100 draw results of Power 6/55
        from ketquadientoan.com and saving to TXT.
        Note: Excludes the wildcard number (last number) as requested.
v1.0.1: Fixed jackpot extraction - expanded column search range and improved parsing logic
v1.1.0: Added wildcard number (7th number) for Jackpot 2. Now includes all 7 numbers
        in the output format: date|n1,n2,n3,n4,n5,n6,n7|j1|j2
v1.1.1: Switched primary source to minhchinh.com (new logic: fetch_page_content_minhchinh
        + parse_results_minhchinh). ketquadientoan.com kept as backup source (old functions
        preserved). Added --source CLI flag: '--source backup' forces ketquadientoan,
        '--source minhchinh' forces minhchinh; default tries minhchinh then falls back.
"""

import requests
from bs4 import BeautifulSoup
import os
import re

class Power655Scraper:
    """
    A professional scraper to collect lottery results for Power 6/55.
    Designed for reliability and easy future expansion to other lottery types.
    Primary source: minhchinh.com. Backup source: ketquadientoan.com.
    """

    def __init__(self):
        # Primary source URL for Power 6/55
        self.minhchinh_url = "https://www.minhchinh.com/truc-tiep-xo-so-tu-chon-power-655.html"
        # Backup source URL for Power 6/55 (ketquadientoan)
        self.base_url = "https://www.ketquadientoan.com/tat-ca-ky-xo-so-power-655.html"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        self.target_count = 100
        self.output_file = "power_655_results.txt"

    def fetch_page_content(self):
        """
        Fetches the HTML content from the website (ketquadientoan.com - backup source).
        """
        try:
            response = requests.get(self.base_url, headers=self.headers, timeout=20)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            print(f"Error fetching data: {e}")
            return None

    def fetch_page_content_minhchinh(self):
        """
        Fetches the HTML content from minhchinh.com (primary source).
        """
        try:
            response = requests.get(self.minhchinh_url, headers=self.headers, timeout=20)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            print(f"Error fetching data from minhchinh.com: {e}")
            return None

    def parse_results(self, html_content):
        """
        Parses HTML to extract winning numbers and jackpot values.
        Now includes the wildcard number (7th number) for Jackpot 2.
        Output format: date|n1,n2,n3,n4,n5,n6,n7|j1|j2
        (Logic cũ dành cho ketquadientoan.com - backup source, được giữ nguyên.)
        """
        if not html_content:
            return []

        soup = BeautifulSoup(html_content, 'html.parser')
        extracted_data = []

        # Robust Table Detection
        table = None
        all_tables = soup.find_all('table')

        for t in all_tables:
            header_text = t.get_text().lower()
            if 'kỳ' in header_text or 'ngày quay' in header_text:
                table = t
                break

        if not table:
            table = soup.find('table')

        if not table:
            print("Critical Error: Could not find any table on the page.")
            return []

        rows = table.find_all('tr')
        start_index = 1 if rows[0].find('th') or 'kỳ' in rows[0].get_text().lower() else 0

        for row in rows[start_index:]:
            if len(extracted_data) >= self.target_count:
                break

            cols = row.find_all('td')
            if len(cols) >= 2:
                # Extracting winning numbers from the second column
                numbers_container = cols[1].find_all(['span', 'div'], class_=['ball', 'result-number'])

                if not numbers_container:
                    # Fallback to direct text parsing if no specific classes found
                    numbers = [n.strip() for n in cols[1].get_text(" ", strip=True).split() if n.strip().isdigit()]
                else:
                    numbers = [n.get_text(strip=True) for n in numbers_container if n.get_text(strip=True).isdigit()]

                if numbers:
                    # Tạo chuỗi nối các số kết quả để phát hiện lỗi parse prize
                    # (khi prize bị ghi nhầm thành chuỗi nối các số kết quả do cấu trúc bảng thay đổi)
                    # Kiểm tra cả 2 trường hợp: 6 số chính và tất cả số (kể cả wildcard)
                    numbers_concat_6 = ''.join(numbers[:6]) if len(numbers) >= 6 else ''.join(numbers)
                    numbers_concat_all = ''.join(numbers)  # Tất cả số (kể cả wildcard nếu có)

                    # Extract date from the first column
                    date_text = cols[0].get_text(strip=True)
                    date_match = re.search(r'\d{2}/\d{2}/\d{4}', date_text)
                    draw_date = date_match.group(0) if date_match else "??/??/????"

                    # Lấy giá trị Jackpot 1 và Jackpot 2 - mở rộng phạm vi tìm kiếm
                    jackpots = []

                    # Mở rộng phạm vi tìm kiếm jackpot (từ cột 2 đến cột 6)
                    # Xử lý trường hợp cột bị lệch hoặc cấu trúc bảng thay đổi
                    search_cols = cols[2:7] if len(cols) > 6 else cols[2:]

                    for col in search_cols:
                        span = col.find('span', class_='hidden-xs')
                        if span:
                            text = span.get_text(strip=True)
                        else:
                            text = col.get_text(strip=True)
                            # Loại bỏ các ký tự phân cách như ≈, ~, VND, đ
                            for sep in ['≈', '~', 'VND', 'đ']:
                                if sep in text:
                                    text = text.split(sep)[0]

                        # Lọc và chỉ giữ lại các chữ số của giải thưởng
                        jackpot_val = ''.join(filter(str.isdigit, text))

                        # Kiểm tra giá trị hợp lệ (jackpot Power 6/55 thường có ít nhất 10 chữ số)
                        # Đồng thời loại bỏ trường hợp prize bị parse nhầm thành chuỗi nối các số kết quả
                        if jackpot_val and len(jackpot_val) >= 10 and jackpot_val != numbers_concat_6 and jackpot_val != numbers_concat_all:
                            jackpots.append(jackpot_val)
                        elif jackpot_val == "0":
                            # Giữ đúng thứ tự cột: ghi nhận jackpot = 0 (không có người trúng)
                            jackpots.append("0")

                        # Power 6/55 có 2 jackpot, dừng khi đã tìm đủ
                        if len(jackpots) >= 2:
                            break

                    # Nếu không tìm thấy đủ jackpot, thử tìm trong tất cả các cột
                    if len(jackpots) < 2:
                        for col in cols:
                            text = col.get_text(strip=True)
                            # Tìm các chuỗi số dài (có thể là jackpot)
                            numbers_found = re.findall(r'\d{10,}', text)
                            for num in numbers_found:
                                # Bỏ qua nếu số tìm được trùng với chuỗi nối các số kết quả (lỗi parse)
                                if num == numbers_concat_6 or num == numbers_concat_all:
                                    continue
                                if num not in jackpots:
                                    jackpots.append(num)
                                    if len(jackpots) >= 2:
                                        break
                            if len(jackpots) >= 2:
                                break

                    # Đảm bảo luôn có đủ 2 jackpot (điền 0 nếu thiếu) để giữ format file
                    while len(jackpots) < 2:
                        jackpots.append("0")

                    # Nối date, numbers và 2 jackpot bằng dấu |
                    jackpot_str = "|".join(jackpots)
                    extracted_data.append(f"{draw_date}|{','.join(numbers)}|{jackpot_str}")

        return extracted_data

    def parse_results_minhchinh(self, html_content):
        """
        Parses minhchinh.com HTML to extract Date, Winning Numbers and 2 Jackpots.
        Cấu trúc bảng (bảng kết quả): table.table-mini-result nằm trong #bangthongkexoso.
        - Cột 0: Ngày mở thưởng dạng "dd/mm/yyyy"
        - Cột 1: div.balls chứa 6 span.mini-ball + 1 span.mini-ball.pw (wildcard)
        - Cột 2: Jackpot 1, có dấu phẩy phân tách hàng nghìn -> bỏ dấu phẩy
        - Cột 3: Jackpot 2
        """
        if not html_content:
            return []

        soup = BeautifulSoup(html_content, 'html.parser')
        extracted_data = []

        # Định vị bảng kết quả trong #bangthongkexoso (bảng đầu tiên có class table-mini-result)
        table = None
        bangtk = soup.find(id='bangthongkexoso')
        if bangtk:
            table = bangtk.find('table', class_='table-mini-result')
        if not table:
            for t in soup.find_all('table', class_='table-mini-result'):
                header_text = t.get_text().lower()
                if 'kết quả' in header_text or 'ngày' in header_text:
                    table = t
                    break
        if not table:
            print("Critical Error: Could not find minhchinh results table (table-mini-result).")
            return []

        rows = table.find_all('tr')
        if not rows:
            return []
        # Hàng đầu tiên là header (<th>) -> bỏ qua
        start_index = 1 if rows[0].find('th') or 'ngày' in rows[0].get_text().lower() else 0

        for row in rows[start_index:]:
            if len(extracted_data) >= self.target_count:
                break

            cols = row.find_all('td')
            if len(cols) < 4:
                continue

            # Cột 0: Ngày mở thưởng, ví dụ "27/08/2026"
            date_text = cols[0].get_text(strip=True)
            date_match = re.search(r'\d{2}/\d{2}/\d{4}', date_text)
            draw_date = date_match.group(0) if date_match else "??/??/????"

            # Cột 1: Kết quả - div.balls chứa 6 span.mini-ball + 1 span.mini-ball.pw (wildcard)
            numbers = []
            balls_div = cols[1].find('div', class_='balls')
            if balls_div:
                for span in balls_div.find_all('span', class_='mini-ball'):
                    txt = span.get_text(strip=True)
                    if txt.isdigit():
                        numbers.append(txt)
            if not numbers:
                # Fallback: phân tích trực tiếp text trong cột
                numbers = [n.strip() for n in cols[1].get_text(" ", strip=True).split() if n.strip().isdigit()]

            # Power 6/55 có 7 số (6 số chính + wildcard); bỏ qua dòng không đủ
            if len(numbers) != 7:
                continue

            # Cột 2, 3: Jackpot 1 & Jackpot 2
            jackpots = []
            numbers_concat = ''.join(numbers)
            for col in cols[2:]:
                if len(jackpots) >= 2:
                    break
                text = col.get_text(strip=True)
                jackpot_val = ''.join(filter(str.isdigit, text))
                if jackpot_val and jackpot_val != numbers_concat:
                    jackpots.append(jackpot_val)
            # Đảm bảo luôn có đủ 2 jackpot (điền 0 nếu thiếu) để giữ format file
            while len(jackpots) < 2:
                jackpots.append("0")

            # Format: Date|Numbers|Jackpot1|Jackpot2
            jackpot_str = "|".join(jackpots)
            extracted_data.append(f"{draw_date}|{','.join(numbers)}|{jackpot_str}")

        return extracted_data

    def read_all_lines(self):
        """
        Đọc tất cả các dòng từ file.
        Trả về list các dòng đã strip, không bao gồm dòng trống.
        Trả về list rỗng nếu file không tồn tại.
        """
        if not os.path.exists(self.output_file):
            return []

        try:
            with open(self.output_file, 'r', encoding='utf-8') as f:
                return [line.strip() for line in f if line.strip()]
        except Exception as e:
            print(f"Error reading file: {e}")
            return []

    def extract_key(self, record):
        """
        Trích xuất key duy nhất từ một record.
        Power 6/55 có 1 kỳ/ngày → key = date (cột đầu tiên).
        """
        return record.split('|')[0]

    def run(self, source=None):
        """
        Main execution flow sử dụng Date-keyed Reconciliation:
        - Đọc tất cả dòng cũ từ file, build dict theo key (date)
        - Cào dữ liệu mới từ web, build dict theo key (date)
        - Merge: key trùng → cập nhật (self-correct số/jackpot)
        - Key mới → thêm vào đầu file
        - Key chỉ có trong file cũ → giữ nguyên
        - Ghi lại toàn bộ file

        source:
            None (mặc định) → thử minhchinh (primary) trước; nếu thất bại/trống thì
                               tự fallback sang ketquadientoan (backup).
            "minhchinh"      → chỉ dùng minhchinh.
            "backup"         → chỉ dùng ketquadientoan (để test/debug).
        """
        print("Starting Date-keyed Reconciliation scraper for Power 6/55...")

        # Bước 1: Đọc dữ liệu cũ
        existing_lines = self.read_all_lines()
        print(f"Existing records in file: {len(existing_lines)}")

        # Bước 2: Cào dữ liệu mới từ web (chọn nguồn theo source)
        all_fetched = []
        source_used = None

        if source == "backup":
            html = self.fetch_page_content()
            if html:
                all_fetched = self.parse_results(html)
            source_used = "ketquadientoan"
        elif source == "minhchinh":
            html = self.fetch_page_content_minhchinh()
            if html:
                all_fetched = self.parse_results_minhchinh(html)
            source_used = "minhchinh"
        else:
            # Mặc định: minhchinh primary, ketquadientoan backup
            html = self.fetch_page_content_minhchinh()
            if html:
                all_fetched = self.parse_results_minhchinh(html)
            if all_fetched:
                source_used = "minhchinh"
            else:
                print("minhchinh không trả về dữ liệu, fallback sang ketquadientoan (backup)...")
                html = self.fetch_page_content()
                if html:
                    all_fetched = self.parse_results(html)
                source_used = "ketquadientoan"

        if not all_fetched:
            print("No data fetched from any source.")
            return
        print(f"Fetched {len(all_fetched)} records from {source_used}.")

        # Bước 3: Build dict từ dữ liệu mới (key = date)
        fetched_dict = {}
        for rec in all_fetched:
            key = self.extract_key(rec)
            fetched_dict[key] = rec

        # Bước 4: Reconcile - merge old + new
        updated_count = 0
        added_count = 0
        unchanged_count = 0
        seen_keys = set()
        merged = []

        for old_line in existing_lines:
            key = self.extract_key(old_line)
            seen_keys.add(key)
            if key in fetched_dict:
                new_line = fetched_dict[key]
                if new_line != old_line:
                    merged.append(new_line)
                    updated_count += 1
                else:
                    merged.append(old_line)
                    unchanged_count += 1
            else:
                # Key chỉ tồn tại trong file cũ, không còn trên web → giữ nguyên
                merged.append(old_line)

        # Bước 5: Thêm record mới (key chưa từng xuất hiện trong file cũ)
        new_records = []
        for rec in all_fetched:
            key = self.extract_key(rec)
            if key not in seen_keys:
                new_records.append(rec)
                added_count += 1
                seen_keys.add(key)

        # Prepend: record mới nhất ở đầu file
        if new_records:
            merged = new_records + merged

        # Bước 6: Ghi file
        with open(self.output_file, 'w', encoding='utf-8') as f:
            for record in merged:
                f.write(record + '\n')

        # Bước 7: In thống kê
        print(f"Reconciliation complete.")
        print(f"  - Updated (self-corrected): {updated_count}")
        print(f"  - Added (new draws):       {added_count}")
        print(f"  - Unchanged:               {unchanged_count}")
        print(f"  - Total records in file:   {len(merged)}")
        print("Process completed.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Power 6/55 scraper (minhchinh.com primary, ketquadientoan backup).")
    parser.add_argument("--source", choices=["minhchinh", "backup"], default=None,
                        help="Chọn nguồn cào: 'minhchinh' (chỉ dùng minhchinh), "
                             "'backup' (chỉ dùng ketquadientoan). Mặc định: minhchinh "
                             "trước, tự fallback sang ketquadientoan nếu thất bại.")
    args = parser.parse_args()
    scraper = Power655Scraper()
    scraper.run(source=args.source)
