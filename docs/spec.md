# ccprofile MVP 仕様（変更反映版）

## 最新の変更

当初仕様の Go と API 用 macOS Keychain は、利用者の「Keychain は不要かもしれない」「ワンライナーで導入できるシェルスクリプトが嬉しい」という希望に合わせて変更した。トークンの保存方式の回答はまだないため、秘密情報を永続保存しない方式を採用する。

- ソースは `src/launcher.sh` と `src/ccprofile.py` に分け、インストール時に連結する。生成された単体ファイル `ccprofile` を sh または直接実行する。
- 内部処理は Python 3.11 以降の標準ライブラリ。追加パッケージ不要。
- API トークンは端末の非表示入力または `CCPROFILE_TOKEN` で毎回渡す。
- Keychain、平文ファイル、設定へのトークン保存は行わない。
- 永続登録がないため `token set` / `token delete` を廃止する。
- Go 成果物と `go test ./...` は Python の自動テストへ置き換える。
- 公開後のシェルインストーラーとローカルの一行インストールを提供する。

## 目的・境界

Claude Code の公式アカウントと Anthropic Messages API 互換サービスを任意の名前で切り替える。初期プロファイル、事業者固有の URL・モデル・例外処理を持たない。MVP の正式サポートは macOS と zsh。Linux はオフラインテストに用いる。

通信プロキシ、OpenAI API のプロトコル変換、OAuth 資格情報の読み書きやコピー、GUI、デーモン、プラグイン・MCP 同期、モデルのオンライン取得、課金管理は対象外。

## CLI

```text
ccprofile add NAME --type claude [--use-default-config]
ccprofile add NAME --type api --base-url URL --model MODEL --auth bearer|x-api-key
ccprofile list
ccprofile show NAME
ccprofile edit NAME [--base-url URL] [--model MODEL] [--auth bearer|x-api-key]
ccprofile remove NAME
ccprofile login NAME
ccprofile run NAME [-- CLAUDE_ARGS...]
ccprofile doctor [NAME]
ccprofile --version
```

各サブコマンドは `--help` を持つ。名前は `^[a-zA-Z][a-zA-Z0-9_-]{0,63}$`。重複名を暗黙に上書きしない。名前と種別の変更は新規作成とする。`claude` は API フラグを拒否する。`api` は URL・モデル・認証方式を必須とする。

URL は HTTPS のベース URL とし、ユーザー情報、クエリ、フラグメント、`/messages` の個別 API パスを拒否する。literal / percent-encoded の `.` / `..` パス要素も拒否し、正規化による endpoint 判定の回避を防ぐ。HTTP localhost の例外はない。追加時に認証・通信を行わない。

`list` / `show` は非機密設定とトークンを保存しない旨を表示する。`edit` は API 設定を検証後に原子的に保存する。認証方式の変更後は次回の起動でその方式のトークンを明示的に渡す。

`remove` は定義だけを削除し、残す実データの場所を表示する。再帰的な削除や `--purge` は実装しない。

`login` は `claude` プロファイルだけに対応し、専用の環境で `claude auth login` を呼ぶ。`--help` による対応確認に失敗したバージョンはエラーとする。パスワードや OAuth トークンは本ツールで受け取らない。

## 設定・保存

設定は `~/.config/ccprofile/config.toml`（0600）に保存し、管理ディレクトリは 0700。利用者所有であることと、シンボリックリンクでないことを検証する。ユーザーが任意の保存パスを CLI フラグで指定する機能は設けない。

各プロファイルに暗号学的乱数から生成した 128 bit の不変 ID（32 桁の小文字 hex）を付与する。Claude 設定ディレクトリは `~/.config/ccprofile/profiles/ID/`、外部 Anthropic 設定ディレクトリはその配下の `anthropic/` とする。どちらも所有者・0700・symlink でないことを検証する。名前から構築せず、同名で作り直しても旧設定を再利用しない。

設定は TOML version 1。未知のフィールド、重複 ID、不正な値を拒否する。最大ファイルサイズは 1 MiB。ロックの中で read-modify-write を行い、同じディレクトリの一時ファイルに書いて fsync 後、atomic rename する。書き込みや rename に失敗しても以前のファイルを維持する。手動編集は公式操作方法としない。

```toml
version = 1

[profiles.example]
id = "0123456789abcdef0123456789abcdef"
type = "api"
base_url = "https://example.com"
model = "available-model-id"
auth = "bearer"
```

## 既存環境

通常の `add` は `~/.claude` を移動・コピー・再利用しない。既存環境を使うには `add NAME --type claude --use-default-config` を明示する。既存ディレクトリが必須で、同時に一プロファイルだけに結び付ける。`use_default_config = true` を保存する。専用プロファイルでは二つの設定ディレクトリの変数を固定する。

