"""匯出 CSV 共用：Excel 可直接開（UTF-8 BOM），而且擋掉公式注入。

姓名、地址、紀事都是使用者自己打的；儲存格開頭是 = + - @ 時 Excel 會當成公式執行
（OWASP「CSV Injection」）。這類儲存格前面加一個單引號，Excel 會當成文字顯示。
"""
import csv
import io

from fastapi.responses import Response

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def csv_response(header: list[str], rows, filename: str) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(v) for v in row])
    return Response("\ufeff" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})
