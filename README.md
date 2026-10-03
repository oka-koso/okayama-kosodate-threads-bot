# Okayama Kosodate Threads Bot

岡山子育てナビのThreads自動投稿Botです。

## 運用ルール

- 1日3〜5投稿
- JST 7:00〜22:59の間にランダム分散
- 投稿間隔は最低90分
- 基本は日常・育児あるある・保活あるある
- サイト紹介 + アフィリエイト紹介は合計で週2回まで
- 宣伝枠は別日に分散
- アフィリエイト投稿は広告表記を付ける
- 実際に起きた出来事を捏造する一人称投稿はしない
- 同じ日常文の短期連投を避ける
- 投稿済みスロットは state.json に記録して二重投稿を防止

## Secrets

GitHub Actions repository secrets:

- `THREADS_ACCESS_TOKEN`
- `THREADS_USER_ID`

## 手動テスト

`Test Threads Post` workflow で任意テキストを手動投稿できます。
