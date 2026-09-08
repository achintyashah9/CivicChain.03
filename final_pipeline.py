"""
final_budget_pipeline.py
===========================
ONE pipeline for Indian municipal budget PDFs, any regional language.

    python3 final_budget_pipeline.py <pdf> <out_csv> [--lang kan+eng]
"""

import re
import csv
import sys
import subprocess
import tempfile
import argparse
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass

NUM_RE = re.compile(r'^-?[\d,]+\.\d{1,2}$')       # currency amounts
GENERIC_NUM_RE = re.compile(r'^-?[\d,]+(\.\d+)?$')  # any number (used post-discovery)
CODE_RE = re.compile(
    r'^[A-Za-z]{0,2}\d{1,3}-[\d-]{2,}$'       # BBMP/Chennai style: "01-130702", "120-10-01-00"
    r'|^[A-Z]{1,3}\d{1,3}[A-Z]{0,3}\d{2,4}[A-Z]{0,2}$'  # Pune/PMC style: "RI11A101", "RE11A101A"
)


def is_ascii(t):
    return bool(re.match(r'^[A-Za-z0-9.\-&/,()]+$', t))


# ---------------------------------------------------------------------------
# STAGE 1
# ---------------------------------------------------------------------------

def classify_document(pdf_path: str) -> str:
    try:
        out = subprocess.run(["pdffonts", pdf_path], capture_output=True, text=True).stdout
    except FileNotFoundError:
        raise RuntimeError("Poppler not found on PATH ('pdffonts' missing).")
    font_lines = [l for l in out.splitlines()[2:] if l.strip()]
    return "digital" if font_lines else "scanned"


# ---------------------------------------------------------------------------
# STAGE 2a -- digital: bbox extraction + auto column discovery
# ---------------------------------------------------------------------------

def extract_bbox(pdf_path: str):
    xml_path = tempfile.NamedTemporaryFile(suffix=".xml", delete=False).name
    subprocess.run(["pdftotext", "-bbox", pdf_path, xml_path], check=True)
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for el in root.iter():
        if '}' in el.tag:
            el.tag = el.tag.split('}', 1)[1]
    pages = []
    for page in root.findall('.//page'):
        words = [(float(w.get('yMin')), float(w.get('xMin')),
                  float(w.get('xMax')), w.text or '')
                 for w in page.findall('word')]
        pages.append(words)
    return pages


def cluster_1d(xs, gap=10):
    if not xs:
        return []
    xs = sorted(xs)
    clusters, cur = [], [xs[0]]
    for x in xs[1:]:
        if x - cur[-1] <= gap:
            cur.append(x)
        else:
            clusters.append(cur)
            cur = [x]
    clusters.append(cur)
    return clusters


def page_fingerprint(words) -> int:
    xs = [x1 for y, x0, x1, t in words if NUM_RE.match(t)]
    if len(xs) < 5:
        return 0
    return len([c for c in cluster_1d(xs, gap=10) if len(c) >= 2])


def group_pages_by_layout(pages):
    fps = [page_fingerprint(words) for words in pages]
    counts = Counter(fps)
    real_fps = {fp for fp, n in counts.items() if n >= 3 and fp > 0}
    groups = {fp: [] for fp in real_fps}
    groups.setdefault("other", [])
    for i, fp in enumerate(fps):
        groups[fp if fp in real_fps else "other"].append(i)
    return {k: v for k, v in groups.items() if v}


