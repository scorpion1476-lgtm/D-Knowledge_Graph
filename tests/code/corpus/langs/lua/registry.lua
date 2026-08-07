local shapes = require("shapes")

local Registry = {}

function Registry.add(self, item)
  self.items[#self.items + 1] = item
end

function Registry:seed()
  self:add(1)
end

function emptyRegistry()
  return { items = {} }
end
