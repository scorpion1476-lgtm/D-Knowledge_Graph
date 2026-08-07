{ pkgs ? import <nixpkgs> { } }:

let
  shellHook = x: x;

  packages = {
    python = pkgs.python3;
  };
in
{
  inherit shellHook packages;
}
