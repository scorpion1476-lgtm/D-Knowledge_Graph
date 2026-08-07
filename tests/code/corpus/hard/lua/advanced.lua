local M = {}

M.config = {}

function M.config.load(path)
  return path
end

local function makeCounter()
  local count = 0
  return function()
    count = count + 1
    return count
  end
end

Account = {}
Account.__index = Account

function Account.new(balance)
  return setmetatable({ balance = balance }, Account)
end

function Account:deposit(amount)
  self.balance = self.balance + amount
end

return M
