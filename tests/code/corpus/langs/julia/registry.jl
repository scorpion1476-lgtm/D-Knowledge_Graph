using Base

module Registry

struct Store
    count::Int
end

function seed(s::Store)
    return s.count
end

end

empty_store() = 0
