# GameWith-notifier

GameWithの「最新ニュース」「リリース予定」「新作」を差分監視し、Discordの3チャンネルへ通知するbot。

## 構成

```
.github/workflows/dispatch.yml   # repository_dispatch / workflow_dispatchで起動
.github/workflows/tests.yml      # push/PR時にtests.pyを実行
config.py                        # URL・環境変数・閾値
main.py                          # 通常実行（3チャンネル処理）
scraper.py                       # gamewith.jp のHTML解析（requests + BeautifulSoup、JS実行不要）
differ.py                        # 前回stateとの差分検出
notifier.py                      # Discord embed生成・送信
state_manager.py                 # state/*.json の読み書き
date_utils.py                    # 「9月22日」/「26年9月」の解析
init_state.py                    # 初回既読化のみ（通知なし）
test_notify.py                   # 3チャンネルへの疎通テスト通知
tests.py                         # 回帰テスト（外部通信なし・`python tests.py`）
state/*.json                     # 実行のたびに更新される状態ファイル
```

## 通知ルール

| チャンネル | 検知条件 | 備考 |
|---|---|---|
| 最新ニュース | 新しい記事IDを検知したら通知 | 一覧の途中に古い記事が後から挿入されても、ID集合の差分で検知するため取りこぼさない |
| 新作 | 新しいゲームIDを検知したら通知 | 同上 |
| リリース予定 | 配信日が「月日」まで判明した時点で通知。「年月」のみの間は通知しない | 通知後に日付が変わった（延期・前倒し）場合は🔁付きで再通知。「9月22日→26年12月」のように精度が粗くなる延期も対象。「9月22日→9月22日（火）」のような表記ゆれのみの変化では再通知しない |

日付は「9月22日」「26年9月」に加え、「2026年9月」「9月22日（月）」「26年9月下旬」「２６年９月」（全角）も解析する。
解析できない表記（「未定」等）は通知対象外とし、新しい表記パターンに気付けるようログに残す。

通知はDiscord embedの`title`にリンクを埋め込み、本文にURLはそのまま貼らない。

## エラー時の挙動

- 通信エラー（3回リトライしても失敗）→ 一時的な障害とみなし**静かにスキップ**、1時間後の次回実行に任せる。6回連続失敗した時点でニュースチャンネルへ1通だけアラート。
- ページ構造エラー（想定した要素が見つからない＝サイト側の仕様変更でリトライしても直らない）→ **即座にニュースチャンネルへ通知**。
- 同じページ構造エラーが続く間は、2回目以降の通知を抑制する（毎時間同じ警告が飛ぶのを防ぐ）。復旧後に再発した場合は改めて通知する。
- 1回の実行あたりの通知上限は10件（`config.MAX_NOTIFY_PER_RUN`）。超過分は破棄せず`state/*.json`内の`queue`に積んで次回実行時に優先送信する。
- Discordに400番台で拒否された通知（内容起因で何度送っても通らないもの）は破棄する。queueの先頭で詰まって後続の通知が出せなくなるのを防ぐため。
- queueの保持上限は200件（`config.MAX_QUEUE_SIZE`）。Webhook設定ミス等で送信できない状態が続いてもstateが無限に膨らまない。

## stateについて

- `known_ids`は「最近見た順」のリストで、上限2000件（`differ.KNOWN_IDS_LIMIT`）。1ページの掲載数は20件程度なので、掲載中の記事が「未知」に戻って再通知されることはない。
- ニュースのIDはURLのパス全体（例: `gamedb/15584/articles/63543`）。末尾の番号だけでは`/pc/article/show/4338`と衝突し、別記事を取りこぼす。旧方式で記録済みのIDも既知として扱うため、移行時の再通知は起きない。移行済みの旧IDは次回実行時にstateから削除する（残すと番号の衝突で新着を取りこぼすため）。
- リリース予定は掲載が消えてから120日（`differ.GAME_RETENTION_DAYS`）で記録を削除する。
- ワークフローは`concurrency`で直列化している。外部cronと手動実行が重なっても二重通知・pushの衝突が起きない。

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

## テスト

```
python tests.py
```

外部通信は行わないため、Webhookやネットワークなしで実行できる。push・PR時にはGitHub Actions（`tests.yml`）でも自動実行される。
