"""Spike 22: 第13章の実測。
A. 経路の選択: 決定論の規則（choose_route）と、LLM に経路を選ばせる方式を、ラベル付きの質問で比べる。
B. Flow のオーバーヘッド: 同じ質問を、サービスを直接呼ぶ場合と、Flow 経由の場合で比べる（Token・時間・成功）。
使い方: s22_flow_routing.py [A|B|AB] [回数]
"""
import collections
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import shared.analysis_service as service
from shared.analysis_flow import choose_route, run_question
from shared.review_requests import ReviewStore
from shared.sales_data import get_llm
from shared.tracing import trace_run

LABELED = [
    # (質問, 正しい経路)
    ("売上全体の状況を教えてください。", "single"), ("地域別の売上を比較してください。", "single"),
    ("カテゴリ別の売上ランキングを教えて。", "single"), ("法人と個人の売上の違いは？", "single"),
    ("一番売れている商品は？", "single"), ("来月の売上を予測してください。", "single"),
    ("キャンセルが多い地域はどこ？", "single"), ("要レビューの注文は何件ですか？", "single"),
    ("O0001の売上はいくらですか？レビューは必要ですか？", "single"), ("O0001を登録した場合のレビュー件数は？", "single"),
    ("データ品質を確認したうえで売上傾向と業務上の示唆をまとめてください。", "integrated"),
    ("データの品質に問題がないか見てから、売上を分析して。", "integrated"),
    ("品質チェック込みで売上を総括して。", "integrated"),
    ("売上分析と、データの品質についての所見を教えてください。", "integrated"),
    ("データに欠損や異常がないか確認したうえで、売上の傾向と打ち手をまとめて。", "integrated"),
    ("データが信頼できるか確かめてから、示唆を出して。", "integrated"),
    ("入力データの状態をチェックして、そのうえで売上分析と提言をお願いします。", "integrated"),
    ("売上の傾向と、業務上の示唆を、データの確からしさも含めて整理してください。", "integrated"),
    ("O0001をレビュー対象として登録してください。", "review_request"), ("O0003のレビュー依頼を出してください。", "review_request"),
    ("O0005を要レビューに追加して。", "review_request"), ("O0007をレビュー対象にして。", "review_request"),
    ("注文O0012をレビューに回してください。", "review_request"), ("O0020のレビューを、お願いします。", "review_request"),
]
PROMPT = """売上分析アシスタントへの質問を、次のいずれか1つに分類してください。ラベルだけを出力します。
- single: 売上の集計・分析の質問（データ品質の確認は含まない）
- integrated: データ品質の確認を含む、統合的な分析の依頼
- review_request: 特定の注文を、レビュー対象として登録・依頼する、状態を変える依頼
質問: {q}
ラベル:"""
LABELS = ("single", "integrated", "review_request")


def llm_route(llm, q):
    out = str(llm.call([{"role": "user", "content": PROMPT.format(q=q)}])).strip().lower()
    return next((l for l in LABELS if l in out), "?")


def routing(repeat):
    llm = get_llm()
    det = [(q, want, choose_route(q)) for q, want in LABELED]
    lm = [(q, want, [llm_route(llm, q) for _ in range(repeat)]) for q, want in LABELED]
    print(f"A. 決定論: 正解 {sum(w == g for _, w, g in det)}/{len(det)}")
    for q, w, g in det:
        if w != g:
            print(f"   誤り（正解 {w} → {g}）: {q}")
    ok = sum(g == w for _, w, gs in lm for g in gs)
    print(f"A. LLM（各{repeat}回）: 正解 {ok}/{len(lm) * repeat}")
    for q, w, gs in lm:
        wrong = [g for g in gs if g != w]
        if wrong:
            print(f"   誤り（正解 {w} → {collections.Counter(wrong).most_common()}）: {q}")
    unstable = sum(len(set(gs)) > 1 for _, _, gs in lm)
    print(f"A. LLM: 同じ質問で答えが変わった質問 {unstable}/{len(lm)}")
    fp = sum(g == "review_request" and w != "review_request" for _, w, gs in lm for g in gs)
    print(f"A. LLM: 依頼でないのに review_request へ進んだ回数 {fp}")


def overhead(n):
    q = "地域別の売上を分析してください。"
    tmp = Path(tempfile.mkdtemp())
    store = ReviewStore(tmp / "r.db")
    for label, call in (("direct", lambda: service.run_analysis(q)),
                        ("flow  ", lambda: run_question(q, "user-001", review_store=store, flow_db=tmp / "f.db"))):
        rows = []
        for _ in range(n):
            with trace_run() as t:
                try:
                    r = call()
                    ok = bool(getattr(r, "result", r)) if label == "flow  " else True
                except service.AnalysisError:
                    ok = False
            s = t.summary()
            rows.append((ok, s["attempts"] <= 1, s["prompt_tokens"] + s["completion_tokens"], s["duration_ms"]))
            print(label, rows[-1], flush=True)
        print(f"B. {label}: n={n} success={sum(r[0] for r in rows)} first_pass={sum(r[1] for r in rows)} "
              f"avg_tokens={round(statistics.mean(r[2] for r in rows))} avg_ms={round(statistics.mean(r[3] for r in rows))}", flush=True)


mode = sys.argv[1] if len(sys.argv) > 1 else "AB"
count = int(sys.argv[2]) if len(sys.argv) > 2 else 3
if "A" in mode:
    routing(count)
if "B" in mode:
    overhead(count)
