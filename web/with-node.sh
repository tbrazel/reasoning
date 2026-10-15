#!/bin/sh
# Select build tools for this command and its children, not the caller's shell.
if command -v node >/dev/null 2>&1 && node web/check-node.cjs >/dev/null 2>&1; then
    exec "$@"
fi

reasoning_node_version=$(tr -d '\r\n' < .nvmrc)
reasoning_nvm_dir=${NVM_DIR:-${XDG_CONFIG_HOME:+$XDG_CONFIG_HOME/nvm}}
reasoning_nvm_dir=${reasoning_nvm_dir:-$HOME/.nvm}
if [ -s "$reasoning_nvm_dir/nvm.sh" ] && [ -r "$reasoning_nvm_dir/nvm.sh" ]; then
    export NVM_DIR="$reasoning_nvm_dir"
    # A function supplies positional arguments reliably in dash as well as bash.
    reasoning_load_nvm() { . "$NVM_DIR/nvm.sh"; }
    if reasoning_load_nvm --no-use &&
        command -v nvm >/dev/null 2>&1 &&
        nvm use --silent "$reasoning_node_version" >/dev/null 2>&1 &&
        node web/check-node.cjs >/dev/null 2>&1; then
        printf 'reasoning-build: using installed Node.js %s via nvm (this command only).\n' "$(node --version)" >&2
        exec "$@"
    fi
    printf 'reasoning-build: nvm could not select a compatible installed Node.js %s.\n' "$reasoning_node_version" >&2
    printf 'Install it once: . "%s/nvm.sh" && nvm install %s\n' "$reasoning_nvm_dir" "$reasoning_node_version" >&2
else
    if command -v node >/dev/null 2>&1; then
        node web/check-node.cjs >&2
    else
        printf 'reasoning-build: Node.js is missing; install Node.js %s with your version manager.\n' "$reasoning_node_version" >&2
    fi
    printf 'reasoning-build: nvm was not found or readable at %s/nvm.sh; set NVM_DIR if it is installed elsewhere.\n' "$reasoning_nvm_dir" >&2
fi
printf '%s\n' 'Retry the same make command afterward. No Node installation, default-version change, or cloud access was attempted.' >&2
exit 2
