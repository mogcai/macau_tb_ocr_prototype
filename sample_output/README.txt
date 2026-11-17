呢個資料夾內的文件係 2026 年第 45 期公報的結構檢查樣本：
- documents.csv：26 張 TB 頁面的 metadata、圖片尺寸及 OCR 語言建議。
- issue45_tb_page_manifest.csv / .xlsx：同一份頁面清單，方便篩選。
- run_report.json：inspect-only 執行摘要。

注意：這些是 OCR 前的頁面／版面檢查結果，並非已辨識完成的 Balancete 帳戶或金額。請安裝 PaddleOCR 後按 README 執行 OCR，再人工建立 ground truth 驗證金額。