default 再利用時は、親の `CLAUDE_CONFIG_DIR` と `ANTHROPIC_CONFIG_DIR` を除去し、両方未設定のまま起動する。標準 `~/.claude` と `~/.config/anthropic` を使うための明示的な例外で、親の独自パスは再利用しない。macOS の既存資格情報名前空間を変えないことが目的。default Anthropic scope は作成・資格情報検査しない。`show` の `anthropic_config_dir` は default では null、専用プロファイルでは管理下のパスとする。資格情報の読み取り・コピーは行わない。

## 起動と秘密情報

PATH で `claude` を解決し、引数を配列で渡す。`eval` やシェル文字列を組み立てない。`--` の後ろをそのまま転送する。API モードでは `--model MODEL` を追加し、利用者側の `--model` と `--model=...` を拒否する。

プロバイダー関連の名前空間を除去し、プロファイルから必要な値だけを再構築する。未知の将来変数も同じ名前空間なら除去する。Claude の環境変数には README 記載の小さな allowlist を用いる。一般環境変数とプロキシ、ロケールは維持する。親シェルは変更しない。

Claude モードには API 認証・接続先を残さない。API モードでは選択 URL と Bearer または X-Api-Key の一つだけを設定する。親の認証情報や他プロファイルにフォールバックしない。

トークン未指定時は端末で非表示入力を行う。端末なし・非表示入力不可の場合はエラーにし、エコーする入力にフォールバックしない。端末入力は noncanonical とし、入力の切り詰めを防ぐ。トークンは空白を含まない printable ASCII の 1–65536 文字。過大な入力はメモリを制限して行末まで消費した後に拒否し、途中でエコーを戻して残りの秘密を露出させない。erase と Ctrl-U による編集、Ctrl-D による取り消しを扱う。CLI 引数、設定、出力、ログに載せない。`CCPROFILE_TOKEN` 自体も子環境から除去する。環境変数は同じユーザー権限のプロセスから見える場合がある。

`execve` でプロセスを置き換え、標準入出力、端末、終了コード、SIGINT/SIGTERM を引き継ぐ。入力後および SIGINT/SIGTERM/SIGHUP による中断時は、元の端末属性とシグナルハンドラーを復元する。余分な端末入力は消費・破棄し、Claude へ秘密の残りを渡さない。

## doctor

Claude コマンド、正式対応 OS、設定の構文・権限、必須項目、管理ディレクトリの作成・書き込み、渡されたトークンの形式、settings の競合を確認する。プロファイルがゼロでも管理ルートを作成・検証し、一時書き込みを行う。専用プロファイルでは Claude / Anthropic 両ディレクトリを確認する。API リクエストや OAuth 資格情報の読み取りを行わない。トークン未指定時は情報表示だけで入力を求めない。

プロファイル・プロジェクト・既知の macOS managed settings の JSON を検査する。Git の read-only メタデータ問い合わせで worktree の main checkout にある有効な local settings も検査する。Git 不在でも通常のディレクトリは祖先を検査するが、`.git` ファイル・リンクで別メタデータを参照している場合は解決を省略せず安全にエラーとする。問い合わせ失敗・所有権不適合・解決できない構成も起動を拒否する。祖先設定を読み込む実クライアントの報告があるため、cwd からの祖先検査は維持する。接続先、認証、設定ディレクトリの環境変数、認証 helper、API モードの model 等の競合を、値を表示せずに報告する。起動前にも同じ検査を行う。設定を自動修正しない。

検査できない動的設定や利用者が渡す `--settings` などの限界は README に明記する。認証成功・モデル対応・API 互換性を保証しない。

## 受け入れ条件と成果物

自動テストでは任意名での CRUD、不正名・重複・URL・欠落拒否、設定ディレクトリの分離、既存環境への明示接続、Bearer/API-key とモデル、環境変数の除去、引数の正確な転送、トークンなしの起動拒否、秘密を含まない出力、失敗時の既存設定維持、削除時の実データ保持、終了コード・シグナル、同時更新と起動、インストールを確認する。実 API を使わず、偽の `claude` を利用する。

手動確認は macOS での個人・仕事 OAuth ログイン分離、さくらの AI Engine の実接続、対応モデル、対話・tool use・streaming。確認済みの実 Claude Code バージョンは現時点ではない。

成果物は `src/launcher.sh`、`src/ccprofile.py`、`install.sh`、`README.md`、`docs/spec.md`、`docs/plan.md`、`tests/`、`.github/workflows/test.yml`、`LICENSE`。

## 参照資料

- https://code.claude.com/docs/en/env-vars
- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/settings
- https://manual.sakura.ad.jp/cloud/ai-engine/02-howto.html
- https://manual.sakura.ad.jp/api/cloud/portal/?api=ai-engine-inference-api
