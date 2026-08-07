#!/usr/bin/env bash
set -euo pipefail

source ./lib/common.sh

build() {
  compile "$1"
  package "$1"
}

function package {
  tar -czf out.tgz "$1"
}

deploy() {
  build release
  upload out.tgz
}

test_smoke() {
  deploy
}
