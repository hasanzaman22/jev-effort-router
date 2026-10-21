# Records docs/demo.gif. Run from the repository root with a Jev API key exported:
#   vhs docs/demo.tape
Output docs/demo.gif

Set Shell "bash"
Set FontFamily "Menlo"
Set FontSize 15
Set Width 980
Set Height 300
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms

Hide
Type "export PATH=$PWD/bin:$PATH PS1='$ ' && clear"
Enter
Show

Type 'jev-claude --dry-run "what does git rebase do?"'
Enter
Sleep 2.5s
Type 'jev-claude --dry-run "add a --verbose flag to the CLI"'
Enter
Sleep 2.5s
Type 'clear'
Enter
Type 'jev-claude --dry-run "users get logged out randomly, find the cause"'
Enter
Sleep 2.5s
Type 'jev-claude --dry-run "design multi-region cache replication without stale reads"'
Enter
Sleep 4s
