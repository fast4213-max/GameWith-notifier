# GameWith-notifier

GameWithの「最新ニュース」「リリース予定」「新作」を差分監視し、Discordの3チャンネルへ通知するbot。

## 構成

```
.github/workflows/dispatch.yml   # repository_dispatch / workflow_dispatchで起動
config.py                        # URL・環境変数・閾値
main.py                          # 通常実行（3チャンネル処理）
scraper.py                       # gamewith.jp のHTML解析（requests + BeautifulSoup、JS実行不要）
differ.py                        # 前回stateとの差分検出
notifier.py                      # Discord embed生成・送信
state_manager.py                 # state/*.json の読み書き
date_utils.py                    # 「9月22日」/「26年9月」の解析
init_state.py                    # 初回既読化のみ（通知なし）
test_notify.py                   # 3チャンネルへの疎通テスト通知
state/*.json                     # 実行のたびに更新される状態ファイル
```

## 通知ルール

| チャンネル | 検知条件 | 備考 |
|---|---|---|
| 最新ニュース | 新しい記事IDを検知したら通知 | 一覧の途中に古い記事が後から挿入されても、ID集合の差分で検知するため取りこぼさない |
| 新作 | 新しいゲームIDを検知したら通知 | 同上 |
| リリース予定 | 配信日が「月日」まで判明した時点で通知。「年月」のみの間は通知しない | 通知後に日付が変わった（延期・前倒し）場合は🔁付きで再通知 |

通知はDiscord embedの`title`にリンクを埋め込み、本文にURLはそのまま貼らない。

## エラー時の挙動

- 通信エラー（3回リトライしても失敗）→ 一時的な障害とみなし**静かにスキップ**、1時間後の次回実行に任せる。6回連続失敗した時点でニュースチャンネルへ1通だけアラート。
- ページ構造エラー（想定した要素が見つからない＝サイト側の仕様変更でリトライしても直らない）→ **即座にニュースチャンネルへ通知**。
- 1回の実行あたりの通知上限は10件（`config.MAX_NOTIFY_PER_RUN`）。超過分は破棄せず`state/*.json`内の`queue`に積んで次回実行時に優先送信する。

## セットアップ

### 1. GitHub Secrets

- `DISCORD_WEBHOOK_NEWS`
- `DISCORD_WEBHOOK_RELEASE_SCHEDULE`
- `DISCORD_WEBHOOK_NEW_TITLES`

### 2. リポジトリの権限設定

Settings → Actions → General → Workflow permissions → **Read and write permissions**
（stateファイルをActions自身がcommit&pushするために必要。※本リポジトリは設定済み）

### 3. 初回実行（既読化）

本番実行の前に、通知なしで現状を既読化する。

```
event_type: init-notify
```
を`repository_dispatch`で1回だけ叩くか、Actionsタブから`workflow_dispatch`（mode: init-notify）を手動実行する。

### 4. 外部cronの設定（cron-job.org）

- URL: `https://api.github.com/repos/{owner}/{repo}/dispatches`
- Method: POST
- Headers:
  - `Authorization: Bearer {PAT}`
  - `Accept: application/vnd.github+json`
  - `Content-Type: application/json`
- Body: `{"event_type": "run-notify"}`
- 実行間隔: 1時間ごと

疎通確認用に`{"event_type": "test-notify"}`のジョブも用意しておくと便利（普段はOFF）。

PATは`repo`スコープのclassic PATで、cron-job.org側の設定にのみ使用する（GitHub Secretsには入れない）。