def discover_columns(pages, page_indices):
    all_words = [w for i in page_indices for w in pages[i]]
    num_x1 = [x1 for y, x0, x1, t in all_words if NUM_RE.match(t)]
    code_x0 = [x0 for y, x0, x1, t in all_words if CODE_RE.match(t)]

    value_clusters = [c for c in cluster_1d(num_x1, gap=8)
                       if len(c) >= max(5, len(page_indices) // 3)]
    value_clusters.sort(key=lambda c: sum(c) / len(c))
    value_cols = []
    for idx, c in enumerate(value_clusters, start=1):
        right = max(c) + 3
        left = min(c) - 40
        value_cols.append((f"value_{idx}", (left, right)))

    if code_x0:
        code_clusters = sorted(cluster_1d(code_x0, gap=15), key=len, reverse=True)
        best = code_clusters[0]
        code_col = (min(best) - 5, max(best) + 40)
    else:
        code_col = (0, 80)

    particulars_col = (code_col[1], value_cols[0][1][0] if value_cols else 300)
    return {"code_col": code_col, "particulars_col": particulars_col, "value_cols": value_cols}


def in_bin(x, b):
    return b[0] <= x < b[1]


def parse_digital_page(words, cols, dept_name, pno, layout_name):
    ascii_words = [(y, x0, x1, t) for y, x0, x1, t in words if is_ascii(t)]
    code_col, particulars_col, value_cols = cols["code_col"], cols["particulars_col"], cols["value_cols"]

    dept_lines = {}
    for y, x0, x1, t in ascii_words:
        if in_bin(x0, code_col) and re.match(r'^\d{1,3}-[A-Za-z]', t) and not CODE_RE.match(t):
            dept_lines.setdefault(round(y), []).append((x0, t))
    if dept_lines:
        first_y = sorted(dept_lines.keys())[0]
        toks = sorted(dept_lines[first_y])
        full = " ".join(tok for _, tok in toks)
        _, _, name = full.partition('-')
        dept_name = name.strip() or dept_name

    anchors = sorted({(y, t) for y, x0, x1, t in ascii_words
                       if in_bin(x0, code_col) and CODE_RE.match(t)})
    anchor_ys = [y for y, t in anchors]

    rows = []
    for idx, (ay, code) in enumerate(anchors):
        band_top = ay - 12
        band_bot = (anchor_ys[idx + 1] - 12) if idx + 1 < len(anchor_ys) else 1e9

        all_part = sorted((y, x0, t) for y, x0, x1, t in words
                           if in_bin(x0, particulars_col) and band_top <= y < band_bot)
        lines, cur_y, cur = [], None, []
        for y, x0, t in all_part:
            if cur_y is None or abs(y - cur_y) <= 2.5:
                cur.append((x0, t)); cur_y = cur_y if cur_y is not None else y
            else:
                lines.append(cur); cur, cur_y = [(x0, t)], y
        if cur:
            lines.append(cur)

        local_kept, english_kept = [], []
        for toks in lines:
            toks.sort()
            ws = [t for _, t in toks]
            if not ws:
                continue
            if ws[0].lower() == "total":
                break
            if any(not is_ascii(t) for t in ws):
                continue
            wordy = sum(1 for t in ws if re.search(r'[A-Za-z]{2,}', t))
            if wordy / len(ws) >= 0.5:
                english_kept.append(" ".join(ws))
        particulars_english = " ".join(english_kept)

        values = {}
        for label, bin_ in value_cols:
            cand = sorted((y, t) for y, x0, x1, t in words
                           if in_bin(x0, bin_) and band_top <= y < band_bot and GENERIC_NUM_RE.match(t))
            values[label] = cand[0][1] if cand else ""

        if not any(v for v in values.values()):
            continue

        rows.append({
            "layout": layout_name, "page": pno, "dept_name": dept_name, "item_code": code,
            "particulars_english": particulars_english, **values
        })
    return rows, dept_name


def run_digital(pdf_path: str):
    pages = extract_bbox(pdf_path)
    groups = group_pages_by_layout(pages)
    all_rows = []
    for fp, page_indices in groups.items():
        if fp == "other":
            continue
        cols = discover_columns(pages, page_indices)
        layout_name = f"layout_{fp}col"
        dept_name = ""
        for i in page_indices:
            rows, dept_name = parse_digital_page(pages[i], cols, dept_name, i + 1, layout_name)
            all_rows.extend(rows)
    return all_rows


# ---------------------------------------------------------------------------
# STAGE 2b -- explicit strategies for cities whose table shape doesn't fit
# ---------------------------------------------------------------------------

CHENNAI_ROW_RE = re.compile(
    r'^([A-Z]{1,2})\s+([\d\-]+)\s+([\d\-]+)\s+(.+?)\s+'
    r'(-?[\d,]+)\s+(-?[\d,]+)\s+(-?[\d,]+)\s+(-?[\d,]+)\s*$'
)
CHENNAI_DEPT_RE = re.compile(r'^[A-Z][A-Z ]+DEPARTMENT$')


def run_chennai(pdf_path: str):
    text = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                           capture_output=True, text=True).stdout
    rows, current_dept = [], ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if CHENNAI_DEPT_RE.match(line):
            current_dept = line.title(); continue
        m = CHENNAI_ROW_RE.match(line)
        if m:
            zone, func_code, account_code, particulars, a, b, c, d = m.groups()
            rows.append({
                "city": "chennai", "dept_name": current_dept, "zone": zone,
                "function_code": func_code, "item_code": account_code,
                "particulars_english": particulars.strip(),
                "value_1": a, "value_2": b, "value_3": c, "value_4": d,
            })
    return rows


_GHMC_NUM_OR_DASH = r'(?:-|\d[\d,]*\.\d+)'
GHMC_ROW_RE = re.compile(
    rf'^(\d{{1,3}})\s+(\d{{6,8}})\s+(.+?)\s+((?:{_GHMC_NUM_OR_DASH}\s+){{5,}}{_GHMC_NUM_OR_DASH})\s*$'
)
GHMC_SECTION_RE = re.compile(
    r'^(Establishment Expenditure|Administrative Expenditure|Health & Sanitation|'
    r'Infrastructure|Green Budget|Public Convenience|Own Revenues|'
    r'Grants & Assigned Revenues|Debt Service)\b', re.IGNORECASE
)


def run_ghmc(pdf_path: str):
    text = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                           capture_output=True, text=True).stdout
    rows, current_section = [], ""
    period_labels = ["actuals_prev_year", "budget_estimate_this_year",
                      "actuals_upto_period", "revised_budget_estimate",
                      "budget_estimate_next_year"]
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        sect = GHMC_SECTION_RE.match(line)
        if sect:
            current_section = sect.group(1)
        m = GHMC_ROW_RE.match(line)
        if m:
            sl_no, code, desc, values_str = m.groups()
            values = re.findall(_GHMC_NUM_OR_DASH, values_str)
            row = {"city": "ghmc", "section": current_section, "item_code": code,
                   "particulars_english": desc.strip()}
            for i, label in enumerate(period_labels):
                start = i * 3
                if start + 2 < len(values):
                    row[f"value_{i*3+1}"] = values[start]
                    row[f"value_{i*3+2}"] = values[start + 1]
                    row[f"value_{i*3+3}"] = values[start + 2]
            rows.append(row)
    return rows


