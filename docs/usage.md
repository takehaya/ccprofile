# 設定・トークン・開発の詳細

[README に戻る](../README.md)

## トークンとプロファイル操作

API プロファイルでは、環境変数 `CCPROFILE_TOKEN` が未設定の場合に `run` が端末でトークンを非表示入力として受け取ります。`ccprofile` はトークンを保存しません。トークン未設定で端末もない場合は安全にエラーにします。入力をエコーする標準入力へのフォールバックはしません。許可する長さは 1–65,536 文字の空白を含まない printable ASCII です。端末の行バッファによる切り詰めを避け、過大な入力は行末まで受け取ってから拒否します。Backspace / Delete と Ctrl-U で編集でき、Ctrl-D は入力を取り消します。

環境変数 `CCPROFILE_TOKEN` がある場合は、その値を今回の起動に使用します。トークンをコマンドラインに直接書かず、シェルの履歴にも残さないでください。zsh では、例えば次のように入力できます。

```zsh
read -rs 'token?API token: '
print
CCPROFILE_TOKEN="$token" ccprofile run sakura
unset token
```

毎回の入力を省きたい場合は、zsh なら `~/.zshrc`、bash なら `~/.bashrc` に次の設定をエディターで追加できます。実際のトークンを含む行をターミナルで直接実行せず、シェル履歴への記録を避けてください。

```sh
export CCPROFILE_TOKEN='ここを実際のトークンに置き換える'
```

設定を読み込んでから起動します。

```sh
source ~/.zshrc  # bash の場合は source ~/.bashrc
ccprofile run sakura
```

