open Js

type config = {debug: bool}

let normalise = (c: config) => c.debug

let load = () => normalise({debug: true})
