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

### Use Sakura AI Engine

```sh
ccprofile add sakura --type api \
  --base-url https://api.ai.sakura.ad.jp \
  --model preview/Kimi-K2.7-Code
ccprofile run sakura
```

Omit `--auth` to use `bearer`.
Add `--auth x-api-key` only for services that require that authentication method.
Use an HTTPS base URL.

Find the model ID under "Available models" (「利用可能なモデル」) in the control panel ([Sakura operation guide](https://manual.sakura.ad.jp/cloud/ai-engine/03-operation-guide.html)).
We have not tested a live connection with this configuration.
Check the model details for Anthropic Messages API support.
Use the same setup process for other providers; ccprofile has no provider-specific behavior.

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

## 4. Common commands

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
