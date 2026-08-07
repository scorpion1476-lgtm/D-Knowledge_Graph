local shapes = require("shapes")

type Registry = { count: number }

local function emptyRegistry(): Registry
  return { count = 0 }
end

function seed(r: Registry)
  r.count = 1
end
