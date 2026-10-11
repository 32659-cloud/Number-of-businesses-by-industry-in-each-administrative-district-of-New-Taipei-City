"""ETL: 新北市商業各行政區行業別家數

讀取 data/raw/ 的原始 CSV，清理、驗證後輸出到 data/processed/ 與 docs/data.js。
只用 Python 標準函式庫，執行方式：  python etl/etl.py
驗證失敗會直接丟出 AssertionError，不會產出檔案。
"""
import csv
import sys
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "new_taipei_business_count_by_district_industry.csv"
OUT = ROOT / "data" / "processed"
DOCS = ROOT / "docs"

# 新北市 29 個行政區（官方順序）。用來檢查原始資料有沒有少區、多區、錯字。
DISTRICTS = [
    "板橋區", "三重區", "中和區", "永和區", "新莊區", "新店區", "樹林區", "鶯歌區",
    "三峽區", "淡水區", "汐止區", "瑞芳區", "土城區", "蘆洲區", "五股區", "泰山區",
    "林口區", "深坑區", "石碇區", "坪林區", "三芝區", "石門區", "八里區", "平溪區",
    "雙溪區", "貢寮區", "金山區", "萬里區", "烏來區",
]

# 名稱更正：原始資料的行業名稱與「行政院主計總處行業統計分類」不一致者。
# 保留原始名稱在 industry_raw 欄，方便對照。
INDUSTRY_RENAME = {
    "電子及燃氣供應業": "電力及燃氣供應業",  # 原始資料的「電子」為筆誤，官方分類為「電力」
    "公共行政及國防;強制性社會安全": "公共行政及國防；強制性社會安全",  # 半形分號改全形
}