NAGPUR_ROW_RE = re.compile(
    r'^([A-Z]\d{9,12})\s+(.+?)\s+(-?\d+\.\d{2})\s+(-?\d+\.\d{2})\s+(-?\d+\.\d{2})\s*$'
)
NAGPUR_CATEGORY_RE = re.compile(r'^([A-Z][A-Za-z .&\-]+)\((\d{3,4})\)\s')


def run_nagpur(pdf_path: str):
    text = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                           capture_output=True, text=True).stdout
    rows, current_category = [], ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        cat = NAGPUR_CATEGORY_RE.match(line)
        if cat:
            current_category = cat.group(1).strip()
        m = NAGPUR_ROW_RE.match(line)
        if m:
            code, desc, budget, used, remaining = m.groups()
            rows.append({
                "city": "nagpur", "section": current_category, "item_code": code,
                "particulars_english": desc.strip(),
                "value_1": budget, "value_2": used, "value_3": remaining,
            })
    return rows


def is_real_amount(t: str) -> bool:
    if re.match(r'^-?\d{1,2}(,\d{2,3})+(\.\d+)?$', t):
        return True
    stripped = t.replace(",", "")
    if re.match(r'^-?\d+(\.\d+)?$', stripped) and len(re.sub(r'\D', '', stripped)) >= 5:
        return True
    return False


