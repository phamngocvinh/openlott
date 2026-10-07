# Deeplott Scraper → Firestore

Repo public chỉ chứa phần **cào kết quả xổ số** (Keno, Lotto 5/35, Mega 6/45, Power 6/55)
và **đồng bộ lên Cloud Firestore** bằng Firebase Admin SDK.

## Thành phần
- `python/*_scraper.py` — 4 scraper (nguồn chính: minhchinh.com, dự phòng: ketquadientoan.com).
- `python/firestore_helper.py` — khởi tạo Firestore client (đọc credentials từ biến môi trường).
- `python/firestore_push.py` — đẩy file kết quả lên Firestore (có guard chống mất dữ liệu).
- `python/firestore_sync_scraper.py` — luồng chính: chạy 4 scraper → push lên Firestore.
- `.github/workflows/firestore_scraper_workflow.yml` — chạy tự động trên GitHub Actions.

## Lịch chạy
- Mỗi 10 phút, chỉ trong khung giờ quay (giờ VN 06:00–22:30).
- Có thể chạy thủ công qua `workflow_dispatch` (tùy chọn `dry_run`).
- **Keep-alive:** vì repo không commit kết quả, workflow tự commit file `.keepalive`
  1 lần/ngày để tránh GitHub tự tắt schedule sau 60 ngày không hoạt động.

## Không lưu dữ liệu trong repo
Repo **không chứa file `.txt` kết quả**. Mỗi lần chạy sẽ tự lấy dữ liệu hiện có trên
Firestore làm base (bootstrap), gộp với dữ liệu mới cào từ web, rồi push trở lại.

## Cấu hình (GitHub Secrets)
Thêm secret sau ở **Settings → Secrets and variables → Actions**:
- `FIREBASE_SERVICE_ACCOUNT` — nội dung JSON của service account key (Firebase Admin SDK).

Workflow sẽ ghi secret này ra file tạm và set `FIREBASE_CREDENTIALS_PATH` cho script đọc.
Không có secret nào khác được nhúng trong mã nguồn.

## Yêu cầu quyền
- Service account cần quyền ghi/đọc **Cloud Firestore** (vd: role `Cloud Datastore User`
  hoặc `Firebase Admin`).
- Collection `results/{mega645,power655,lotto535,keno}` sẽ được tạo tự động khi ghi lần đầu.

## Bảo mật
- Mọi thông tin nhạy cảm (token, key) **chỉ** nằm trong GitHub Secrets, không nằm trong repo.
- Khuyến nghị bật **secret scanning + push protection** cho repo.