def roc_to_ad(roc_ym: str) -> str:
    """民國年月 '11506' -> '2026-06'。民國年 + 1911 = 西元年。"""
    assert roc_ym.isdigit() and len(roc_ym) in (5, 6), f"日期格式異常: {roc_ym!r}"
    year, month = int(roc_ym[:-2]) + 1911, int(roc_ym[-2:])
    assert 1 <= month <= 12, f"月份異常: {roc_ym!r}"
    return f"{year}-{month:02d}"


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 主控台預設 cp950，印不出部分符號
    report = []
    log = lambda s="": (report.append(s), print(s))

    # ---------- 1. 讀取（utf-8-sig 會自動去掉檔頭 BOM） ----------
    with open(RAW, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == ["section", "industry", "quantity", "date"], reader.fieldnames
        raw_rows = list(reader)
    log(f"[讀取] {RAW.name}：{len(raw_rows)} 筆")

    # ---------- 2. 清理 ----------
    rows, renamed = [], defaultdict(int)
    for r in raw_rows:
        section = r["section"].strip()
        industry_raw = r["industry"].strip()
        industry = INDUSTRY_RENAME.get(industry_raw, industry_raw)
        if industry != industry_raw:
            renamed[(industry_raw, industry)] += 1
        qty_text = r["quantity"].strip()
        assert qty_text.isdigit(), f"家數不是非負整數: {r}"
        rows.append({
            "section": section,
            "industry": industry,
            "industry_raw": industry_raw,
            "quantity": int(qty_text),
            "roc_ym": r["date"].strip(),
            "month": roc_to_ad(r["date"].strip()),
        })
    for (old, new), n in renamed.items():
        log(f"[名稱更正] {old} -> {new}（{n} 筆）")

    # ---------- 3. 驗證 ----------
    months = sorted({r["month"] for r in rows})
    industries = list(dict.fromkeys(r["industry"] for r in rows))  # 保留原始出現順序
    log(f"[期間] {months}")

    # 3a 行政區：剛好 29 區、名稱與官方一致
    assert set(r["section"] for r in rows) == set(DISTRICTS), "行政區名單與官方 29 區不一致"
    # 3b 不重複：每個 (區, 行業, 月) 只能出現一次
    keys = [(r["section"], r["industry"], r["month"]) for r in rows]
    assert len(keys) == len(set(keys)), "有重複的 (區, 行業, 月)"
    # 3c 完整：29 區 × 行業數 × 月數 = 筆數，沒有缺格
    expected = len(DISTRICTS) * len(industries) * len(months)
    assert len(rows) == expected, f"筆數 {len(rows)} != 29 × {len(industries)} × {len(months)} = {expected}"
    log(f"[完整性] 29 區 × {len(industries)} 行業 × {len(months)} 月 = {expected} 筆，無缺漏、無重複 ✔")

    # 3d 加總核對：各區加總、各行業加總、總計，三種算法必須一致
    by_district, by_industry = defaultdict(int), defaultdict(int)
    for r in rows:
        by_district[r["section"]] += r["quantity"]
        by_industry[r["industry"]] += r["quantity"]
    total = sum(r["quantity"] for r in rows)
    assert sum(by_district.values()) == total == sum(by_industry.values())
    # 另以「區 × 行業」矩陣獨立加一次（逐列、逐欄再加總）
    matrix = {(r["section"], r["industry"]): r["quantity"] for r in rows}
    row_sums = sum(sum(matrix[(d, i)] for i in industries) for d in DISTRICTS)
    col_sums = sum(sum(matrix[(d, i)] for d in DISTRICTS) for i in industries)
    assert row_sums == col_sums == total
    log(f"[加總核對] 各區加總 = 各行業加總 = 逐列逐欄加總 = 全市 {total:,} 家 ✔")

    # 3e 可疑資料：整個行業全市為 0，標示但不刪除
    all_zero = [i for i in industries if by_industry[i] == 0]
    for i in all_zero:
        log(f"[警告] 「{i}」29 區全部為 0，疑為原始資料缺漏或該類不在統計範圍，圖表中標註說明")

    # ---------- 4. 衍生指標 ----------
    # 占比 = 該區該行業 / 該區總計；全市占比 = 該行業全市 / 全市總計
    # 集中度（區位商數 LQ）= 區內占比 / 全市占比，>1 表示該區此行業比全市平均更集中
    for r in rows:
        d_total, i_total = by_district[r["section"]], by_industry[r["industry"]]
        r["share_in_district_pct"] = round(r["quantity"] / d_total * 100, 2) if d_total else 0
        r["share_in_city_pct"] = round(i_total / total * 100, 2) if total else 0
        r["lq"] = round((r["quantity"] / d_total) / (i_total / total), 2) if d_total and i_total else None

    # ---------- 5. 輸出 ----------
    OUT.mkdir(parents=True, exist_ok=True)
    fields = ["section", "industry", "industry_raw", "quantity", "roc_ym", "month",
              "share_in_district_pct", "share_in_city_pct", "lq"]
    with open(OUT / "district_industry.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    with open(OUT / "district_totals.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["section", "total_businesses", "share_of_city_pct"])
        for d in DISTRICTS:
            w.writerow([d, by_district[d], round(by_district[d] / total * 100, 2)])
    with open(OUT / "industry_totals.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["industry", "total_businesses", "share_of_city_pct"])
        for i in industries:
            w.writerow([i, by_industry[i], round(by_industry[i] / total * 100, 2)])
    log(f"[輸出] data/processed/ 三個 CSV 已寫入")

    # 網頁用資料：直接內嵌成 data.js，不需要 fetch，本機開檔與 GitHub Pages 都能用
    payload = {
        "month": months[0], "roc_ym": rows[0]["roc_ym"], "total": total,
        "districts": DISTRICTS, "industries": industries, "zero_industries": all_zero,
        "validation": list(report),
        "rows": [[DISTRICTS.index(r["section"]), industries.index(r["industry"]), r["quantity"]] for r in rows],
    }
    DOCS.mkdir(exist_ok=True)
    (DOCS / "data.js").write_text(
        "// 由 etl/etl.py 自動產生，請勿手動修改\nwindow.DATA = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8")
    log("[輸出] docs/data.js 已寫入")
    (OUT / "validation_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
