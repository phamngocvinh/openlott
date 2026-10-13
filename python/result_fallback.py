#!/usr/bin/env python3
"""
result_fallback.py - Schema + phát hiện field thiếu + ghép field cho 4 loại vé.

Mục tiêu:
    Hỗ trợ cơ chế "dự phòng theo field": khi nguồn chính (minhchinh.com) trả về một
    kỳ bị thiếu field (thiếu bộ số, thiếu hoặc jackpot = 0, thiếu giờ quay...), scraper
    có thể lấy bù CHỈ NHỮNG FIELD CÒN THIẾU từ nguồn dự phòng (ketquadientoan.com).
    Nguồn dự phòng chỉ được gọi khi thực sự có field thiếu (lazy).

Vì sao cần module dùng chung:
    Cả 4 scraper dùng cùng một định dạng file kết quả. Việc phát hiện "field nào thiếu"
    và "ghép field thiếu" là logic giống nhau, chỉ khác cấu hình mỗi game. Tập trung ở
    một nơi giúp tránh trùng lặp và tránh hardcode rải rác (quy tắc No Hardcoded Strings).

Định dạng file kết quả (GIỮ NGUYÊN, không đổi):
    mega645 : DD/MM/YYYY|n1,n2,n3,n4,n5,n6|jackpot
    power655: DD/MM/YYYY|n1,n2,n3,n4,n5,n6,n7|jackpot1|jackpot2
    lotto535: DD/MM/YYYY|HH|n1,n2,n3,n4,n5,n6|jackpot
    keno    : DD/MM/YYYY,HH:MM,n1,n2,...,n20

Quy ước "field thiếu":
    - Bộ số: số lượng số khác số lượng mong đợi của game.
    - Jackpot: rỗng, không phải chữ số, bằng "0", hoặc quá ngắn (< ngưỡng min digits).
      (Người dùng xác nhận "kéo jackpot = 0đ" là trường hợp thiếu dữ liệu.)
    - Giờ quay (lotto): field thời gian rỗng.

Cách dùng (trong scraper):
    from result_fallback import GameId, detect_missing_fields, merge_missing_fields
    from result_fallback import record_key, ParseOutcome

    outcome = ParseOutcome(records=valid_records, incomplete=partial_records)
    missing = detect_missing_fields(GameId.MEGA, rec)
    merged, filled = merge_missing_fields(GameId.MEGA, primary_rec, backup_rec)

Phiên bản lịch sử (Version History):
    v1.0.0: Bản đầu tiên. Định nghĩa schema 4 game, detect_missing_fields,
            merge_missing_fields, record_key và ParseOutcome.
"""

import re
from dataclasses import dataclass, field


class GameId:
    """Định danh game, dùng làm khóa tra cứu schema (tránh hardcode chuỗi rải rác)."""
    MEGA = "mega645"
    POWER = "power655"
    LOTTO = "lotto535"
    KENO = "keno"


# Tên các field logic, dùng thống nhất giữa detect/merge và log.
FIELD_NUMBERS = "numbers"
FIELD_TIME = "time"
FIELD_JACKPOT = "jackpot"


class SchemaConstants:
    """Hằng số dùng chung cho mọi game (tập trung, không hardcode rải rác)."""

    # Ký tự phân tách field trong 1 dòng kết quả
    PIPE_SEPARATOR = "|"
    COMMA_SEPARATOR = ","

    # Giá trị jackpot được coi là THIẾU dữ liệu
    MISSING_JACKPOT_VALUE = "0"

    # Định dạng ngày/giờ để kiểm tra hợp lệ
    DATE_PATTERN = re.compile(r"^\d{2}/\d{2}/\d{4}$")
    TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")
    TIME_CODE_PATTERN = re.compile(r"^\d{2}H$")


@dataclass(frozen=True)
class GameSchema:
    """
    Mô tả cấu trúc 1 dòng kết quả của một game.

    Attributes:
        sep: ký tự phân tách field.
        packed_numbers: True nếu bộ số nằm GỌN trong 1 field (phân tách bằng dấu phẩy),
            False nếu mỗi số là 1 field riêng (keno).
        numbers_index: chỉ số field chứa bộ số (packed) hoặc field đầu tiên của bộ số.
        expected_numbers: số lượng số mong đợi của game.
        jackpot_indices: các chỉ số field jackpot theo đúng thứ tự hiển thị.
        min_jackpot_digits: số chữ số tối thiểu để coi jackpot là hợp lệ.
        time_index: chỉ số field thời gian quay (lotto = "HH"), None nếu không có.
        time_is_code: True nếu field thời gian là mã kỳ dạng "13H"/"21H" (lotto),
            False nếu là "HH:MM" (keno).
        key_indices: các chỉ số field hợp thành khóa định danh kỳ.
    """

    sep: str
    packed_numbers: bool
    numbers_index: int
    expected_numbers: int
    jackpot_indices: tuple
    min_jackpot_digits: int
    time_index: int = None
    time_is_code: bool = False
    key_indices: tuple = (0,)


