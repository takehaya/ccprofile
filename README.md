# ccprofile

Switch between Claude Code accounts and API endpoints with named profiles.

```sh
ccprofile run personal
ccprofile run work
ccprofile run sakura
```

Choose your own profile names and keep settings and session history in separate directories for each profile.

## 1. Install

You need macOS, Claude Code, and Python 3.11 or later.
If Python is missing or too old, install it with `brew install python`.

```sh
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/takehaya/ccprofile/main/install.sh | sh
```

Use the same command to update.
You do not need Go, pip, or additional Python packages.

Find the installed command at `~/.local/bin/ccprofile`.
If your shell cannot find it, add the following line to `~/.zshrc` (`~/.bashrc` for bash), then reload that file.

```sh
export PATH="$HOME/.local/bin:$PATH"
```

The installer does not modify your shell startup files.
Set `CCPROFILE_INSTALL_DIR` to choose a different installation directory.
If you have already cloned the repository, run `sh install.sh --local` from the checkout.

## 2. Add a profile

Choose a connection type.

| Connection | `--type` | Authentication |
|---|---|---|
| Personal or work Claude account | `claude` | Official Claude Code login |
| Sakura AI Engine or another Anthropic Messages API-compatible service | `api` | API token |

### Use your existing Claude Code login

```sh
ccprofile add personal --type claude --use-default-config
ccprofile run personal
```

This requires an existing `~/.claude` directory.
Only one profile can use the default configuration at a time.

### Use another Claude account

```sh
ccprofile add work --type claude
ccprofile login work
ccprofile run work
```

This creates a dedicated configuration directory.
Use `login` to sign in to your work account.

### Use an API service

Register an Anthropic Messages API-compatible service with `--type api`, its base URL, and a model ID.
Choose any profile name.
For example, to use Sakura AI Engine:

```sh
ccprofile add sakura --type api \
  --base-url https://api.ai.sakura.ad.jp \
  --model preview/Kimi-K2.7-Code
ccprofile run sakura
```

Omit `--auth` to use `bearer`.
Add `--auth x-api-key` only for services that require that authentication method.
Use an HTTPS base URL.

