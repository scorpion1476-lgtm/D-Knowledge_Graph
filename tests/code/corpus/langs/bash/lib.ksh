#!/bin/ksh

source ./env.ksh

configure() {
  export PATH="/usr/local/bin:$PATH"
}

start() {
  configure
  run_server
}