class FallbackSchema:
    """
    Bảng schema cho 4 game. Mọi giá trị cấu hình tập trung ở đây.
    """

    SCHEMAS = {
        GameId.MEGA: GameSchema(
            sep=SchemaConstants.PIPE_SEPARATOR,
            packed_numbers=True,
            numbers_index=1,
            expected_numbers=6,
            jackpot_indices=(2,),
            min_jackpot_digits=10,
            key_indices=(0,),
        ),
        GameId.POWER: GameSchema(
            sep=SchemaConstants.PIPE_SEPARATOR,
            packed_numbers=True,
            numbers_index=1,
            expected_numbers=7,
            jackpot_indices=(2, 3),
            min_jackpot_digits=10,
            key_indices=(0,),
        ),
        GameId.LOTTO: GameSchema(
            sep=SchemaConstants.PIPE_SEPARATOR,
            packed_numbers=True,
            numbers_index=2,
            expected_numbers=6,
            jackpot_indices=(3,),
            min_jackpot_digits=8,
            time_index=1,
            time_is_code=True,
            key_indices=(0, 1),
        ),
        GameId.KENO: GameSchema(
            sep=SchemaConstants.COMMA_SEPARATOR,
            packed_numbers=False,
            numbers_index=2,
            expected_numbers=20,
            jackpot_indices=(),
            min_jackpot_digits=0,
            time_index=1,
            time_is_code=False,
            key_indices=(0, 1),
        ),
    }

    @classmethod
    def for_game(cls, game: str) -> GameSchema:
        """Lấy schema của game; raise KeyError rõ ràng nếu game không hỗ trợ."""
        schema = cls.SCHEMAS.get(game)
        if schema is None:
            raise KeyError(f"Không có schema cho game: {game}")
        return schema


def _split_fields(game: str, record: str) -> list:
    """Tách 1 dòng kết quả thành danh sách field theo separator của game."""
    schema = FallbackSchema.for_game(game)
    return record.strip().split(schema.sep)


def _jackpot_field_names(schema: GameSchema) -> list:
    """
    Trả về tên field logic cho từng jackpot theo thứ tự.
    1 jackpot -> ["jackpot"]; nhiều jackpot -> ["jackpot1", "jackpot2", ...]
    """
    if len(schema.jackpot_indices) <= 1:
        return [FIELD_JACKPOT for _ in schema.jackpot_indices]
    return [f"{FIELD_JACKPOT}{i + 1}" for i in range(len(schema.jackpot_indices))]


def is_valid_jackpot(value: str, min_digits: int) -> bool:
    """
    Kiểm tra một giá trị jackpot có hợp lệ không.

    Không hợp lệ (coi là thiếu dữ liệu) khi:
        - rỗng,
        - không phải toàn chữ số,
        - bằng "0" (người dùng xác nhận 0đ = thiếu dữ liệu),
        - ngắn hơn ngưỡng min_digits.
    """
    if value is None:
        return False
    stripped = str(value).strip()
    if stripped == "" or not stripped.isdigit():
        return False
    if stripped.lstrip("0") == "":
        # Toàn số 0 (vd "0", "00", "000") -> không hợp lệ
        return False
    return len(stripped) >= min_digits


def _extract_numbers(schema: GameSchema, parts: list) -> list:
    """Trích danh sách số của 1 record theo schema (packed hoặc rời)."""
    if schema.packed_numbers:
        if schema.numbers_index >= len(parts):
            return []
        raw = parts[schema.numbers_index]
        return [n.strip() for n in raw.split(",") if n.strip() != ""]
    # Bộ số rời (keno): lấy từ numbers_index đến hết
    return [n.strip() for n in parts[schema.numbers_index:] if n.strip() != ""]


def record_key(game: str, record: str) -> str:
    """
    Trả về khóa định danh kỳ của record (dùng để ghép giữa 2 nguồn).

    Ví dụ:
        mega/power -> "DD/MM/YYYY"
        lotto      -> "DD/MM/YYYY|HH"
        keno       -> "DD/MM/YYYY,HH:MM"
    """
    schema = FallbackSchema.for_game(game)
    parts = _split_fields(game, record)
    key_parts = [parts[i] if i < len(parts) else "" for i in schema.key_indices]
    return schema.sep.join(key_parts)


def detect_missing_fields(game: str, record: str) -> list:
    """
    Phát hiện các field còn thiếu của một record.

    Args:
        game: mã game (GameId.*).
        record: dòng kết quả theo định dạng file.

    Returns:
        Danh sách tên field thiếu (rỗng = record đầy đủ). Thứ tự chuẩn:
        numbers (nếu thiếu) -> time (nếu có) -> jackpot(s).
    """
    schema = FallbackSchema.for_game(game)
    parts = _split_fields(game, record)
    missing: list = []

    # 1) Bộ số
    numbers = _extract_numbers(schema, parts)
    if len(numbers) != schema.expected_numbers:
        missing.append(FIELD_NUMBERS)
    elif not all(n.isdigit() for n in numbers):
        missing.append(FIELD_NUMBERS)

    # 2) Thời gian quay (chỉ lotto/keno)
    if schema.time_index is not None:
        time_value = parts[schema.time_index].strip() if schema.time_index < len(parts) else ""
        if schema.time_is_code:
            valid_time = bool(SchemaConstants.TIME_CODE_PATTERN.match(time_value))
        else:
            valid_time = bool(SchemaConstants.TIME_PATTERN.match(time_value))
        if not valid_time:
            missing.append(FIELD_TIME)

    # 3) Jackpot
    names = _jackpot_field_names(schema)
    for name, idx in zip(names, schema.jackpot_indices):
        value = parts[idx].strip() if idx < len(parts) else ""
        if not is_valid_jackpot(value, schema.min_jackpot_digits):
            missing.append(name)

    return missing