Find model IDs in your provider's documentation or control panel.
For Sakura, open "Available models" (「利用可能なモデル」) in the control panel ([Sakura operation guide](https://manual.sakura.ad.jp/cloud/ai-engine/03-operation-guide.html)).
We have not tested a live connection with this configuration.
Check the model details for Anthropic Messages API support.
Use the same setup process for other providers; ccprofile has no provider-specific behavior.

### Set a custom model's context window

If Claude Code reports an unknown model and assumes a 200K window, check the context limit with your API provider.
For an API profile whose provider supports 262,144 tokens, save that limit with:

```sh
ccprofile edit sakura --context-window 262144
ccprofile run sakura
```

You can also pass `--context-window TOKENS` when adding an API profile.
Use a positive whole number of tokens, without a `k` or `M` suffix.
Inspect the saved value with `ccprofile show sakura`.
To remove the override:

```sh
ccprofile edit sakura --clear-context-window
```

ccprofile passes the saved value as `CLAUDE_CODE_MAX_CONTEXT_TOKENS` for that profile only.
Use this to set the limit Claude Code assumes.
Your provider's actual context limit still applies.
Check the limit again when changing models; other edits keep the saved value.
Whether Claude Code applies the override depends on its version and how it resolves the model ID; see [custom model context windows](https://code.claude.com/docs/en/model-config#correct-the-window-for-a-gateway-or-custom-model-id).

## 3. Supply an API token

When `ccprofile run sakura` prompts for a token, paste it and press Enter.
Your input is not displayed.
For Sakura, use the full `UUID:secret` value ([token creation instructions](https://manual.sakura.ad.jp/cloud/ai-engine/02-howto.html)).

To reuse the token, open `~/.zshrc` (`~/.bashrc` for bash) in an editor and add:

```sh
export CCPROFILE_TOKEN='replace-with-your-token'
```

```sh
source ~/.zshrc  # For bash: source ~/.bashrc
ccprofile run sakura
```

You are saving the token as plain text in your shell startup file, so keep that file out of Git.
You will use this token for all API profiles.
If your services need different tokens, [supply a token for each invocation](docs/usage.md#トークンとプロファイル操作).

ccprofile itself does not store tokens or access Keychain.
You do not need an API token for a `claude` profile.

## 4. Sessions in tmux

When you start `ccprofile run` from a terminal outside tmux and tmux is installed, Claude Code runs in a tmux session named after the current folder.
For example, a run in `~/src/my.app` creates the session `my_app`.
Detach with `Ctrl-b d`; Claude Code keeps running.

When you run the same profile in the same folder again, ccprofile lists the existing sessions with each one's title, last activity, and last few lines of output.
Press Enter to reopen the most recent session, or answer `y` to start another one (`my_app-2`, and so on).
If there are several sessions, choose one by number.

```text
Existing sessions for this folder:
  1) my_app  [detached, 5m ago]  ✳ Fix login tests
       │ ● Running tests...
  2) my_app-2  [detached, 2h ago]  ✳ Update README
       │ Done. README updated.
Open a new session? [y/N]:
Session to open [1-2] (default 1):
```

Sessions are matched by folder and profile, so `run work` never reopens a `personal` session.
Add `--no-tmux` (`ccprofile run --no-tmux work`) to run in the current terminal.
ccprofile does not use tmux when tmux is missing, when you are already inside tmux, or when input or output is not a terminal.

A new session receives the tmux server's environment, not your current shell's.
If `CCPROFILE_TOKEN` is not set in that environment, the session prompts for the token.

Use `ccprofile tmux` to run any other program the same way:

```sh
alias codexy='ccprofile tmux codex -- --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust'
alias agyd='ccprofile tmux agy -- --dangerously-skip-permissions'
```

Sessions are matched by folder and program name.
Unlike `run`, `ccprofile tmux` passes the environment through unchanged and does not read profiles.
Arguments after `--` apply only when ccprofile creates a session; reopening a session attaches to the running program.

## 5. Common commands

| Task | Command |
|---|---|
| List profiles or inspect settings | `ccprofile list` / `ccprofile show sakura` |
| Change a registered model | `ccprofile edit sakura --model preview/Kimi-K2.7-Code` |
| Check local configuration | `ccprofile doctor sakura` |
| Remove a profile definition | `ccprofile remove sakura` |
| Pass arguments to Claude Code | `ccprofile run work -- -p 'Review this code'` |

The `remove` command leaves Claude's data in place.
The `doctor` command does not test API connectivity or authentication.
Use `edit` to change an API profile's model.
Run any subcommand with `--help` for details.

For a shorter command, add an alias to your shell startup file:

```sh
alias ccd='ccprofile run personal -- --dangerously-skip-permissions'
```

Run `ccd` to launch the `personal` profile.
This example skips Claude Code's permission checks.

## Reference and development

Find your configuration under `~/.config/ccprofile/`.
Adding a profile without `--use-default-config` does not move or copy your existing `~/.claude` directory.
API use is subject to the selected service's contract, billing, and terms of use.

We have not verified any version of the real Claude Code CLI.
We still need to test login, tool use, and streaming manually on macOS.
We use a fake Claude executable in the automated tests.

- [Configuration, tokens, environment variables, compatibility, and development (Japanese)](docs/usage.md)
- [Specification (Japanese)](docs/spec.md)
- [Implementation plan (Japanese)](docs/plan.md)
- [OCR review fixes (Japanese)](docs/review-fixes.md)

Read the source in the [shell launcher](src/launcher.sh) and [Python implementation](src/ccprofile.py).
During installation, the installer concatenates them into a single file.
[MIT License](LICENSE).
