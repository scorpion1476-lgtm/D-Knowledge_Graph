{ pkgs ? import <nixpkgs> { } }:

let
  toUpper = s: builtins.toString s;

  buildInputs = {
    core = pkgs.hello;
  };

  mkApp = name: name;
in
{
  inherit toUpper mkApp buildInputs;
}
