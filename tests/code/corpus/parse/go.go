package main

import "fmt"

type Point struct {
	X int
}

func (p Point) Norm() int {
	return p.X
}

func makePoint() Point {
	return Point{}
}

func goHelper() int {
	fmt.Println("hi")
	return 1
}
