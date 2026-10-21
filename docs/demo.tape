# Records docs/demo.gif: Claude Code with the plugin loaded from this folder.
# Run from the repository root, logged in to Claude Code, with a Jev API key exported:
#   vhs docs/demo.tape
Output docs/demo.gif

Set Shell "bash"
Set FontFamily "Menlo"
Set FontSize 14
Set Width 1100
Set Height 620
Set Padding 16
Set Theme "Catppuccin Mocha"
Set TypingSpeed 30ms
Set PlaybackSpeed 1.5

Hide
Type `unset $(env | grep -o '^CLAUDE[A-Z_]*') && export PS1='$ ' && clear`
Enter
Type `claude --plugin-dir . --model sonnet`
Enter
Sleep 8s
Show

Sleep 1s
Type "what does git rebase do? Answer in one sentence."
Enter
Sleep 18s
Type "Design multi-region cache replication without stale reads. Answer in two sentences, no tools."
Enter
Sleep 55s
