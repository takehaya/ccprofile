# OCR レビュー対応記録

2026-10-15 の初回 Open Code Review に対し、実装上の 6 件を修正した。初回の判定・個別レビュー・相互検証は、ローカルの `.ocr/sessions/2026-10-15-main/rounds/round-1/` に変更せず保持する。これは修正と検証の記録であり、新しい OCR レビューの判定ではない。

| 指摘 | 対応 | 回帰検証 |
|---|---|---|
| F1: 外部 Anthropic scope が共有される | 専用 ID 配下の `anthropic/` を作成・検証し、`ANTHROPIC_CONFIG_DIR` に設定 | 偽クライアントで global / A / B の設定選択、0700、symlink 拒否 |
| F2: default の明示パスで macOS の名前空間が変わり得る | default 再利用時は両設定先変数を除去して未設定にする。標準保存先の再利用を明文化 | unset / explicit の名前空間を区別する偽クライアントと、親の独自パス除去 |
| F3: worktree 本体の local settings が検査から漏れる | read-only Git 問い合わせで main checkout の設定を追加。解決できない参照・Git 不在のリンク構成は起動拒否 | 実際の兄弟 worktree、ネストした cwd、separate git-dir、競合時に偽 Claude が起動しないこと |
| F5: 長い端末トークンが切り詰められる | bounded noncanonical 入力に変更。過大入力も行末まで非表示で消費して拒否し、端末属性とハンドラーを復元 | 5,000 / 65,536 文字の完全一致、65,537 文字拒否、編集、Ctrl-D、SIGINT/SIGTERM/SIGHUP、全 termios 属性の復元 |
| F6: 初回 doctor が保存先を検査しない | 管理ルートの作成と一時ファイルへのデータ書き込み・fsync を、プロファイル表と独立して実施 | 不正な `.config`、書き込み不可、作成・データ書き込み失敗、probe の cleanup |
| F7: URL の dot segment で endpoint 拒否を回避 | percent-decode 後の `.` / `..` 要素を拒否 | add / edit の literal / encoded パス拒否、失敗時の既存設定保持、通常 `/api` / `/v1` の許可 |

実装は [bin/ccprofile](../bin/ccprofile)、追加した 17 回帰テストは [tests/test_review_regressions.py](../tests/test_review_regressions.py)。既存テストの default・環境変数の期待値も更新した。

## F4 の扱い

「HOME の祖先設定は必ず無関係」という前提は、上流で確認された読み込み挙動と矛盾するため採用しない。祖先検査を維持し、対応版における設定スコープの確認事項として README に記載した。古い設定を自動削除・移動・変更する処理は加えていない。

## 検証結果

Linux / Python 3.14.4 で以下を確認した。

- `python3 -m unittest discover -s tests -v`: 30 件成功（既存 13 + 追加 17）。
- Python 構文検査、installer のシェル構文検査、シェルからの CLI 起動成功。
- Git worktree の問い合わせは設定ファイルを変更しない。
- 実 API、OAuth、Keychain、実ユーザーの資格情報は使用していない。

macOS の実ログイン、既存 Keychain 名前空間の引き継ぎ、実 Claude の認証・設定ソース選択は未確認。偽クライアントの成功を実機確認として扱わない。特殊な worktree 構成で安全に解決できない場合は、設定検査を省略せずエラーにする。
