# crewai-kindle-public

Kindle本『**CrewAIで育てるAIエージェント** ― 売上データ分析からCrew・Flow・Productionまで』の読者向けコンパニオンリポジトリです。

本書で扱う実行可能なソースコード、テスト、Evaluation Case、サンプルデータを公開しています。

> **Crew = Agency（判断・役割分担）**  
> **Flow = Deterministic Backbone（順序・状態・分岐・Retry・Human Gate）**

## セットアップ

```bash
uv sync
cp .env.example .env
```

`.env` に `OPENAI_API_KEY` を設定してください。

## 公開している内容

```text
data/            共通CSV
chapters/        章ごとの実装
shared/          章をまたいで共有するコード
tests/           unit / integration / postgres / regression / evaluations
evaluations/     Evaluation Case・ランナー・判定基準
examples/        Technical Spike・MCP検証コード
docs/figures/    本文で使用した実行画面・検証図
docs/spike_results.md
                 技術検証結果
Dockerfile
docker-compose.yml
docker-compose.prod.yml
```

商品企画書、執筆計画書、詳細目次、本文原稿、レビュー記録、EPUB生成物などの出版制作資料は、このPublicリポジトリには含めません。

## Source Snapshot

- `main`
  - 後続修正を含む最新の読者向け完成版
- `crewai-kindle-chXX-v1.0`
  - 各章を完成させた時点の、読者公開対象だけを含むSource Snapshot

Private制作リポジトリから、公開許可リストだけを抽出して生成しています。

## ライセンス

MIT License
