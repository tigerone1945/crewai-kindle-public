# 第4章 StreamlitでHuman Interfaceを作る

ブラウザから質問を入力し、第3章の Structured Output を画面の各部分に表示します。
画面は「入力・送信・表示」だけを担当し、分析は `shared/analysis_service.py` に任せます。

## 起動

リポジトリのルートで実行します。

```bash
uv run streamlit run chapters/ch04-streamlit-ui/app.py
```

ブラウザが開きます（通常は http://localhost:8501）。質問例のボタンを押すと入力欄に反映され、「分析する」で実行します。
分析には LLM を呼ぶため、数秒かかります。

## 確認

```bash
uv run pytest tests/unit/test_ch04_ui.py
```

Streamlit の `AppTest` を使い、ブラウザなし・LLMなしで画面の動作を確認します。

- 初期表示（タイトル・質問例・フォーム）、質問例ボタンで入力欄が埋まる
- 空の質問・500文字を超える質問は、分析を呼ばずに警告する
- 成功時に、概要・指標の表・発見・次に確認すること・JSON が表示される
- 対応できない質問（`unsupported`）では、指標を出さずに案内を表示する
- 検証に通らなかったとき・想定外のエラーのとき、内部情報（検証メッセージ・接続先など）を画面に出さない

## ファイル

| ファイル | 内容 |
|---|---|
| `app.py` | Streamlit の画面 |
| `../../shared/analysis_service.py` | 分析サービス（画面から呼ぶ入口） |
| `../../docs/figures/ch04-streamlit-result.jpg` | 実行画面（本文の図） |

## 既知の制限

- 画面を操作すると、その都度スクリプト全体が再実行されます。結果は送信した回にだけ表示され、別のボタンを押すと消えます。
- 会話の文脈は保持しません（第5章で扱います）。
