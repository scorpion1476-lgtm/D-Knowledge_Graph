open Belt

type user = {name: string, age: int}

type role = Admin | Guest

let render = (u: user) => u.name

let make = (u: user) => render(u)

module Inner = {
  let go = () => make({name: "a", age: 1})
}
