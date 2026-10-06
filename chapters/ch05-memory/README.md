# 第5章 MemoryでContextを管理する

会話の文脈（短期）と、利用者の好み（長期）を分けて扱います。売上などの数値は Memory に保存しません。

| 種類 | 持ち場所 | 内容 | 寿命 |
|---|---|---|---|
| 会話の文脈（短期） | 画面のセッション | 直前までの質問・分析種別・要約 | 会話の間だけ。保存しない |
| 好み（長期） | CrewAI Memory（`data/memory/`） | 表現・形式の好み | 削除するまで |
| 売上の数値 | CSV（Database） | Business Fact | Memory へは保存しない。毎回 Tool で再計算 |

## 起動

リポジトリのルートで実行します。

```bash
uv run streamlit run chapters/ch05-memory/app.py
```

- サイドバーで好みを登録・削除できます（例: 要約は必ず「結論:」で始める）。数値・秘密情報・個人情報は登録できません。
- 質問すると、適用した好みが回答の下に表示されます。
- 「では一番高かった地域は？」のような続きの質問は、直前の会話を踏まえて答えます。
- 「新しい会話を始める」で会話の文脈を消します（登録した好みは残ります）。

好みは `data/memory/` に保存されます（Git の対象外）。

## 確認

```bash
uv run pytest tests/unit        # Memory Policy・会話文脈・画面（LLMを呼ばない。毎回同じ結果）
uv run pytest tests/integration/test_ch05_memory.py  # LLMと本物のMemoryを使う（APIキーが必要。出力は確率的）
```

## ファイル

| ファイル | 内容 |
|---|---|
| `app.py` | 会話画面（履歴・好みの登録と削除） |
| `../../shared/memory_policy.py` | Memory Policy と `PreferenceStore`（好みの保存・取得・削除） |
| `../../shared/conversation.py` | 会話の文脈（`Turn`） |
| `../../shared/analysis_service.py` | 好みと文脈を質問に添える `compose_prompt()` |
| `../../docs/figures/ch05-memory-conversation.jpg` | 実行画面（本文の図） |

## 利用者ID

画面では利用者IDを入力欄で指定しています。本番では、認証済みの利用者IDから決めます（第16章）。
入力値をそのまま信じると、他の人の好みを読み書きできてしまいます。
