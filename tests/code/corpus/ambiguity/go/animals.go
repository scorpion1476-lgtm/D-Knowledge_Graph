package animals

type Dog struct{}

func (d Dog) Speak() string {
	return "woof"
}

type Cat struct{}

func (c Cat) Speak() string {
	return "meow"
}

func RunDog() string {
	d := Dog{}
	return d.Speak()
}

func RunCat() string {
	c := Cat{}
	return c.Speak()
}

func UniqueHelper() int {
	return 1
}

func UsesUnique() int {
	return UniqueHelper()
}
