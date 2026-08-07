package advanced

import "fmt"

type Number interface {
	~int | ~float64
}

func Sum[T Number](values []T) T {
	var total T
	for _, v := range values {
		total += v
	}
	return total
}

type Counter struct {
	n int
}

func (c *Counter) Increment() {
	c.n++
}

func (c Counter) Value() int {
	return c.n
}

var Printer = func(s string) {
	fmt.Println(s)
}

func init() {
	Printer("ready")
}
