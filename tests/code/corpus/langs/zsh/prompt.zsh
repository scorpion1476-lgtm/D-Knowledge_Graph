source ./colours.zsh

render_prompt() {
  colourise "$1"
}

function colourise {
  print -P "$1"
}