def is_complete(game: str, record: str) -> bool:
    """True nếu record không thiếu field nào."""
    return not detect_missing_fields(game, record)


def _field_to_index(schema: GameSchema, field_name: str) -> int:
    """Ánh xạ tên field logic sang chỉ số field trong record."""
    if field_name == FIELD_NUMBERS:
        return schema.numbers_index
    if field_name == FIELD_TIME:
        return schema.time_index
    names = _jackpot_field_names(schema)
    if field_name in names:
        return schema.jackpot_indices[names.index(field_name)]
    return -1


def merge_missing_fields(game: str, primary: str, backup: str) -> tuple:
    """
    Ghép các field còn thiếu của `primary` từ `backup`, GIỮ NGUYÊN định dạng dòng.

    Chỉ field nào thiếu ở primary VÀ hợp lệ ở backup mới được thay. Các field đã có
    giá trị ở primary (kể cả jackpot lớn) tuyệt đối không bị ghi đè.

    Args:
        game: mã game.
        primary: record nguồn chính (có thể thiếu field).
        backup: record nguồn dự phòng cùng kỳ (có thể None).

    Returns:
        (merged_record, filled_fields):
            merged_record: dòng sau khi ghép (bằng primary nếu không ghép được gì).
            filled_fields: danh sách tên field đã được bù từ backup.
    """
    if backup is None:
        return primary, []

    missing = detect_missing_fields(game, primary)
    if not missing:
        return primary, []

    schema = FallbackSchema.for_game(game)
    parts = _split_fields(game, primary)
    backup_parts = _split_fields(game, backup)
    filled: list = []

    for field_name in missing:
        idx = _field_to_index(schema, field_name)
        if idx < 0 or idx >= len(backup_parts):
            continue

        if field_name == FIELD_NUMBERS:
            backup_numbers = _extract_numbers(schema, backup_parts)
            if len(backup_numbers) != schema.expected_numbers:
                continue
            if not all(n.isdigit() for n in backup_numbers):
                continue
            if schema.packed_numbers:
                _ensure_len(parts, idx + 1)
                parts[idx] = ",".join(backup_numbers)
            else:
                _ensure_len(parts, schema.numbers_index)
                parts[schema.numbers_index:] = list(backup_parts[schema.numbers_index:])
            filled.append(field_name)
            continue

        if field_name == FIELD_TIME:
            backup_value = backup_parts[idx].strip()
            if schema.time_is_code:
                valid = bool(SchemaConstants.TIME_CODE_PATTERN.match(backup_value))
            else:
                valid = bool(SchemaConstants.TIME_PATTERN.match(backup_value))
            if valid:
                _ensure_len(parts, idx + 1)
                parts[idx] = backup_value
                filled.append(field_name)
            continue

        # Jackpot
        backup_value = backup_parts[idx].strip()
        if is_valid_jackpot(backup_value, schema.min_jackpot_digits):
            _ensure_len(parts, idx + 1)
            parts[idx] = backup_value
            filled.append(field_name)

    if not filled:
        return primary, []

    return schema.sep.join(parts), filled


def _ensure_len(parts: list, needed: int) -> None:
    """Bù các field rỗng nếu record ngắn hơn vị trí cần ghi."""
    while len(parts) < needed:
        parts.append("")


@dataclass
class ParseOutcome:
    """
    Kết quả parse 1 nguồn cho 1 game.

    Attributes:
        records: danh sách record đầy đủ (đúng định dạng file).
        incomplete: danh sách record CHƯA đầy đủ (thiếu field), giữ nguyên định dạng
            để scraper thử vá từ nguồn dự phòng; nếu vá không xong sẽ bị loại bỏ.
        order: TẤT CẢ record (đầy đủ + thiếu) theo ĐÚNG thứ tự xuất hiện trên trang.
            Dùng để giữ nguyên thứ tự thời gian (mới→cũ) khi ghép dự phòng và khi
            prepend vào file kết quả (tránh kỳ cũ chen lên trên kỳ mới).
    """

    records: list = field(default_factory=list)
    incomplete: list = field(default_factory=list)
    order: list = field(default_factory=list)

    def add(self, game: str, record: str) -> None:
        """
        Thêm 1 record theo thứ tự đọc được: tự phân loại đầy đủ/thiếu và luôn
        lưu vào `order` để bảo toàn thứ tự gốc của nguồn.
        """
        self.order.append(record)
        if detect_missing_fields(game, record):
            self.incomplete.append(record)
        else:
            self.records.append(record)
