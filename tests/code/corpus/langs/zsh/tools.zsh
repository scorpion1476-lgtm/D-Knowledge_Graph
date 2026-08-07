#!/usr/bin/env zsh

source ./aliases.zsh

reload() {
  configure
}

function configure {
  autoload -Uz compinit
}

deploy() {
  reload
}
