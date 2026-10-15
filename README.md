# ccprofile

**Claude Code のアカウントや API 接続先を、名前で切り替える CLI。**

```sh
ccprofile run personal
ccprofile run work
ccprofile run sakura
```

名前は自由に登録できます。プロファイルごとに設定・履歴の保存先を分けます。

## 1. インストール

macOS、Claude Code、Python 3.11 以降が必要です。Python が古い・入っていない場合は `brew install python` で導入してください。

```sh
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/takehaya/ccprofile/main/install.sh | sh
```

**更新も同じコマンドです。** Go・pip・追加 Python パッケージは不要です。

保存先は `~/.local/bin/ccprofile`。コマンドが見つからない場合は、次を `~/.zshrc`（bash なら `~/.bashrc`）に追加して読み込んでください。

```sh
export PATH="$HOME/.local/bin:$PATH"
```

インストーラーはシェルの起動設定を変更しません。別の保存先は `CCPROFILE_INSTALL_DIR` で指定できます。clone 済みなら `sh install.sh --local` でインストールできます。

## 2. プロファイルを登録

使いたい方式を選んでください。

| 方式 | `--type` | 認証方法 |
|---|---|---|
| Claude の個人・仕事用アカウント | `claude` | Claude Code の公式ログイン |
| さくらなどの Anthropic Messages API 互換サービス | `api` | API トークン |

### いまの Claude Code のログインを使う

```sh
ccprofile add personal --type claude --use-default-config
ccprofile run personal
```

既存の `~/.claude` が必要です。この設定を使えるプロファイルは一つだけです。

### 別の Claude アカウントを使う

```sh
ccprofile add work --type claude
ccprofile login work
ccprofile run work
```

専用の保存先が作られます。`login` で仕事用のアカウントにログインしてください。

### さくらの AI Engine を使う

```sh
ccprofile add sakura --type api \
  --base-url https://api.ai.sakura.ad.jp \
  --model preview/Kimi-K2.7-Code
ccprofile run sakura
```

**`--auth` は省略すると `bearer` です。** `x-api-key` 方式のサービスだけ `--auth x-api-key` を付けてください。URL は HTTPS のベース URL を指定します。

モデル ID は管理画面の「利用可能なモデル」から確認できます（[さくらの操作ガイド](https://manual.sakura.ad.jp/cloud/ai-engine/03-operation-guide.html)）。上記は設定例で、実接続は未検証です。モデル詳細で **Anthropic Messages API への対応**を確認してください。事業者固有の処理はありません。

## 3. API トークンを渡す

`ccprofile run sakura` の入力プロンプトに貼り付けて Enter を押します。入力は画面に表示されません。さくらでは `UUID:シークレット` 全体を使います（[発行手順](https://manual.sakura.ad.jp/cloud/ai-engine/02-howto.html)）。

**毎回入力したくない場合**は、`~/.zshrc`（bash なら `~/.bashrc`）をエディターで開き、次を追加してください。

```sh
export CCPROFILE_TOKEN='ここを実際のトークンに置き換える'
```

```sh
source ~/.zshrc  # bash なら source ~/.bashrc
ccprofile run sakura
```

トークンは起動設定ファイルに平文で残るため、Git にコミットしないでください。この環境変数は全 API プロファイルで共通です。サービスごとに異なるトークンを使う場合は、[起動ごとに渡す方法](docs/usage.md#トークンとプロファイル操作)を使ってください。

`ccprofile` 自身はトークンを保存せず、Keychain を操作しません。`claude` プロファイルでは API トークンは不要です。

## 4. よく使う操作

| やりたいこと | コマンド |
|---|---|
| 一覧・設定を見る | `ccprofile list` / `ccprofile show sakura` |
| 登録済みのモデルを変える | `ccprofile edit sakura --model preview/Kimi-K2.7-Code` |
| ローカル設定を診断する | `ccprofile doctor sakura` |
| プロファイル定義を削除する | `ccprofile remove sakura` |
| 引数を Claude Code に渡す | `ccprofile run work -- -p 'このコードをレビューして'` |

`remove` は Claude の実データを残します。`doctor` は API 通信・認証成功を検証しません。API プロファイルのモデル変更には `edit` を使ってください。各コマンドの詳細は `--help` で確認できます。

短いコマンドにしたい場合は、シェルの起動設定に alias を追加できます。

```sh
alias ccd='ccprofile run personal -- --dangerously-skip-permissions'
```

`ccd` で `personal` を起動します。この例では Claude Code の権限確認をスキップします。

## 詳しい仕様・開発

設定は `~/.config/ccprofile/` に保存されます。通常の登録では既存の `~/.claude` を移動・コピーしません。API 利用は接続先サービスの契約・課金・利用規約に従います。

**実 Claude Code の動作確認済みバージョンはまだありません。** macOS での実ログイン、tool use、streaming は手動確認が必要です。現在の自動テストは偽の Claude コマンドを使います。

- [設定・トークン・環境変数・互換性・開発手順](docs/usage.md)
- [仕様書](docs/spec.md)
- [実装計画](docs/plan.md)
- [OCR レビューへの対応](docs/review-fixes.md)

ソースは [シェルの起動部分](src/launcher.sh) と [Python 本体](src/ccprofile.py) に分かれ、インストール時に単一ファイルへ連結されます。[MIT License](LICENSE)。