この方法では起動設定ファイルにトークンが平文で残ります。そのファイルを Git にコミットしないでください。bash のログインシェルでは `~/.bash_profile` などから `~/.bashrc` を読み込む設定も必要です。`CCPROFILE_TOKEN` は全 API プロファイルで共通なので、サービスごとにトークンが異なる場合は起動ごとに渡してください。さくらのトークンは `UUID:シークレット` 全体を使います（[発行手順](https://manual.sakura.ad.jp/cloud/ai-engine/02-howto.html)）。

`bearer` は `ANTHROPIC_AUTH_TOKEN`、`x-api-key` は `ANTHROPIC_API_KEY` に渡します。`CCPROFILE_TOKEN` 自体は Claude Code へ引き継ぎません。親環境にある `ANTHROPIC_API_KEY` などをトークンの代わりに再利用することはありません。

```sh
ccprofile edit sakura --model preview/Kimi-K2.7-Code
ccprofile remove sakura
```

名前・種別の変更は新しいプロファイルを作成してください。認証方式を編集した後は、その方式に合うトークンを次回の起動で渡してください。永続的なトークン登録がないため `token set` / `token delete` はありません。

`remove` はプロファイル定義だけを削除し、残した Claude データのパスを表示します。同名のプロファイルを再作成しても新しい ID を付け、古い設定を再利用しません。`--purge` はありません。

`--` の後ろは Claude Code にそのまま渡します。シェル展開や `eval` はありません。API プロファイルの `--model` は登録値で固定し、追加の `--model` / `--model=...` は競合として拒否します。

すべてのサブコマンドに `--help` があります。バージョン確認は `ccprofile --version`。

## 保存場所と既存環境

```text
~/.config/ccprofile/               # 0700
  config.toml                     # 0600、非機密設定のみ
  .lock                           # 0600、更新の排他制御
  profiles/                       # 0700
    <ランダムな不変 ID>/          # 0700、Claude Code の設定・履歴
      anthropic/                  # 0700、Claude が使う外部 Anthropic 設定の分離先
```

表示名からパスを作りません。設定はロックの中で更新し、同じディレクトリの一時ファイルに書き込んでから atomic rename します。設定ファイルの手動編集は公式操作方法ではありません。管理ディレクトリ・設定の権限が不適切な場合やシンボリックリンクの場合はエラーにします。

通常の `add` は `~/.claude` を読み取ったりコピーしたりしません。専用プロファイルでは `CLAUDE_CONFIG_DIR` に加えて `ANTHROPIC_CONFIG_DIR` も内部 ID ごとのディレクトリに固定し、ホームにある active/default Anthropic プロファイルを自動利用しません。資格情報を読む・コピーする操作は行わず、Claude 自身がそれぞれのディレクトリを使います。

既存環境を使う場合に限り `--use-default-config` を指定します。既存の `~/.claude` が必要で、同時に一つのプロファイルだけが利用できます。この場合は親からの設定を除去した後、`CLAUDE_CONFIG_DIR` と `ANTHROPIC_CONFIG_DIR` を両方とも未設定にして、標準の `~/.claude` と `~/.config/anthropic` を明示的に再利用します。親で指定した独自の保存先は引き継ぎません。`show` の `anthropic_config_dir: null` は、この標準動作を表します。

デフォルトのパスを明示しただけでも macOS の資格情報名前空間が変わるという [上流報告](https://github.com/anthropics/claude-code/issues/92252) があるため、既存環境では変数を未設定にします。専用プロファイルを作成した場合は `login` で改めてログインしてください。keyless Console ログインなど外部 Anthropic scope を使う方式の実動作も、対応する Claude 版で確認が必要です。

OAuth 処理は公式 `claude auth login` に委譲します。本ツールは OAuth 資格情報を読み書きしません。Claude Code 自身が macOS Keychain に OAuth 資格情報を保存することはありますが、本ツールは Keychain を操作しません。

## 環境変数と doctor

認証・接続先の混入を防ぐため、`ANTHROPIC_*`、`CLAUDE_*`、`CLAUDECODE`、`AWS_*`、`AZURE_*`、`GOOGLE_*`、`GCLOUD_*`、`GCP_*`、`VERTEX_*`、`BEDROCK_*`、`CCPROFILE_*` を一旦除去し、選択した設定だけを構築します。Claude の変数は以下の明示的な allowlist だけを引き継ぎます。

- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`
- `CLAUDE_CODE_DISABLE_TERMINAL_TITLE`
- `CLAUDE_CODE_ACCESSIBILITY`
- `CLAUDE_CODE_SYNTAX_HIGHLIGHT`
- `CLAUDE_CODE_MAX_OUTPUT_TOKENS`

`HOME`、`PATH`、`TERM`、ロケール、プロキシなどの一般環境変数は維持します。クラウドの環境変数を必要とする Claude のツール処理については、この制限にも注意してください。

`doctor` は Claude コマンド、OS、設定の構文と権限、必須項目、設定ディレクトリの作成・書き込み、渡されたトークンの形式、競合する設定を確認します。プロファイル未登録でも管理ルートの作成・一時書き込みを検査します。専用プロファイルの Claude / Anthropic 設定ディレクトリも確認します。

プロファイルの `settings.json` / `settings.local.json`、現在のプロジェクトと祖先の設定、Git worktree の main checkout 側 local settings、macOS の既知の managed settings と JSON フラグメントを検査します。Git メタデータの確認には PATH の `git` を使います。Git がない環境でも通常のディレクトリでは祖先を検査します。`.git` がファイルやリンクで別のメタデータを参照する場合は、Git を利用できずに設定の解決を省略することはせず、起動・検査をエラーにします。Git の問い合わせ失敗、所有権不適合、解決できない特殊な worktree 構成も同様です。`apiKeyHelper`、プロバイダーの環境変数、API モードの `model` などを検出し、値を表示せずに報告します。競合は `run` / `login` の前にも検査します。`doctor` は検査用の一時ファイルと管理ディレクトリを作成する場合があります。

祖先の `~/.claude` も実際に読み込まれる版があるため、この検査は維持しています。[上流で確認された設定スコープの問題](https://github.com/anthropics/claude-code/issues/80791)があり、`CLAUDE_CONFIG_DIR` だけではすべての設定分離を保証できません。競合があれば起動を拒否し、既存設定を自動変更しません。対応版で実際に読み込まれる設定を確認してください。

API への確認リクエストは送りません。トークンが未指定でも `doctor` は入力を求めず、起動時に入力する旨を表示します。

**制限:** `--settings` などの利用者引数、動的な設定変更、サーバー配布や OS 管理の設定までは完全に検証しません。接続先・認証を上書きする設定を追加しないでください。本ツールはプロキシや認証の強制機構ではありません。

## 秘密情報と互換性

本ツールはトークンを保存せず、表示・ログ出力もしません。子プロセスには環境変数で渡すため、同じユーザー権限のプロセスや診断ツールから見える可能性があります。Claude Code 自身の出力、フック、プラグインなどの振る舞いは本ツールでは制御できません。機密情報の診断出力に注意してください。

API プロファイルの送信先は登録 URL です。各サービスの契約・課金・利用規約に従ってください。Anthropic Messages API の形式だけでなく tool use、streaming などの実装にも依存するため、サードパーティモデルの互換性を保証しません。OpenAI API からのプロトコル変換は行いません。

**動作確認済みの実 Claude Code バージョン: まだありません。** このリポジトリでは偽の Claude コマンドを使うオフラインテストのみを実施しています。認証ソースを模した偽クライアント、実 Git worktree、一時端末を使って、設定先と資格情報名前空間、競合拒否、長いトークン入力を検証します。実ログインの分離と既存ログインの引き継ぎ、さくらの AI Engine、対話・ツール呼び出し・ストリーミングは macOS で手動確認してください。未対応の `claude auth login` はエラーにします。

公式資料は [環境変数](https://code.claude.com/docs/en/env-vars)、[CLI リファレンス](https://code.claude.com/docs/en/cli-reference)、[設定と優先順位](https://code.claude.com/docs/en/settings)。実装時に Bedrock、Vertex、Foundry、Mantle、Anthropic AWS と OAuth 関連の正式な変数名を確認しました。

## 開発・テスト

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile src/ccprofile.py
sh -n install.sh
sh -n src/launcher.sh
python3 src/ccprofile.py --version
```

ソースはシェルの起動部分 [src/launcher.sh](../src/launcher.sh) と Python 本体 [src/ccprofile.py](../src/ccprofile.py) に分かれています。インストーラーがこの順で連結し、単体の `ccprofile` を生成します。GitHub 上ではそれぞれの言語でハイライトされ、Python 本体は直接実行して開発できます。テストではインストーラーが生成した実行ファイルを使い、シェルからの起動も確認します。

テストは実 API や実 OAuth 認証を使いません。CI は macOS / Linux、Python 3.11 / 3.14。Linux は開発用オフラインテスト環境で、正式サポート対象には含めません。

仕様と実装計画は [docs/spec.md](spec.md)、[docs/plan.md](plan.md)、OCR の指摘への対応は [docs/review-fixes.md](review-fixes.md) を参照してください。MIT License。