def run_ocr(pdf_path: str, lang: str = "eng"):
    import pytesseract
    from pdf2image import convert_from_path

    info = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True).stdout
    total_pages = int(re.search(r"Pages:\s+(\d+)", info).group(1))

    all_rows, current_section = [], ""
    SECTION_RE = re.compile(
        r'(BALANCE SHEET|INCOME.*EXPENDITURE|RECEIPTS AND PAYMENTS|CASH ?FLOW|'
        r'FINANCIAL PERFORMANCE|ACCOUNTING POLICIES|Schedule\s+[A-Z\-]*\d+\S*)',
        re.IGNORECASE)

    for i in range(1, total_pages + 1):
        images = convert_from_path(pdf_path, dpi=200, first_page=i, last_page=i)
        img = images[0]
        try:
            osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
            rotate = osd.get("rotate", 0)
        except pytesseract.TesseractError:
            rotate = 0
        if rotate:
            img = img.rotate(-rotate, expand=True)

        data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
        words = [(float(data["top"][j]), float(data["left"][j]), 0.0, txt.strip())
                 for j, txt in enumerate(data["text"]) if txt.strip()]

        line_text = " ".join(t for _, _, _, t in sorted(words))
        m = SECTION_RE.search(line_text)
        if m:
            current_section = m.group(0)

        lines, cur_y, cur = [], None, []
        for y, x0, _, t in sorted(words):
            if cur_y is None or abs(y - cur_y) <= 8:
                cur.append((x0, t)); cur_y = cur_y if cur_y is not None else y
            else:
                lines.append(cur); cur, cur_y = [(x0, t)], y
        if cur:
            lines.append(cur)

        for toks in lines:
            toks.sort()
            ws = [t for _, t in toks]
            label, nums = [], []
            for t in ws:
                (nums if is_real_amount(t) else label).append(t)
            particulars = " ".join(label).strip()
            if not particulars or not nums:
                continue
            row = {"page": i, "section": current_section, "particulars_english": particulars}
            for k, n in enumerate(nums[:4], start=1):
                row[f"value_{k}"] = n
            all_rows.append(row)
        print(f"  page {i}/{total_pages} OCR'd (lang={lang})")
    return all_rows


# ---------------------------------------------------------------------------
# STAGE 4 -- normalize + write
# ---------------------------------------------------------------------------

ALL_FIELDS = ["city", "layout", "page", "section", "dept_name", "zone",
              "function_code", "item_code", "particulars_english",
              "value_1", "value_2", "value_3", "value_4", "value_5",
              "value_6", "value_7", "value_8", "value_9", "value_10",
              "value_11", "value_12", "value_13", "value_14", "value_15"]


def clean_number(t: str):
    if not t:
        return ""
    cleaned = t.replace(",", "").strip()
    try:
        return float(cleaned) if "." in cleaned else int(cleaned)
    except ValueError:
        return t


def write_csv(rows, out_path):
    with open(out_path, "w", newline='', encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=ALL_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            out = {k: r.get(k, "") for k in ALL_FIELDS}
            for k in out:
                if k.startswith("value_"):
                    out[k] = clean_number(out[k])
            w.writerow(out)


# ---------------------------------------------------------------------------
# ORCHESTRATOR
# ---------------------------------------------------------------------------

def run_pipeline(pdf_path: str, out_csv: str, lang: str = "eng", city: str = None):
    import os
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"Input PDF not found: '{pdf_path}'")

    if city == "chennai":
        print("[1/3] city -> 'chennai' (explicit linear-text strategy)")
        rows = run_chennai(pdf_path)
        print(f"[2/3] extracted {len(rows)} rows")
        write_csv(rows, out_csv)
        print(f"[3/3] write_csv -> {out_csv}")
        return rows

    if city == "ghmc":
        print("[1/3] city -> 'ghmc' (explicit linear-text strategy)")
        rows = run_ghmc(pdf_path)
        print(f"[2/3] extracted {len(rows)} rows")
        write_csv(rows, out_csv)
        print(f"[3/3] write_csv -> {out_csv}")
        return rows

    if city == "nagpur":
        print("[1/3] city -> 'nagpur' (explicit linear-text strategy)")
        rows = run_nagpur(pdf_path)
        print(f"[2/3] extracted {len(rows)} rows")
        write_csv(rows, out_csv)
        print(f"[3/3] write_csv -> {out_csv}")
        return rows

    doc_type = classify_document(pdf_path)
    print(f"[1/3] classify_document -> '{doc_type}'")

    if doc_type == "digital":
        rows = run_digital(pdf_path)
        print(f"[2/3] auto-discovered layout(s), extracted {len(rows)} rows")
    else:
        rows = run_ocr(pdf_path, lang=lang)
        print(f"[2/3] OCR (lang={lang}) extracted {len(rows)} rows")

    write_csv(rows, out_csv)
    print(f"[3/3] write_csv -> {out_csv}")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Convert any Indian municipal budget PDF to CSV.")
    ap.add_argument("pdf")
    ap.add_argument("out_csv")
    ap.add_argument("--lang", default="eng")
    ap.add_argument("--city", default=None, choices=["chennai", "ghmc", "nagpur"])
    args = ap.parse_args()
    run_pipeline(args.pdf, args.out_csv, lang=args.lang, city=args.city)